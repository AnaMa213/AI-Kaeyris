# ADR 0005 — LLMAdapter, providers et premier job d'inférence

- **Statut** : accepté
- **Date** : 2026-05-02
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (architecture monolithe modulaire), ADR 0002 (services 3-fichiers), ADR 0004 (machinerie de jobs), CLAUDE.md §2.4 (séparation services/adapters), CLAUDE.md §3 (DeepInfra par défaut)

## Contexte

Le Jalon 4 introduit **les premiers vrais appels LLM** dans le projet. Trois finalités :

1. **Préparer le Jalon 5 (service JDR)** : ce service aura besoin de résumer des transcriptions.
2. **Respecter la règle anti-vendor** (CLAUDE.md §2.4) : aucun service métier ne doit nommer DeepInfra ni aucun autre fournisseur.
3. **Préserver la portabilité Pi 5 ↔ PC** confirmée par le owner : le projet doit pouvoir tourner sur un Raspberry Pi 5 (cloud uniquement, modèles légers) ou sur un PC RTX 4090 + 32 Go (full local possible). Le swap doit se faire **par variable d'environnement**, sans toucher au code métier.

Une dizaine de questions structurantes :

1. Quel **pattern d'interface** pour les LLM ?
2. Quelles **opérations** l'interface couvre-t-elle dès ce jalon ?
3. Quel **SDK Python** pour parler aux APIs ?
4. **Comment** sélectionner le provider au runtime ?
5. Quelles **implémentations** livre-t-on dans ce jalon ?
6. Comment **catégoriser les erreurs** (retry-able vs définitives) ?
7. **Streaming** dès maintenant ou plus tard ?
8. **Tracking des coûts / tokens** : qui mesure, où ça va ?
9. Quel est le **premier vrai job** qui consomme l'adapter ?
10. **Spec Kit** : on l'introduit comment ?

## Décision

### 1. Interface : `LLMAdapter` en `typing.Protocol`

Définie dans `app/adapters/llm.py`. Pattern **Adapter** (Gamma et al., 1994). Pas de classe abstraite (`ABC`) :

```python
from typing import Protocol

class LLMAdapter(Protocol):
    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str: ...
```

`Protocol` (PEP 544) est préféré à `ABC` parce qu'il permet le **structural subtyping** : une classe est un `LLMAdapter` si elle a les bonnes méthodes, sans héritage explicite. Plus pythonique, mieux outillé par mypy/pyright.

### 2. Opération unique : `complete(system, user, max_tokens)`

L'interface n'expose **qu'une seule méthode**, paramétrée par un prompt système (instructions de comportement) et un prompt utilisateur (le contenu).

**Pourquoi pas de `summarize` dans l'interface** : un résumé n'est pas une opération atomique, c'est une **stratégie métier** (style narratif pour un JDR, formel pour une réunion, technique pour des notes…). Mettre `summarize` dans l'adapter forcerait l'adapter à connaître les styles attendus par chaque service — violation directe de CLAUDE.md §2.4 (l'adapter doit être vendor-neutral ET service-neutral).

À la place : chaque service métier définit son **prompt système** dans son propre `app/services/<feature>/logic.py` et appelle `complete(system=..., user=transcript)`.

**Hors scope de ce jalon** :

- `chat(messages)` (multi-tour) — à introduire si un service en a besoin (Jalon 5+)
- `embed(text)` (vecteurs pour RAG) — Jalon 5+ si on fait du RAG
- `count_tokens(text)` — utile pour estimer le coût avant l'appel ; à introduire au besoin
- Streaming (`complete_stream`) — voir section 7

### 3. SDK Python : `openai>=1.50` (client async)

On utilise le **client OpenAI officiel** (`AsyncOpenAI`) pour parler à tous les providers compatibles OpenAI :

- DeepInfra → `base_url=https://api.deepinfra.com/v1/openai`
- Ollama → `base_url=http://host:11434/v1`
- vLLM → `base_url=http://host:8000/v1`
- Groq → `base_url=https://api.groq.com/openai/v1`
- Together AI → `base_url=https://api.together.xyz/v1`
- OpenAI direct → `base_url=https://api.openai.com/v1`

Un seul SDK couvre 6+ providers. Si un jour on veut Anthropic Claude (pas compatible OpenAI), on ajoutera **une dépendance optionnelle** `anthropic` et un `AnthropicLLMAdapter` dédié.

### 4. Sélection du provider : variables d'environnement

Dans `app/core/config.py` :

```python
LLM_PROVIDER: Literal["deepinfra", "ollama", "openai", "groq", "mock"] = "deepinfra"
LLM_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"
LLM_API_KEY: str = ""
LLM_BASE_URL: str = ""           # vide = défaut du provider
LLM_TIMEOUT_SECONDS: int = 60
LLM_MAX_TOKENS_DEFAULT: int = 1000
```

