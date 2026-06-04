# Journal d'apprentissage

## 2026-06-03 - BD-9 : preparation audio serveur

### Ce qui a ete fait

- Ajout de `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` pour plafonner les uploads bruts cote API avant ecriture definitive.
- L'upload JDR stocke maintenant le M4A brut sous `.tmp/audio-reduce/<session_id>/raw.m4a`, garde la reponse `AudioUploadOut`, et conserve un seul job visible de type `transcription`.
- Le worker prepare un artefact durable `audios/<session_id>.m4a` via `ffmpeg`, met a jour `AudioSource`, supprime le brut, puis appelle l'adapter de transcription.
- Les erreurs de preparation marquent la session et le job en echec de transcription; les erreurs de transcription apres preparation conservent le fichier prepare pour retry ou suppression explicite.
- `DELETE /audio` supprime maintenant le fichier prepare, les restes de brut, les transcriptions, chunks, artifacts et `current_job_id`.

### Ce que j'ai appris

- **Garder le contrat public stable reduit le cout frontend** : la preparation serveur reste une etape interne du job `transcription`, sans nouvel etat `reducing` ni job `audio_reduce`.
- **Le fichier temporaire n'est pas le fichier metier** : stocker le brut sous `.tmp/` puis promouvoir seulement l'artefact prepare clarifie le cycle de vie et simplifie `GET /audio`.
- **Les limites applicatives doivent etre testables** : meme si un proxy peut imposer une limite HTTP, `KAEYRIS_AUDIO_MAX_UPLOAD_BYTES` donne un comportement metier deterministe et un `413` documente.

### Limitations acceptees

- Pas de job separe de preparation audio : YAGNI tant que le front n'a pas besoin d'observer cette etape.
- Pas de strategie de retention du brut original : il est supprime apres preparation pour limiter l'usage disque.

---

## 2026-05-01 — Jalon 0 : Foundations

### Ce qui a été fait

- Création de l'arborescence cible définie en §4.1 du `CLAUDE.md` : `app/{core,services/_template,adapters}`, `tests/`, `docker/`, `docs/adr/`.
- `pyproject.toml` : Python 3.12+, dépendances runtime (`fastapi`, `uvicorn[standard]`, `pydantic-settings`) et dev (`pytest`, `pytest-asyncio`, `httpx`, `ruff`). Configuration `ruff` (line-length 100, target py312) et `pytest` (`testpaths = ["tests"]`, `asyncio_mode = "auto"`).
- `app/core/config.py` : `Settings` Pydantic minimal qui lit `.env` (12-Factor §III). Un seul réglage `APP_VERSION`.
- `app/main.py` : application FastAPI avec un unique endpoint `GET /health` retournant `{"status":"ok","version":<APP_VERSION>}`.
- `tests/test_health.py` : un test asynchrone qui valide statut 200 et JSON exact via `httpx.AsyncClient` + `ASGITransport`.
- `docker/Dockerfile` : `python:3.12-slim`, utilisateur non-root `app`, couches optimisées (deps avant code), `EXPOSE 8000`, `CMD uvicorn`.
- `docker-compose.yml` : un seul service `api` (YAGNI — pas de Postgres ni Redis avant Jalon 3), volume `./app:/app/app` pour le hot-reload, `--reload` ajouté côté compose pour préserver la parité dev/prod de l'image.
- `.env.example`, `.gitignore` (interdiction de commit `.env` — §2.6), `README.md` mis à jour avec setup local et tests.

### Ce que j'ai appris

