# Runbook — exploitation du service `ai-kaeyris`

> Procédures opérationnelles pour le déploiement sur PC fixe LAN.
> Compagnon de [`docs/adr/0010-deployment.md`](./adr/0010-deployment.md) (décisions structurantes).

## Vue d'ensemble

| Composant | Image | Réseau | Port host |
|---|---|---|---|
| `api` | `ghcr.io/anama213/ai-kaeyris:latest` | `internal` | — (via Caddy) |
| `worker` | idem | `internal` | — |
| `migrations` | idem (one-shot) | `internal` | — |
| `postgres` | `postgres:16-alpine` | `internal` | — |
| `redis` | `redis:7-alpine` | `internal` | — |
| `caddy` | `caddy:2-alpine` | `internal` + `edge` | **80** |
| `prometheus` | `prom/prometheus:v2.55.0` | `internal` | — |
| `grafana` | `grafana/grafana:11.3.0` | `internal` | **3000** |
| `watchtower` | `containrrr/watchtower:latest` | `internal` | — |

**Endpoints accessibles depuis le LAN** :
- API : `http://<host>/...` (Caddy proxy port 80)
- Grafana : `http://<host>:3000` (login admin + `GRAFANA_ADMIN_PASSWORD`)
- `/metrics` : `http://<host>/metrics` (basic auth `metrics` + mot de passe lié au hash `CADDY_METRICS_HASH`)

## Première installation sur le PC fixe

Prérequis : Docker Desktop (ou Docker Engine + Compose v2) installé, repo clôné.

```powershell
# 1. Générer les secrets — NE PAS commit
$pg = -join ((1..32) | ForEach-Object { [char](Get-Random -Min 65 -Max 91) })
$grafana = -join ((1..24) | ForEach-Object { [char](Get-Random -Min 65 -Max 91) })

# 2. Générer le hash bcrypt pour /metrics
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'choisis-un-mot-de-passe'
# → copie le hash $2a$14$... dans CADDY_METRICS_HASH

# 3. Copier .env.example → .env et compléter les valeurs ci-dessus
Copy-Item .env.example .env
# Éditer .env :
#   POSTGRES_PASSWORD=<le $pg>
#   GRAFANA_ADMIN_PASSWORD=<le $grafana>
#   CADDY_METRICS_HASH='$2a$14$...'  (entre simples quotes)
#   LLM_API_KEY=<ta clé DeepInfra>
#   TRANSCRIPTION_API_KEY=<idem>
#   API_KEYS='<argon2-hashed>'        (cf. scripts/generate_api_key.py)

# 4. Démarrer la stack
docker compose -f docker-compose.prod.yml up -d

# 5. Suivre le démarrage
docker compose -f docker-compose.prod.yml logs -f
```

**Vérifications post-démarrage** :
- `docker compose -f docker-compose.prod.yml ps` → tous `healthy` ou `running`.
- `curl http://localhost/healthz` → `200 {"status":"ok"}`.
- `curl http://localhost/readyz` → `200` si DB+Redis OK.
- Navigateur `http://localhost:3000` → login Grafana, le dashboard "AI-Kaeyris — Overview" est déjà visible.

## Déploiement d'une nouvelle version

**Cas nominal — automatique via Watchtower** :

```
git push origin main
└─ CI passe (Jalon 7 gates)
└─ release.yml build & push ghcr.io/anama213/ai-kaeyris:latest
└─ [~5 min plus tard] Watchtower pull, restart migrations + api + worker
```

Aucune action manuelle sur le PC.

**Cas explicite — force le pull immédiat** :

```powershell
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Rollback rapide

```powershell
# 1. Identifier le SHA précédent
docker images ghcr.io/anama213/ai-kaeyris

# 2. Override KAEYRIS_IMAGE dans .env vers ce SHA
# Edit .env :
#   KAEYRIS_IMAGE=ghcr.io/anama213/ai-kaeyris:main-<sha_précédent>

# 3. Désactiver Watchtower temporairement pour éviter qu'il re-pull :latest
docker compose -f docker-compose.prod.yml stop watchtower

