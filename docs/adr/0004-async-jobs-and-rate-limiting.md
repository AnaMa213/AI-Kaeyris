# ADR 0004 — Traitement asynchrone (Redis + RQ) et rate limiting

- **Statut** : accepté
- **Date** : 2026-05-02
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (architecture), ADR 0003 (auth), CLAUDE.md §3 (stack verrouillée : Redis + RQ)

## Contexte

À partir du Jalon 4 (DeepInfra) et surtout Jalon 5 (transcription audio JDR), l'API devra exécuter des opérations longues (plusieurs minutes). Un traitement synchrone est exclu :

- Les clients HTTP timeout entre 30 et 120 secondes
- Un worker uvicorn bloqué sur un appel LLM ne sert plus aucune autre requête
- Un crash en cours d'exécution = travail perdu, pas de reprise

Ce jalon pose la **machinerie de jobs asynchrones** sur laquelle les services métier suivants vont s'appuyer. Il solde aussi la dette du Jalon 2 sur le rate limiting (qui attendait Redis pour être implémentable proprement).

Huit questions structurantes :

1. Quelle lib de queue ?
2. Implémenter de vrais jobs ou seulement la machinerie ?
3. TTL des résultats de jobs ?
4. Politique de retry ?
5. Garanties d'idempotence ?
6. Endpoint de statut des jobs ?
7. Rate limiting maintenant ou plus tard ?
8. Architecture Docker Compose ?

## Décision

### 1. Lib de queue : RQ (Redis Queue)

CLAUDE.md §3 verrouille déjà ce choix. RQ — https://python-rq.org — est :

