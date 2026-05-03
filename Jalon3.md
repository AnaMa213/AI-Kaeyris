# Jalon 3 — Async processing (walkthrough pédagogique)

> Document explicatif détaillé : étapes, **pourquoi**, alternatives écartées, normes respectées, limitations.
> Public : toi qui apprends. Document à relire dans 6 mois.

---

## Sommaire

1. [Objectif et menaces couvertes](#1-objectif-et-menaces-couvertes)
2. [Étape 0 — ADR 0004 avant le code](#2-étape-0--adr-0004-avant-le-code)
3. [Étape 1 — Dépendances `redis`, `rq`, `fakeredis`](#3-étape-1--dépendances-redis-rq-fakeredis)
4. [Étape 2 — Configuration et client Redis](#4-étape-2--configuration-et-client-redis)
5. [Étape 3 — Rate limiting sliding window](#5-étape-3--rate-limiting-sliding-window)
6. [Étape 4 — Machinerie de jobs](#6-étape-4--machinerie-de-jobs)
7. [Étape 5 — Jobs de démonstration](#7-étape-5--jobs-de-démonstration)
8. [Étape 6 — Compose : 3 services](#8-étape-6--compose--3-services)
9. [Étape 7 — Tests](#9-étape-7--tests)
10. [Normes et bonnes pratiques respectées](#10-normes-et-bonnes-pratiques-respectées)
11. [Choix alternatifs envisagés et écartés](#11-choix-alternatifs-envisagés-et-écartés)
12. [Limitations acceptées](#12-limitations-acceptées)
13. [Ce que ce jalon prépare pour la suite](#13-ce-que-ce-jalon-prépare-pour-la-suite)

---

## 1. Objectif et menaces couvertes

### Selon CLAUDE.md §5

> Jalon 3 : **Redis + RQ, worker, idempotent jobs, retry policy**

### Pourquoi maintenant

À partir du Jalon 4 (DeepInfra) et surtout Jalon 5 (transcription audio JDR), on aura des opérations qui prennent **des minutes**. En synchrone, ça pose 3 problèmes :

1. **Timeout client** : 30-120s max côté HTTP standards.
2. **Saturation serveur** : un worker bloqué sur un appel LLM de 5 min ne sert plus rien d'autre.
3. **Travail perdu en cas de crash** : aucun mécanisme de reprise.

La solution : **enqueue + worker en arrière-plan + statut consultable**.

### Menaces OWASP couvertes

| Menace | Comment on s'en protège |
|---|---|
| **API4:2023 Unrestricted Resource Consumption** | Rate limiting Redis sliding window par API key |
| **API8:2023 Security Misconfiguration** | Redis non exposé sur l'host, save snapshot configuré, image Alpine minimale |

### Hors scope

- ❌ Service métier réel async (Jalon 4-5)
- ❌ Endpoint de statut centralisé (à décider quand on aura un vrai cas)
- ❌ Idempotency-Key header (Jalon 5)
- ❌ rq-scheduler / cron-like
- ❌ rq-dashboard / monitoring (Jalon 6)
- ❌ Plusieurs queues (priority/default)

---

## 2. Étape 0 — ADR 0004 avant le code

### Ce qui a été fait

Rédaction de [`docs/adr/0004-async-jobs-and-rate-limiting.md`](./docs/adr/0004-async-jobs-and-rate-limiting.md). 8 décisions :

1. RQ comme lib (déjà acté CLAUDE.md §3)
2. Périmètre : machinerie + jobs factices, pas de vrai service async
3. TTL : 24h succès / 7j échecs
4. Retry : transient → 3× exponentiel `[10,30,90]`s, permanent → 0
5. Idempotence : discipline du dev de job
6. Endpoint statut : reporté
7. Rate limiting : sliding window Redis maintenant (solde la dette du Jalon 2)
8. Compose : 3 services, 1 image, 1 queue, volume persistant

### Pourquoi avant le code

Même discipline que Jalons 1 et 2. L'ADR force à expliciter ce qu'on **ne fait pas** (alternatives écartées) — c'est le reflet de notre réflexion, pas seulement notre code.

Pendant la rédaction, on a aussi tranché des sous-questions concrètes : "rate limiting maintenant ou plus tard ?" → maintenant, parce que Redis est déjà installé pour les jobs et que la dette du Jalon 2 doit être soldée.

---

## 3. Étape 1 — Dépendances `redis`, `rq`, `fakeredis`

### Ce qui a été fait

Ajouts dans [`pyproject.toml`](./pyproject.toml) :

```toml
dependencies = [
    ...
    "redis>=5.0",
    "rq>=2.0",
]

dev = [
    ...
    "fakeredis",
]
```

Puis `pip install -e ".[dev]"`.

### Pourquoi RQ

CLAUDE.md §3 a tranché. Récapitulatif court : RQ est lisible (~2000 LOC), Redis-only (cohérent avec notre choix de broker), stable, suffisant pour notre échelle. Celery serait surdimensionné.

### Pourquoi `fakeredis` plutôt qu'un Redis réel en tests

`fakeredis` (https://github.com/cunla/fakeredis-py) émule Redis en mémoire avec une API compatible `redis-py`. Avantages :
- 0 setup, démarrage instantané, parfait pour CI
- Pas de port à gérer, pas de cleanup entre tests
- Couvre 95% des commandes Redis utilisées en pratique

Limites : ne reproduit pas certains comportements low-level (cluster, transactions atomiques avec scripts Lua). Pour ces cas, on testera contre un vrai Redis (testcontainers ou Redis dans Compose).

### Alternatives écartées

- **`redis-py-lock` ou autres** : pas besoin de locks distribués à ce stade.
- **Serveur Redis embarqué Python** : projet `redislite` existe mais moins maintenu que `fakeredis`.
- **Pas de tests sur l'algo rate limit** : intolérable, c'est de la sécu.

---

## 4. Étape 2 — Configuration et client Redis

### Configuration ([`app/core/config.py`](./app/core/config.py))

```python
REDIS_URL: str = "redis://localhost:6379/0"
RATE_LIMIT_PER_MINUTE: int = 60
RATE_LIMIT_WINDOW_SECONDS: int = 60
```

3 nouveaux champs, tous configurables via env var (12-Factor §III). Le défaut `localhost:6379` correspond au cas dev hybride (Redis Docker, API venv). En Compose, surchargé par `REDIS_URL=redis://redis:6379/0`.

### Client ([`app/core/redis_client.py`](./app/core/redis_client.py))

```python
@lru_cache(maxsize=1)
def _build_client() -> Redis:
    return Redis.from_url(settings.REDIS_URL, decode_responses=False)


def get_redis() -> Redis:
    return _build_client()
```

### Pourquoi `lru_cache(maxsize=1)`

`Redis.from_url` ouvre une connection TCP. Sans cache, chaque appel à `get_redis()` ouvrirait une nouvelle. `lru_cache(1)` rend `_build_client` idempotent : un seul client partagé pour tout le processus.

C'est l'équivalent du pattern singleton sans la lourdeur. `redis-py` lui-même a un connection pool intégré → un seul "client" exploite plusieurs connexions au besoin.

### Pourquoi `decode_responses=False`

RQ pickle les arguments de jobs en bytes. Si `decode_responses=True`, `redis-py` essaie de décoder ces bytes en str avant de les rendre, ce qui casse RQ. Avec `False`, on récupère les bytes bruts.

C'est un détail subtil qu'on ne voit que quand RQ casse silencieusement. Documentation `redis-py` : https://redis.io/docs/clients/python/

### Pourquoi `get_redis` séparé en plus du `_build_client`

Pour permettre l'override en tests via `app.dependency_overrides[get_redis] = lambda: fake_redis`. Comme pour `get_registered_keys` au Jalon 2, le pattern est canonique FastAPI.

---

## 5. Étape 3 — Rate limiting sliding window

### Ce qui a été fait

[`app/core/rate_limit.py`](./app/core/rate_limit.py) implémente :

- `_check_and_record(redis, bucket, *, limit, window_seconds) -> (allowed, retry_after)`
- `enforce_rate_limit(auth, redis_client) -> AuthenticatedKey` — la dépendance FastAPI
- `RateLimitedError(AppError)` — exception 429 avec `Retry-After`

### Algorithme sliding window expliqué

À chaque requête, on fait 4 opérations Redis :

1. **`ZREMRANGEBYSCORE key 0 (now - window)`** — supprime les requêtes plus vieilles que la fenêtre
2. **`ZCARD key`** — compte les requêtes restantes (= dans la fenêtre courante)
3. Si count >= limite → 429
4. Sinon : **`ZADD key now random_id`** + **`EXPIRE key window_seconds`**

Le `random_id` (`secrets.token_hex(8)`) est essentiel : `ZADD` avec un membre déjà existant agirait comme un update du score (= déplace l'entrée dans le temps). Avec un membre toujours unique, chaque requête est compté une fois et une seule.

### Pourquoi sliding window vs fixed window

Comparaison concrète :

**Fixed window** (compteur par minute) :
- 11h59:59 — client envoie 60 req → compteur de 11h = 60
- 12h00:00 — nouvelle minute, compteur de 12h reset à 0 → client peut envoyer 60 req
- **Résultat : 120 req en 2 secondes** alors que la limite "60/min" semble interdire ça

**Sliding window** (sorted set des N dernières secondes) :
- 11h59:59 — 60 req en moins de 60s → bucket plein
- 12h00:00 — la fenêtre [12h-60s, 12h] inclut encore les 60 req de 11h59:59 → 429

Sliding window est plus précis pour ~5 lignes de plus.

### Pourquoi le bucket = nom de la clé API et pas l'IP

3 raisons :

1. **Identité authentifiée** vs identité réseau : on rate-limite **qui** envoie, pas **d'où** ça vient.
2. **NAT et proxies** : plusieurs clients légitimes peuvent partager une IP. Limiter par IP les pénalise tous.
3. **Spoofing facile** : `X-Forwarded-For` est un header arbitraire ; sans config reverse-proxy soignée, n'importe qui peut le forger.

Conséquence : on ne rate-limite **pas** les requêtes anonymes. Les routes publiques (`/health`, `/docs`) ne sont pas rate-limitées non plus, mais elles sont peu coûteuses.

### Pourquoi la dépendance dépend de `require_api_key`

```python
def enforce_rate_limit(
    auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    redis_client: ...,
) -> AuthenticatedKey:
```

L'auth tourne en premier. Si une requête arrive sans token valide → 401 immédiat, sans toucher Redis. Avantages :
- Un attaquant anonyme ne consomme pas de ressources Redis
- Un attaquant anonyme ne pollue pas le bucket d'une vraie clé
- Les logs de 401 et 429 restent distincts (cas d'usage différent en debug)

### Le détail subtil : `member = f"{now}:{token_hex(8)}"`

```python
member = f"{now}:{secrets.token_hex(8)}"
pipe.zadd(key, {member: now})
```

**Pourquoi `now` dans le membre ET dans le score** : si on n'avait que le score, plusieurs requêtes simultanées (même `time.time()` au µs près) auraient le même membre → `ZADD` agirait comme un update au lieu d'ajouter une entrée.

`token_hex(8)` ajoute 64 bits d'entropie → collisions impossibles en pratique.

### Race condition acceptée

La séquence "ZREMRANGEBYSCORE → ZCARD → check → ZADD" n'est **pas atomique**. Race possible :
- T0 : 100 requêtes arrivent en même temps
- T0+ε : toutes les 100 voient `ZCARD = 0` (sous la limite)
- T0+2ε : toutes les 100 font `ZADD` → bucket = 100 alors que la limite est 60

À notre échelle (mono-utilisateur), cette race est inobservable. Si elle devenait un problème, on passerait à un script Lua atomique (Redis exécute les scripts Lua en single-threaded).

### Réponse 429 conforme

```python
raise RateLimitedError(
    detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
    headers={"Retry-After": str(retry_after)},
)
```

Le header `Retry-After` est défini par **RFC 9110 §10.2.3** — https://www.rfc-editor.org/rfc/rfc9110#name-retry-after. Les clients HTTP standards le respectent (notamment les libs avec retry exponentiel, comme `httpx`).

`retry_after` est calculé comme "la plus vieille entrée du bucket sortira de la fenêtre dans X secondes" — précis, pas un défaut arbitraire.

### Alternatives écartées

- **Lib `slowapi`** : 30 lignes économisées contre une dep ; pas de gain pédagogique.
- **Fixed window** : moins précis (cf. plus haut).
- **Token bucket** : valide aussi mais moins lisible que sliding window pour ce cas. Plus utile quand on autorise des bursts contrôlés.
- **Rate limit dans le middleware** au lieu de la dépendance : un middleware ne connaît pas `auth.name` (qui résulte d'une dépendance). Devrait re-faire le travail d'auth → mauvaise séparation.

---

## 6. Étape 4 — Machinerie de jobs

### Ce qui a été fait

[`app/jobs/__init__.py`](./app/jobs/__init__.py) expose :

- `TransientJobError` / `PermanentJobError` (documentaires)
- `get_default_queue(redis_client) -> Queue`
- `enqueue_job(queue, func, *args, transient_errors=True, **kwargs) -> Job`

### Pourquoi un wrapper `enqueue_job`

Sans wrapper, chaque appelant écrirait :

```python
queue.enqueue(
    my_func, *args,
    result_ttl=86400,
    failure_ttl=604800,
    retry=Retry(max=3, interval=[10, 30, 90]),
)
```

Avec wrapper :

```python
enqueue_job(queue, my_func, *args)
```

Centralise la policy. Si demain on change le retry de `[10,30,90]` à `[5,15,45]`, on touche un seul fichier. C'est une application directe du **DRY** sur la config opérationnelle.

### Pourquoi `transient_errors=True` par défaut

Statistiquement, la plupart des erreurs de jobs au monde réel sont transientes (réseau, 5xx upstream, timeout). Faire des erreurs permanentes le défaut serait erreur sur la prudence : on aurait moins de retries → plus de jobs perdus.

Mais pour un job dont les erreurs sont strictement programmation (validation interne, logique business), passer `transient_errors=False` économise 3× le temps d'exécution avant le fail final.

### Pourquoi des exceptions distinctes plutôt qu'une option `retry: bool` à raise

L'option `transient_errors` au moment du **enqueue** détermine si RQ va tenter de retry. Mais à l'intérieur du job, le code a parfois besoin de signaler "non, là c'est définitif, ne retentez pas même si vous étiez configuré pour".

Avec les exceptions, le code peut faire :

```python
def my_job():
    try:
        do_thing()
    except RequestException as e:
        raise TransientJobError(...)  # retry possible
    except ValidationError as e:
        raise PermanentJobError(...)  # ne pas retry
```

À ce stade, RQ ne lit pas le type d'exception pour décider de retry — c'est de la documentation/discipline. Au Jalon suivant, on pourra ajouter un worker hook qui inspecte le type pour zéro-out les retries restants. YAGNI pour aujourd'hui.

### Alternatives écartées

- **Décorateur `@kaeyris_job`** sur la fonction : couple la fonction au decorator de RQ, plus rigide.
- **Pas de wrapper, inline `queue.enqueue`** : duplication de la policy partout.
- **Retry par exception type côté worker** maintenant : nécessite custom worker class, complexité. À introduire si besoin réel.

---

## 7. Étape 5 — Jobs de démonstration

[`app/jobs/demo.py`](./app/jobs/demo.py) contient :

```python
def add(a: int, b: int) -> int:
    return a + b

def simulate_long_task(seconds: float) -> str:
    time.sleep(seconds)
    return f"slept {seconds}s"
```

### Pourquoi ces jobs en particulier

- `add` : trivial, **directement testable** sans Redis. Sert à vérifier le mécanisme d'enqueue (sérialisation des args, TTLs).
- `simulate_long_task` : vérifie qu'un worker consomme **réellement** des jobs en arrière-plan, qu'il sleep et libère, qu'il rend un résultat.

### Pourquoi pas de validation type stricte

```python
def add(a: int, b: int) -> int:
    return a + b
```

Pas de `if not isinstance(a, int): raise PermanentJobError`. Pourquoi ? Parce que :

1. Les annotations types sont indicatives — Python ne valide pas à l'exécution
2. Un appelant qui passe `add("a", 1)` aura naturellement un `TypeError` Python — c'est explicite
3. Ajouter une validation = boilerplate, et au-delà, c'est le rôle de l'appelant (le service métier) de valider AVANT d'enqueuer

Le test `test_add_propagates_type_error` documente explicitement ce contrat.

---

## 8. Étape 6 — Compose : 3 services

### Avant (Jalon 0-2)

Un seul service `api`.

### Après ([docker-compose.yml](./docker-compose.yml))

```yaml
services:
  redis:    # broker
  api:      # FastAPI
  worker:   # consomme la queue
```

### Pourquoi pas de `ports:` sur `redis`

Sécurité Redis : un Redis sur le LAN sans auth est une cible classique. Sans `ports:`, Redis n'est joignable que depuis les autres services Compose (réseau interne). Si tu as besoin d'un `redis-cli` :

```powershell
docker compose exec redis redis-cli
```

### Pourquoi `command: redis-server --save 60 1`

`save 60 1` = snapshot toutes les 60s s'il y a au moins 1 modification. Évite la perte totale en cas de redémarrage du conteneur. Pas l'AOF (append-only file) qui serait surdimensionné pour notre usage et plus lent.

### Pourquoi `volumes: redis-data:/data`

Le snapshot Redis est écrit dans `/data`. Sans volume nommé, il serait perdu au `docker compose down`. Avec, il survit aux redémarrages (mais pas à `docker compose down -v` qui supprime aussi les volumes).

### Pourquoi `worker` partage l'image de `api`

Le worker exécute le code de `app/jobs/`. Il a besoin du même Python, des mêmes deps, du même code. Une seule image = DRY parfait.

Différence : `command:` change. `api` lance `uvicorn`, `worker` lance `rq worker default --url ...`.

### Pourquoi `--url` explicite sur `rq worker`

RQ ne lit pas `REDIS_URL` automatiquement de l'environnement. On peut soit :
- Passer `--url redis://...` explicitement (notre choix, lisible)
- Setup `RQ_REDIS_URL` env var (RQ supporte ça aussi, peu documenté)
- Faire un script Python qui crée la connexion et lance le worker

Le `--url` explicite est le plus lisible.

### Pourquoi `depends_on` sans healthcheck

`depends_on:` garantit l'ordre de démarrage mais pas la **disponibilité** : Redis peut être en train de démarrer quand l'API tente de s'y connecter. Deux options :
- Ajouter un `healthcheck:` sur Redis et `condition: service_healthy` sur l'API
- Laisser les clients (`redis-py`, `rq`) gérer leurs propres retry de connexion

On part sur la **deuxième** option : ces libs retentent automatiquement, c'est plus simple. En pratique, le démarrage prend < 1 seconde, on n'observe jamais l'erreur.

### Alternatives écartées

- **Healthcheck Redis avec `condition: service_healthy`** : plus rigoureux mais ajoute de la complexité Compose, peu de gain.
- **Image `redis:7`** (non-alpine) : 100 Mo en plus pour rien.
- **Plusieurs queues nommées** : sur-ingénierie.
- **`depends_on:` avec `restart: unless-stopped`** : utile en prod, pas en dev.
- **Redis en hors-Docker** : oblige une install OS, brise la promesse "git clone + docker compose up".

---

## 9. Étape 7 — Tests

### Vue d'ensemble

12 nouveaux tests (29 au total) :

```
tests/core/test_rate_limit.py     6 tests
tests/jobs/test_demo.py           2 tests
tests/jobs/test_enqueue.py        4 tests
```

### `test_rate_limit.py` — décomposition

**3 tests sur l'algorithme** (`_check_and_record` direct, sans FastAPI) :
- allows under limit
- blocks at limit
- isolates buckets (un user hitting cap n'affecte pas un autre)

**3 tests sur la dépendance** (mini-app FastAPI + fakeredis) :
- under threshold → 200
- above threshold → 429 + `Retry-After` + Problem Details
- non authentifié → 401, **pas** 429 (ordre des dépendances correct)

### `test_enqueue.py` — décomposition

**4 tests** vérifient la policy par défaut sur les TTLs et le retry :
- Queue name correct
- TTLs corrects
- `transient_errors=True` → `retries_left=3`, `retry_intervals=[10,30,90]`
- `transient_errors=False` → `retries_left=None`

### Pourquoi `monkeypatch.setattr(...)` pour les paramètres rate limit

```python
monkeypatch.setattr(
    "app.core.rate_limit.settings.RATE_LIMIT_PER_MINUTE", 3, raising=True
)
```

Plutôt que d'attendre 60 requêtes pour atteindre la limite, on baisse la limite à 3 dans le test. `monkeypatch` est le mécanisme pytest standard pour patcher des attributs : automatiquement nettoyé en fin de test.

### Pourquoi un fixture `known_key`

Argon2 hash est lent (~10ms). Si on hashait dans chaque test, ça ralentirait. Le fixture le fait une fois par test qui le demande, et c'est mémorisé pendant la durée du fixture.

### Test ordre auth → rate limit

```python
async def test_rate_limit_blocks_unauthenticated_with_401_not_429(...):
    response = await client.get("/protected")  # pas de header
    assert response.status_code == 401   # PAS 429
```

Vérifie explicitement que **l'auth tourne en premier**. Si on changeait l'ordre des dépendances, ce test casserait. C'est un test de protection contre la régression.

### Alternatives écartées

- **Test avec un vrai Redis** : nécessiterait un service à démarrer, pollution de l'environnement, dépend de l'OS. fakeredis suffit pour 95%.
- **Mock Redis avec `unittest.mock`** : l'algo sliding window utilise plusieurs commandes (`ZREMRANGEBYSCORE`, `ZCARD`, `ZADD`, `EXPIRE`) ; mocker tout ça est plus lourd qu'utiliser fakeredis.
- **Test de la séquentialité du retry** : compliqué à tester sans worker réel. La config est testée (`retries_left=3`, `retry_intervals=[10,30,90]`), l'exécution est validée par RQ lui-même (couvert par leur suite de tests).

---

## 10. Normes et bonnes pratiques respectées

| Norme | Application |
|---|---|
| **OWASP API4:2023** Unrestricted Resource Consumption | Rate limiting authentifié |
| **OWASP API8:2023** Security Misconfiguration | Redis non exposé, save snapshot configuré |
| **RFC 9110 §10.2.3** | `Retry-After` header sur 429 |
| **RFC 9457** | Body Problem Details sur 429 (hérité Jalon 1) |
| **12-Factor §III** Config | `REDIS_URL`, `RATE_LIMIT_*` via env var |
| **12-Factor §IV** Backing services | Redis attaché via URL, swappable |
| **12-Factor §VIII** Concurrency | API et workers comme processus distincts |
| **12-Factor §IX** Disposability | Workers RQ peuvent être tués/redémarrés sans perte de jobs |
| **DRY** | `enqueue_job` centralise la policy |
| **Secure by default** | Redis sans port, rate limit après auth, retries safe par défaut |
| **YAGNI** | Pas d'endpoint statut, pas de scheduler, pas de monitoring |

---

## 11. Choix alternatifs envisagés et écartés

### Lib de queue

| Alternative | Pourquoi écartée |
|---|---|
| Celery | Surdimensionné, complexe, pas justifié à notre échelle |
| Dramatiq | Valable mais communauté plus petite que RQ |
| arq | Async natif sympa mais oblige tous les jobs à être `async def` |
| Huey | Plus minimaliste mais moins de docs |

### Pattern de jobs

| Alternative | Pourquoi écartée |
|---|---|
| Décorateur `@kaeyris_job(...)` sur la fonction | Couple à une queue, plus rigide |
| Class `JobDefinition` avec validate/run | Sur-ingénierie pour des fonctions |
| RQ direct sans wrapper | Duplication de la policy à chaque appel |

### Rate limiting

| Alternative | Pourquoi écartée |
|---|---|
| Lib `slowapi` | 30 lignes économisées contre une dep externe |
| Fixed window (compteur) | Permet des bursts à la frontière de la fenêtre |
| Token bucket | Valable mais moins lisible pour ce besoin |
| Rate limit par IP | NAT, X-Forwarded-For, complexité |
| Rate limit en mémoire process | Casse en multi-instance |

### Compose

| Alternative | Pourquoi écartée |
|---|---|
| Images séparées api / worker | DRY brisé, deux Dockerfiles à maintenir |
| Plusieurs queues | YAGNI |
| Healthcheck Redis | Complexité Compose, pas de gain réel |
| Redis exposé sur l'host | Risque sécu |

---

## 12. Limitations acceptées

| Limitation | Pourquoi acceptée | À reprendre quand |
|---|---|---|
| Pas de retry jitter | Risque théorique avec 1 seul worker | Multi-worker (Jalon 8 ?) |
| Pas de scheduling cron | Pas de besoin actuel | Jalon 5+ si besoin |
| Pas d'endpoint statut central | Spéculatif sans vrai service | Jalon 4-5 selon usage |
| Pas d'idempotency-key API | Pas de side effects externes encore | Jalon 5 |
| Race condition µs sur sliding window | Acceptable à notre échelle | Migration script Lua si besoin |
| Worker pas de hot-reload | Limitation RQ ; workaround manuel | Si rythme dev devient un frein |
| Pas de monitoring jobs (rq-dashboard, Prometheus) | Reporté Jalon 6 (observabilité) | Jalon 6 |
| Pas d'auth Redis (`requirepass`) | Réseau Compose interne, pas exposé | Jalon 8 (déploiement Pi) |

---

## 13. Ce que ce jalon prépare pour la suite

### Jalon 4 — Adapters + DeepInfra

- Premier vrai job : `app/jobs/llm.py::summarize(text, model)` qui appellera `app.adapters.llm.LLMAdapter`
- Distinction `TransientJobError` (5xx DeepInfra) vs `PermanentJobError` (validation prompt)
- Endpoint `/services/<service>/summarize` : enqueue + retourne 202 + job_id

### Jalon 5 — Service JDR

- Job `transcribe(audio_path)` (long, plusieurs minutes)
- Probablement introduction d'un endpoint statut (Q6 reportée)
- Probablement introduction de `Idempotency-Key` (Q5 reportée)
- Migration vers DB pour le store des clés API (résout aussi rotation et scaling) → pourrait inclure une table `jobs` pour audit

### Jalon 6 — Observabilité

- Métriques Prometheus : `rq_jobs_pending{queue}`, `rq_jobs_failed_total`, `rate_limit_blocked_total`
- Logs structurés sur enqueue/start/success/failure
- Tracing : un correlation ID propagé du request HTTP au job worker

### Jalon 7 — CI/CD

- Tests jobs/rate limit tournent automatiquement
- Scan deps : `redis`, `rq`, `fakeredis` audités
- Scan secrets : aucune chaîne Redis URL avec password en dur

### Jalon 8 — Pi 5 deployment

- Redis durci : `requirepass`, peut-être TLS si exposé hors LAN
- Multi-worker possible (`replicas: 2` sur le service `worker`)
- Caddy reverse-proxy peut faire un rate-limit complémentaire au niveau réseau

---

## Référence rapide — checklist DoD du Jalon 3

| Critère CLAUDE.md §7 | État |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `pytest` | ✅ 29 passed |
| `docker compose up --build` | 🟡 à tester (3 services + rebuild) |
| `curl /health` + worker démarre | 🟡 à tester |
| README à jour | ✅ section async + rate limit ajoutée |
| Entrée journal | ✅ |
| ADR | ✅ ADR 0004 |
| Commit pushed | 🟡 reste à faire |
