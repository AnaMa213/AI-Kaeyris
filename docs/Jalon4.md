# Jalon 4 — Adapters + Spec Kit intro (walkthrough pédagogique)

> Document explicatif détaillé : étapes, **pourquoi**, alternatives écartées, normes respectées, limitations.
> Public : toi qui apprends. À relire dans 6 mois.

---

## Sommaire

1. [Objectif et menaces couvertes](#1-objectif-et-menaces-couvertes)
2. [Étape 0 — ADR 0005 avant le code](#2-étape-0--adr-0005-avant-le-code)
3. [Étape 1 — Dépendance `openai>=1.50`](#3-étape-1--dépendance-openai150)
4. [Étape 2 — Configuration LLM](#4-étape-2--configuration-llm)
5. [Étape 3 — Interface `LLMAdapter`](#5-étape-3--interface-llmadapter)
6. [Étape 4 — `OpenAICompatibleLLMAdapter`](#6-étape-4--openaicompatibleadapter)
7. [Étape 5 — `MockLLMAdapter` et factory](#7-étape-5--mockllmadapter-et-factory)
8. [Étape 6 — Premier vrai job `llm_complete`](#8-étape-6--premier-vrai-job-llm_complete)
9. [Étape 7 — Tests](#9-étape-7--tests)
10. [Étape 8 — Spec Kit (introduction documentaire)](#10-étape-8--spec-kit-introduction-documentaire)
11. [Normes et bonnes pratiques respectées](#11-normes-et-bonnes-pratiques-respectées)
12. [Choix alternatifs envisagés et écartés](#12-choix-alternatifs-envisagés-et-écartés)
13. [Limitations acceptées](#13-limitations-acceptées)
14. [Ce que ce jalon prépare pour la suite](#14-ce-que-ce-jalon-prépare-pour-la-suite)

---

## 1. Objectif et menaces couvertes

### Selon CLAUDE.md §5

> Jalon 4 : **LLMAdapter agnostic, DeepInfra impl, introduce Spec Kit workflow**

### Pourquoi maintenant

C'est le jalon où le projet rencontre **enfin** son métier : appeler un LLM. Trois finalités structurantes :

1. **Préparer le Jalon 5** (service JDR) qui aura besoin de résumer des transcriptions
2. **Verrouiller l'anti-vendor** (CLAUDE.md §2.4) — aucun service métier ne doit nommer DeepInfra
3. **Préserver la portabilité Pi 5 / PC RTX 4090** demandée par l'owner — switcher de provider doit se faire en `.env`, pas dans le code

### Menaces / risques adressés

| Risque | Mitigation |
|---|---|
| **Vendor lock-in DeepInfra** | Pattern Adapter : tout vendor s'enchaîne derrière `LLMAdapter` |
| **Coûts cachés** (consommation LLM non tracée) | Logging des `usage.prompt_tokens` / `completion_tokens` à chaque appel |
| **Cascading failures** (LLM down → tout down) | Mapping erreur transient/permanent + retry policy de RQ (ADR 0004) |
| **Tests lents/coûteux** (vrais appels LLM en CI) | `MockLLMAdapter` — déterministe, instantané, sans réseau |
| **Confusion d'archi** (où mettre les prompts ?) | Décision : **adapter neutre**, prompts métier dans les services |

### Hors scope

- ❌ Service métier réel utilisant LLM (Jalon 5 — JDR)
- ❌ Streaming (`complete_stream`) — pas d'UI temps réel
- ❌ `embed`, `chat` multi-tour — YAGNI, ajouter si besoin
- ❌ TranscriptionAdapter (Whisper) — Jalon 5
- ❌ Fallback cloud→local automatique — Jalon 9 optionnel
- ❌ Spec Kit installé/configuré — introduction documentaire seulement

---

## 2. Étape 0 — ADR 0005 avant le code

### Ce qui a été fait

Rédaction de [`docs/adr/0005-llm-adapter-and-providers.md`](./adr/0005-llm-adapter-and-providers.md). 10 décisions :

1. Interface `typing.Protocol` (PEP 544)
2. **Une seule méthode** `complete(system, user, max_tokens)` *(décision affinée en cours de jalon — voir étape 5)*
3. SDK `openai>=1.50` (couvre 6+ providers)
4. Sélection provider via env vars
5. Implémentations livrées : `OpenAICompatibleLLMAdapter` + `MockLLMAdapter`
6. Erreurs : `LLMError` racine, `TransientLLMError` vs `PermanentLLMError`, mapping HTTP explicite
7. Streaming reporté
8. Cost tracking : loggé, pas exposé
9. Premier job : `llm_complete` (générique)
10. Spec Kit : introduction documentaire seulement

20+ alternatives écartées avec leur raison.

### Le moment clé du jalon : la simplification de l'interface

Initialement, l'ADR proposait deux méthodes : `complete` et `summarize`. Pendant la conception, une question pertinente du owner :

> "Le prompt du summarize pourra être adapté en fonction du service. Pour mon service JDR, je voudrais un résumé narratif. Pour un autre service de réunions, je voudrais un résumé formel. Je devrais réécrire des fonctions summarize différentes ?"

Réponse : **non — on retire `summarize` de l'interface**. Le résumé n'est pas une opération atomique mais une **stratégie métier**. Mettre `summarize` dans l'adapter forcerait l'adapter à connaître les styles attendus → violation directe de CLAUDE.md §2.4.

L'interface a donc été simplifiée à **une seule méthode `complete(system, user, max_tokens)`**. Chaque service met son propre `system` prompt dans son `app/services/<feature>/logic.py`.

C'est le genre de question structurante qu'on attrape en concevant l'ADR avant de coder — coût d'erreur si on l'avait écrit puis modifié : 30 minutes vs 2 heures de refacto.

---

## 3. Étape 1 — Dépendance `openai>=1.50`

### Ce qui a été fait

Ajout dans [`pyproject.toml`](../pyproject.toml) :
```toml
dependencies = [
    ...
    "openai>=1.50",
]
```

`pip install -e ".[dev]"` → `openai 2.33.0` (la version "majeure 1.x" est devenue 2.x récemment, l'API n'a pas changé).

### Pourquoi le SDK `openai` officiel et pas `httpx` direct

Le SDK `openai` :
- Maintenu par OpenAI eux-mêmes
- Async-first (`AsyncOpenAI`)
- Gère retries internes, timeouts, sérialisation/désérialisation Pydantic
- Expose des exceptions typées (`AuthenticationError`, `RateLimitError`, etc.) qu'on peut catch catégoriquement
- Supporte n'importe quelle URL `base_url` → DeepInfra, Ollama, Groq, vLLM, Together AI, OpenAI direct

Faire `httpx` direct nous obligerait à réimplémenter ~200 lignes de logique connue. Cas typique d'utilisation d'une lib officielle.

### Pourquoi pas `litellm` (autre lib qui unifie tous les providers)

`litellm` (https://github.com/BerriAI/litellm) abstrait **par-dessus** plusieurs SDKs (OpenAI, Anthropic, Cohere, etc.). Plus puissant mais :
- Une couche d'abstraction de plus = magie + complexité
- On n'a pas (encore) besoin de providers non-OpenAI-compatibles (Anthropic Claude direct, Cohere…)
- Tant que tous nos providers parlent OpenAI-compatible, le SDK `openai` natif suffit

À reconsidérer si on supporte 5+ providers natifs distincts.

### Alternatives écartées

- **`httpx` direct** : trop de réimplémentation
- **`litellm`** : surdimensionné aujourd'hui
- **SDK natif par provider** (`deepinfra-py`, `groq`, `anthropic`...) : multiplie les déps, pas justifié

---

## 4. Étape 2 — Configuration LLM

### Ce qui a été fait

6 nouveaux champs dans [`app/core/config.py`](../app/core/config.py) :

```python
LLM_PROVIDER: str = "deepinfra"
LLM_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"
LLM_API_KEY: str = ""
LLM_BASE_URL: str = ""
LLM_TIMEOUT_SECONDS: float = 60.0
LLM_MAX_TOKENS_DEFAULT: int = 1000
```

Documentés dans [`.env.example`](../.env.example) avec un exemple Ollama commenté pour le RTX 4090.

### Pourquoi `LLM_API_KEY` séparé d'`API_KEYS` (Jalon 2)

Ce sont **deux choses différentes** :

| Variable | Direction | Rôle |
|---|---|---|
| `API_KEYS` | clients → notre API | Liste des clés que **nos clients** présentent pour nous appeler |
| `LLM_API_KEY` | notre API → DeepInfra | Clé que **nous** présentons à DeepInfra pour les appeler |

Confondre les deux serait une faute : la clé DeepInfra est privée, les `API_KEYS` sont distribuées aux clients. Deux env vars distinctes garantissent la clarté.

### Pourquoi `LLM_BASE_URL` peut être vide

Si vide, la factory utilise le **base URL par défaut** du provider (un dict statique dans `app/adapters/llm.py`). Permet une config minimale pour les providers connus (juste `LLM_PROVIDER=deepinfra` + `LLM_API_KEY=...`). Si on veut overrider (ex : un proxy interne), on remplit `LLM_BASE_URL`.

### Pourquoi `LLM_PROVIDER: str` plutôt que `Literal[...]`

J'ai choisi `str` plutôt que `Literal["deepinfra", "ollama", ...]` pour :
- Permettre d'ajouter facilement un provider sans toucher à `config.py`
- Garder la validation côté factory (qui vérifie `provider not in _DEFAULT_BASE_URLS`)

C'est un compromis : on perd un peu de type safety au profit d'extensibilité. Acceptable à notre échelle.

---

## 5. Étape 3 — Interface `LLMAdapter`

### Ce qui a été fait

Dans [`app/adapters/llm.py`](../app/adapters/llm.py) :

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

### Pourquoi `Protocol` et pas `ABC`

Comparaison :

| Aspect | `ABC` | `Protocol` |
|---|---|---|
| Héritage | obligatoire (`class X(LLMAdapter):`) | non requis |
| Vérification | runtime (instanciation) | statique (mypy/pyright) |
| Mocking | classes mock doivent hériter | n'importe quelle classe avec la bonne forme |
| Adoption progressive | difficile (refacto requis) | trivial |
| Style Python moderne | années 2000 | 2018+ |

`Protocol` est plus **pythonique** (duck typing typé) et plus moderne (PEP 544, Python 3.8+). Référence : https://realpython.com/python-protocol/

### Pourquoi `*, system, user, max_tokens` (kwargs-only)

Le `*,` impose que ces paramètres soient passés par nom (`complete(system="...", user="...", max_tokens=10)`). Avantages :
- Lisibilité au site d'appel (on lit explicitement quel argument)
- Pas de risque d'inversion (`complete(user, system)` au lieu de `complete(system, user)`)
- Permet d'ajouter des paramètres optionnels (futur `temperature`, `top_p`) sans casser les appelants

### Alternatives écartées

- **`ABC`** : moins moderne, plus de cérémonie pour mocker
- **Multiple méthodes** (`summarize`, `chat`, `embed`...) : YAGNI, et `summarize` violait la séparation services/adapters
- **Méthode acceptant un seul `prompt: str`** : forcerait à concaténer manuellement system + user, plus fragile
- **Interface async + sync** : YAGNI, le SDK `openai` est async

---

## 6. Étape 4 — `OpenAICompatibleLLMAdapter`

### Ce qui a été fait

Une seule classe concrète qui sert pour **tous** les providers compatibles OpenAI :

```python
class OpenAICompatibleLLMAdapter:
    def __init__(self, *, provider, model, api_key, base_url=None, timeout_seconds=60.0):
        ...
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    async def complete(self, *, system, user, max_tokens):
        # appel SDK + mapping erreurs + logging usage
```

### Pourquoi UNE classe pour 6+ providers

Tous les providers OpenAI-compatibles parlent **exactement la même API** : `/v1/chat/completions`, mêmes champs (`model`, `messages`, `max_tokens`...), mêmes formats d'erreur. **Aucune raison** de dupliquer 200 lignes pour `DeepInfraLLMAdapter`, `OllamaLLMAdapter`, etc.

Une seule classe paramétrée par `base_url` + `api_key` + `model`. La factory choisit la bonne combinaison selon `LLM_PROVIDER`.

Le jour où un provider non-OpenAI-compatible arrive (Anthropic Claude, Cohere natif), on créera une classe **séparée** (`AnthropicLLMAdapter`, par exemple). C'est le moment où la duplication devient justifiée — quand l'API diffère vraiment.

### Mapping HTTP → exceptions

Trois groupes :

```python
# Transient — RQ doit retry
APITimeoutError       → TransientLLMError
APIConnectionError    → TransientLLMError
RateLimitError (429)  → TransientLLMError
InternalServerError (5xx) → TransientLLMError

# Permanent — RQ ne doit pas retry
AuthenticationError (401)    → PermanentLLMError
PermissionDeniedError (403)  → PermanentLLMError
BadRequestError (400)        → PermanentLLMError
NotFoundError (404)          → PermanentLLMError
UnprocessableEntityError (422) → PermanentLLMError

# Fallback : autres status
APIStatusError 5xx → TransientLLMError
APIStatusError 4xx → PermanentLLMError
APIError (catch-all) → PermanentLLMError (fail safe)
```

### Pourquoi le fallback "fail-safe" sur `PermanentLLMError`

Si une erreur inconnue surgit, on choisit **par défaut** de la traiter comme permanente. Pourquoi ? Parce que retry sur erreur inconnue, c'est :
- Polluer les logs de retries inutiles
- Consommer des ressources (quota DeepInfra) sur un cas qu'on ne comprend pas
- Masquer un bug

Mieux vaut faire échouer une fois et investiguer.

### Logging structuré des `usage`

```python
logger.info(
    "llm.complete",
    extra={
        "provider": self.provider,
        "model": self.model,
        "prompt_tokens": ...,
        "completion_tokens": ...,
        "duration_ms": ...,
    },
)
```

Pour aujourd'hui, c'est dans `logging` standard. Au Jalon 6, structlog reprend ce pattern et envoie en JSON vers une stack d'observabilité. Pas de réécriture de l'adapter — le format `extra={...}` est compatible structlog.

### Pourquoi pas exposer `usage` dans le retour

Garder la signature `complete() -> str` simple. Si un service métier a besoin du compte précis (facturation, quota), on créera `complete_with_usage() -> tuple[str, Usage]`. YAGNI maintenant.

---

## 7. Étape 5 — `MockLLMAdapter` et factory

### `MockLLMAdapter`

```python
class MockLLMAdapter:
    async def complete(self, *, system, user, max_tokens):
        return f"[mock complete] system={system[:30]!r} user={user[:30]!r}"
```

Déterministe, instantané, sans réseau. **Le format exact est partie du contrat** : les tests assert dessus, donc tout changement casse les tests intentionnellement.

### Factory `build_llm_adapter`

```python
def build_llm_adapter() -> LLMAdapter:
    provider = settings.LLM_PROVIDER.lower()
    if provider == "mock":
        return MockLLMAdapter()
    if provider not in _DEFAULT_BASE_URLS:
        raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r}")
    if not settings.LLM_API_KEY and provider not in {"ollama", "vllm"}:
        raise RuntimeError("LLM_API_KEY is required for cloud providers")
    return OpenAICompatibleLLMAdapter(...)
```

### Pourquoi tolérer une clé vide pour `ollama` et `vllm`

Ces providers tournent en **local**. Ils n'ont pas de notion d'authentification ; ils ignorent le contenu de la clé. Demander à l'utilisateur de mettre une vraie clé serait absurde — on tolère vide (et la factory remplit `"noop"` pour satisfaire le SDK).

DeepInfra/OpenAI/Groq exigent une vraie clé → la factory rejette si vide.

### `get_llm_adapter` avec `lru_cache`

```python
@lru_cache(maxsize=1)
def get_llm_adapter() -> LLMAdapter:
    return build_llm_adapter()
```

Un seul adapter par processus → connection pool partagé. Les tests appellent `get_llm_adapter.cache_clear()` (fixture `autouse=True`) pour ne pas polluer entre tests.

### Alternatives écartées

- **`build_llm_adapter` instancié à l'import** (module-level) : empêche d'ajuster les settings après import (tests, configs dynamiques).
- **Sous-classes par provider** (`DeepInfraLLMAdapter(OpenAICompatibleLLMAdapter)`) : fausse séparation puisqu'elles ne diffèrent qu'en config.
- **MockLLMAdapter retourne du Lorem ipsum aléatoire** : non déterministe → tests flaky.

---

## 8. Étape 6 — Premier vrai job `llm_complete`

### Ce qui a été fait

```python
# app/jobs/llm.py
def llm_complete(*, system: str, user: str, max_tokens: int = 500) -> str:
    adapter = get_llm_adapter()
    try:
        return asyncio.run(adapter.complete(system=system, user=user, max_tokens=max_tokens))
    except TransientLLMError as exc:
        raise TransientJobError(str(exc)) from exc
    except PermanentLLMError as exc:
        raise PermanentJobError(str(exc)) from exc
```

### Pourquoi un job générique et pas spécifique JDR

CLAUDE.md §2.3 — YAGNI. On n'a pas encore de service JDR. Un job générique suffit pour valider la chaîne (enqueue → worker → adapter → réponse). Au Jalon 5, le service JDR créera son propre `app/jobs/jdr.py::summarize_jdr_session(transcript)` qui appellera l'adapter avec **son prompt système narratif**.

`llm_complete` restera ensuite pour les usages ad-hoc ou pourra disparaître. Il n'introduit pas de dette structurelle.

### Pourquoi `asyncio.run` dans un job sync

RQ exécute les jobs en **synchrone**. Notre adapter est **async** (SDK `openai` moderne). Il faut franchir la frontière quelque part :
- `asyncio.run(coro)` crée une nouvelle event loop, exécute la coroutine, ferme la loop
- Coût : ~5 ms par appel
- Acceptable face à des appels LLM de plusieurs secondes

Alternative envisagée : passer RQ en mode async (`rq` 2.x supporte des workers async via `rq worker --serializer ...` et des extensions). Trop de complexité pour zéro gain à notre échelle.

### Mapping erreurs LLM → erreurs job

Cohérent avec ADR 0004 :
- `TransientLLMError` → `TransientJobError` → RQ retry 3 fois `[10s, 30s, 90s]`
- `PermanentLLMError` → `PermanentJobError` → RQ fail immédiatement

Le mapping est trivial mais explicite : il documente que la décision de retry est prise au niveau adapter (qui sait HTTP) et propagée à RQ (qui décide d'agir).

### Alternatives écartées

- **Pas de wrapper, le service appelle l'adapter direct** : casse le découpage "métier prépare le prompt, jobs exécute"
- **Job qui fait `summarize` au lieu de `complete`** : forcerait à mettre le prompt dans le job (donc dans `app/jobs/`) au lieu du service. Mauvaise frontière.
- **Fonction async** : RQ n'aime pas les coroutines comme jobs

---

## 9. Étape 7 — Tests

### Vue d'ensemble

18 nouveaux tests, **47 verts** au total :

```
tests/adapters/test_llm.py       14 tests
tests/jobs/test_llm.py            4 tests
```

### Décomposition `test_llm.py` (adapter)

**Mock adapter (1)** : retour déterministe, contient les inputs

**Factory (5)** :
- mode `mock` → MockLLMAdapter
- provider inconnu → RuntimeError
- cloud sans `LLM_API_KEY` → RuntimeError
- local (`ollama`) sans clé → OK
- `get_llm_adapter` cache l'instance

**Error mapping (8)** : paramétrés sur 8 types d'exceptions OpenAI :
- 4 transient : `APITimeoutError`, `APIConnectionError`, `RateLimitError`, `InternalServerError`
- 4 permanent : `AuthenticationError`, `PermissionDeniedError`, `BadRequestError`, `UnprocessableEntityError`

### Construction d'instances `APIStatusError` en test

Les exceptions OpenAI demandent un `Response` réel à leur constructeur (lib `httpx`). Pour les tests, on bypasse :

```python
def _make_status_exc(cls, status_code: int) -> Exception:
    exc = cls.__new__(cls)
    exc.status_code = status_code
    exc.message = f"synthetic {status_code}"
    exc.body = None
    Exception.__init__(exc, exc.message)
    return exc
```

C'est un peu hack mais bien encadré : on ne touche que les attributs que l'adapter inspecte.

### Décomposition `test_llm.py` (job)

- `llm_complete` avec mock → renvoie la chaîne déterministe
- `TransientLLMError` → `TransientJobError`
- `PermanentLLMError` → `PermanentJobError`
- Mock adapter direct (validation Protocol structurel)

### Pourquoi un fixture `autouse=True` qui clear le cache

```python
@pytest.fixture(autouse=True)
def _clear_adapter_cache():
    get_llm_adapter.cache_clear()
    yield
    get_llm_adapter.cache_clear()
```

`get_llm_adapter` utilise `lru_cache`. Si un test patche `LLM_PROVIDER=mock`, la première instance est cachée. Si un autre test ensuite patche `LLM_PROVIDER=deepinfra` mais ne clear pas le cache, il récupère l'ancienne instance MockLLMAdapter → faux résultats. Le fixture `autouse=True` automatise le clear avant ET après chaque test.

### Le subtil `monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", ...)`

```python
monkeypatch.setattr("app.adapters.llm.settings.LLM_PROVIDER", "mock")
```

Pourquoi pas `app.core.config.settings.LLM_PROVIDER` ? **Parce que `settings` est importé dans `app.adapters.llm`** au moment de l'import du module. Le patch doit cibler le **nom local** (`app.adapters.llm.settings`), pas le module source. Subtilité Python à connaître quand on monkey-patch.

### Alternatives écartées

- **Tests d'intégration avec un vrai DeepInfra** : coûteux, lents, dépendent du réseau. Manuels seulement.
- **Tests via `vcr.py`** (enregistrement de réponses) : marche mais ajoute une dep et de la complexité. fakeredis-style mais pour HTTP.
- **Patcher tout le SDK `openai`** au niveau module : trop large, masque des bugs réels.

---

## 10. Étape 8 — Spec Kit (introduction documentaire)

### Qu'est-ce que Spec Kit

GitHub Spec Kit (https://github.com/github/spec-kit) est un projet open-source qui propose un workflow **Spec-Driven Development** :

1. **`/specify`** : on rédige une spec lisible humain (intention, comportement attendu, exemples)
2. **`/plan`** : un agent dérive un plan d'implémentation depuis la spec
3. **`/tasks`** : le plan est décomposé en tâches concrètes
4. **Implémentation** : on code en suivant les tâches

L'idée : **commencer par ce qu'on veut**, pas par comment on va coder. Réduit le risque d'over-engineering ou de divergence intention/implémentation.

### Pourquoi pas l'installer dans ce jalon

Trois raisons :

1. **Inertie** : ça ajoute une couche d'outillage à apprendre maintenant alors qu'on apprend déjà beaucoup
2. **Notre process actuel est déjà spec-driven** : ADR + question/réponse avec l'owner avant code = équivalent fonctionnel
3. **Mieux essayer en pratique au Jalon 5** : le service JDR aura une feature complète, c'est l'occasion de tester si Spec Kit apporte plus que notre process actuel

### Le pattern qu'on a utilisé jusqu'ici

Dans chaque jalon :
1. Discussion préliminaire avec l'owner (questions à trancher)
2. Rédaction d'un ADR (statut "proposé")
3. Validation par l'owner (statut "accepté")
4. Implémentation
5. Tests + walkthrough Jalon-N.md

C'est **du Spec-Driven Development sans l'outillage**. L'ADR fait office de spec. La discussion fait office de plan.

### Recommandation pour l'avenir

Au Jalon 5, **essayer Spec Kit en pratique** sur la feature JDR. Si ça apporte plus que notre process, l'adopter. Sinon, garder ADR + walkthrough.

---

## 11. Normes et bonnes pratiques respectées

| Norme | Application |
|---|---|
| **CLAUDE.md §2.4** | Aucun nom de vendor dans `app/services/` ni `app/core/` (sauf adapter) |
| **CLAUDE.md §2.3 YAGNI** | Une seule méthode `complete`, pas de streaming, pas d'embed |
| **PEP 544 Protocols** | Interface `LLMAdapter` en Protocol, pas ABC |
| **12-Factor §IV Backing services** | LLM provider attaché via URL, swappable |
| **12-Factor §III Config** | Tout via env vars |
| **Pattern Adapter (GoF 1994)** | Application stricte |
| **Pattern Decorator (GoF 1994)** | Préparé pour Jalon 9 (FallbackLLMAdapter) |
| **Open-Closed Principle** | Ajouter un service = créer un dossier, jamais modifier l'adapter |
| **Test pyramid** | Unit (Mock) >> Integration (real DeepInfra, manuel) >> E2E (Jalon 8) |
| **Fail-safe defaults** | Erreur inconnue → traiter comme permanent |

---

## 12. Choix alternatifs envisagés et écartés

### Pour l'interface

| Alternative | Pourquoi écartée |
|---|---|
| `ABC` au lieu de `Protocol` | Moins moderne, héritage explicite, plus de cérémonie |
| `summarize` baked-in | Force le métier dans l'adapter, casse §2.4 |
| `complete(prompt: str)` à un seul argument | Mélange instructions + contenu, fragile |
| Multiple méthodes (`chat`, `embed`...) | YAGNI |

### Pour le SDK

| Alternative | Pourquoi écartée |
|---|---|
| `httpx` direct | Réimplémentation inutile |
| `litellm` | Couche d'abstraction supplémentaire, pas justifiée |
| SDK natif par provider | Multiplie les déps |

### Pour la sélection du provider

| Alternative | Pourquoi écartée |
|---|---|
| Sous-classes par provider | Duplication tant qu'API identique |
| Fichier `providers.yaml` | Surdimensionné, env vars suffisent |
| Détection magique depuis URL | Fragile, debug pénible |

### Pour le job

| Alternative | Pourquoi écartée |
|---|---|
| Job `summarize_text` métier | Force le métier dans `app/jobs/` |
| Async-native worker (RQ 2.x) | Complexité non justifiée |
| Pas de premier job, attendre Jalon 5 | Ne validerait pas la chaîne adapter↔jobs |

### Pour les erreurs

| Alternative | Pourquoi écartée |
|---|---|
| Une seule classe `LLMError` | Empêche d'exprimer transient/permanent |
| Réutiliser `Job*Error` côté adapter | Couple l'adapter à la couche jobs |
| Mapper toutes les 4xx en transient | Pollue les logs, retries inutiles |
| Aucun mapping (lever brut) | Casse séparation des concerns |

---

## 13. Limitations acceptées

| Limitation | Pourquoi acceptée | À reprendre quand |
|---|---|---|
| Pas de streaming | Pas d'UI temps réel, ~30 lignes futures | UI conversationnelle (Jalon 8+) |
| Pas d'`embed` ni `chat` | YAGNI | RAG ou multi-tour requis |
| Pas de `count_tokens` | YAGNI | Estimation de coût avant appel |
| Pas de fallback automatique | Pattern Decorator possible plus tard | Jalon 9 (optionnel) |
| Pas de validation modèle | 404 → PermanentLLMError lisible | Pas un vrai problème |
| MockLLMAdapter pas de simulation latence/erreur | Tests dédiés patchent | Si besoin de stress-test |
| Pas d'audit log appels LLM agrégé | Log structuré seulement | Jalon 6 (observabilité) |
| Spec Kit non installé | Notre process déjà spec-driven | Jalon 5 (essai pratique) |

---

## 14. Ce que ce jalon prépare pour la suite

### Jalon 5 — Service JDR

- Création `app/services/jdr/` avec son propre `JDR_SYSTEM_PROMPT` (style narratif)
- `app/jobs/jdr.py::summarize_jdr_session(transcript)` qui appelle `adapter.complete(system=JDR_SYSTEM_PROMPT, user=transcript, ...)`
- `TranscriptionAdapter` (Whisper) — adapter séparé pour audio→texte
- Possible adoption Spec Kit en pratique
- Migration store API keys → DB (résoudre rotation et scaling)

### Jalon 6 — Observabilité

- Le `logger.info("llm.complete", extra=...)` devient un log structlog JSON
- Métriques Prometheus : `llm_requests_total{provider, model, status}`, `llm_tokens_total{type=prompt|completion}`, `llm_duration_seconds_bucket`
- Tracing : un `correlation_id` propagé du HTTP request au job worker à l'appel LLM

### Jalon 7 — CI/CD

- Tests adapter tournent en CI sans frais DeepInfra (MockLLMAdapter)
- Scan déps : `openai` audité par `pip-audit`
- Test d'intégration optionnel (smoke test contre DeepInfra) déclenché manuellement

### Jalon 8 — Pi 5 deployment

- Multi-arch Docker (l'image FastAPI ne change pas, l'image worker non plus)
- Caddy reverse-proxy

### Jalon 9 (optionnel) — Local inference + fallback

- `FallbackLLMAdapter(LLMAdapter)` qui compose `primary` + `fallback`
- Try cloud, except `TransientLLMError` → fallback local (Ollama)
- Adapter de plus, **zéro modification** des services métier

---

## Référence rapide — checklist DoD du Jalon 4

| Critère CLAUDE.md §7 | État |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `pytest` | ✅ 47 passed |
| `docker compose up --build` | 🟡 à valider (dep `openai` ajoutée → rebuild) |
| Endpoints répondent + worker tourne | 🟡 à valider |
| README à jour | ✅ section LLM ajoutée |
| Entrée journal | ✅ |
| ADR | ✅ ADR 0005 |
| Commit pushed | 🟡 reste à faire |