# 4. Redéployer
docker compose -f docker-compose.prod.yml up -d api worker migrations

# 5. Une fois le problème root-causé et corrigé sur main, ré-activer Watchtower
docker compose -f docker-compose.prod.yml up -d watchtower
```

⚠️ **Rollback de migration** : Alembic supporte `downgrade` mais cela suppose que la migration soit réversible. Pour les migrations data-mutating (e.g. 0004), tester `alembic downgrade -1` en dev avant.

```powershell
docker compose -f docker-compose.prod.yml run --rm migrations alembic downgrade -1
```

## Troubleshooting

### `migrations` exit code ≠ 0

Symptôme : `docker compose ps` montre migrations en `exited (1)`, api et worker bloqués en `created`.

```powershell
# Voir l'erreur exacte
docker compose -f docker-compose.prod.yml logs migrations

# Causes typiques :
# - Postgres pas encore prêt (devrait pas — depends_on healthy)
# - Conflit de révision Alembic (rare)
# - Schema incompatible avec une rev plus ancienne
```

### `/readyz` renvoie 503

```powershell
# Le body JSON contient le détail par check (db, redis)
curl http://localhost/readyz | jq

# Inspecter le composant cassé
docker compose -f docker-compose.prod.yml logs postgres
docker compose -f docker-compose.prod.yml exec redis redis-cli ping
```

### API ne répond pas via Caddy

```powershell
# Tester direct l'API (bypass Caddy)
docker compose -f docker-compose.prod.yml exec api curl http://localhost:8000/healthz

# Si OK ici mais pas via :80, problème côté Caddy
docker compose -f docker-compose.prod.yml logs caddy
```

### Grafana ne montre aucune métrique

```powershell
# Vérifier que Prometheus scrappe bien l'API
docker compose -f docker-compose.prod.yml exec prometheus wget -qO- http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'
# Doit afficher "up" pour ai-kaeyris-api

# Vérifier que l'API expose /metrics
docker compose -f docker-compose.prod.yml exec api curl http://localhost:8000/metrics | head -20
```

### Watchtower ne pull pas

```powershell
docker compose -f docker-compose.prod.yml logs watchtower

# Causes :
# - Image GHCR privée + pas de docker login → ajouter creds dans ~/.docker/config.json sur l'hôte
# - Label `com.centurylinklabs.watchtower.enable=true` absent → check compose
# - WATCHTOWER_POLL_INTERVAL trop court → revoir
```

## Sauvegardes

**Postgres** :

```powershell
# Snapshot one-shot
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U kaeyris kaeyris > backup-$(Get-Date -Format yyyyMMdd-HHmmss).sql
```

**Audios + transcriptions** : volume `kaeyris-data`.

```powershell
docker run --rm -v ai-kaeyris_kaeyris-data:/data -v ${PWD}:/backup alpine `
  tar czf /backup/kaeyris-data-$(Get-Date -Format yyyyMMdd).tar.gz -C /data .
```

**Grafana dashboards** : source de vérité = `docker/grafana/dashboards/*.json` dans le repo. Pas besoin de back up le volume `grafana-data` (recréable depuis le provisioning).

## Rotation des secrets

| Secret | Procédure |
|---|---|
| `POSTGRES_PASSWORD` | Stop api+worker → `ALTER USER kaeyris PASSWORD '...'` → update `.env` → up -d. |
| `GRAFANA_ADMIN_PASSWORD` | `docker compose exec grafana grafana-cli admin reset-admin-password '...'` puis update `.env`. |
| `CADDY_METRICS_HASH` | Régénérer via `caddy hash-password`, update `.env`, restart caddy. |
| `LLM_API_KEY` / `TRANSCRIPTION_API_KEY` | Update `.env`, restart api + worker. |
| `API_KEYS` (clés utilisateurs) | `python scripts/generate_api_key.py <name>`, append à `API_KEYS` dans `.env`, restart api. Anciennes clés restent valides tant que présentes. |
