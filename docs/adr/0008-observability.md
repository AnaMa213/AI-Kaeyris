# ADR 0008 — Observabilité du service `ai-kaeyris` (Jalon 6)

- **Statut** : accepté
- **Date** : 2026-05-19
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (monolithe modulaire), ADR 0004 (jobs RQ + retry), ADR 0006 (service `kaeyris-jdr` Jalon 5), CLAUDE.md §3 (stack lockée — `structlog` + `prometheus-client`)
- **Dérivé de** : pas de Spec Kit pour ce jalon (feature techno-transverse sans ambiguïté métier).

## Contexte

Le Jalon 5 + sub-jalon 5.5 livrent un service qui tourne en E2E avec DeepInfra, valeur métier visible. Mais le service est **aveugle** : pas de logs structurés, pas de métriques, pas de healthcheck readiness, pas de traces. Quand un job RQ stagne ou un appel LLM coûte trop cher, il n'y a aucun moyen propre de comprendre ce qui s'est passé sans grepper des `print` ad-hoc.

CLAUDE.md §3 verrouille `structlog` (logs) et `prometheus-client` (métriques) dans la stack — mais ces déclarations n'ont **jamais été implémentées** au Jalon 4 où ADR 0005 a posé le `LLMAdapter`. C'est aspirationnel, pas l'état réel : 8 modules utilisent `logging` stdlib avec des appels `printf`-style, sans configuration JSON, sans context-vars de corrélation.

Le Jalon 6 (cf. CLAUDE.md §5) matérialise les 3 piliers de l'observabilité (concept formalisé par Cindy Sridharan, *Distributed Systems Observability* 2018) **avant** le Jalon 7 (CI/CD) et le futur déploiement sur PC fixe — sinon on déploie aveugle.

## Décisions

### 1. Logs structurés via `structlog`, bridge stdlib → structlog, JSON activable par env var

Phase 1 du jalon. 4 décisions sous-jacentes :

- **Bridge stdlib** : pas de remplacement du `logging` standard, mais redirection via `structlog.stdlib.LoggerFactory`. Avantage : les libs tierces (httpx, sqlalchemy, openai) continuent d'écrire dans `logging` stdlib et leurs messages sont automatiquement structurés au passage.
- **Format env-driven** : `LOG_FORMAT=console` (défaut) pour le dev (renderer humain coloré), `LOG_FORMAT=json` pour la prod (JSONRenderer ligne par ligne). `LOG_LEVEL` env var pour ajuster verbosité.
- **Convention `event.name` snake_case dotted** : `startup.api_keys_bootstrapped`, `llm.complete`, `ffprobe.unparseable`, etc. Pas de phrases libres en français — les noms sont des identifiants techniques, le contenu humain va dans les kwargs.
- **Corrélation par contextvars** : `request_id` (UUIDv4 ou trust du header `X-Request-Id`) bound au début de chaque requête HTTP via `RequestContextMiddleware`. Auto-merge dans tous les logs émis pendant la requête grâce à `structlog.contextvars.merge_contextvars`. Pas de plumbing manuel à chaque log site.

**Alternatives rejetées** :

| Alternative | Pourquoi rejetée |
|---|---|
| Garder stdlib + JsonFormatter sur le Handler | Pas de support natif des contextvars → corrélation manuelle à chaque log site, fragile. |
| `loguru` au lieu de `structlog` | API simpliste mais incompatible avec la philosophie "stdlib bridge" — loguru remplace plutôt que d'augmenter. Friction avec les libs tierces. |
| Activer JSON systématiquement | Renderer console est immensément plus lisible en dev local. JSON c'est pour les agrégateurs (`docker logs | jq` côté prod). |

### 2. Métriques Prometheus côté `prometheus-client`, naming `kaeyris_*`, cardinalité bornée

Phase 2. 9 métriques applicatives sur 4 dimensions :