Une **factory** `get_llm_adapter()` (FastAPI dependency) lit ces vars et instancie l'implémentation appropriée. Cache `lru_cache` pour partager une seule instance par processus.

Pour le owner : sur Pi 5 → DeepInfra cloud. Sur PC RTX 4090 → Ollama local. Switch = 3 lignes de `.env`, zéro changement de code, zéro rebuild Docker.

### 5. Implémentations livrées dans ce Jalon

| Adapter | Rôle | Quand utilisé |
|---|---|---|
| `OpenAICompatibleLLMAdapter` | Implémentation générique pour tous les providers compatibles OpenAI | DeepInfra, Ollama, vLLM, Groq, OpenAI direct |
| `MockLLMAdapter` | Réponses déterministes, instantanées, sans réseau | Tous les tests unitaires de logique métier |

Pas de sous-classe par provider (DeepInfra, Ollama…) tant que tous parlent l'API OpenAI : un seul `OpenAICompatibleLLMAdapter` paramétré par `base_url` + `api_key` + `model`.

### 6. Catégorisation des erreurs

Hiérarchie dans `app/adapters/llm.py` :

```python
class LLMError(Exception): ...                       # racine adaptateur
class TransientLLMError(LLMError): ...               # 5xx, timeout, rate limit, conn refused
class PermanentLLMError(LLMError): ...               # 4xx (sauf 429), prompt invalide, auth
```

