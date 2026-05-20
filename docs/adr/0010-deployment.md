# ADR 0010 — Déploiement sur PC fixe LAN (Jalon 8)

- **Statut** : accepté
- **Date** : 2026-05-20
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : CLAUDE.md §3 (stack lockée — Caddy, PostgreSQL, Docker), §4.1 (`docs/runbook.md`), §5 (roadmap Jalon 8), ADR 0006 (Postgres cible), ADR 0008 (observabilité préalable), ADR 0009 (CI/CD précédente)
- **Dérivé de** : pas de Spec Kit (techno-transverse, comme Jalons 6 et 7)

## Contexte

À la fin du Jalon 7, le code est validé par 5 gates en CI, mais :
- Aucun artefact reproductible n'est publié (juste le commit Git, qu'il faut rebuild localement à chaque déploiement).
- Aucun chemin automatisé entre `git push origin main` et "l'API tourne sur ma machine cible".
- Le dev tourne en SQLite + compose hot-reload — ce n'est pas la prod (dette dev/prod parity, 12-Factor §X).
- La cible de déploiement a changé : Pi 5 devient **optionnel**, le PC fixe Windows 11 sur LAN privée est la nouvelle prod.

Le Jalon 8 (CLAUDE.md §5) industrialise ce chemin **après** le Jalon 6 (observabilité) et le Jalon 7 (CI/security) — ordre intentionnel : on ne déploie pas aveugle, et on ne déploie pas sans tests automatisés.

## Décisions

### 1. Pattern de delivery = pull-based GHCR + Watchtower

| Pattern | Description | Choisi ? |
|---|---|---|
| **A — Pull-based via GHCR + Watchtower** | GH Actions push image vers `ghcr.io/anama213/ai-kaeyris:latest`. Watchtower tourne en sidecar sur le PC, poll GHCR toutes les 5 min, pull et redeploy auto. | ✅ |
| B — Pull manuel via GHCR | GH Actions push image. Toi tu lances `docker compose pull && up -d` quand tu veux. | ❌ Pas de CD réel. |
| C — Push via tunnel Tailscale/Cloudflare | Runner GH atteint le PC via tunnel sortant. | ❌ Surface d'attaque. |
| D — Build local sur le PC | PC pull le code, builde l'image en local. | ❌ Pas de reproductibilité cross-machine. |

**Raisonnement** : le PC fixe est derrière un routeur sans IP publique exposée. Le pull-based supprime toute exigence de port ouvert et garde la **trust boundary** côté Git/GHCR (où la CI fait déjà tout le filtrage Jalon 7).

**Trade-off** : ~5 min de latence entre `git push` et "le code tourne en prod". Acceptable pour un projet perso ; à reconsidérer si le besoin d'un déploiement plus rapide se manifeste (auquel cas, on pourra ajouter une notification Watchtower → réduire l'intervalle à 60s).

