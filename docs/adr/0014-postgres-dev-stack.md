# ADR 0014 - PostgreSQL dans le stack de développement

## Statut

Accepté - 2026-06-11

## Contexte

Le worker RQ échouait par intermittence sur `sqlite3.OperationalError: database
is locked` lors d'une transcription, pendant que le front interrogeait
`GET /services/jdr/jobs/{id}`. Cause racine : le stack dev faisait tourner l'API
et le worker comme **deux processus distincts** partageant le **même fichier
SQLite** (`./data/kaeyris.db`, bind-mount). SQLite n'autorise qu'un seul écrivain
et sérialise lecteurs/écrivain par verrou fichier ; l'engine n'avait ni
`busy_timeout` ni mode WAL, donc l'écriture du worker échouait immédiatement dès
que l'API tenait un verrou de lecture. La barre de progression BD-10 a augmenté
la fréquence de polling, rendant le bug fréquent.

La constitution (CLAUDE.md §3) désigne déjà PostgreSQL comme base cible et SQLite
comme « dev allowed », et impose la parité dev/prod (12-Factor §X, §2.7).
`asyncpg` était déjà une dépendance et `docker-compose.prod.yml` contenait déjà
des services `postgres` + `migrations`.

## Decision

Le stack de développement (`docker-compose.yml`) tourne désormais sur PostgreSQL,
en mirrorant le pattern prod :

- Service `postgres` (`postgres:16-alpine`, volume nommé `postgres-data`,
  healthcheck `pg_isready`). Identifiants dev par défaut `kaeyris/kaeyris/kaeyris`,
  surchargés par `POSTGRES_*` dans `.env`. Le compose **prod** garde, lui, zéro
  défaut (12-Factor : échouer bruyamment).
- Service one-shot `migrations` (`alembic upgrade head`) qui s'exécute après que
  Postgres soit `healthy` ; `api` et `worker` attendent sa complétion
  (`service_completed_successfully`).
- `api`/`worker`/`migrations` partagent une même image `ai-kaeyris:dev` et
  pointent `DATABASE_URL` vers `postgresql+asyncpg://...@postgres:5432/...` via
  `environment:` (qui surcharge le défaut SQLite du `.env`).

Aucun code applicatif n'a changé : `app/core/config.py` lit déjà `DATABASE_URL`
depuis l'environnement et `migrations/env.py` le câble déjà. SQLite reste le
défaut hors-Docker (tests in-memory, `uvicorn` sur l'hôte).

## Consequences

Le `database is locked` disparaît : PostgreSQL gère nativement plusieurs
connexions concurrentes (lecteurs API + écrivain worker). Le dev gagne la parité
avec la prod (même moteur, mêmes migrations exécutées), ce qui réduit les
divergences de comportement SQL. Coût : un conteneur de plus en local et une
base qui démarre vide à la première bascule — les données du fichier SQLite ne
sont pas reprises, il faut recréer le premier GM via `/auth/setup` (procédure
reseed déjà documentée). Cela anticipe en dev une partie du Jalon 8 (Postgres),
déviation volontaire de l'ordre de la roadmap, justifiée par la correction du bug
et la parité dev/prod.

## Alternatives rejetees

- **Durcir SQLite (WAL + `busy_timeout`)** : plus léger, mais WAL est fragile sur
  un bind-mount Docker Desktop Windows (mémoire partagée `-shm`), ne donne pas la
  parité prod, et laisse SQLite comme moteur dev divergent. Retenu seulement comme
  filet de sécurité optionnel pour le run local hors Docker.
- **Migrer aussi les tests vers Postgres** : inutile et coûteux ; les tests
  unitaires restent sur SQLite in-memory, mono-processus, sans contention.
- **Réduire la fréquence de polling côté front** : traite le symptôme, pas la
  limite de concurrence SQLite.