- **Différence venv vs Docker dans un workflow pro** : le venv reste utile pour le dev local et l'exécution rapide des tests (boucle de feedback < 1s avec `pytest`), tandis que Docker garantit la parité avec la prod et l'intégration. Décision actée pour ce projet : combo des deux, venv pour l'itération, Docker pour les vérifications d'intégration.
- **Rôle exact de `ruff`** : ce n'est pas "juste un linter". C'est un outil unique écrit en Rust qui remplace `flake8 + black + isort + pyupgrade` (et une partie de `pylint`). Deux modes : `ruff check .` (lint) et `ruff format .` (formattage). Vitesse 10-100× supérieure aux outils Python historiques. Source : https://docs.astral.sh/ruff
- **Pourquoi `pip install -e ".[dev]"`** : le `-e` (editable) installe le projet en mode développement — toute modification du code est immédiatement visible sans réinstallation. Le `.[dev]` active l'extra `dev` défini dans `pyproject.toml` (`pytest`, `httpx`, `ruff`).
- **Ordre des couches dans un Dockerfile** : `COPY pyproject.toml` puis `RUN pip install` AVANT `COPY app` permet à Docker de garder la couche d'installation en cache tant que les dépendances n'ont pas changé. Inverser cet ordre déclenche un `pip install` à chaque modif de code source — minutes perdues à chaque build.
- **`ASGITransport` pour les tests httpx** : permet d'appeler l'app FastAPI directement en mémoire, sans démarrer de vrai serveur ni binder de port. Pas de flakiness, pas de cleanup, exécution quasi-instantanée. Pattern standard pour tester une app ASGI.
- **`env_file` dans Compose exige le fichier par défaut** : si `.env` est absent, `docker compose up` refuse de démarrer. Solution naturelle : `Copy-Item .env.example .env` à la première utilisation. Solution avancée disponible depuis Compose v2.24 : `env_file: [{ path: .env, required: false }]` pour rendre le fichier optionnel (utile en CI ou pour onboarding rapide).
- **Conventional Commits = contrat de communication** : le format `feat:`, `fix:`, `chore:`… n'est pas cosmétique. Il rend l'historique parsable par des outils (génération de changelog, détection de bumps semver), et discipline le découpage en commits atomiques. Standard documenté : https://www.conventionalcommits.org
- **Différence ADR vs journal vs memo** : l'ADR (`docs/adr/`) capture le **pourquoi** d'une décision structurante, immuable une fois acceptée (on en crée une nouvelle qui "supersede" plutôt que d'éditer). Le journal (`docs/journal.md`) trace l'apprentissage chronologique. Le memo/playbook (`memo.md`, `playbook.md`) condense la connaissance opérationnelle réutilisable. Les trois ne se substituent pas.

---

## 2026-05-02 — Jalon 1 : Modular API skeleton

### Ce qui a été fait

- **ADR 0002** rédigé puis accepté : trois décisions structurantes (structure de service en 3 fichiers `router/schemas/logic`, `_template` non monté en prod, RFC 9457 Problem Details fait main).
- **`app/core/errors.py`** : classe de base `AppError` + 3 exception handlers FastAPI (custom `AppError`, `RequestValidationError` Pydantic, catch-all `Exception`). Format de réponse RFC 9457 conforme avec `Content-Type: application/problem+json`.
- **`app/services/_template/`** matérialisé en 3 fichiers : `schemas.py` (Pydantic), `logic.py` (pure, aucune dépendance FastAPI), `router.py` (`POST /services/_template/echo`).
- **`app/main.py`** enrichi : métadonnées OpenAPI (title, version, description), tag `health` sur `/health`, appel à `register_exception_handlers(app)`. Le router `_template` n'est **pas** inclus.
- **5 nouveaux tests** : 3 sur le template (echo nominal, message manquant, message vide) via fixture `template_app` qui monte un mini-app dédié ; 2 sur les erreurs (`AppError` custom transformé en 418 Problem Details, `RuntimeError` non géré transformé en 500 générique sans leak du message).
- **`memo.md`** enrichi avec la section "Créer un nouveau service" (workflow `Copy-Item` + 6 étapes).
- **README.md** mis à jour avec mention de la doc OpenAPI auto et pointeurs vers les docs internes.

### Ce que j'ai appris

- **Différence routing / validation / métier** : avec FastAPI, le `router.py` ne fait que router et appeler `logic.py`. La validation est entièrement déléguée à Pydantic via `schemas.py`. La logique métier est testable sans démarrer FastAPI — c'est pour ça que `logic.py` ne doit jamais importer `fastapi`. Cette discipline coûte 0 ligne de plus mais rend les tests unitaires triviaux.
- **RFC 9457 Problem Details** : un standard IETF qui définit un format JSON unique pour toutes les erreurs HTTP (`type`, `title`, `status`, `detail`, `instance`). Content-Type `application/problem+json` au lieu de `application/json`. Adopté par Microsoft, Zalando, et de plus en plus d'APIs publiques. Coût d'implémentation maison : ~50 lignes.
- **Fixture pytest pour tester un router en isolation** : on crée un mini `FastAPI()` dans un `conftest.py`, on y monte uniquement le router à tester, on y attache les handlers via `register_exception_handlers()`. Permet de tester un service sans le polluer dans l'app principale ni avoir à démonter la prod. Pattern réutilisable pour tous les futurs services métier.
- **`raise_app_exceptions=False` sur `ASGITransport`** : par défaut httpx re-lève les exceptions non gérées dans les tests (utile pour debug). Pour tester qu'un handler catch-all transforme bien une `Exception` non prévue en réponse HTTP, il faut désactiver ce comportement, sinon la `RuntimeError` remonte avant d'atteindre Starlette.
- **Préfixe `_` sur `_template`** : convention Python signifiant "interne / privé / pas pour la prod". Renforce le message que ce n'est pas un service réel mais un modèle de copie. Cohérent avec le fait qu'il n'est pas monté.
- **Cache de couches Docker en pratique** : après `docker compose up --build`, l'ancienne image existe toujours sous le tag `<none>` (image "dangling"). Docker ne supprime jamais une image automatiquement par sécurité (rollback, conteneurs actifs). À nettoyer périodiquement avec `docker image prune`.

### Limitations acceptées (à reprendre dans des jalons futurs)

- Type URI Problem Details (`https://kaeyris.local/errors/...`) pointe vers un domaine non hébergé. À documenter ou remplacer par `about:blank` quand on aura une page d'erreurs.
- Pas de handler pour FastAPI `HTTPException` (raisé par exemple par `Depends`). YAGNI tant qu'on n'utilise pas ce pattern.
- Logging non configuré (Jalon 6 — structlog).
- Pas de correlation ID propagé dans les logs (Jalon 6).
- `openapi_tags` (descriptions des tags dans Swagger) non défini ; cosmétique.

---

## 2026-05-02 — Jalon 2 : Authentication

### Ce qui a été fait

- **ADR 0003** rédigé puis accepté (Bearer token RFC 6750, stockage env var `API_KEYS`, hash Argon2id, rate limiting reporté au Jalon 3, security headers fait main, routes publiques explicites, comparaison constant-time).
- **`app/core/auth.py`** : dataclasses `APIKeyEntry` et `AuthenticatedKey`, fonctions `parse_api_keys()` / `get_registered_keys()`, dépendance FastAPI `require_api_key`. Vérification via `argon2.PasswordHasher.verify()` (constant-time intrinsèque). Sous-classes `UnauthorizedError(AppError)` (401 + `WWW-Authenticate`) et `ForbiddenError(AppError)` (403, prête pour la révocation future).
- **`app/core/security_headers.py`** : middleware Starlette qui ajoute 5 headers OWASP à toutes les réponses (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP `default-src 'none'`, HSTS).
- **`app/core/errors.py`** étendu pour supporter des headers HTTP par exception (via attribut de classe `default_headers` immutable). Permet à `UnauthorizedError` d'attacher `WWW-Authenticate` automatiquement.
- **`app/core/config.py`** : champ `API_KEYS: str` ajouté.
- **`scripts/generate_api_key.py`** : script CLI qui produit une clé aléatoire (32 octets URL-safe via `secrets.token_urlsafe`) et son hash Argon2id, avec un message d'aide à coller dans `.env`.
- **`app/main.py`** : middleware `SecurityHeadersMiddleware` enregistré.
- **`pyproject.toml`** : dépendance `argon2-cffi` ajoutée. `pip install -e ".[dev]"` pour récupérer.
- **11 nouveaux tests** : 4 sur `parse_api_keys`, 5 sur `require_api_key` (header manquant, malformé, clé inconnue, clé valide, registre vide), 2 sur le middleware (présence des headers en 200 et en 404). Total : **17 tests verts**.
- **Docs** : `memo.md` (section Authentification + workflow git), `README.md` (section Authentification avec exemples), `.env.example` (champ `API_KEYS=` documenté).

### Ce que j'ai appris

- **Format Argon2** : `$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`. La présence de virgules dans la section paramètres exige un séparateur autre que `,` quand on liste plusieurs clés dans une env var. Choix : `;`. Ça paraît anodin mais c'est typiquement le genre de détail qui casse une implémentation au pire moment si on ne le voit pas tôt.
- **Dépendance FastAPI overridable pour les tests** : en faisant `require_api_key` dépendre de `Depends(get_registered_keys)`, on peut overrider `get_registered_keys` dans les tests via `app.dependency_overrides[get_registered_keys] = lambda: ...`. Pattern propre, pas besoin de monkey-patcher `settings`.
- **Timing attack en pratique** : `argon2.PasswordHasher.verify()` est constant-time par construction. Pour la comparaison du nom de clé éventuellement exposé en log, utiliser `secrets.compare_digest()`. Référence : https://en.wikipedia.org/wiki/Timing_attack.
- **Mutable class attributes en Python** : un dict comme `headers: dict = {}` au niveau classe est partagé entre toutes les instances et entre toutes les sous-classes — danger réel si quelqu'un mute. Solution : tuple immuable (`default_headers: tuple[tuple[str, str], ...] = ()`) puis copie en dict dans `__init__`.
- **Pourquoi `setdefault` dans le middleware** : `response.headers.setdefault(k, v)` n'écrase pas un header déjà posé par la route. Permet à un endpoint spécifique d'imposer une politique CSP plus stricte sans être réécrit par le middleware global.
- **WWW-Authenticate sur 401** : RFC 6750 §3 exige ce header sur les réponses 401 émises par une API qui accepte le schéma Bearer. Sans, certains clients HTTP refusent même de retenter l'authentification.
- **`secrets.token_urlsafe(32)` produit ~43 caractères** (256 bits encodés base64url sans padding). Suffisant pour résister au brute-force complet quel que soit le hashage choisi.
- **Séparation "secure by default"** : on a choisi que les routes soient protégées sauf liste publique explicite. Si on oubliait `dependencies=[Depends(require_api_key)]` en montant un service, FastAPI le servirait sans auth. C'est un risque connu — le mitiger via revue de code et tests sera essentiel quand le projet grossira.

### Limitations acceptées (à reprendre)

- **Pas de rate limiting** — Jalon 3 (Redis).
- **Pas de scopes / permissions par clé** — toutes les clés actives ont les mêmes droits sur tous les services protégés.
- **Rotation de clé = redémarrage** (la variable d'env est lue au démarrage du conteneur). À résoudre quand on aura un store DB.
- **Stockage limité à ~5 clés en pratique** (chaîne d'env var devient pénible à éditer).
- **Pas d'audit log** des authentifications (succès/échecs). Repoussé au Jalon 6 (observabilité).
- **Pas de mitigation timing-attack inter-entrées** : le temps total de `_verify_against_registry` dépend du nombre d'entrées dans le registre (pas du contenu, mais ça expose la taille du registre). Acceptable à notre échelle.
- **Pas de handler pour FastAPI `HTTPException`** — toujours pas utilisé en interne.
- **Pas de `Server` header masqué** : Starlette ne l'ajoute pas, mais Caddy en Jalon 8 pourrait le faire ; à vérifier alors.

---

## 2026-05-02 — Jalon 3 : Async processing

### Ce qui a été fait

- **ADR 0004** rédigé puis accepté (RQ, Redis, machinerie pure + jobs factices, retry transient/permanent, TTLs 24h/7j, idempotence = discipline dev, **rate limiting maintenant** via sliding window Redis).
- **`app/core/redis_client.py`** : factory `get_redis()` (FastAPI dependency) avec cache `lru_cache` pour partager une seule connexion.
- **`app/core/rate_limit.py`** : algorithme sliding window via `ZREMRANGEBYSCORE` + `ZCARD` + `ZADD`, dépendance `enforce_rate_limit` chaînée à `require_api_key`. Exception `RateLimitedError` (429 + header `Retry-After`).
- **`app/jobs/__init__.py`** : helpers `get_default_queue()`, `enqueue_job()` qui applique TTLs et `Retry(max=3, interval=[10,30,90])`. Exceptions `TransientJobError` / `PermanentJobError` (documentaires).
- **`app/jobs/demo.py`** : jobs `add(a, b)` et `simulate_long_task(s)` pour valider l'infra.
- **`app/core/config.py`** : champs `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_WINDOW_SECONDS`.
- **`docker-compose.yml`** : passage de 1 à 3 services (`redis`, `api`, `worker`). Redis non exposé sur l'host. Volume persistant `redis-data`.
- **`pyproject.toml`** : dépendances `redis>=5.0`, `rq>=2.0` (runtime) et `fakeredis` (dev).
- **`.env.example`** : `REDIS_URL`, `RATE_LIMIT_*` documentés.
- **12 nouveaux tests** : 3 sur l'algorithme rate limit, 3 sur la dépendance `enforce_rate_limit` (allow / block / 401 prioritaire sur 429), 2 sur `add()` direct, 4 sur la policy d'enqueue (TTLs et retry). Total : **29 tests verts**.
- **Docs** : `memo.md` (sections async jobs et rate limit), `README.md` (mention 3 services Compose + commandes), `docs/journal.md` (cette entrée), `Jalon3.md` (walkthrough).

### Ce que j'ai appris

- **Découplage processus API ↔ worker** : ils partagent la même image Docker mais sont deux processus avec des entrypoints différents (`uvicorn` vs `rq worker`). Le worker doit pouvoir importer le code des jobs (`app.jobs.demo.add`) → `WORKDIR /app` + bind-mount `./app:/app/app` rendent ça transparent.
- **Sérialisation des arguments de jobs** : RQ pickle les arguments pour les stocker dans Redis. Tu ne peux pas passer un objet "vivant" (connection DB, FastAPI Request, instance avec lambdas, etc.). Discipline : passer **uniquement des types primitifs ou des dataclasses simples**. C'est exactement la même contrainte que pour les paramètres d'un appel REST — pas un hasard.
- **Sliding window vs fixed window** : avec un fixed window, un client peut burst 60 req à 11h59:59 + 60 req à 12h00:00 = 120 req en 2 secondes. Sliding window évite ce comportement en mesurant les N dernières secondes glissantes. Coût : un sorted set Redis au lieu d'un compteur INCR. ~5 lignes de plus pour ce gain.
- **fakeredis** : émule Redis en mémoire pour les tests. API compatible avec `redis-py`. 0 setup, 0 démarrage, parfait pour 95% des tests. Limite : ne reproduit pas tous les comportements low-level (cluster, pubsub avancé) — pour ces cas, tester contre un vrai Redis.
- **Ordre des dépendances FastAPI** : `enforce_rate_limit` dépend de `require_api_key`. Si la requête arrive sans auth, le 401 sort **avant** que le rate limit ne s'évalue. C'est voulu : un attaquant non authentifié ne pollue pas le bucket d'une vraie clé.
- **Cache des dépendances FastAPI par requête** : `Depends(require_api_key)` peut apparaître plusieurs fois dans une même requête (ex : ajouté à `dependencies=` ET à un `enforce_rate_limit` qui lui-même dépend de `require_api_key`) ; FastAPI cache le résultat pour la requête, ça ne tourne qu'une fois. Argon2 verify (lent) n'est donc pas répété.
- **`secrets.token_hex(8)` + timestamp** comme membre du sorted set : on ajoute toujours un membre **unique**, sinon `ZADD` agit comme un update du score. L'unicité garantit que chaque requête est compté.
- **Pourquoi pas de `--reload` pour le worker** : RQ ne propose pas le hot-reload. Workaround dev : `rq worker --burst` qui sort une fois la queue vide, à relancer après chaque modif. Ou `rq worker` simple + Ctrl+C / restart. Acceptable au rythme actuel.
- **`depends_on` dans Compose ne garantit pas la disponibilité** du service : Redis peut être démarré mais pas encore prêt à accepter des connexions quand l'API démarre. `redis-py` et `rq` gèrent les retry de connexion eux-mêmes — pas besoin d'un healthcheck Compose.
- **`save 60 1` dans Redis** : snapshot toutes les 60s s'il y a au moins 1 modification. Évite la perte totale en cas de redémarrage du conteneur. Pas de l'AOF (append-only file), qui serait surdimensionné pour notre usage.

### Limitations acceptées

- **Pas de retry jitter** (anti-troupeau) : risque théorique avec un seul worker, négligeable. À ajouter quand on aura plusieurs workers.
- **Pas de scheduling cron-like** : pas de `rq-scheduler` installé. Si besoin futur (ex : ménage périodique des résultats), à introduire.
- **Pas d'endpoint statut central des jobs** : repoussé à Jalon 4-5 quand on aura un vrai cas d'usage.
- **Pas d'idempotency key généralisée** : repoussé à Jalon 5.
- **Race condition microscopique** sur le sliding window (ZREMRANGEBYSCORE → ZCARD → ZADD non atomique). À notre échelle, négligeable. À résoudre via script Lua si besoin.
- **Worker pas de hot-reload** : RQ ne le supporte pas, workaround manuel.
- **Pas de monitoring** des jobs (rq-dashboard, Prometheus exporter) : reporté Jalon 6.

---

## 2026-05-02 — Jalon 4 : Adapters + Spec Kit intro

### Ce qui a été fait

- **ADR 0005** rédigé puis accepté (LLMAdapter via `typing.Protocol`, SDK `openai>=1.50` pour 6+ providers compatibles, méthode unique `complete(system, user, max_tokens)`, MockLLMAdapter pour tests, factory paramétrée par env vars, mapping HTTP → TransientLLMError/PermanentLLMError).
- **`app/adapters/llm.py`** : interface `LLMAdapter` (Protocol), `OpenAICompatibleLLMAdapter` paramétrable (DeepInfra/Ollama/Groq/vLLM/Together/OpenAI), `MockLLMAdapter` déterministe, factory `build_llm_adapter()` + `get_llm_adapter()` (FastAPI dependency, cache `lru_cache`).
- **Tableau des `_DEFAULT_BASE_URLS`** : 6 providers OpenAI-compatibles préconfigurés.
- **Hiérarchie d'erreurs** : `LLMError` racine, `TransientLLMError` (5xx, timeout, 429), `PermanentLLMError` (4xx hors 429, auth invalide, prompt malformé).
- **`app/jobs/llm.py::llm_complete`** : premier vrai job, générique (`system` + `user` paramètres), mapping `LLMError` → `JobError` pour que la retry policy de RQ s'applique.
- **`app/core/config.py`** : 6 nouvelles env vars `LLM_*`.
- **`.env.example`** : section LLM avec exemple Ollama commenté pour le RTX 4090.
- **`pyproject.toml`** : dépendance `openai>=1.50`.
- **18 nouveaux tests** : 14 sur l'adapter (Mock, factory, error mapping pour 8 types d'erreurs OpenAI), 4 sur le job (mock, transient mapping, permanent mapping). **47 tests verts** au total.
- **Doc** : `memo.md` (section LLM adapter avec switching cloud/Ollama), `README.md` (section vendor-neutral), `Jalon4.md` (walkthrough en cours).

### Ce que j'ai appris

- **`Protocol` (PEP 544) vs `ABC`** : `Protocol` permet le **structural subtyping** — une classe est un `LLMAdapter` si elle a les bonnes méthodes, **sans héritage explicite**. C'est plus pythonique, mieux outillé par mypy/pyright, et plus naturel pour des mocks de test (pas besoin d'hériter pour mocker). Réf : https://peps.python.org/pep-0544/.
- **Pourquoi `complete(system, user)` et pas `summarize(text)`** : le résumé est une **stratégie métier** (style narratif JDR vs formel réunion vs technique notes) — son template appartient au service, pas à l'adapter. Mettre `summarize` dans l'adapter casserait la séparation services/adapters de CLAUDE.md §2.4. Cette discussion en cours de jalon a clarifié le pattern et conduit à simplifier l'interface.
- **Un seul SDK pour 6 providers** : DeepInfra, Ollama, vLLM, Groq, Together AI, OpenAI exposent **tous** une API compatible OpenAI. Le SDK `openai` Python paramétré par `base_url` couvre tout. Gros multiplicateur : on apprend une API, on accède à tout l'écosystème.
- **Mapping HTTP → exceptions de l'adapter** : le SDK `openai` expose des classes typées (`AuthenticationError`, `RateLimitError`, `InternalServerError`…). On les attrape catégoriquement (transient vs permanent) et on remap vers nos exceptions. Cohérent avec la retry policy ADR 0004 : `TransientLLMError` → `TransientJobError` → RQ retry, `PermanentLLMError` → `PermanentJobError` → fail franc.
- **`asyncio.run` dans un job RQ sync** : RQ exécute des jobs sync, mais le SDK `openai` moderne est async. On franchit la frontière par `asyncio.run(adapter.complete(...))`. Coût : ~5 ms par appel (création d'event loop), négligeable face à des appels LLM de plusieurs secondes.
- **`lru_cache(maxsize=1)` sur la factory** : un seul adapter par processus → connection pool partagé du `AsyncOpenAI`. Pour les tests, `get_llm_adapter.cache_clear()` à invoquer (fixture `autouse=True`).
- **Logging des `usage` tokens** : DeepInfra/OpenAI renvoient `prompt_tokens`, `completion_tokens` dans la réponse. On les logge mais on ne les expose pas dans la signature de `complete` (YAGNI). Antichambre du tracking de coûts du Jalon 6.
- **Distinction "providers locaux" vs "cloud"** : Ollama et vLLM tolèrent une clé bidon (placeholder), DeepInfra/OpenAI exigent une vraie clé. La factory ne lève pas d'erreur sur clé vide pour ollama/vllm.
- **Construction d'instances `APIStatusError` en tests** : les exceptions OpenAI demandent un objet `Response` réel à leur constructeur. Pour les tests, on bypass via `cls.__new__(cls)` + setattr manuel des champs (status_code, message, body…). Pratique pour tester le mapping sans monter un serveur HTTP.
- **`monkeypatch.setattr` sur `app.adapters.llm.settings.LLM_PROVIDER`** plutôt que sur `app.core.config.settings.LLM_PROVIDER` : il faut viser le **nom local** dans le module qui le lit, pas le module source. Sinon le patch ne s'applique pas (binding au moment de l'import).
- **Switching cloud → local sans rebuild Docker** : 3 lignes de `.env` à modifier puis `docker compose down && up`. C'est exactement ce que le pattern Adapter promet, validé en pratique.

### Limitations acceptées

- **Pas de streaming** (`complete_stream`) : pas d'UI temps réel à ce stade ; coût futur ~30 lignes.
- **Pas d'`embed` ni de `chat` multi-tour** dans l'interface : YAGNI, à introduire selon les besoins du Jalon 5+.
- **Pas de `count_tokens` exposé** : utile pour estimer le coût avant l'appel ; à introduire au besoin.
- **Pas de fallback automatique** (cloud → local en cas d'échec) : pattern Decorator faisable plus tard, repoussé au Jalon 9.
- **Pas de validation du modèle au build de l'adapter** : si `LLM_MODEL` est inexistant chez le provider, l'erreur sort à la première requête (404 → PermanentLLMError). Acceptable.
- **`MockLLMAdapter` ne simule pas la latence** ni les erreurs : si on veut tester un timeout, on patche directement l'adapter dans le test.
- **Pas d'audit log des appels LLM** côté DB ou métriques agrégées : log structuré seulement, agrégation au Jalon 6.
- **Spec Kit non installé** : introduction documentaire dans `Jalon4.md`. À essayer en pratique au Jalon 5 si une feature complexe le justifie.

---

## 2026-05-18 — Jalon 5 : `kaeyris-jdr`, premier service métier complet

### Ce qui a été fait

- **Spec Kit utilisé en pratique** : `/speckit.specify` → `/speckit.clarify` (5 questions actées, voir ADR 0006) → `/speckit.plan` → `/speckit.tasks` → `/speckit.implement` par sous-lots. Les artefacts `specs/001-kaeyris-jdr/{spec,research,data-model,contracts,quickstart,tasks}.md` ont servi de référence pendant toute l'exécution.
- **ADR 0006** rédigé en début de jalon, consolide 5 décisions structurantes (ORM SQLAlchemy 2.x async, transcription via `TranscriptionAdapter` agnostique, auth roles GM/player DB-backed avec bootstrap depuis env var, mode live stub documenté, structure interne du service en 3 couches).
- **Persistance** : SQLAlchemy 2.x async + Alembic + aiosqlite (SQLite en dev, asyncpg/PostgreSQL repoussé Jalon 8). 8 tables `jdr_*` (`api_keys`, `pjs`, `sessions`, `audio_sources`, `transcriptions`, `session_pj_mappings`, `artifacts`, `jobs`). Migration `0001_initial.py` aller-retour fonctionnel.
- **Adapter de transcription** : `OpenAICompatibleTranscriptionAdapter` paramétré couvre cloud (OpenAI/Groq/DeepInfra) et local (futur hôte RTX 4090 + faster-whisper + pyannote sur le LAN). `MockTranscriptionAdapter` pour les tests.
- **Auth roles** : extension de `app/core/auth.py` — la table `jdr_api_keys` devient source de vérité, l'env var `API_KEYS` (Jalon 2) ne sert plus qu'au bootstrap au premier démarrage. `Role.GM`/`Role.PLAYER`, `require_gm`/`require_player` exposés comme dépendances FastAPI.
- **US1 (MVP)** : upload M4A → job `transcribe_session_job` (chunking ffmpeg client-side pour limiter le blast radius des hallucinations Whisper) → purge automatique du fichier source post-transcription → `_generate_narrative` (résumé en prose française fidèle au transcript).
- **US2** : fiche structurée `{npcs, locations, items, clues}` produite via un prompt strict JSON, parsing tolérant aux variations (fence ```json``` ou substring `{…}`), fallback en quatre listes vides plutôt que 500.
- **US3** : CRUD PJ, mapping `speaker_label → pj_id` par session avec invalidation cascade des artefacts `pov:*` quand le mapping change, génération d'un POV par PJ mappé (un appel LLM par PJ avec un user prompt préfixé d'un en-tête de scoping).
- **US4** : enrôlement de joueurs (`POST /players` génère un token URL-safe ≥ 32 octets, retourné en clair une seule fois ; hash Argon2 stocké), endpoints `/me/*` strictement scopés au PJ courant (FR-014 testé : impossible de fuiter le POV d'un autre joueur).
- **US5** : stub live publié — `POST /live/sessions` répond 501 Problem Details, `WS /live/stream` ferme code 1011 à la connexion. Le schéma futur des messages (`audio.chunk`, `session.end`, `transcript.partial`) est documenté en commentaires dans `app/services/jdr/live/router.py` pour alimenter l'OpenAPI.
- **Rendu Markdown** des artefacts (`render_transcription_md`, `render_narrative_md`, `render_elements_md`, `render_pov_md`) pour publier les résumés dans un format directement utilisable côté joueur.
- **Tests** : 248 tests verts (suite complète), TDD strict sur chaque US (tests rouges avant implémentation), test critique `test_player_access.py` qui acte FR-014.
- **8 commits incrémentaux** (Conventional Commits, format `feat(jdr): … (US3 sub-lot 5a)` etc.).

### Ce que j'ai appris

- **Spec-driven development sous Spec Kit, expérience pratique** : la friction perçue au démarrage (5 doc Markdown à digérer avant d'écrire une ligne) se rentabilise dès qu'on doit faire une décision non triviale en cours d'impl — par exemple, "le `pov:*` doit-il être invalidé sur changement de mapping ?" → la réponse est dans `data-model.md §6` sans avoir à re-réfléchir. Le tasks.md découpé par US permet aussi des commits atomiques bien plus propres.
- **`use_alter=True` sur les FK pour casser les cycles** : `api_keys.pj_id → pjs.id` et `pjs.owner_gm_key_id → api_keys.id` forment un cycle qui empêche Alembic de générer le `CREATE TABLE` dans le bon ordre. SQLAlchemy règle ça en émettant un `ALTER TABLE … ADD CONSTRAINT` après la création des deux tables. Source : https://docs.sqlalchemy.org/en/20/core/constraints.html#sqlalchemy.schema.ForeignKey.params.use_alter
- **Path param `{pj_id}.md` ne marche pas avec FastAPI** : Starlette match `{name}` avec `[^/]+`, donc `<uuid>.md` est avalé en entier dans le path param, puis la conversion `UUID` échoue → 422. Solution adoptée : un seul handler `GET /povs/{pj_id_str}` qui dispatch selon le suffixe `.md`. Trade-off documenté en commentaire de la route.
- **Layered exceptions strictes** : chaque couche ne connaît que ses propres exceptions, jamais celles de la couche en aval. Exemple `DuplicatePjNameError` (repo) → `DuplicatePjError` (logic) → `DuplicatePjConflictError` (route, 409). Verbose mais limpide à la lecture ; aucun `IntegrityError` ne fuite jusqu'au router.
- **Audio chunking client-side pour cap blast-radius Whisper** : sur sessions longues (~2h), Whisper peut entrer en "repetition loop" et répéter la même phrase sur plusieurs minutes. En découpant en chunks de 30 s avant l'appel API, un loop ne peut contaminer qu'un seul chunk au lieu de toute la session. Mis en place dans `app/services/jdr/audio.py` via ffmpeg, configurable via `TRANSCRIPTION_CHUNK_DURATION_SECONDS`.
- **`secrets.token_urlsafe(32)` pour les tokens joueurs** : standard Python pour générer un secret URL-safe d'au moins 256 bits d'entropie. Le serveur n'en garde que le hash Argon2 ; le plaintext n'est exposé qu'une seule fois dans la réponse `201` de `POST /players`.
- **Pattern "session par requête" via dépendance FastAPI** : `get_db_session` yield une `AsyncSession`, commit en sortie / rollback sur exception. Pattern canonique, overridable en tests via `app.dependency_overrides[get_db_session] = make_db_session_dep` pour brancher SQLite en mémoire.
- **Convention `kind = "narrative" | "elements" | "pov:<pj_id>"`** dans `jdr_artifacts` avec PK composite `(session_id, kind)` : permet une UPSERT propre (`INSERT … ON CONFLICT … DO UPDATE`) sans gérer un `updated_at` séparé, et le `kind LIKE 'pov:%'` côté `invalidate_pov_artifacts` fait une suppression cascade très lisible.
- **Stub avec contrat OpenAPI** : déclarer un endpoint qui retourne toujours 501 mais avec un Pydantic body complet (`LiveSessionInit`) fait en sorte que `/openapi.json` documente la surface future. Discoverabilité du contrat sans payer le coût de l'implémentation — pattern réutilisable.
- **Bootstrap idempotent depuis env var** : le hook `on_startup` lit `API_KEYS` mais ne l'importe que si la table est vide. Un redémarrage avec `API_KEYS` toujours présent n'a aucun effet (log info). Évite les doubles imports en prod, sans casser le path d'onboarding Jalon 2.

### Limitations acceptées

- **Diarisation absente avec le provider cloud par défaut** : OpenAI Whisper API ne sépare pas les locuteurs → tous les segments arrivent avec `speaker_label="unknown"` → les résumés POV restent pauvres. Bascule prévue vers l'hôte GPU LAN (faster-whisper + pyannote) — la procédure côté GPU host est documentée dans [`docs/services/jdr.md`](./services/jdr.md) §5, mais le wrapper lui-même est hors scope (repo séparé à venir).
- **Single-shot summarisation** : pour des sessions de 2h+, le prompt user (transcription complète) peut dépasser ~30-45k tokens, à risque de "lost in the middle" avec la plupart des modèles. Pas de stratégie map-reduce pour l'instant — à mettre en place au Jalon 6+ quand une première session réelle montrera la limite.
- **Validation E2E avec une vraie clé DeepInfra non automatisée** : la suite pytest tourne sans appel LLM réel (mock `_StubLLM`). La validation `quickstart.md` doit être exécutée manuellement avant de fermer formellement le Jalon 5 (cf. T076 dans tasks.md).
- **Pas de PostgreSQL en dev** : SQLite + `aiosqlite` couvre tous les tests. Le passage à `asyncpg`/PostgreSQL est verrouillé pour le Jalon 8 (déploiement Pi) — `DATABASE_URL` change, le code reste identique.
- **Mode live = stub uniquement** : aucun chunk audio ingéré en streaming au Jalon 5. Implémentation prévue Jalon 6+ avec un bot Discord en amont.

---

## 2026-05-18 — Sub-jalon 5.5 : mode `non_diarised` (feature 002)

### Ce qui a été fait

- **Spec Kit complet réutilisé** pour la deuxième fois : `/speckit-specify` → `/speckit-clarify` (3 réponses A/A/A actées) → `/speckit-plan` (genère research, data-model, contracts, quickstart) → `/speckit-tasks` (60 tâches numérotées par US) → `/speckit-implement` (workflow strict utilisé pour la première fois). Pivot de scope en cours de session : un premier `/speckit-specify` partait sur une "session_summary" générique, jeté après clarification du besoin réel ; le second tour a livré le scope final correct.
- **ADR 0007** rédigé : 4 décisions structurantes (tag posé à la création + immuable, persistance `chunks.summary_text` inline, prompts système réutilisés, atomicité de la cascade avec LLM hors transaction). Plus 3 décisions de surface tranchées via clarify.
- **Migration Alembic 0003** : `ALTER jdr_sessions ADD transcription_mode` + `CREATE TABLE jdr_chunks` + `CREATE TABLE jdr_session_players`. Aller-retour testé OK. `server_default='diarised'` garantit la rétro-compat des sessions Jalon 5.
- **ORM étendu** : enum `TranscriptionMode`, classes `Chunk` et `SessionPlayer`. Repositories `ChunkRepository`, `SessionPlayerRepository`. `JobKind.SUMMARY` ajouté.
- **Pipeline forké en interne sans modifier le contrat HTTP** : `_transcribe_session`, `_generate_narrative`, `_generate_elements`, `_generate_povs` détectent `session.transcription_mode` et adaptent leur stockage/lecture. Helper commun `_load_session_source_document` qui retourne le bon document selon le mode.
- **Map-reduce `_generate_summary`** : reset cascade dans une transaction courte commitée AVANT les LLM calls (FR-011), puis map par chunk (commit par chunk), puis reduce final (skippé si 1 seul chunk). Mapping erreurs LLM → JobError cohérent ADR 0004.
- **Nouveaux endpoints** : `POST/GET /chunks`, `POST/GET /players`, `POST/GET/.md /artifacts/summary`. Extensions transparentes de `POST /sessions` (champ `transcription_mode`) et `PATCH /sessions/{id}` (rejet de `transcription_mode`).
- **Cross-mode isolation** stricte : 7 codes erreur nouveaux (`wrong-mode`, `no-summary`, `no-chunks`, `invalid-player-list`, `invalid-transcription-mode`, `immutable-field`, plus `pj-not-found` de US4).
- **6 commits incrémentaux** sur la branche `002-non-diarised-mode` : scaffolding (Phase 1+2), US1, US2, US3, et le polish final.
- **Tests** : 301 tests verts au total (248 Jalon 5 préservés sans modification + 53 nouveaux : 10 chunker, 17 US1, 13 US2, 13 US3). Pas une seule régression côté Jalon 5 (FR-014 garanti par construction).

### Ce que j'ai appris

- **Spec Kit en mode strict `/speckit-implement` vs implémentation libre** : sur ce projet (rodé, tests-first installé, solo), la différence est faible — le `tasks.md` détaillé est déjà la checklist exécutable. La valeur de `/speckit-implement` est sur la traçabilité (`[X]` cochés au fur et à mesure) et la discipline phase-par-phase forcée. Sur une équipe ou un projet moins discipliné, ce serait plus rentable.
- **Python 3.12 + StrMixinEnum + SQLAlchemy `String` = piège silencieux** : `str(MyEnum.X)` retourne `"MyEnum.X"` (repr) au lieu de `"x"` (value) depuis 3.11, contrairement à 3.10. Conséquence : `mapped_column(String(16))` sur un enum mixin stocke `"TranscriptionMode.NON_DIARISED"` au lieu de `"non_diarised"`. Toutes les lectures `WHERE col == "non_diarised"` ratent. Solution : `Enum(MyEnum, native_enum=False, length=16)` qui passe par `.value` correctement. Aligné avec le pattern Jalon 5 (Role, SessionState, etc.). Caught in 11 failing tests then ~5 min to debug — leçon mémorable. Source : https://docs.python.org/3/library/enum.html#enum.StrEnum
- **Map-reduce LLM transactionnel : courte transaction de reset puis LLM hors transaction** : si on englobe les LLM calls dans une transaction DB unique (jusqu'à 5 min pour 60k chars), on bloque les autres workers + risque de timeout côté Postgres. Pattern propre : reset+delete dans une transaction courte commitée AVANT les LLM calls. Si le map fail, l'ancien `summary` global survit mais les `chunks.summary_text` et les artefacts dérivés ont déjà été nettoyés — état dégradé mais cohérent, MJ peut relancer.
- **Persistance inline `summary_text` sur `Chunk` plutôt qu'artefacts séparés** : décision actée en clarify. Évite l'explosion du nombre de rows dans `jdr_artifacts` (qui contiendrait 1 row par chunk × N sessions au lieu d'1 row globale). Reset cascade = simple `UPDATE WHERE session_id = ...`, atomique à coût constant.
- **Réutiliser les system prompts existants entre modes** : décision moins évidente qu'il n'y paraît — on a été tenté de créer `NARRATIVE_SYSTEM_PROMPT_NON_DIARISED`. Mais le system prompt définit la *nature* (récit, fiche, POV), pas la *forme* de l'input. Dupliquer = risque de divergence non intentionnelle au fil des révisions. Le user prompt (côté job) embarque les variations spécifiques au mode.
- **`request.json()` pour détecter une clé dans un PATCH avec Pydantic strict** : `SessionUpdate` n'a pas `transcription_mode` comme champ, donc Pydantic ignore silencieusement la clé (`extra="ignore"`). Pour la détecter et raise 422, on lit le body brut via `request: Request` injecté en argument. FastAPI cache le body donc le double-parse est gratuit. Pattern utilisable pour tout champ immuable post-création.
- **`server_default` Alembic pour les migrations rétroactives non-nullable** : `ALTER TABLE jdr_sessions ADD COLUMN transcription_mode VARCHAR(16) NOT NULL` aurait fail sur les rows Jalon 5 existantes (pas de valeur à insérer). `server_default='diarised'` fait que SQLite/Postgres remplit automatiquement. Migration zéro-clic, aucun script de backfill nécessaire.
- **Spec Kit pivot en cours de session** : tenter `/speckit-specify` puis se rendre compte que le scope est mal cadré arrive — pas grave. La branche locale + dossier `specs/00X` non commité se jettent proprement (`git branch -D`). C'est exactement le rôle de `/speckit-clarify` : forcer la question "est-ce vraiment ce qu'on veut ?" avant que l'effort d'implémentation se déclenche.

### Limitations acceptées

- **POV qualitativement limités en non_diarised** : sans speaker labels, le LLM doit deviner qui agit depuis le contexte des résumés chunked. Limite explicite dans le user prompt POV. À ré-évaluer après l'arrivée de la diarisation locale (Jalon 9) — pourrait alors être étendu en map-reduce POV-aware.
- **Mode immuable post-création** : un MJ qui s'est trompé doit créer une nouvelle session. Trade-off assumé pour éviter la complexité d'une conversion `segments ↔ chunks` qui ne préserverait pas la fidélité du texte.
- **`/me/*` joueur réservé aux sessions `diarised`** au sub-jalon courant. Un joueur sur une session non_diarised voit 409 wrong-mode. À reconsidérer si la première vraie session non_diarised révèle un besoin UX joueur concret.
- **Map-reduce sur mode `diarised`** : hors scope (à reconsidérer Jalon 9+ quand les sessions diarisées prendront aussi du volume).
- **Validation E2E avec une vraie clé DeepInfra non automatisée** : pareil que T076 du Jalon 5 — à exécuter manuellement avant clôture formelle du sub-jalon (T059 dans `tasks.md`).

---

## 2026-05-19 — Jalon 6 : Observability (logs + métriques + healthchecks + traces)

### Ce qui a été fait

- **Pas de Spec Kit** : décision actée en début de jalon car la feature est techno-transverse sans ambiguïté métier (stack lockée CLAUDE.md §3 sur structlog + prometheus-client, decisions tranchées au prompt). 5 phases distinctes pilotées via un mini-plan inline + ADR 0008 à la fin. Premier jalon hors `/speckit-*` du projet.
- **Phase 1 — Logs structurés** (`structlog`) : découverte gênante que `structlog` était locké dans CLAUDE.md §3 mais **jamais réellement installé ni utilisé** (8 modules en `logging` stdlib avec calls printf-style). Phase 1 a donc été plus lourde qu'estimée : ajout de la dépendance + bridge stdlib → structlog + migration des 8 modules + reformulation des 17 calls hotpath en idiom `event.name` + kwargs. Plus un `RequestContextMiddleware` qui mint un `request_id` UUIDv4 (ou trust un `X-Request-Id` entrant) et le bind au context structlog → corrélation auto sans plumbing.
- **Phase 2 — Métriques Prometheus** : 9 séries `kaeyris_*` (HTTP, LLM, transcription, jobs) sur 4 dimensions, naming Prometheus standard, cardinalité bornée par usage des **route templates** plutôt que paths concrets (sinon explosion 1 série/UUID). Endpoint `/metrics` text exposition, instrumentation try/finally autour de chaque adapter pour garantir la mesure même sur exception.
- **Phase 3 — Healthchecks** : `/healthz` (liveness sans dépendance), `/readyz` (DB + Redis pingués, 503 + détail per-check si fail), `/health` legacy gardé en alias. Pattern Kubernetes-style même hors K8s (utile pour systemd / Docker Compose healthcheck / sondes futures).
- **Phase 4 — OpenTelemetry scaffolding** : décision la plus discutée — finalement **opt-in minimal** plutôt que skip complet. Auto-instrumentation FastAPI/SQLAlchemy/httpx via `OTEL_ENABLED=true`, exporter console ou OTLP/HTTP vers `OTEL_EXPORTER_OTLP_ENDPOINT`. **Pas de spans manuels custom** — différés Jalon 8 où un collector réel sera monté. Test isolation forcée via mock des instrumentors (sinon ils patchent globalement les frameworks et fuitent entre tests).
- **Phase 5 — Polish** : ADR 0008 (4 décisions structurantes + alternatives rejetées), README endpoints + section observabilité, `docs/memo.md` env vars + cheatsheet, `.env.example` complet.
- **5 commits incrémentaux** sur branche `003-observability` : un par phase, rollback granulaire facile.
- **322 tests verts** au total : 301 anciens (Jalon 5 + sub-jalon 5.5) + 21 nouveaux (6 logging + 3 metrics + 4 healthchecks + 8 tracing). Zéro régression Jalon 5/5.5.

### Ce que j'ai appris

- **CLAUDE.md §3 "stack lockée" peut être aspirationnel** : structlog y était mais inexistant en pratique. Leçon : auditer concrètement avant d'estimer (j'ai estimé Phase 1 à ½ j, c'était presque 1 j à cause de la migration des 8 modules). Audit avant estimation, toujours.
- **Bridge `logging` stdlib → `structlog` plutôt que remplacement total** : les libs tierces (`httpx`, `sqlalchemy.engine`, `openai`) écrivent dans le `logging` standard. Si on switche à un autre framework de log (loguru) qui ignore stdlib, on perd leurs messages. Le bridge structlog les capture en passant. Source : https://www.structlog.org/en/stable/standard-library.html
- **Python 3.12 + StrMixinEnum** : déjà rencontré sub-jalon 5.5. Cette fois OK car j'utilise des `os.environ.get(..., default).lower()` qui sont des strings purs.
- **Cardinalité Prometheus = piège n°1** : si tu mets un UUID dans un label, c'est game over (1 série/UUID, le scrape met 2 min, la consommation RAM grimpe en gigas, AlertManager devient inutilisable). Le pattern correct : label `route` = **template FastAPI** (`/sessions/{session_id}/...`), pas le path concret. Documenté avec emphase dans `app/core/metrics_middleware.py`. Source : https://prometheus.io/docs/practices/naming/#labels
- **try/finally pour mesurer la durée même sur exception** : pattern simple mais facile à oublier. Sans le `finally`, un appel LLM qui timeout n'est pas compté dans `kaeyris_llm_call_duration_seconds` → biais des p99 vers la baisse. J'ai utilisé `outcome = "success"` comme valeur par défaut + setter dans chaque except.
- **`prometheus_client` métriques sont au niveau module** : enregistrées dans `REGISTRY` global. Pas idéal pour isoler les tests (les compteurs accumulent entre tests). Solution pragmatique : ne pas asserter sur les valeurs exactes, asserter sur les **noms** présents et le **format** de sortie via parsing du `/metrics`. Suffit pour ce qu'il faut prouver.
- **OTEL auto-instrumentation modifie l'état global du process** : `FastAPIInstrumentor.instrument_app(app)` monkey-patche la classe FastAPI globalement. Si un test active OTEL et un autre crée une nouvelle FastAPI(), elle hérite de l'instrumentation. Fuite gênante qui fait planter les tests suivants avec des erreurs d'export de span. Solution adoptée : mock les 3 instrumentors dans le test de tracing (`monkeypatch.setattr(tracing_module, "FastAPIInstrumentor", MagicMock())`), tester juste le code path. Validation réelle est manuelle.
- **Healthcheck `/readyz` ne devrait PAS ping le LLM provider** : tentation initiale puis arbitrage — un ping LLM coûte de l'argent (call API payant) et n'a pas de "ping gratuit" côté OpenAI-compatible. La santé du provider LLM est surveillée via `kaeyris_llm_calls_total{outcome="permanent"}` qui monte, pas via un check synchrone. Réflexe à garder : un healthcheck doit être bon marché ET corrélé à la santé réelle.
- **redis-py est sync, le reste async** : `redis.ping()` bloque. Dans un handler FastAPI async, on l'enveloppe dans `asyncio.to_thread(...)` pour ne pas bloquer le loop. ~5 lignes mais ça évite que `/readyz` freeze le worker entier si Redis répond lentement.
- **Spec Kit n'est pas obligatoire pour tout** : la décision de skip Spec Kit pour le Jalon 6 a fait gagner ~½ jour de cérémonie. Critère : si les décisions sont déjà documentées dans la stack lockée et qu'il n'y a pas d'arbitrage métier ouvert (FRs ambigus), implem libre + ADR à la fin suffit. Spec Kit pour les features structurantes (Jalon 5 service entier, sub-jalon 5.5 mode non_diarised), pas pour les couches techno transverses.

### Limitations acceptées

- **Pas de dashboard Grafana ni d'alerting Alertmanager** : le scope est instrumentation seule. Visualisation + alerting au Jalon 8 (déploiement PC fixe avec sidecars Docker Compose).
- **OTEL réelle activation jamais testée en CI** : les tests mockent les instrumentors. La première validation en conditions réelles arrivera au Jalon 8 contre un vrai collector.
- **`/readyz` ne check pas les workers RQ vivants** : un worker mort se voit via `kaeyris_jobs_total` plat, pas via `/readyz`. Limite assumée — un check synchrone exigerait soit un ping Redis sur la queue (déjà couvert) soit un mécanisme de heartbeat custom.
- **Pas de profiling intégré** (`py-spy`, cProfile dumps) : à introduire si une session réelle révèle un bottleneck non-explicable via les métriques.
- **6 deps OTEL ajoutées même si inactives par défaut** : footprint mémoire +~25 Mo au démarrage. Acceptable mais pas zéro.
- **Pas de validation E2E formelle sur la nouvelle stack obs** : à faire avant de fermer le jalon (lancer une session JDR réelle, scraper `/metrics` à plusieurs instants, vérifier que les histograms se peuplent, que le summary `_generate_summary` met bien à jour le compteur `kaeyris_jobs_total{kind="summary",outcome="succeeded"}`, etc.).

## 2026-05-20 — Hotfix `transcription_mode` Enum lookup (post-Jalon 5.5)

### Le bug

`GET /services/jdr/sessions` renvoyait un `500` avec `LookupError: 'diarised' is not among the defined enum values. Possible values: DIARISED, NON_DIARISE..`. La migration `0003_non_diarised_mode` avait posé un `server_default='diarised'` (lowercase, le `.value` de l'enum) sur la nouvelle colonne `transcription_mode`. Le mapping SQLAlchemy `Enum(TranscriptionMode, native_enum=False)` matche par défaut les valeurs DB contre **le nom** des membres (UPPERCASE) — donc le `SELECT` cassait sur toutes les rows héritant du default.

### Le fix (commit `0cdca84`)

Ajout de `values_callable=lambda enum_cls: [m.value for m in enum_cls]` sur la colonne, qui force SQLAlchemy à matcher par `.value` (lowercase, source: [doc SA Enum.params.values_callable](https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.Enum.params.values_callable)). Scope limité à `transcription_mode` car c'est la seule colonne avec un `server_default` lowercase ; appliquer le fix à `mode`/`state`/`role`/`kind`/`status` aurait invalidé les rows historiques écrites en UPPERCASE par l'ORM.

Test régression à 3 cas dans `tests/services/jdr/test_transcription_mode_enum_lookup.py` :
- Une row dont le `transcription_mode` est posé par le `server_default` lowercase → ORM SELECT OK.
- Une row avec `'non_diarised'` explicite → ORM SELECT OK.
- Roundtrip ORM insert→read.

### Le second bug (et l'ADR de la pédagogie)

Mon premier hotfix a inversé le sens du mismatch sans s'en rendre compte : les rows déjà créées en E2E sub-jalon 5.5 contenaient `'NON_DIARISED'` (UPPERCASE, ce que l'ORM écrivait avant le fix). Après le fix, l'ORM matche par `.value` → ces rows-là devenaient illisibles. Symptôme identique mais inversé : `LookupError: 'NON_DIARISED' is not among the defined enum values`.

**Cause racine de ma rétrospective** : j'avais identifié ce risque en analysant le fix mais j'ai supposé que la DB ne contiendrait que des rows lowercase. Mauvais pari — la phase de QA sub-jalon 5.5 avait créé plusieurs sessions UPPERCASE via l'ORM. Mes tests n'ont pas couvert le scenario "row UPPERCASE pré-fix".

### Migration 0004 (commit `3327640`)

`UPDATE jdr_sessions SET transcription_mode = LOWER(transcription_mode) WHERE transcription_mode IN ('DIARISED', 'NON_DIARISED')`. Downgrade pour la réversibilité. Test régression ajouté qui insère raw SQL une row UPPERCASE, applique l'UPDATE, et asserte que l'ORM lit `TranscriptionMode.NON_DIARISED`.

### Ce que j'ai appris

- **`values_callable` est la convention par défaut souhaitable** quand on veut que la sérialisation Enum corresponde à ce que l'API REST expose via Pydantic. Si on l'avait mis dès le Jalon 5, le bug n'aurait jamais existé.
- **Un test de régression doit couvrir le "before-state" et le "after-state"** d'une migration. Pour un fix qui change une convention de stockage, il faut tester les rows pré-existant le fix, pas juste le scenario nominal.
- **Les server_default Alembic doivent être lockstep avec la sérialisation ORM**. Idéalement, utiliser `server_default=MyEnum.X.value` ET `values_callable` ensemble (et c'est ce qu'on a fini par faire).

## 2026-05-20 — Jalon 7 : CI/CD + Security hardening

### Phases livrées

1. **CI GitHub Actions** (`ci.yml`) : `lint` (ruff) + `test` (pytest -q) sur push `main` et PR vers main. Concurrency group pour annuler les runs périmés. Badge dans README.
2. **SAST bandit** : configuration dans `[tool.bandit]` (`exclude_dirs` pour tests/migrations, `skips=[B101]`), job CI gate `--severity-level medium` — 0 finding M/H au baseline (5 Low ignorés sur les appels subprocess `ffmpeg`/`ffprobe`).
3. **Dependency scan pip-audit** : non-bloquant (`continue-on-error: true`). 1 CVE upstream identifiée au baseline (`idna 3.13` → CVE-2026-45409, fix `3.15`) — surfacée dans les logs CI, à traiter quand le fix sort.
4. **Secrets scan gitleaks** : `gitleaks/gitleaks-action@v2` avec config `.gitleaks.toml` (allowlist `.env.example`, `tests/`, `scripts/generate_api_key.py`). Bloquant.
5. **Pre-commit hooks** : `.pre-commit-config.yaml` qui miroir la CI (ruff, bandit, gitleaks + hygiène trailing whitespace/EOF/large-files/detect-private-key). Installation optionnelle.
6. **ADR `0009-cicd-security.md`** + cette entrée + entrée mémo (commandes locales).

### Décisions structurantes

- **bandit > semgrep** : mono-langage Python à 5k LoC, semgrep est sur-dimensionné et dépend du registry en ligne.
- **pip-audit > safety / snyk** : OSV gratuit, sans cap, sans compte.
- **gitleaks > trufflehog / detect-secrets** : moins bruyant, pas de baseline à maintenir.
- **pip-audit non-bloquant** : pragmatisme. Un CVE upstream sans patch immédiat ne doit pas bloquer une PR sans lien. Discipline humaine de relire les logs.
- **Pre-commit optionnel mais documenté** : la CI reste le filet de sécurité primaire ; les hooks accélèrent juste le feedback local.

### Ce que j'ai appris

- **Le `concurrency` group est sous-utilisé** : sans lui, push 3 commits successifs lance 3 workflows full, dont 2 deviennent obsolètes immédiatement. Avec `cancel-in-progress: true`, seul le dernier tourne.
- **`severity-level medium` sur bandit est le bon défaut** pour démarrer. Bloquer sur Low aurait imposé de désactiver B404/B603/B607 ou de wrapper chaque subprocess dans un `# nosec` — bruit pour zéro valeur.
- **Sécurité par défaut ≠ paranoïa par défaut** : `pip-audit` bloquant donnerait l'illusion de la rigueur mais friction quotidienne sans valeur ajoutée (les CVE upstream ne sont pas exploitables dans un contexte API privée LAN). Le bon réflexe : surveiller, pas bloquer.
- **Pre-commit + gitleaks staged-only**, c'est rapide (<1s) parce qu'il scan le diff, pas l'historique. La CI scanne l'historique complet via `fetch-depth: 0` pour rattraper les forced-push.

### Limitations acceptées

- **Pas de coverage tracking** : repoussé au Jalon 8 quand on aura une cible chiffrée. Mesurer un % sans budget actionnable = vanity metric.
- **pip-audit non-bloquant** : à promouvoir à bloquant quand on aura un tracker formel pour le triage CVE (Linear ou GitHub Projects).
- **Pas de signature de commits** (`commit.gpgsign`) : pas de chaîne de confiance entre `main` et l'image Docker. À ajouter au Jalon 8 si on déploie sur infra non-locale.
- **Pas de SBOM** (CycloneDX/syft) : hors-scope, projet perso. À ajouter avec la signature d'image Docker.
- **Branch protection rules côté GitHub à activer manuellement** : la CI peut être bypass-ée par un admin tant que les rules ne sont pas configurées. La doc README pointe l'action à faire.

## 2026-05-20 — Jalon 8 : Déploiement PC fixe LAN (Postgres + Caddy + Watchtower + monitoring)

### Phases livrées

1. **Dockerfile prod-ready** : HEALTHCHECK baked sur `/healthz`, layering optimisé (pyproject.toml AVANT source pour cache), shipping de `migrations/` + `alembic.ini`. `.dockerignore` qui prune .git/.venv/.env*/data/tests/docs.
2. **PostgreSQL en prod** : ajout `asyncpg>=0.30` aux deps runtime. Aucun code change dans `app/core/db.py` — le switch est purement via `DATABASE_URL`. Les migrations 0001-0004 utilisent uniquement des types portables.
3. **`docker-compose.prod.yml`** : 9 services orchestrés — postgres + redis + migrations (one-shot) + api + worker + caddy + watchtower + prometheus + grafana. Réseaux séparés `internal` / `edge`. Volumes nommés pour persistance.
4. **Caddy reverse proxy HTTP** : seul service publié sur le host (port 80). `/metrics` gated par basic auth (hash bcrypt en env). Headers de sécurité, strip du banner `Server`.
5. **Workflow `release.yml`** : build multi-arch (amd64 + arm64 via QEMU) + push GHCR sur `main` et tags `v*`. Watchtower poll 5min, scope label-enable pour ne toucher que api/worker/migrations.
6. **Prometheus + Grafana** auto-provisionnés : datasource locked, dashboard `kaeyris-overview.json` à 5 panels (HTTP rate/p95, jobs rate/duration, LLM tokens).
7. **ADR 0010** + nouveau **`docs/runbook.md`** + cette entrée + memo + README.

### Décisions structurantes

- **Pattern delivery = Pull-based GHCR + Watchtower** (option A) : le PC fixe est derrière un routeur sans IP publique, le pull supprime toute exigence de port ouvert. ~5 min de latence acceptable pour un projet perso.
- **Postgres en prod** (vs SQLite) : ferme la dette dev/prod parity (12-Factor §X) et débloque la concurrence write multi-worker.
- **Caddy HTTP only** : LAN privée 192.168.x.x, HTTPS via internal CA forcerait trust-store install sur chaque client. Promotion HTTPS triviale plus tard (1 ligne dans le Caddyfile).
- **Multi-arch amd64+arm64** malgré Pi 5 optionnel : ~30s build supplémentaires, $0, garde l'option matérielle ouverte.
- **Migrations comme service one-shot** : isolation des préoccupations, visibilité via `docker compose ps`, Watchtower-friendly (re-run automatique sur nouvelle image).

### Ce que j'ai appris

- **`depends_on.condition: service_completed_successfully`** est exactement le bon outil pour orchestrer des one-shot tasks comme les migrations. Sans ça, on aurait besoin d'un init container ou d'un script d'entrypoint qui pollue le Dockerfile.
- **Watchtower label-enable** est crucial sur un compose avec stateful services : sans `WATCHTOWER_LABEL_ENABLE=true`, postgres/redis/caddy seraient pull-restart à chaque release upstream — désastreux pour les volumes attachés et les connexions ouvertes.
- **Caddy `basic_auth` (v2.8+)** prend un hash bcrypt PAS le plaintext. La directive existe depuis longtemps mais a été renommée de `basicauth` à `basic_auth`. La commande `caddy hash-password --plaintext` est la voie officielle.
- **`docker compose config --quiet`** est la meilleure CI-friendly validation : exit non-zero si une env var manque, silence si tout résout. Bien meilleur que `up --dry-run`.
- **GHA cache `type=gha,mode=max`** est gratuit et accélère les builds multi-arch de ~60s → ~20s sur les itérations.
- **Dev/prod parity ne veut pas dire "même DB"** mais "même classe d'incidents". SQLite en prod aurait été acceptable pour un mono-worker — mais avec RQ workers en parallèle, le risque de corruption sur fsync race justifie Postgres même à ce scale.

### Limitations acceptées

- **Pas de secret manager** : `.env` sur la machine hôte contient `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, clés API. Acceptable pour projet perso, à promouvoir vers Vault/Doppler si le projet sort de la sphère perso.
- **Watchtower `rw` Docker socket** : trade-off classique automation vs least-privilege. Mitigé par le scope `label-enable` mais reste un super-pouvoir.
- **Pas de canary / blue-green** : tout déploiement est un swap atomique de `:latest`. Acceptable au scale actuel.
- **Pas de validation E2E du déploiement complet** : `docker compose config` valide la syntaxe mais pas le `up` réel. Le user fera le run réel sur son PC fixe. Documenté dans le runbook.
- **`CADDY_METRICS_HASH` initial dans `.env.example`** est un placeholder évident (`REPLACE_ME_WITH_REAL_HASH`) — gitleaks scan le repo, le placeholder ne déclenche pas la règle bcrypt. Le user doit obligatoirement le régénérer avant le premier `up`.
- **HTTPS skipped** : justification dans ADR 0010, promotion triviale plus tard.

---

## 2026-05-27 — Feature 003 : User/password auth web

### Ce qui a été fait

- Ajout d'un setup initial sans mot de passe par défaut : `GET /services/jdr/auth/setup/status`, puis `POST /services/jdr/auth/setup` crée le premier GM seulement si `core_users` est vide.
- Ajout des tables `core_users` et `core_web_sessions` avec migration Alembic `0005_user_password_auth.py`.
- Ajout du login web `POST /services/jdr/auth/login` avec `username + profile + password`, réponse 200 et cookie HTTP-only `session`.
- Ajout de la gestion GM des comptes : création, liste, modification, suppression logique, garde-fou contre la suppression du dernier GM actif.
- Conservation des API keys existantes : Bearer reste prioritaire ; le cookie est utilisé seulement sans header `Authorization`.
- Tests ciblés : hashing, sessions actives/expirées/révoquées, setup, login, logout, gestion users, et cookie-auth sur route protégée.

### Ce que j'ai appris

- **Le setup initial est meilleur qu'un `admin/admin` connu** : il évite le piège OWASP des credentials hardcodés tout en gardant une UX de première installation simple. Source : https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password
- **Session opaque côté serveur > JWT pour ce besoin** : le backend peut révoquer immédiatement une session supprimée ou logout sans gérer de blacklist. C'est plus simple pour un monolithe avec DB.
- **Compatibilité progressive** : les routes JDR existantes stockent encore l'ownership via `jdr_api_keys.id`. Donner aux GM web une clé interne non exposée évite de refactorer tout le modèle JDR dans la même feature.
- **Un cookie HTTP-only ne suffit pas seul** : il réduit l'exposition au JavaScript, mais il faut aussi limiter les origins CORS et garder `SameSite=Lax`. Référence cookie HTTP : https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies

### Limitations acceptées

- Pas d'OAuth/OIDC, invitation email, reset email ou self-service signup public.
- Les profils web sont limités à `gm` et `user`; `user` n'est pas encore mappé au rôle JDR `player`.
- La garantie anti double-setup est verrouillée par process applicatif ; en multi-process strict, il faudra renforcer avec contrainte/lock DB.

---

## 2026-05-31 — Feature 004 : Campaign auth context BD-4

### Ce qui a été fait

- Ajout de `GET /services/jdr/auth/me` pour le front web : cookie `session` obligatoire, réponse publique `user` + `active_campaign`, `Cache-Control: no-store`.
- Ajout des tables `jdr_campaigns` et `jdr_campaign_members`, de `core_users.default_campaign_id`, et de `campaign_id` sur `jdr_sessions` / `jdr_pjs`.
- First-run setup et création d'utilisateurs rattachent automatiquement les comptes à la campagne V1 par défaut.
- Les listes et accès JDR principaux filtrent maintenant par campagne active dérivée côté serveur.
- Tests ciblés ajoutés pour `/auth/me`, memberships, adoption/backfill, isolation campagne, et régressions auth existantes.

### Ce que j'ai appris

- **Le commit 003 n'était pas BD-4** : login/setup/users peut fonctionner tout en laissant `/auth/me` non implémenté. Une feature d'auth web et une feature de contexte runtime ne sont pas le même contrat.
- **Le scope ne doit pas venir du client** : ne pas accepter `campaign_id` dans les bodies évite un contournement d'autorisation classique. La campagne active vient de la session ou du PJ lié à une clé joueur.
- **Compatibilité progressive** : garder les API keys Bearer prioritaires permet aux clients machine existants de continuer, tout en ajoutant le contexte riche nécessaire au front.

### Limitations acceptées

- Pas de CRUD campagne ni de switch campagne dans BD-4.
- Les colonnes `campaign_id` restent tolérantes côté ORM pendant la transition ; les chemins applicatifs nouveaux les renseignent systématiquement.
- Les API keys GM sans utilisateur web associé restent un mode legacy à surveiller si on ajoute du multi-campagne réel.

---

## 2026-06-01 — BD-5 : Datetime JSON avec timezone explicite

### Ce qui a été fait

- Ajout d'un helper transversal `app/core/datetime_serialization.py` pour interpréter les datetimes naïves comme UTC, convertir les datetimes aware en UTC, et sérialiser avec suffixe explicite.
- Branchement de ce helper dans les schémas Pydantic JDR et user/auth plutôt que dans les handlers HTTP.
- Tests de contrat sur les réponses session, liste session, PJ, users, et sur les variantes d'input `Z`, offset numérique, et naïf.

### Ce que j'ai appris

- `DateTime(timezone=True)` ne garantit pas à lui seul que toutes les valeurs ressortent aware dans tous les environnements, notamment avec SQLite en dev. Le contrat public doit donc être testé au niveau HTTP.
- Une correction de sérialisation est moins risquée qu'une migration globale quand les colonnes sont déjà déclarées timezone-aware et que le bug visible est le JSON de sortie.
- Les tests doivent viser le symptôme client (`"2026-05-31T18:00:00"` sans suffixe) plutôt que seulement le type Python interne.

### Limitations acceptées

- Pas de migration DB ajoutée tant qu'aucun test ne prouve un problème de stockage non normalisable à la sortie.
- `/services/jdr/auth/me` n'expose pas de datetime aujourd'hui ; le test associé reste un garde souple pour valider ses datetimes si le payload évolue.

---

## 2026-06-01 — BD-6 : CRUD campagnes et filtre sessions

### Ce qui a été fait

- Ajout du CRUD campagne web : liste, détail, création, modification, suppression prudente des campagnes vides.
- Ajout de `description` sur `jdr_campaigns` et des agrégats front `session_count` / `last_session_at`.
- `POST /services/jdr/sessions` exige désormais un `campaign_id` explicite ; `GET /services/jdr/sessions?campaign_id=...` filtre par campagne après contrôle de membership.
- Les PJ publics restent globaux au MJ pour BD-6, afin de ne pas mélanger CRUD campagne et refonte du modèle de personnages.
- Les nouveaux champs datetime de campagne utilisent le même contrat BD-5 : sortie avec suffixe timezone explicite.

### Ce que j'ai appris

- **Un changement de contrat doit faire rougir les anciens tests** : les tests historiques qui créaient une session sans `campaign_id` ont cassé, ce qui a forcé une migration explicite des scénarios et évité une compatibilité fantôme.
- **Supprimer est une décision métier, pas juste SQL** : refuser la suppression d'une campagne avec sessions protège l'historique et évite un cascade delete dangereux pour un journal de partie.
- **Ne pas étendre le scope PJ trop tôt** : garder les PJ globaux au MJ respecte BD-6 et évite de transformer une feature de navigation campagne en refonte de gestion des personnages.

### Limitations acceptées

- Pas encore de switch de campagne active côté profil : le front peut sélectionner une campagne via les endpoints et passer `campaign_id` sur les sessions.
- Les API keys GM restent supportées comme mode legacy ; le contrôle riche de membership est porté par les sessions web.
- Pas d'ADR ajouté : les décisions appliquées étaient déjà cadrées par la spec BD-6 et ne dépassent pas le plan.

---

## 2026-06-01 — BD-7 : Identity refacto et scoping campagne des PJ

### Ce qui a été fait

- Remplacement du vocabulaire public `profile` par `system_role` sur les comptes web : `admin` pour l'administration globale, `user` pour les comptes standards.
- Séparation explicite entre autorité portail et autorité campagne : `/services/jdr/users` est réservé aux admins, tandis qu'un utilisateur standard authentifié peut créer une campagne et en devient GM.
- Renommage du rôle de membership campagne `player` en `pj` côté web, sans casser le rôle API-key legacy `player` utilisé par les tokens joueur `/me/*`.
- Scoping des PJ par campagne : `campaign_id` obligatoire en sortie et en base, `user_id` optionnel pour lier un PJ à un compte, fallback V1 sur la campagne par défaut du GM web.
- Ajout de la migration Alembic BD-7 et d'un ADR dédié : `docs/adr/0013-identity-refacto-pj-scoping.md`.

### Ce que j'ai appris

- **Un rôle global n'est pas un rôle métier local** : un admin portail ne doit pas être confondu avec un GM de campagne, et un GM de campagne ne doit pas automatiquement administrer les comptes.
- **Le vocabulaire public compte autant que le schéma** : exposer `pj` au front évite de mélanger l'ancien token joueur avec le rôle de membre de campagne.
- **Le scoping doit être serveur-side** : filtrer les PJ seulement côté front serait fragile ; la logique vérifie la membership campagne avant create/list/mapping.

### Limitations acceptées

- Pas de RBAC fin, invitation email, audit log ou transfert de propriété campagne dans BD-7.
- La migration est pensée pour une purge local/staging : les environnements avec des PJ orphelins doivent être nettoyés ou reseed avant upgrade.
- Les API keys GM/player restent un mode legacy supporté ; le contrôle riche de membership web passe par les sessions.

---

## 2026-06-03 — BD-8 : current_job_id et accès audio source

### Ce qui a été fait

- Ajout de `Session.current_job_id` avec migration Alembic et exposition additive dans `SessionOut`.
- L'upload audio crée une projection `jdr_jobs` et pose le job de transcription courant sur la session.
- Le job de transcription conserve maintenant l'audio source après succès ou échec ; seul un DELETE explicite marque l'audio purgé.
- Ajout de `GET /services/jdr/sessions/{session_id}/audio` avec réponse fichier complète, headers player, et support des ranges `206`.
- Refonte de `DELETE /services/jdr/sessions/{session_id}/audio` : reset idempotent vers `created`, suppression des transcriptions/chunks/artifacts dérivés, vidage de `current_job_id`, refus uniquement pendant `transcribing`.

### Ce que j'ai appris

- **Un pointeur de job n'est pas l'historique des jobs** : garder la row `jdr_jobs` tout en vidant `current_job_id` donne au front un signal clair sans effacer l'audit local.
- **La disponibilité audio est une règle métier distincte de la transcription** : réussir une transcription ne signifie plus que le fichier source doit disparaître. Le cycle de vie est maintenant piloté par l'action explicite de remplacement.
- **Les endpoints binaires ont besoin de tests de headers** : pour un player navigateur, `Accept-Ranges`, `Content-Length` et `Content-Range` font partie du contrat observable autant que le corps de réponse.

### Limitations acceptées

- La lecture audio est ouverte aux membres web de la campagne active ; upload et suppression restent des actions GM.
- Pas de signed URL ni stockage objet : fichier local sous `KAEYRIS_DATA_DIR`, cohérent avec le périmètre monolithe local.
---

## 2026-06-03 — BD-10 : Progression réelle des jobs de transcription

### Ce qui a été fait

- `JobOut` expose deux champs best-effort nullables : `phase`
  (`reducing | transcribing | done | failed`) et `progress_percent` (0..100).
- Le worker écrit la progression sur la métadonnée du job RQ via
  `_ProgressReporter` ; `_transcribe_with_optional_chunking` reçoit un
  callback `(chunks_done, chunks_total)` queue-agnostique. `100` n'est émis
  qu'après persistance + transition d'état réussies (chunks plafonnés à 99).
- `GET /services/jdr/jobs/{id}` projette et valide la métadonnée via
  `_project_progress_meta` : valeur absente/expirée/malformée ⇒ `null`,
  jamais de `500`. Un échec émet `phase="failed"` sans percent et préserve
  donc la dernière progression connue.
- Contrat public régénéré dans `docs/context/api/openapi.json` ; doc service
  et mémo enrichis.

### Ce que j'ai appris

- **Une métadonnée transitoire suffit pour un besoin UX best-effort** :
  `job.meta` RQ évite une colonne DB et un write par chunk, tout en gardant
  le polling existant comme seule surface (pas de SSE prématuré — YAGNI).
- **Séparer le dénominateur de la file** : le helper de chunking connaît le
  nombre réel de chunks mais ignore RQ ; un simple callback garde la logique
  testable sans Redis et la spécificité queue au bord du job.
- **L'absence de donnée doit rester un état valide** : valider la métadonnée
  côté route (au lieu de faire confiance) empêche une donnée Redis douteuse
  de transformer un job valide en erreur serveur.

### Limitations acceptées

- Pas de SSE/WebSocket, pas de pub/sub, pas d'historique de progression en base.
- `phase` ne contient pas `queued` (déjà porté par `status`) et ne pilote
  jamais la complétion ; `status` reste la source de vérité du cycle de vie.
- Progression instrumentée sur le seul chemin de transcription ; les autres
  jobs (narrative/elements/povs/summary) renvoient `phase`/`progress_percent`
  à `null`.
