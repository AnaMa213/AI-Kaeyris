# AI Platform — Project Constitution

> This file is loaded by Claude Code at every session start. It is the single source of truth for project conventions, principles, and architecture. Treat it as binding.

---

## 1. Project context

**Project**: Personal AI sandbox platform, hosted on a Raspberry Pi 5, accessed via REST API on the local network.

**Architecture style**: Modular monolith ("Monolith First", Fowler 2015) designed to evolve toward a service-oriented platform if usage grows.

**Owner**: Junior developer full-stack engineer (6 years experience) learning enterprise architecture by building a real project.

**Primary AI provider**: DeepInfra (cloud), accessed via an agnostic adapter so other providers (Claude, Mistral, Groq, local models) can be plugged in without changing business logic.

**First service** (Jalon 5): JDR/RPG session audio summarization — transcription + summary. Scope to be defined when the jalon starts (YAGNI).

**Future services**: Open. New services should be addable without modifying existing ones.

---

## 2. Constitution — non-negotiable principles

### 2.1 Honesty over speed

- Never invent libraries, APIs, or behaviors. If unsure, say so and search docs.
- Cite sources for any general claim about software engineering (with URL when possible).
- Distinguish: (1) verified facts, (2) reasoned estimates, (3) opinions and trade-offs.
- Never fabricate test results. Run tests; report what actually happened.

### 2.2 Pedagogy over output volume

- Explain _why_ before _what_ in every code change.
- Add inline comments only on non-obvious decisions (no narration of trivial code).
- Prefer small, focused commits with clear messages.
- One logical change per session unless the user explicitly asks for more.

### 2.3 YAGNI — You Aren't Gonna Need It

- Implement only what the current jalon requires.
- No premature abstraction. No "future-proof" structure nobody requested.
- If tempted to add scope: stop, ask the user, propose adding it to a future jalon.

### 2.4 Strict separation of concerns

- `app/services/<feature>/` = business logic for one feature (no cross-imports between services)
- `app/adapters/` = external integrations (LLM, transcription, storage providers)
- `app/core/` = cross-cutting concerns (auth, config, logging, error handling)
- Business code must NEVER reference a vendor name (DeepInfra, Anthropic, etc.). It calls the adapter interface only.

### 2.5 Test discipline

- Test pyramid: many unit tests, fewer integration, very few E2E (Cohn 2009).
- Every public endpoint must have at least one test.
- For non-trivial logic, prefer test-first.
- A jalon is not "done" until `pytest` passes and `ruff check` is clean.

### 2.6 Security by default

- Secrets only via environment variables. Never commit `.env`.
- All inputs validated by Pydantic.
- Follow OWASP API Security Top 10 (2023): broken auth, broken authorization, resource consumption, etc.
- Dependencies regularly scanned (Dependabot, pip-audit) starting Jalon 7.

### 2.7 12-Factor App compliance