**Source** : [Watchtower docs — Periodic checks](https://containrrr.dev/watchtower/arguments/#poll-interval), [GHCR overview](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

### 2. PostgreSQL en prod, SQLite en dev (closes dev/prod parity debt)

**Choisi** : service `postgres:16-alpine` dans `docker-compose.prod.yml`, driver `asyncpg` ajouté aux deps runtime. Le switch est purement environnemental via `DATABASE_URL` :
- Dev : `sqlite+aiosqlite:///./data/kaeyris.db`
- Prod : `postgresql+asyncpg://kaeyris:<pwd>@postgres:5432/kaeyris`

Pas de code change dans `app/core/db.py` : SQLAlchemy 2.x est portable, les migrations Alembic 0001-0004 utilisent uniquement des types `sa.Uuid`, `sa.JSON`, `sa.Enum`, `sa.DateTime(timezone=True)` qui marchent sur les deux backends.

**Alternative rejetée — rester sur SQLite en prod** : aurait été plus simple, mais 12-Factor §X (dev/prod parity) recommande la même DB. SQLite ne supporte pas la concurrence write multi-process — donc plusieurs workers RQ partagent un fichier SQLite est un risque opérationnel (corruption sur fsync race), même si le pattern actuel sérialise les writes via la transaction.

**Trade-off** : ~50 Mo d'image Postgres dans le compose, ~5 min de setup la première fois (CREATE DATABASE + Alembic upgrade head). Largement absorbé par le pattern Watchtower qui automatise tout après le premier `up`.

### 3. Caddy reverse proxy HTTP, /metrics gated par basic auth

**Choisi** :
- Caddy v2 fronte l'API sur `:80` LAN (port 80, pas 443).
- Tout le reste (postgres, redis, api, worker, prometheus, grafana) reste sur le network Docker `internal` sans port host publié.
- `/metrics` est gated par `basic_auth` (utilisateur `metrics`, hash bcrypt via env var `CADDY_METRICS_HASH`).

| Alternative | Pourquoi rejetée |
|---|---|
| nginx | Stack lockée Caddy (CLAUDE.md §3). Caddy a une syntaxe Caddyfile plus lisible, HTTPS auto-Let's-Encrypt si on en a besoin plus tard, et la même conf reverse-proxy en 3 lignes. |
| Pas de reverse proxy (API direct sur :8000) | Couple le port d'écoute à la conf appli, expose /metrics sans auth, pas de strip de banner `Server: uvicorn`. |
| HTTPS via internal CA | Force le trust-store install sur chaque client (laptop, téléphone). Non justifié sur LAN privée. Promotion **future** triviale : changer `:80` en `<hostname>` dans le Caddyfile et Caddy gère le reste. |
| HTTPS via Cloudflare Tunnel | Expose l'API en Internet en plus du LAN. Hors-scope pour un projet perso non-public. |

**Headers de sécurité ajoutés** : `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, retrait du header `Server`. Pas de HSTS (inutile sans HTTPS).

### 4. Observabilité = Prometheus + Grafana auto-provisionnés

**Choisi** : services `prometheus` (v2.55) et `grafana` (11.3) dans le compose, avec provisioning automatique :
- `docker/prometheus/prometheus.yml` — scrape `api:8000/metrics` toutes les 15s, rétention TSDB 15 jours.
- `docker/grafana/provisioning/datasources/` — datasource Prometheus locked (pas d'édition UI).
- `docker/grafana/provisioning/dashboards/` — auto-load des JSON sous `docker/grafana/dashboards/`.
- 1 dashboard `kaeyris-overview.json` à 5 panels (HTTP req rate, p95 latency, RQ jobs, LLM tokens, job duration p50/p95).

**Pourquoi maintenant et pas en Jalon 6** : le Jalon 6 a explicitement reporté la visualisation (cf. ADR 0008 §Limitations). Coupler la stack au déploiement évite l'écueil "instrumentation orpheline".

**Alternative rejetée — Datadog/New Relic SaaS** : commercial, abonnement, et le pipeline `app → /metrics → Prometheus pull → Grafana` est déjà le standard CNCF gratuit. À reconsidérer uniquement si on déploie en multi-host.

**Sources** : [Prometheus best practices — naming](https://prometheus.io/docs/practices/naming/), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).

### 5. Build multi-arch (amd64 + arm64) malgré Pi 5 optionnel

**Choisi** : `release.yml` builde `linux/amd64,linux/arm64` via Docker Buildx + QEMU.

**Pourquoi alors que Pi 5 est optionnel** :
- Coût marginal : ~30s de build supplémentaires sur le runner GH, $0 sur GitHub-hosted.
- Garde l'option Pi 5 ouverte sans avoir à modifier `release.yml` plus tard.
- Permet de redéployer sur n'importe quel SBC ARM (Pi 5, RockPro64, hôte virtualisé sur un Mac M-series) sans rework.

**Alternative rejetée — amd64 only** : économise ~30s par release mais ferme l'option d'expansion matérielle.

### 6. Migrations Alembic comme service one-shot

**Choisi** : service `migrations` dans le compose, image identique à api/worker, command = `alembic upgrade head`, `restart: "no"`. Les services `api` et `worker` dépendent de `migrations` via `condition: service_completed_successfully`.

**Pourquoi pas dans l'entrypoint du conteneur api** :
- **Isolation des préoccupations** : migrer la DB et servir des requêtes HTTP sont deux jobs différents avec des modes de défaillance différents. Une migration en cours doit échouer le démarrage de l'API, pas la laisser sortir un /healthz vert avec un schéma inconsistant.
- **Visibilité** : `docker compose ps` montre clairement la phase de migration. En cas d'échec, le code de sortie est tracé sans grepper les logs.
- **Watchtower-friendly** : quand Watchtower pull une nouvelle image, `migrations` re-tourne automatiquement (grâce au label), garantissant que toute nouvelle révision Alembic est appliquée avant que api/worker reprennent.

**Trade-off** : un service de plus à orchestrer, et `docker compose up -d` doit attendre que `migrations` exit 0 (~5-10s la première fois). Coût négligeable.

## Conséquences

✅ Le pipeline `git push → image GHCR → host PC` tourne en autonome, intervention manuelle uniquement pour la première initialisation des secrets.
✅ La dette dev/prod parity (12-Factor §X) est fermée pour la persistance.
✅ L'observabilité Jalon 6 a enfin sa visualisation : Grafana sur `http://<host>:3000` montre les métriques scrappées en direct.
✅ Pi 5 redevient possible sans modif (image arm64 publiée).
⚠️ Le secret `POSTGRES_PASSWORD` initial est dans `.env` sur la machine hôte — pas dans un secret manager. Acceptable pour un projet perso, à promouvoir vers HashiCorp Vault / Doppler / sealed-secrets si le projet sort de la sphère perso.
⚠️ Watchtower a un accès `rw` au Docker socket — c'est le minimum pour qu'il puisse drive le daemon. Trade-off classique : automation vs principle of least privilege.
⚠️ Le déploiement n'est testé qu'en `:latest`. Pas de canary, pas de blue/green. Acceptable au scale actuel ; à reconsidérer si on a un trafic non-trivial.

## Sources

- [12-Factor App — X. Dev/prod parity](https://12factor.net/dev-prod-parity)
- [Docker Compose — depends_on conditions](https://docs.docker.com/compose/how-tos/startup-order/#control-startup)
- [Watchtower — Container update automation](https://containrrr.dev/watchtower/)
- [GitHub Container Registry — Authenticating](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Caddy v2 — Basic auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth)
- [Prometheus — Best practices](https://prometheus.io/docs/practices/)
- [Grafana — Provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
