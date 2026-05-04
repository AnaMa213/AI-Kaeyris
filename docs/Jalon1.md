# Jalon 1 — Modular API skeleton (walkthrough pédagogique)

> Document explicatif détaillé de tout ce qui a été fait dans le Jalon 1, du **pourquoi**, des normes respectées et des alternatives qu'on aurait pu prendre.
> Public : toi, qui apprends. Document à relire dans 6 mois pour te souvenir.

---

## Sommaire

1. [Objectif du jalon](#1-objectif-du-jalon)
2. [Étape 0 — Rédiger l'ADR avant de coder](#2-étape-0--rédiger-ladr-avant-de-coder)
3. [Étape 1 — `app/core/errors.py` (gestion d'erreurs RFC 9457)](#3-étape-1--appcoreerrorspy-gestion-derreurs-rfc-9457)
4. [Étape 2 — `app/services/_template/` (3 fichiers)](#4-étape-2--appservices_template-3-fichiers)
5. [Étape 3 — `app/main.py` (câbler le tout)](#5-étape-3--appmainpy-câbler-le-tout)
6. [Étape 4 — Les tests](#6-étape-4--les-tests)
7. [Étape 5 — Documentation (memo, README, journal)](#7-étape-5--documentation-memo-readme-journal)
8. [Normes et bonnes pratiques respectées](#8-normes-et-bonnes-pratiques-respectées)
9. [Choix alternatifs envisagés et écartés](#9-choix-alternatifs-envisagés-et-écartés)
10. [Limitations acceptées (dette consciente)](#10-limitations-acceptées-dette-consciente)
11. [Ce que ce jalon prépare pour la suite](#11-ce-que-ce-jalon-prépare-pour-la-suite)

---

## 1. Objectif du jalon

Selon [`CLAUDE.md`](../CLAUDE.md) §5, le Jalon 1 doit livrer :

> **Service `_template`, error handling, OpenAPI doc**

Concrètement, on transforme le squelette du Jalon 0 (qui n'avait qu'un `/health`) en une **base de code modulable** : un pattern clair pour ajouter de nouveaux services, une gestion d'erreurs cohérente sur toute l'API, et une doc auto-générée propre.

**Ce qu'on ne fait pas** : pas d'authentification (Jalon 2), pas de queue (Jalon 3), pas d'adapter LLM (Jalon 4), pas de DB. C'est l'application stricte de la règle YAGNI (CLAUDE.md §2.3).

---

## 2. Étape 0 — Rédiger l'ADR avant de coder

### Ce qui a été fait

Avant la première ligne de code, on a rédigé [`docs/adr/0002-service-structure-and-error-format.md`](./adr/0002-service-structure-and-error-format.md). L'ADR (Architecture Decision Record) acte trois décisions :

1. Chaque service métier = trois fichiers (`router.py`, `schemas.py`, `logic.py`) avec règles d'imports strictes.
2. Le `_template` n'est **pas monté** dans l'app principale.
3. Le format d'erreur unifié = **RFC 9457 Problem Details**, implémenté **fait main**.

### Pourquoi avant le code

C'est une bonne pratique de la communauté ADR (https://adr.github.io) : un ADR doit être rédigé **au moment de la décision**, pas après. Sinon il devient une justification a posteriori, pas une vraie réflexion.

### Norme respectée

- **CLAUDE.md §7.7** — la DoD impose un ADR si décision significative. Trois décisions structurantes ici, une seule ADR pour les regrouper (elles sont liées).
- **MADR** (Markdown Any Decision Records, https://adr.github.io/madr/) — format standard suivi : Contexte / Décision / Alternatives écartées / Conséquences / Conditions de re-évaluation.

### Alternative écartée

- **Coder d'abord, ADR après** : tentation classique, mauvaise habitude. On finit par justifier ce qu'on a fait au lieu de réfléchir à ce qu'on devrait faire.
- **Pas d'ADR du tout** : éliminé par le projet (CLAUDE.md §7.7 exige un ADR si décision significative).

---

## 3. Étape 1 — `app/core/errors.py` (gestion d'erreurs RFC 9457)

### Ce qui a été fait

Création du module [`app/core/errors.py`](../app/core/errors.py) qui contient :

- Une classe `AppError(Exception)` racine, sous-classable pour chaque type d'erreur métier.
- Une fonction `register_exception_handlers(app)` qui enregistre 3 handlers FastAPI :
  - `AppError` → réponse Problem Details avec le `status_code` de l'exception
  - `RequestValidationError` (Pydantic 422) → réponse Problem Details enrichie d'un champ `errors` listant les champs invalides
  - `Exception` (catch-all) → réponse 500 générique sans leak du stack trace côté client
- Une fonction privée `_problem_response(...)` qui construit la réponse JSON conforme à la RFC 9457.

### Pourquoi RFC 9457

La RFC 9457 (https://www.rfc-editor.org/rfc/rfc9457.html, juillet 2023) standardise un format JSON pour les erreurs HTTP. Elle remplace la RFC 7807 (2016).

**Champs définis** :

| Champ | Type | Rôle |
|---|---|---|
| `type` | URI | identifiant du type d'erreur (lié à une doc) |
| `title` | string | titre court lisible humain |
| `status` | int | code HTTP (cohérent avec la réponse) |
| `detail` | string | message spécifique à cette occurrence |
| `instance` | URI/path | identifiant de la requête fautive |

**Content-Type** : `application/problem+json` (et non `application/json`). Permet aux clients de détecter automatiquement qu'il s'agit d'une erreur structurée.

### Pourquoi une classe `AppError` plutôt que des `HTTPException` directes

Trois raisons :

1. **Découplage du transport** : `AppError` est levée par la logique métier (`logic.py`). Si demain on expose la même logique en gRPC ou en CLI, on n'a pas à toucher au métier — seul le handler change.
2. **Catalogage** : on liste explicitement les types d'erreurs métier (sous-classes de `AppError`). Plus lisible que des `HTTPException(404, "user not found")` éparpillés.
3. **Cohérence** : un seul format de sortie, garanti par les handlers centraux.

### Pourquoi 3 handlers et pas un seul

Chaque exception a une logique de transformation différente :

- `AppError` a déjà `status_code`, `error_type`, `title` → on fait juste le mapping.
- `RequestValidationError` a une structure spécifique Pydantic (`exc.errors()`) → on extrait la liste des champs invalides.
- `Exception` est inattendu → on **n'expose rien** au client (sécurité), on log côté serveur.

### Le détail subtil : `raise_app_exceptions=False` dans les tests

Par défaut, `httpx.ASGITransport` re-lève les exceptions non gérées dans le test (utile pour debug). Mais si on veut **vérifier** que notre handler `Exception` catch-all transforme bien une `RuntimeError` en réponse 500 Problem Details, il faut désactiver ce comportement, sinon la `RuntimeError` remonte avant d'atteindre Starlette.

```python
transport = ASGITransport(app=app, raise_app_exceptions=False)
```

C'est documenté dans httpx mais subtil quand on découvre.

### Norme respectée

- **CLAUDE.md §2.6 sécurité par défaut** : pas de stack trace côté client, log côté serveur.
- **CLAUDE.md §2.4 séparation des concerns** : `errors.py` est dans `core/`, pas dans un service.
- **OWASP API Security Top 10** (https://owasp.org/API-Security/), notamment API3:2023 Broken Object Property Level Authorization et API8:2023 Security Misconfiguration : un format d'erreur cohérent rend les contrôles plus simples.

### Alternatives écartées

- **Lib `fastapi-problem-details`** : ajoute une dépendance jeune (~quelques centaines d'étoiles GitHub) pour un code de 50 lignes qu'on maîtrise. À reconsidérer si on dépasse 10-15 types d'erreurs ou si on a besoin de fonctionnalités avancées.
- **Format maison non standardisé** (`{"error": {...}}`) : zéro outils clients, divergence avec les autres APIs, pas d'audit possible.
- **Garder la réponse FastAPI par défaut** (`{"detail": "..."}`) : incohérente (HTML pour 500, JSON pour 422), pas de `type` ni `instance`, pas de Content-Type standard.

---

## 4. Étape 2 — `app/services/_template/` (3 fichiers)

### Ce qui a été fait

Le dossier [`app/services/_template/`](../app/services/_template/) contient maintenant :

#### [`schemas.py`](../app/services/_template/schemas.py) — modèles Pydantic

```python
class EchoRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, description="...")

class EchoResponse(BaseModel):
    echo: str = Field(..., description="...")
```

Rôle : déclarer le **contrat public** du service. Les clients voient ces champs dans Swagger UI. Pydantic valide automatiquement les inputs (longueur, type, présence) et génère la doc.

#### [`logic.py`](../app/services/_template/logic.py) — métier pur

```python
def echo(payload: EchoRequest) -> EchoResponse:
    return EchoResponse(echo=payload.message)
```

Rôle : la **logique métier**, totalement indépendante de FastAPI ou HTTP. Testable sans serveur.

**Règle absolue** : `logic.py` n'importe **jamais** `fastapi`. Si tu vois un `from fastapi import ...` dans un `logic.py`, c'est un bug architectural à corriger.

#### [`router.py`](../app/services/_template/router.py) — routage HTTP

```python
router = APIRouter(prefix="/services/_template", tags=["_template"])

@router.post("/echo", response_model=EchoResponse, status_code=200)
def post_echo(payload: EchoRequest) -> EchoResponse:
    return logic.echo(payload)
```

Rôle : **adapter** entre HTTP et la logique métier. Le router :
- Déclare l'URL et la méthode HTTP
- Spécifie les schemas en entrée/sortie (Pydantic les valide automatiquement)
- Délègue à `logic.py`

### Pourquoi 3 fichiers et pas 1

Si on mettait tout dans un seul fichier (le réflexe au début) :

- On ne peut plus tester `logic.py` sans démarrer FastAPI → tests plus lents, fixtures plus complexes
- Le fichier devient illisible quand le service grossit (~300 lignes, c'est rapide)
- On tend à coupler la logique métier au transport HTTP — quand on voudra exposer en gRPC ou en CLI, on devra tout refactorer

3 fichiers, c'est le minimum viable de séparation. Pas du Clean Architecture façon Robert C. Martin (avec 5 couches), juste **3 responsabilités lisibles** : modèles / métier / routage.

### Pourquoi `_template` n'est pas monté en prod

Le dossier `_template` est un **modèle de copie**, pas un service réel. On y met l'exemple le plus minimaliste possible (un endpoint `echo`) pour que tout dev (toi, dans 3 mois) puisse :

1. `Copy-Item -Recurse app\services\_template app\services\<mon_service>`
2. Renommer schémas, préfixe, tag
3. Adapter `logic.py` au métier
4. Ajouter `app.include_router(<mon_service>.router)` dans `main.py`

L'inclure dans la prod aurait deux inconvénients :
- Pollue la doc OpenAPI publique (`/docs` afficherait `_template/echo`)
- Donne l'illusion qu'il y a un "service template" en production

On le teste tout de même via une fixture pytest qui crée un mini-app dédié (voir étape 4).

### Pourquoi le préfixe `_`

Convention Python : un identifiant qui commence par `_` est **privé / interne**. Renforce le message qu'il ne s'agit pas d'un service exposé.

### Norme respectée

- **CLAUDE.md §2.4** : "no cross-imports between services". `_template` n'importe rien depuis un autre service.
- **CLAUDE.md §4.2** : le template est explicitement le point de départ pour copier un nouveau service.
- **Single Responsibility Principle** (Martin, Clean Code) : chaque fichier a une seule responsabilité claire.

### Alternatives écartées

- **Tout dans un fichier `service.py`** : sur-couplage, intestable sans serveur.
- **Plus de couches dès le départ** (ex : `repository.py`, `use_case.py`, `controller.py` façon Hexagonal Architecture) : sur-ingénierie pour ce projet (CLAUDE.md §9).
- **Monter `_template` en prod** : pollue la doc API.
- **Pas de template du tout, juste une convention écrite** : moins concret, plus facile à oublier ou mal appliquer.

---

## 5. Étape 3 — `app/main.py` (câbler le tout)

### Ce qui a été fait

[`app/main.py`](../app/main.py) a été enrichi :

```python
app = FastAPI(
    title="AI-Kaeyris",
    version=settings.APP_VERSION,
    description="Plateforme AI personnelle — monolithe modulaire FastAPI.",
)
register_exception_handlers(app)

@app.get("/health", tags=["health"], summary="Vérifie que l'API est en vie.")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}
```

### Trois petits changements, gros impact

1. **Métadonnées OpenAPI** (`title`, `version`, `description`) : Swagger UI affiche ces infos en haut de `/docs`. Cinq lignes pour une doc visiblement professionnelle au lieu d'une page générique.
2. **`register_exception_handlers(app)`** : une seule ligne qui câble les 3 handlers d'erreur. Toute l'app utilise désormais le format Problem Details.
3. **`tags=["health"]` et `summary=...`** sur `/health` : dans Swagger UI, l'endpoint est groupé sous "health" et a un libellé clair. Pour quelques caractères, gros gain de lisibilité.

### Ce qui n'est PAS dans `main.py`

Notamment **pas** d'`include_router` pour `_template`. C'est intentionnel (cf. ADR 0002).

### Norme respectée

- **OpenAPI** (https://www.openapis.org/) : standard de description d'API REST. FastAPI le génère automatiquement, on n'a qu'à fournir les métadonnées.
- **12-Factor §III "Config"** : la version vient de `settings.APP_VERSION` (env var), pas hardcodée.

### Alternatives écartées

- **Pas de métadonnées** : doc moins exploitable par les outils clients.
- **App factory** (`def create_app() -> FastAPI: ...`) : utile pour tester avec différentes configs, mais YAGNI au Jalon 1. À introduire quand on en aura besoin (peut-être Jalon 2 pour les tests d'auth).
- **Inclure `_template`** : décision déjà actée dans l'ADR.

---

## 6. Étape 4 — Les tests

### Ce qui a été fait

5 nouveaux tests, organisés par responsabilité (CLAUDE.md §4.1 prescrit `tests/core/` et `tests/services/`).

#### `tests/services/_template/conftest.py` — fixture mini-app

```python
@pytest.fixture
def template_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(template_router)
    return app
```

C'est la **fixture clé** : on crée un FastAPI() dédié au test, on y monte uniquement le router à tester, on y attache les handlers d'erreur. Permet de tester `_template` en isolation, **sans** le monter dans l'app principale.

#### `tests/services/_template/test_router.py` — 3 tests

- `test_echo_returns_payload` : nominal, vérifie statut 200 et JSON correct.
- `test_echo_rejects_missing_message` : 422 quand le champ `message` manque, format Problem Details, location `["body", "message"]`.
- `test_echo_rejects_empty_message` : 422 quand `message=""` (validation `min_length=1`).

#### `tests/core/test_errors.py` — 2 tests

- `test_app_error_renders_problem_details` : on crée une app de test avec une route qui lève `_TeapotError(detail="No coffee here")`, on vérifie que la réponse est exactement `{type, title, status: 418, detail, instance}` avec `Content-Type: application/problem+json`.
- `test_unexpected_exception_returns_generic_500` : on lève une `RuntimeError("boom")`, on vérifie que le client reçoit un 500 générique **sans** le message "boom" (no leak).

### Pourquoi tester comme ça

- **Isolation** : chaque test crée sa propre app, pas d'effets de bord entre tests.
- **Vitesse** : `ASGITransport` invoque l'app en mémoire, pas de port à gérer, ~50ms par test.
- **Couverture du contrat** : on teste le cas nominal ET les cas d'erreur. C'est le minimum pour une route d'API.
- **Test du catch-all** : c'est le seul moyen de vérifier qu'une exception non prévue ne leak rien.

### Norme respectée

- **CLAUDE.md §2.5 test discipline** : "Every public endpoint must have at least one test" — fait.
- **Pyramide de tests** (Cohn 2009) : ces tests sont des **tests d'intégration légers** (router + Pydantic + handlers) — beaucoup, rapides, isolés.
- **AAA pattern** (Arrange / Act / Assert) : structure claire dans chaque test.

### Alternatives écartées

- **Tests via `TestClient` sync de FastAPI** : utilise `requests` derrière, marche aussi mais on a choisi `httpx + ASGITransport` pour cohérence avec un éventuel test async (DB, queue).
- **Démarrer un vrai serveur uvicorn dans les tests** : plus lent, plus fragile, port à gérer. À éviter sauf E2E.
- **Mocker les handlers** : on perd la valeur du test.

---

## 7. Étape 5 — Documentation (memo, README, journal)

### Ce qui a été fait

- [`memo.md`](./memo.md) — section "Créer un nouveau service" ajoutée : workflow `Copy-Item` + 6 étapes claires.
- [`README.md`](../README.md) — refonte avec section Documentation interne, tableau des endpoints (incluant `/docs`, `/redoc`, `/openapi.json`), section Architecture, lien vers RFC 9457.
- [`docs/journal.md`](./journal.md) — entrée Jalon 1 datée, structurée en "Ce qui a été fait" / "Ce que j'ai appris" / "Limitations acceptées".

### Pourquoi 3 docs distinctes

Chaque doc a un rôle non substituable :

| Doc | Rôle | Public |
|---|---|---|
| `README.md` | onboarding "5 minutes" | nouveau dev, futur toi |
| `memo.md` | référence rapide commandes/raisons | toi en cours de boulot |
| `docs/journal.md` | trace chronologique d'apprentissage | toi dans 6 mois pour relire ton parcours |
| `docs/adr/*.md` | pourquoi des choix structurants | tout futur lecteur du code |
| `Jalon1.md` (ce doc) | walkthrough pédagogique d'un jalon précis | toi qui apprends |

### Norme respectée

- **CLAUDE.md §7.5 et §7.6** : DoD exige README à jour et entrée journal.
- **Diátaxis framework** (https://diataxis.fr) : doc projet = mélange de tutorial (walkthrough), how-to (memo, README setup), reference (ADR), explanation (journal). Chaque type a sa place.

### Alternative écartée

- **Tout dans le README** : finit par devenir illisible (1000+ lignes). Doc unique = doc qui ne sert plus.
- **Wiki externe (Confluence, Notion)** : sépare la doc du code, doc périme plus vite. Marche pour des grosses équipes, pas ici.

---

## 8. Normes et bonnes pratiques respectées

| Norme | Comment elle s'applique ici |
|---|---|
| **12-Factor §III** (Config) | Métadonnées OpenAPI viennent des `settings`, pas de hardcode |
| **12-Factor §X** (Dev/prod parity) | Le router `_template` n'apparaît pas en prod ; tests isolés |
| **12-Factor §XI** (Logs) | Errors loggés en stdout via `logging` (config Jalon 6) |
| **OWASP API Top 10** | Pas de leak de stack trace, format d'erreur cohérent, validation Pydantic systématique |
| **RFC 9457** | Format Problem Details respecté (champs, Content-Type) |
| **OpenAPI 3** | Doc auto-générée, schémas Pydantic exposés, tags par groupe d'endpoints |
| **Conventional Commits** | Tous les commits du jalon préfixés `feat:`, `docs:`, `test:` |
| **Test Pyramid** (Cohn) | 6 tests rapides, isolés, en mémoire |
| **MADR** (ADR format) | ADR 0002 structuré : Contexte / Décision / Alternatives / Conséquences |
| **YAGNI** (CLAUDE.md §2.3) | Pas d'auth, pas de DB, pas d'app factory, pas de lib externe pour les erreurs |
| **Single Responsibility** | router / schemas / logic = 3 responsabilités séparées |
| **Adapter Pattern** (GoF) | Hérité du Jalon 0, prêt pour Jalon 4 |

---

## 9. Choix alternatifs envisagés et écartés

### Au niveau architecture du service

| Alternative | Pourquoi écartée |
|---|---|
| Tout dans un seul `service.py` | Couplage fort routing/métier, intestable sans serveur |
| 5 couches (controller / use_case / repository / domain / dto) | Sur-ingénierie ; 3 fichiers suffisent à ce stade |
| Pas de couche métier (route appelle directement la DB) | Indéfendable : tests fragiles, refacto pénible |
| `FastAPI(...)` avec app factory `def create_app()` | YAGNI pour ce jalon, à introduire quand utile |

### Au niveau format d'erreur

| Alternative | Pourquoi écartée |
|---|---|
| Format maison `{"error": {"code", "message"}}` | Coût d'invention, pas d'outils, pas de standard |
| Format FastAPI par défaut (`{"detail": "..."}`) | Incohérent (HTML/JSON), pas de Content-Type standard |
| Lib externe `fastapi-problem-details` | Ajoute une dépendance jeune pour 50 lignes qu'on maîtrise |
| RFC 7807 (l'ancêtre) | Obsolète depuis 2023, RFC 9457 la remplace |
| Pas de Content-Type spécifique (`application/json`) | Clients ne peuvent plus auto-détecter le format d'erreur |

### Au niveau template

| Alternative | Pourquoi écartée |
|---|---|
| Monter `_template` en prod | Pollue la doc, donne l'illusion d'un service réel |
| Mettre le template hors de `app/services/` (ex : `templates/`) | Brouille la structure, oblige à des chemins relatifs bizarres |
| Pas de template, juste une doc | Moins concret, plus facile à oublier ou mal appliquer |
| Template avec auth, queue, DB pré-câblées | Sur-ingénierie, force des dépendances qui ne sont pas dans tous les services |

### Au niveau tests

| Alternative | Pourquoi écartée |
|---|---|
| `TestClient` sync FastAPI | Marche, mais on perd la cohérence avec un éventuel async futur |
| Démarrer un vrai serveur dans le test | Plus lent, gestion de ports, fragile |
| Mocker FastAPI/Pydantic | On perd la valeur du test (on teste le mock) |
| Tests dans le même fichier que le code | Marche pour des libs très petites, mauvais réflexe pour une app |

### Au niveau organisation des handlers d'erreur

| Alternative | Pourquoi écartée |
|---|---|
| Un fichier par type d'erreur | Sur-fragmenté pour 3 handlers |
| Définir les handlers comme méthodes d'une classe | Plus de cérémonie pour zéro gain |
| Décorateurs `@app.exception_handler` directement dans `main.py` | Mélange concerns ; mieux d'avoir un module dédié |

---

## 10. Limitations acceptées (dette consciente)

Ces limitations sont volontaires (YAGNI) ou repoussées à un jalon ultérieur. À reprendre quand le moment sera venu.

| Limitation | Pourquoi acceptée | À reprendre quand |
|---|---|---|
| Type URI Problem Details (`https://kaeyris.local/errors/...`) pointe nulle part | Pas de doc d'erreurs publique encore, on aura plus de matière en Jalon 5+ | Quand on déploiera sur le Pi avec Caddy (Jalon 8) ou avant |
| Pas de handler pour `HTTPException` FastAPI | On ne l'utilise pas dans notre code | Quand un service introduira `Depends` qui lève `HTTPException` |
| Logging non configuré | C'est explicitement le scope du Jalon 6 | Jalon 6 |
| Pas de Correlation ID / Request ID | Utile en multi-services, on a un seul service | Jalon 6 (observabilité) |
| `openapi_tags` pas défini | Cosmétique, Swagger fonctionne déjà | Quand on aura plusieurs services et que la doc grossira |
| Pas de versioning d'API (`/v1/`) | Trop tôt, on ne sait pas encore ce qui sera rétro-incompatible | Avant le premier client externe (Jalon 8 ?) |
| Pas de rate limiting sur `_template/echo` | C'est un template non monté | Jalon 2 (auth + rate limiting au niveau core) |
| Pas de healthcheck readiness (`/ready` séparé de `/health`) | Une seule check pour l'instant suffit | Jalon 6 ou Jalon 8 (déploiement) |
| Pas de CI (GitHub Actions) | Scope du Jalon 7 | Jalon 7 |

---

## 11. Ce que ce jalon prépare pour la suite

Le travail fait ici crée des fondations sur lesquelles les jalons suivants vont s'appuyer :

- **Jalon 2 (Auth)** : pourra ajouter un `app/core/auth.py` qui suit la même logique que `errors.py` (un module `core` qui s'enregistre via une fonction). Les exceptions d'auth seront des sous-classes de `AppError` (ex : `UnauthorizedError(AppError)` avec `status_code=401`).
- **Jalon 3 (Async queue)** : la séparation `router/logic` permet de déplacer `logic.py` vers un worker RQ sans toucher au router HTTP. La logique métier est portable.
- **Jalon 4 (Adapters)** : `logic.py` pourra importer des adapters (`from app.adapters.llm import LLMAdapter`) et rester totalement indépendant du vendor.
- **Jalon 5 (Service JDR)** : suivra exactement le pattern `_template` — copier, renommer, monter, tester.
- **Jalon 6 (Observability)** : le `logger.error(..., exc_info=exc, extra={"path": ...})` est déjà structuré pour passer à structlog plus tard sans réécriture.

C'est le bon moment pour valider que **ces fondations te conviennent** : on est encore dans une zone où changer une décision coûte peu. Plus tard, ça coûtera des refactos.

---

## Référence rapide — checklist DoD du Jalon 1

| Critère | État |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `pytest` | ✅ 6 passed |
| `docker compose up --build` | ✅ confirmé |
| `curl /health` + `/docs` | ✅ confirmé |
| README à jour | ✅ |
| Entrée journal | ✅ |
| ADR | ✅ ADR 0002 |
| Commit pushed | 🟡 reste à faire |