- Adapté à notre échelle (Pi 5, mono-utilisateur, quelques dizaines de jobs/jour)
- Lisible (~2000 lignes de code source)
- Stable et largement documenté
- Cohérent avec la philosophie "Boring Technology" (https://boringtechnology.club)

Versions cibles : `rq>=2.0` et `redis>=5.0` (clients Python).

### 2. Périmètre du Jalon : machinerie + job factice, **pas de vrai service async**

On ne livre **pas** d'endpoint async dans ce jalon (YAGNI). On livre :

- Le module `app/jobs/` qui définit le pattern d'écriture d'un job
- Une fonction-job de démonstration (`add(a, b)`, `simulate_long_task(seconds)`) testable
- L'infra Redis + worker dans Compose
- La doc et les tests qui montrent comment écrire/tester un nouveau job

Le premier vrai job arrivera au Jalon 4 (résumé via DeepInfra) ou Jalon 5 (transcription Whisper).

### 3. TTL des résultats : 24h pour les succès, 7 jours pour les échecs

```python
@job("default", result_ttl=86400, failure_ttl=604800)
def my_job(...):
    ...
```

- **24h sur les succès** : permet à un client de poll après une nuit
- **7 jours sur les échecs** : laisse le temps de débugger
- À reconsidérer en Jalon 5 si on a des contraintes de rétention (ex : données JDR sensibles)

### 4. Politique de retry : distinction transient vs permanent

Deux exceptions canoniques :

```python
class TransientJobError(Exception):
    """Erreur ré-essayable (réseau, timeout, 5xx upstream)."""

class PermanentJobError(Exception):
    """Erreur définitive (validation invalide, 4xx client)."""
```

Configuration RQ :

- **TransientJobError** → 3 retries, intervalles **exponentiels** `[10s, 30s, 90s]`
- **PermanentJobError** → 0 retry, échec immédiat
- **Toute autre exception** non typée → 0 retry par défaut (échec franc, à investiguer manuellement)

Le wrapper de job dans `app/jobs/__init__.py` exposera un décorateur `@kaeyris_job(...)` qui applique cette policy.

Pas de **jitter** (aléa anti-troupeau) à ce stade. Acceptable car on n'a qu'un worker. À ajouter quand on aura plusieurs workers (Jalon 8 sur Pi possiblement).

### 5. Idempotence : discipline du développeur de job

RQ ne fournit pas de mécanisme central. Chaque job est responsable d'être idempotent :

- **Lecture seule** : naturellement idempotent
- **Écriture avec ID stable** : `INSERT ... ON CONFLICT DO NOTHING` (à l'arrivée de SQL au Jalon 5)
- **Side effects externes** (email, paiement) : marquage "déjà fait" dans Redis avant l'action

Le mécanisme d'**idempotency key** côté API (header `Idempotency-Key`, RFC en cours https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/) est reporté au Jalon 5 quand on aura un vrai cas. À ce stade, c'est documenté dans `Jalon3.md` et `memo.md`.

### 6. Endpoint de statut des jobs : reporté au premier vrai service

Pas de `GET /services/jobs/<id>` central dans ce jalon. La forme exacte (centralisée vs par service, format de réponse) sera décidée au Jalon 4-5 en fonction du cas d'usage réel.

Décider aujourd'hui serait spéculatif. RQ expose tout ce qu'il faut via `Job.fetch(job_id, connection=redis)`.

### 7. Rate limiting : sliding window par API key, dans Redis, **maintenant**

On solde la dette du Jalon 2.

**Algorithme** : sliding window via sorted set Redis. Pour chaque requête authentifiée :

1. Supprimer les entrées plus vieilles que `now - window_seconds` (`ZREMRANGEBYSCORE`)
2. Compter les entrées restantes (`ZCARD`)
3. Si `count >= limit` → 429 Too Many Requests
4. Sinon, ajouter l'entrée courante (`ZADD`) et étendre le TTL (`EXPIRE`)

**Paramètres par défaut** :

- Limite : `60 requêtes/minute` par API key
- Configurable via env vars : `RATE_LIMIT_PER_MINUTE=60`, `RATE_LIMIT_WINDOW_SECONDS=60`

**Périmètre** :

- Appliqué **après** l'auth, sur les routes protégées uniquement
- Implémenté comme une dépendance FastAPI : `enforce_rate_limit` qui dépend de `require_api_key`
- Routes publiques (`/health`, `/docs`, `/redoc`, `/openapi.json`) **non rate-limitées** — elles sont peu coûteuses, et limiter par IP introduirait de la complexité (X-Forwarded-For, NAT, etc.)

**Réponse en cas de dépassement** :

- HTTP 429 Too Many Requests
- Body Problem Details (`type=.../rate-limited`)
- Header `Retry-After: <seconds>` (RFC 9110 §10.2.3)

**Granularité de course** : la séquence ZREMRANGEBYSCORE → ZCARD → ZADD a une race window microscopique. À notre échelle (mono-utilisateur, ≤60 req/min), c'est acceptable. Si on industrialise, on passera à un script Lua pour l'atomicité totale.

### 8. Architecture Docker Compose : 3 services, 1 image, 1 queue

```yaml
services:
  redis:
    image: redis:7-alpine
    volumes: [redis-data:/data]
    # pas de `ports:` exposés vers l'host par défaut — accessible uniquement
    # depuis les autres services Compose (sécurité Redis).
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]

  api:
    build: ...
    environment:
      REDIS_URL: redis://redis:6379/0
    depends_on: [redis]
    ...

  worker:
    build: ...   # même image que `api`
    command: ["rq", "worker", "default", "--url", "redis://redis:6379/0"]
    environment:
      REDIS_URL: redis://redis:6379/0
    depends_on: [redis]

volumes:
  redis-data:
```

**Choix** :

- **Une seule image** pour `api` et `worker` (DRY — le worker exécute du code de `app/`)
- **Une seule queue `default`** (pas de prioritisation prématurée)
- **Volume persistant `redis-data`** pour ne pas perdre les jobs en cours au redémarrage
- **Redis non exposé** sur l'host (pas de `ports:` 6379) — accessible uniquement via le réseau interne Compose
- **`depends_on`** assure que `redis` démarre avant `api`/`worker`, mais ne garantit pas qu'il soit prêt — les clients (lib `redis-py` côté `api` et `rq worker` côté worker) gèrent les retry de connexion eux-mêmes

**Workflow dev local** : Redis dans Compose (ou via `docker run -p 6379:6379 redis:7-alpine`), API en venv via `uvicorn --reload` pointant sur `REDIS_URL=redis://localhost:6379/0`. C'est l'option **hybride** qui préserve la rapidité d'itération du venv tout en utilisant un vrai Redis. Documentée dans memo.md.

## Alternatives écartées

| Alternative | Raison du rejet |
|---|---|
| **Celery** | Surdimensionné, complexe, pour des projets industriels. Toutes les fonctionnalités qu'il offre nous sont inutiles (multi-broker, workflows chord/group, scheduling beat). |
| **Dramatiq, arq, Huey, Taskiq** | Alternatives modernes valables, mais RQ a la communauté la plus mature et la doc la plus complète. Pas de raison de prendre un risque sur la longévité. |
| **Postgres-as-queue** (lib `pgmq`) | Pas encore de Postgres jusqu'au Jalon 5. Et performances inférieures à Redis pour ce cas d'usage. |
| **AWS SQS / Google Pub/Sub** | Vendor lock-in, latence réseau, coût. Inapproprié pour un Pi. |
| **Lib de rate limit `slowapi`** | Marche mais ajoute une dépendance externe alors que ~30 lignes d'algorithme sliding window suffisent. Cohérent avec les choix Jalon 1 (RFC 9457 fait main) et Jalon 2 (security headers fait main). |
| **Rate limit par fixed window** (compteur + EXPIRE) | Plus simple (~10 lignes) mais imprécis : un client peut envoyer 60 req à la fin d'une minute + 60 au début de la suivante = 120 req en quelques secondes. Sliding window évite ce burst. |
| **Rate limit par IP** | NAT, proxies, X-Forwarded-For à valider. Compliqué pour peu de bénéfice à notre échelle. Préférable de limiter par identité authentifiée. |
| **Rate limit en mémoire (in-process)** | Casse en multi-instance. Acceptable seulement en mono-instance. Si on a Redis, autant l'utiliser. |
| **Workers et API dans des images séparées** | Casse la règle DRY. Une seule image, deux processus, code partagé. |
| **Plusieurs queues dès le départ** (priority/default) | Sur-ingénierie. À introduire quand un job aura un SLA différent (ex : email transactionnel vs batch nocturne). |
| **Endpoint de statut central `GET /jobs/<id>`** | Décision spéculative tant qu'on n'a pas de vrai service async. Repoussé au Jalon 4-5. |
| **Idempotency key généralisée** | Mécanisme complexe (header dédié, store de cache, fenêtre temporelle) pour un bénéfice nul tant qu'on n'a pas de side effects externes. À introduire au Jalon 5 si nécessaire. |
| **Pas de DLQ** | Les jobs échoués atterrissent dans la `FailedJobRegistry` de RQ — équivalent fonctionnel d'une DLQ, accessible via `rq info` ou via l'API Python. C'est suffisant. |
| **Exposer Redis sur l'host** (`ports: 6379:6379`) | Risque sécu (Redis a régulièrement des CVE de configurations par défaut). Inutile pour nos besoins. Le client `redis-cli` peut être lancé via `docker compose exec redis redis-cli` si besoin de debug. |

## Conséquences

**Positives**

- Les futurs services métier (Jalon 4+) peuvent déclencher des opérations longues sans bloquer l'API
- Le rate limiting protège enfin contre l'abus de ressources même si une clé fuit
- Architecture multi-processus prête pour le scaling (plusieurs workers possibles en changeant 2 lignes de Compose)
- Pattern de jobs documenté et testé : un nouveau job se code en ~30 lignes
- Pas de dépendance lib externe pour le rate limiting (cohérence Jalon 1/2)

**Négatives / acceptées**

- L'environnement de dev devient plus lourd : Redis tournant en permanence (en local ou en Compose)
- Le worker doit être redémarré quand le code change (pas de `--reload` équivalent côté RQ — workaround dev : `rq worker --burst` qui sort après avoir vidé la queue, et redémarrage manuel)
- Surface d'attaque en plus : Redis est un service réseau, à durcir au déploiement (Jalon 8)
- Race condition microscopique sur le sliding window — acceptable à notre échelle
- Pas de retry jitter — risque de "thundering herd" théorique, pratique nul avec un seul worker
- Pas de scheduling cron-like (`rq-scheduler` non installé) — à introduire si besoin futur

**Conditions de re-évaluation** (cet ADR sera "superseded" si)

- On a besoin de workflows multi-étapes (chains, groups) → migrer vers Celery
- On dépasse plusieurs centaines de jobs/jour avec contraintes de SLA → ajouter des queues séparées avec prioritisation
- On a besoin de scheduling périodique → ajouter `rq-scheduler` ou équivalent
- Le rate limiting devient un vrai goulot ou expose des bypass → migrer vers une lib mature (`slowapi`, `redis-py-rl`) ou un reverse-proxy
- Production multi-instance → durcir Redis (auth, TLS), migrer rate limit vers script Lua atomique

## Références

- RQ documentation — https://python-rq.org
- redis-py — https://redis.io/docs/clients/python/
- Vincent Driessen, *RQ: Simple Job Queues for Python* (2012) — auteur de RQ et de git-flow
- Sliding window rate limiter pattern — https://blog.cloudflare.com/counting-things-a-lot-of-different-things/
- RFC 9110 §10.2.3 — *Retry-After* header — https://www.rfc-editor.org/rfc/rfc9110#name-retry-after
- IETF draft — *Idempotency-Key Header* — https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/
- Boring Technology Club — https://boringtechnology.club
- Twelve-Factor App §VIII (Concurrency) — https://12factor.net/concurrency
- ADR 0003 (le rate limiting était une dette ouverte de cet ADR)