Mapping HTTP → exception (dans l'adapter OpenAI-compatible) :

| Statut HTTP | Exception | Justification |
|---|---|---|
| 200 | (pas d'exception) | succès |
| 400 / 422 | `PermanentLLMError` | requête malformée — refaire ne change rien |
| 401 / 403 | `PermanentLLMError` | clé invalide — pas un retry |
| 408 / 504 / timeout | `TransientLLMError` | réessayable |
| 429 | `TransientLLMError` | rate limit upstream — réessayable avec backoff |
| 500 / 502 / 503 | `TransientLLMError` | panne provider — réessayable |
| autres | `PermanentLLMError` | par défaut, fail safe (mieux vaut louper qu'éterniser) |

**Liaison avec la machinerie de jobs (ADR 0004)** : l'adapter raise `TransientLLMError` ou `PermanentLLMError`. Le **wrapper de job** (`app/jobs/llm.py`) les attrape et re-raise les types correspondants (`TransientJobError` / `PermanentJobError`) pour que la retry policy de RQ s'applique correctement.

### 7. Streaming : **non** pour ce jalon

Streaming (réponse partielle au fil de l'eau via SSE/chunks) est utile pour des UX type chat. Notre cas d'usage du Jalon 5 (résumé de transcription long, lancé en job async) ne le justifie pas — le client poll le statut, il n'attend pas en direct.

À introduire si :
- On expose un endpoint conversationnel (chat avec un humain en bout)
- Une UI (Jalon 8+) le réclame

Coût d'ajout futur : ~30 lignes (méthode `complete_stream` retournant un `AsyncIterator[str]`). Pas de dette structurelle créée.

### 8. Tracking de coût / usage tokens

Tous les providers OpenAI-compatibles renvoient un objet `usage` avec `prompt_tokens`, `completion_tokens`, `total_tokens`. On les **logge** côté adapter, **sans** les exposer dans le retour de `complete` :

```python
logger.info(
    "llm.complete",
    extra={
        "provider": self.provider,
        "model": self.model,
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "duration_ms": ...,
    },
)
```

**Pourquoi pas dans le retour** : la signature `summarize() -> str` reste simple. Si une feature métier a besoin du compte exact (facturation, quota), on ajoutera une méthode `complete_with_usage() -> tuple[str, Usage]` plus tard. YAGNI maintenant.

Le logging structuré est prêt pour structlog (Jalon 6). Aujourd'hui ça part dans `logging` standard.

### 9. Premier vrai job : `app/jobs/llm.py::llm_complete`

Module `app/jobs/llm.py` :

```python
from app.adapters.llm import get_llm_adapter, TransientLLMError, PermanentLLMError
from app.jobs import TransientJobError, PermanentJobError

def llm_complete(*, system: str, user: str, max_tokens: int = 500) -> str:
    """Generic LLM call, executed in a worker.

    Each service supplies its own system prompt; this job stays neutral.
    Adapter errors are mapped to job error types so RQ's retry policy
    applies (TransientLLMError → retry, PermanentLLMError → no retry).
    """
    adapter = get_llm_adapter()
    try:
        return asyncio.run(adapter.complete(
            system=system, user=user, max_tokens=max_tokens
        ))
    except TransientLLMError as exc:
        raise TransientJobError(str(exc)) from exc
    except PermanentLLMError as exc:
        raise PermanentJobError(str(exc)) from exc
```

`asyncio.run` est nécessaire car RQ exécute des jobs **synchrones** mais notre adapter est async (le SDK OpenAI moderne est async-first). Le worker tourne dans un process dédié, créer une event loop par job est acceptable.

Au Jalon 5, le service JDR créera son propre `app/jobs/jdr.py::summarize_jdr_session(transcript)` qui appellera l'adapter avec **son prompt système narratif**, distinct de celui qu'utiliserait un futur service de comptes-rendus de réunions.

Test : on enqueue le job avec `MockLLMAdapter`, on assert que la sortie matche.

### 10. Spec Kit : introduction documentaire dans ce jalon

Spec Kit (https://github.com/github/spec-kit) est un projet GitHub qui propose un workflow **Spec-Driven Development** via des slash-commands (`/specify`, `/plan`, `/tasks`). Conceptuellement : on rédige une spec, l'outil aide à dériver un plan d'implémentation et une liste de tâches.

**Pour ce jalon** : pas d'installation forcée. On **documente** dans `Jalon4.md` :

- Ce qu'est Spec Kit
- Pourquoi le projet pourrait l'utiliser au Jalon 5+ (services métier non triviaux)
- Le pattern qu'on a utilisé jusqu'ici (ADR + walkthrough Jalon-N.md) est déjà spec-driven dans l'esprit, sans l'outillage

Si un futur jalon a une feature complexe, on essaiera Spec Kit en pratique. À ce stade, l'introduction reste théorique pour ne pas alourdir.

## Alternatives écartées

### Pour l'interface

| Alternative | Pourquoi écartée |
|---|---|
| `ABC` (classe abstraite) au lieu de `Protocol` | Force l'héritage explicite, moins pythonique, moins bien outillé par les type checkers modernes |
| `summarize(text)` baked-in dans l'interface | Forcerait l'adapter à connaître les styles métier (narratif JDR, formel réunion, technique notes…). Casse la séparation services/adapters CLAUDE.md §2.4. Le prompt système est de la logique métier, sa place est dans `app/services/<feature>/`. |
| `complete(prompt: str)` à un seul argument | Mélange instructions et contenu, force le service à concaténer manuellement. La distinction `system`/`user` est l'API standard de tous les LLM modernes (chat completions). |
| Interface enrichie d'emblée (chat, embed, count_tokens) | YAGNI strict (CLAUDE.md §2.3). On ajoute quand un service en a besoin. |
| Une interface par opération (`Summarizer`, `Completer`, …) | Complexité accrue sans bénéfice ; Python n'a pas le problème des interfaces "fat" comme Java. |

### Pour le SDK

| Alternative | Pourquoi écartée |
|---|---|
| `httpx` direct, sans SDK | Réimplémenter retries, parsing d'erreurs, gestion des `usage`. Beaucoup de code pour rien. |
| `litellm` (lib qui unifie tous les providers) | Couche d'abstraction supplémentaire, moins de contrôle, dépendance plus lourde. À reconsidérer si on doit supporter 5+ providers natifs. |
| SDK natif par provider (deepinfra, anthropic, groq) | Multiplie les dépendances. Le SDK `openai` couvre déjà tous les providers compatibles OpenAI. |
| Async-only client : `aiohttp` direct | Marche mais oblige à tout réécrire. Le SDK `openai` est async-first et bien maintenu. |

### Pour la sélection du provider

| Alternative | Pourquoi écartée |
|---|---|
| Détecter le provider depuis l'URL | Magique, fragile, debug pénible |
| Plusieurs sous-classes (`DeepInfraLLMAdapter`, `OllamaLLMAdapter`) | Tant que tous parlent OpenAI-compatible, c'est de la duplication. Un seul `OpenAICompatibleLLMAdapter` paramétré suffit. |
| Lire un fichier `providers.yaml` | Surdimensionné. Env vars suffisent (12-Factor). |
| Hardcoder DeepInfra par défaut sans permettre le switch | Casse la portabilité Pi/PC voulue par l'owner. Inacceptable. |

### Pour les erreurs

| Alternative | Pourquoi écartée |
|---|---|
| Une seule classe `LLMError` | Empêche d'exprimer la sémantique transient/permanent côté retry policy |
| Réutiliser directement `TransientJobError`/`PermanentJobError` côté adapter | Couple l'adapter à la couche jobs. L'adapter doit pouvoir être appelé hors d'un job (ex : depuis un endpoint sync au Jalon 8). |
| Mapper toutes les 4xx en transient (au cas où) | Pollue les logs, retries inutiles, peut épuiser un quota |
| Pas de mapping HTTP → exception, lever brut | Casse la séparation des concerns ; le métier doit pouvoir réagir uniformément |

### Pour le streaming et les coûts

| Alternative | Pourquoi écartée |
|---|---|
| Streaming dès le Jalon 4 | YAGNI ; pas d'UI temps réel à ce stade |
| Cost tracking exposé dans la signature | Pollue toutes les signatures pour un besoin qui n'est pas universel |
| Cost tracking ignoré (pas de log) | Intolérable : on perd la traçabilité de la consommation, indispensable pour DeepInfra payant |
| Cost tracking dans une DB (table `usage`) | Pas de DB encore (Jalon 5+). Logging structuré est l'antichambre, on remontera plus tard. |

### Pour le job d'inférence

| Alternative | Pourquoi écartée |
|---|---|
| Job async natif (`async def`) avec un worker custom | RQ standard est synchrone ; ajouter un worker async = complexité non justifiée pour ce jalon |
| Pas de premier job dans ce jalon, attendre Jalon 5 | Ne validerait pas l'intégration adapter ↔ jobs. Mieux d'avoir un cas concret testé. |
| Job qui appelle l'adapter en mode sync direct (sans `asyncio.run`) | L'adapter est async (SDK OpenAI moderne) ; il faut bien franchir la frontière quelque part |

### Pour Spec Kit

| Alternative | Pourquoi écartée |
|---|---|
| Installer Spec Kit et l'utiliser dès maintenant | Inertie supplémentaire, courbe d'apprentissage en plus du Jalon 4. À voir au Jalon 5. |
| L'ignorer complètement | CLAUDE.md §5 le mentionne explicitement ; au moins le documenter et savoir où on va |
| Adopter un autre framework SDD (DDD strict, BDD…) | Hors scope ; CLAUDE.md §9 dit pragmatique. |

## Conséquences

**Positives**

- Le code métier devient **portable** entre cloud et local sans modification (DeepInfra, Ollama, vLLM, …)
- Tests unitaires triviaux : `MockLLMAdapter` au lieu d'un vrai appel réseau
- L'archi multi-provider du Jalon 9 (fallback) sera implémentable comme un `LLMAdapter` qui en compose deux autres (pattern Decorator)
- L'usage tokens est tracé dès le premier appel — pas de surprise sur la facture DeepInfra
- Le mapping erreurs → jobs respecte la retry policy du Jalon 3 sans surprise
- Une seule dépendance externe ajoutée (`openai>=1.50`) couvre 6+ providers

**Négatives / acceptées**

- `asyncio.run` dans le job ajoute une event loop par exécution. Coût mesuré : ~5-10 ms, négligeable face à des appels LLM de plusieurs secondes.
- Les providers Anthropic / Cohere / etc. (non compatibles OpenAI) demanderont leur propre adapter quand on les voudra (acceptable, prévu).
- L'absence de streaming oblige les UI temps réel (futures) à attendre le résultat complet du job. Pas un problème pour l'usage Jalon 5 (résumé batch).
- Le tracking des coûts est dans les logs, pas dans une vue agrégée. Un dashboard viendra au Jalon 6 (observabilité) ou plus tard.
- Le `MockLLMAdapter` peut diverger du comportement réel des providers (un test passe en mock, casse en prod). Mitigation : un test d'intégration manuel après chaque changement structurant de l'adapter (documenté dans memo.md).

**Conditions de re-évaluation** (cet ADR sera "superseded" si)

- On supporte Anthropic / Cohere / Mistral natif → ajouter un adapter dédié, sans toucher aux services existants
- On a besoin de streaming (UI conversationnelle, dashboard temps réel) → ajouter `complete_stream` ou un `StreamingLLMAdapter`
- On a besoin du tracking de coût exposé (quota par utilisateur, facturation interne) → étendre la signature ou créer `complete_with_usage`
- On dépasse 5 providers OpenAI-compatibles → considérer `litellm` comme couche d'unification
- Les coûts logs ne suffisent plus → introduire une DB ou un service Prometheus de comptage

## Références

- Gamma, Helm, Johnson, Vlissides, *Design Patterns: Elements of Reusable Object-Oriented Software* (1994) — pattern Adapter, pattern Decorator
- PEP 544 — *Protocols: Structural subtyping (static duck typing)* — https://peps.python.org/pep-0544/
- OpenAI Python SDK documentation — https://github.com/openai/openai-python
- DeepInfra OpenAI-compatible API — https://deepinfra.com/docs/advanced/openai_api
- Ollama OpenAI compatibility — https://github.com/ollama/ollama/blob/main/docs/openai.md
- vLLM OpenAI server — https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html
- GitHub Spec Kit — https://github.com/github/spec-kit
- 12-Factor App §IV (Backing services) — https://12factor.net/backing-services
- ADR 0001 (architecture monolithe modulaire — fixe `app/adapters/` comme couche dédiée)
- ADR 0004 (machinerie de jobs — l'adapter sera consommé via les jobs)