- One codebase tracked in Git, many deploys (https://12factor.net/codebase)
- Explicit dependencies via `pyproject.toml` (https://12factor.net/dependencies)
- Config strictly via environment variables (https://12factor.net/config)
- Stateless processes, state in backing services (https://12factor.net/processes)
- Logs as event streams to stdout (https://12factor.net/logs)
- Dev/prod parity (https://12factor.net/dev-prod-parity)

---

## 3. Locked technology stack

Changes to this section require explicit user approval.

| Layer                 | Choice                                        | Rationale                                      |
| --------------------- | --------------------------------------------- | ---------------------------------------------- |
| Language              | Python 3.12+                                  | Mature ecosystem, AI-friendly                  |
| Framework             | FastAPI                                       | OpenAPI native, Pydantic-first, modern async   |
| Validation            | Pydantic v2                                   | Type safety, automatic schema                  |
| Async queue           | Redis + RQ                                    | Simpler than Celery, sufficient for this scale |
| Database              | PostgreSQL (target), SQLite (dev allowed)     | Standard SQL, reliable                         |
| Container             | Docker + Docker Compose                       | Industry standard                              |
| Linter/Formatter      | ruff                                          | Fast, replaces flake8 + black + isort          |
| Tests                 | pytest + httpx                                | Standard combo for FastAPI                     |
| Observability         | structlog (logs), prometheus-client (metrics) | Standard Python observability stack            |
| Reverse proxy (later) | Caddy                                         | Automatic HTTPS, simpler than nginx            |
| Default LLM provider  | DeepInfra                                     | User choice, OpenAI-compatible API             |

**Forbidden without discussion**: switching framework, adding ORM (SQLAlchemy etc.) before Jalon 5, introducing Kubernetes, microservices split.

---

## 4. Architecture and project structure

### 4.1 Target structure

```
ai-platform/
├── CLAUDE.md                  # this file
├── README.md                  # project overview, setup, usage
├── LICENSE
├── .gitignore
├── .env.example               # template, no real secrets
├── pyproject.toml             # deps + tool config (ruff, pytest)
├── docker-compose.yml         # full stack for dev
├── docker/
│   └── Dockerfile             # API image
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app factory
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py          # pydantic-settings, reads env
│   │   ├── auth.py            # API key auth (Jalon 2+)
│   │   ├── logging.py         # structlog setup
│   │   └── errors.py          # standard error handlers
│   ├── services/              # one folder per business feature
│   │   ├── __init__.py
│   │   └── _template/         # copy this to start a new service
│   │       ├── __init__.py
│   │       ├── router.py
│   │       ├── schemas.py
│   │       └── logic.py
│   └── adapters/              # external integrations
│       ├── __init__.py
│       ├── llm.py             # LLMAdapter interface + impls
│       └── transcription.py   # TranscriptionAdapter interface
├── tests/
│   ├── __init__.py
│   ├── conftest.py            # pytest fixtures
│   ├── core/
│   ├── services/
│   └── adapters/
└── docs/
    ├── adr/                   # Architecture Decision Records
    │   └── 0001-platform-architecture.md
    ├── journal.md             # learning journal (one entry per jalon)
    └── runbook.md             # operational procedures (Jalon 7+)
```

### 4.2 Service template rules

When the user asks to create a new service:

1. Copy `app/services/_template/` to `app/services/<new_name>/`
2. Adjust `router.py` prefix to `/services/<new_name>`
3. Mount the router in `app/main.py`
4. Create `tests/services/<new_name>/`
5. Document in `docs/services/<new_name>.md` (created on first service)

### 4.3 Adapter pattern rules

Every external service is wrapped behind an interface in `app/adapters/`:

```python
# Conceptual example, not literal code
class LLMAdapter(Protocol):
    async def summarize(self, text: str, max_tokens: int) -> str: ...

class DeepInfraLLMAdapter(LLMAdapter):
    # implementation
    ...
```

Selection of the implementation is done in `core/config.py` via env variable. The business code never instantiates a concrete adapter.

---

## 5. Jalons roadmap

Each jalon ends with: code passing tests, README updated, journal entry, commit pushed.

| #       | Jalon                      | Outcome                                                                     |
| ------- | -------------------------- | --------------------------------------------------------------------------- |
| 0       | Foundations                | Repo structure, Dockerfile, /health endpoint, CI-ready                      |
| 1       | Modular API skeleton       | Service `_template`, error handling, OpenAPI doc                            |
| 2       | Authentication             | API key auth, hashed storage, rate limiting, security headers               |
| 3       | Async processing           | Redis + RQ, worker, idempotent jobs, retry policy                           |
| 4       | Adapters + Spec Kit intro  | LLMAdapter agnostic, DeepInfra impl, introduce Spec Kit workflow            |
| 5       | First real service         | JDR summarization service (scope decided at jalon start)                    |
| 6       | Observability              | Structured logs, Prometheus metrics, health/readiness, OpenTelemetry traces |
| 7       | CI/CD + Security hardening | GitHub Actions, SAST (bandit), dependency scan, secrets scan                |
| 8       | Pi 5 deployment            | Multi-arch build, Caddy reverse proxy, monitoring on Pi                     |
| 9 (opt) | Local inference            | Whisper-tiny on Pi via local adapter, fallback strategy                     |

---

## 6. Anti-patterns to refuse

When the user (or you, by reflex) tries to:

- Add a feature beyond the current jalon → REFUSE, propose adding to roadmap
- Introduce a fancy pattern (CQRS, event sourcing) without justified need → REFUSE
- Skip tests "because it is just a small change" → REFUSE
- Reference a vendor name in `app/services/` → REFUSE, route via adapter
- Commit `.env` or any secret → REFUSE absolutely
- Hardcode credentials, API keys, URLs → REFUSE, use config
- Modify `app/core/` for a feature need → REFUSE if it can stay in the service
- Generate large amounts of code without explanation → REFUSE, slow down

When you refuse, explain briefly why and propose the alternative.

---

## 7. Definition of Done — per jalon

A jalon is considered complete when ALL of these are true:

1. ✅ `ruff check .` passes with no errors
2. ✅ `pytest` passes with all tests green
3. ✅ `docker compose up` starts the stack without crash
4. ✅ The new endpoint(s) respond correctly to a manual `curl` test
5. ✅ `README.md` updated with the new behavior or setup steps
6. ✅ One entry added to `docs/journal.md` describing what was learned
7. ✅ One ADR added in `docs/adr/` if a significant decision was made
8. ✅ Commit pushed to `main` with descriptive message following Conventional Commits format

> Conventional Commits format: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`. See https://www.conventionalcommits.org

---

## 8. Communication preferences

- Respond to the user in **French** (the user is francophone)
- Cite sources with URL for any general claim about software engineering
- When unsure, ask one precise question before acting
- Detect and gently report user biases: over-engineering, scope creep, "shiny tool" syndrome, perfectionism
- Detect and report your own biases: recency bias, vendor preference, complexity preference

## 9. Personal preferences (from user)

- Strong preference for verified facts over confident guesses
- Wants explanations of trade-offs and alternatives, not just decisions
- Wants to learn enterprise architecture practices, not just "code that runs"
- Pragmatic about norms: 12-Factor + OWASP + basic observability are mandatory; deeper standards (DDD strict, hexagonal) only if/when needed

---

## 10. Documents transverses : `docs/playbook.md` et `docs/memo.md`

Deux fichiers dans `docs/`, complémentaires et tenus à jour par Claude :

**`playbook.md` — méthodo générale**
- Guide projet logiciel pro **agnostique du projet courant** (applicable à toute API, portail, plateforme scalable).
- Phases A→K (cadrage, archi, qualité, ops, delivery…), questions à se poser, frameworks de référence (12-Factor, OWASP, C4, DORA…).
- Léger et scannable. Si une section dépasse ~20 lignes, condenser.
- Sources citées avec URL.

**`memo.md` — aide-mémoire technique**
- Référence rapide : commandes essentielles + raisons en 1 ligne par choix techno et par étape.
- Format = tableaux denses, pas de prose. Pour retrouver vite "comment fait-on X" et "pourquoi on a choisi Y".
- Spécifique à la stack du projet ; à enrichir quand un nouveau geste/outil est adopté.

**Règles communes**:
- Ne jamais dupliquer `CLAUDE.md` — y faire référence.
- Pas d'historique des jalons dans ces fichiers (cela va dans `docs/journal.md`).
- Mettre à jour dans la même session que le changement qui les concerne.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
[`specs/001-kaeyris-jdr/plan.md`](specs/001-kaeyris-jdr/plan.md).
Companion artifacts in the same directory: `spec.md`, `research.md`,
`data-model.md`, `contracts/`, `quickstart.md`.
<!-- SPECKIT END -->