| Dimension | Counters | Histograms |
|---|---|---|
| HTTP | `kaeyris_http_requests_total{method, route, status}` | `kaeyris_http_request_duration_seconds{method, route}` |
| LLM | `kaeyris_llm_calls_total{provider, model, outcome}`, `kaeyris_llm_tokens_total{provider, model, direction}` | `kaeyris_llm_call_duration_seconds{provider, model}` |
| Transcription | `kaeyris_transcription_calls_total{provider, outcome}` | `kaeyris_transcription_duration_seconds{provider}` |
| Jobs RQ | `kaeyris_jobs_total{kind, outcome}` | `kaeyris_job_duration_seconds{kind}` |

**Cardinalité bornée** : le label HTTP `route` est le **template** (`/sessions/{session_id}/artifacts/summary`), pas le path concret. Sinon explosion 1 série par UUID — pattern d'erreur classique documenté par Prometheus (https://prometheus.io/docs/practices/naming/#labels).

**Endpoint** : `GET /metrics` (text exposition), non auth, `include_in_schema=False` (scrape Prometheus, pas client humain).

**Pas dans le scope** : métriques Redis pool (déléguées au future Prometheus Redis exporter), métriques Postgres pool (Jalon 8 quand on bascule), métriques GC Python (très peu utiles solo).

### 3. Healthchecks séparés : `/healthz` (liveness) + `/readyz` (readiness)

Phase 3. Convention Kubernetes-style, même si on n'est pas en K8s — sert aussi pour systemd / Docker Compose healthcheck / sondes futures.

- **`/healthz`** : 200 si le process est vivant. **Aucune** dépendance externe. Intent : orchestrateur redémarre le process iff ça fail.
- **`/readyz`** : 200 si DB + Redis pingués avec succès, 503 sinon. Le body surface chaque check (`{"checks": {"database": "ok", "redis": "fail: ..."}}`) pour qu'un opérateur identifie la dépendance morte sans parser des logs.
- **`/health` legacy** (Jalon 0) : préservé en alias de `/healthz` pour rétro-compat. Pas de breaking change.

**Pas dans le scope** : check du provider LLM dans `/readyz`. Raison : un ping LLM coûte de l'argent (call vers DeepInfra payant) et n'a pas de sémantique "ping" gratuite côté API OpenAI-compatible. Le manque de LLM est détecté via les métriques `kaeyris_llm_calls_total{outcome="permanent"}` qui montent.

### 4. OpenTelemetry **opt-in** scaffolding, sans collector au Jalon 6

Phase 4. Décision la plus discutée : faire ou ne pas faire OTEL dans le Jalon 6.

- **Risque YAGNI** : sans collector (Tempo, Jaeger, OTEL Collector) qui consomme les traces, OTEL ajoute des deps et de la complexité pour zéro valeur immédiate.
- **Risque retro-fit** : si on attend le Jalon 8 (déploiement) pour brancher OTEL, on touche le code à un moment où on veut surtout valider la prod.

**Compromis acté** : **scaffolding** uniquement.

- Module `app/core/tracing.py` avec `setup_tracing(app)` strict no-op quand `OTEL_ENABLED != "true"`.
- Quand activé : tracer provider réel + auto-instrumentation `FastAPIInstrumentor` / `SQLAlchemyInstrumentor` / `HTTPXClientInstrumentor` (gratuites en termes de code, ~3 lignes chacune).
- Exporter : `console` par défaut (stdout), `otlp` via HTTP vers `OTEL_EXPORTER_OTLP_ENDPOINT` (défaut `http://localhost:4318`).
- **Pas de spans manuels custom** sur le pipeline LLM map/reduce à ce jalon. Différé Jalon 8 où on aura un collector pour les visualiser.

**Conséquence** : au Jalon 8 déploiement, brancher un collector = ajouter un service `tempo` ou `jaeger-all-in-one` au `docker-compose.yml` + setter `OTEL_ENABLED=true OTEL_EXPORTER=otlp`. Zéro code à toucher côté API.

## Conséquences

### Positives

- **Service plus diagnosticable** : un job RQ qui patine se voit dans `kaeyris_jobs_total{outcome="transient"}` + logs `request_id`-corrélés.
- **Coûts LLM visibles** : `kaeyris_llm_tokens_total` cumulé permet de chiffrer une session JDR concrète après-coup (utile pour valider la rentabilité du sub-jalon 5.5).
- **Standards respectés** : naming Prometheus correct, healthchecks Kubernetes-style, OTEL prêt à brancher. Aucun retro-fit douloureux prévu.
- **6 commits incrémentaux** sur la branche `003-observability` : un par phase, rollback granulaire facile.
- **322 tests verts** dont 21 nouveaux (6 logging + 3 metrics + 4 healthchecks + 8 tracing) — non-régression confirmée sur les 301 tests Jalon 5 + sub-jalon 5.5.

### Négatives (assumées)

- **6 deps OTEL ajoutées** alors qu'inactives par défaut. Coût d'import et de footprint au démarrage (~50 ms et ~25 Mo de RAM en plus pour les modules importés mais pas exécutés). Acceptable.
- **Pas de dashboard Grafana** ni d'alerting (Alertmanager) au Jalon 6. À monter au Jalon 8 ou jamais (solo, pas critique business).
- **OTEL réelle activation jamais testée en CI** : les tests mockent les instrumentors car ils modifient l'état global du process. Validation = manuelle au Jalon 8 contre un vrai collector.
- **`/health` legacy maintenu en alias** : un peu de dette, mais préserve les anciens clients (ex. Docker Compose healthcheck déjà configuré).

### Neutres

- **Healthchecks ne vérifient pas la queue RQ** : ce serait un check "Redis répond" déjà couvert. Si Redis répond mais que les workers sont morts, `/readyz` reste vert. Limitation acceptée — un worker mort se voit via `kaeyris_jobs_total` qui ne monte pas + Prometheus alerting à terme.
- **Pas de profiling** (`py-spy`, cProfile dump) au Jalon 6. À introduire si une session réelle montre un goulot d'étranglement non-visible via métriques.

## Alternatives rejetées (globales)

| Alternative | Pourquoi rejetée |
|---|---|
| APM commercial (Datadog, New Relic, Honeycomb) | Coût, lock-in, sur-dimensionné pour un projet perso. Le stack OSS (Prometheus + Grafana + Tempo/Loki) couvre 100% des besoins. |
| Logs Loki centralisés | Solo sur PC fixe = `journalctl` ou `docker logs` suffisent. Loki est intéressant à partir de plusieurs hôtes. |
| Faire les 4 piliers au Jalon 7 (CI/CD) à la place | Le Jalon 7 vise la pipeline de build/test/release, pas l'observabilité runtime. Inverser l'ordre rendrait Jalon 7 plus risqué (déployer dans un CI sans monitoring est encore pire). |
| Skip Phase 4 OTEL totalement | Voir §4 ci-dessus. Acté que le scaffolding minimal vaut le coût même sans consommer. |

## Suivi et révision

À revisiter après :

- la première **vraie session JDR sur le nouveau setup observability** — on saura quelles métriques manquent vraiment (par ex. `redis_queue_depth`, `worker_alive`).
- le **Jalon 7 (CI/CD)** : ajouter une étape de scrape de `/metrics` dans la CI pour valider que le format reste correct (smoke test format).
- le **Jalon 8 (déploiement PC fixe)** : brancher un collector réel (`tempo` ou `jaeger-all-in-one`), wirer Grafana + Prometheus en sidecar Docker Compose, écrire les dashboards.

Cet ADR ne sera amendé qu'en cas de **superseding** par un futur ADR (convention immutabilité ADR, cf. ADR 0001).
