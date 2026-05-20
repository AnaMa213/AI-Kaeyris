# ADR 0009 — CI/CD et durcissement sécurité (Jalon 7)

- **Statut** : accepté
- **Date** : 2026-05-20
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : CLAUDE.md §2.6 (Security by default), §2.7 (12-Factor), §3 (stack lockée), ADR 0008 (observabilité préalable)
- **Dérivé de** : pas de Spec Kit pour ce jalon (feature techno-transverse sans ambiguïté métier, comme Jalon 6).

## Contexte

À la fin du Jalon 6, le service tourne, est observable, mais **aucune barrière automatique ne le protège** :

- Aucun workflow CI : un commit qui casse `pytest` ou `ruff` peut atterrir sur `main` sans rien déclencher.
- Pas de SAST : un usage maladroit de `subprocess`, `eval`, `yaml.load` non sécurisé passerait inaperçu.
- Pas de scan des dépendances : CVE upstream sur `idna`, `cryptography`, `urllib3` invisibles tant qu'on ne re-lit pas pyproject.toml.
- Pas de secrets scanning : une copie/colle malheureuse d'une clé DeepInfra dans un test ou un commentaire pourrait fuiter sur GitHub public.
- Aucune contrainte locale : tout repose sur la rigueur du dev — qui oublie parfois `ruff check` avant un commit.

Le Jalon 7 (CLAUDE.md §5) industrialise ces garde-fous **avant** le déploiement (Jalon 8). C'est la bonne séquence : déployer sans CI revient à supprimer les freins d'une voiture avant de prendre l'autoroute.

## Décisions

### 1. CI = GitHub Actions, 4 jobs séparés en parallèle

Phase 1 du jalon. Un seul workflow `.github/workflows/ci.yml`, déclenché sur :

- `push` sur `main`
- `pull_request` ciblant `main`

Concurrency group annule les runs en cours quand un nouveau commit arrive sur le même ref → évite le pile-up sur des branches actives.

| Job | Bloquant ? | Détail |
|---|---|---|
| `lint` | ✅ | `ruff check app tests migrations` |
| `test` | ✅ | `pytest -q` (Python 3.12, `pip install -e ".[dev]"`) |
| `security-sast` | ✅ (sur Medium+) | `bandit -c pyproject.toml -r app --severity-level medium` |
| `security-deps` | ❌ | `pip-audit --desc` avec `continue-on-error: true` |
| `security-secrets` | ✅ | `gitleaks/gitleaks-action@v2` avec config `.gitleaks.toml` |

**Pourquoi 4 jobs séparés et pas un seul script** : isolation des temps de feedback. Un lint qui échoue ne doit pas masquer un test qui passait — et inversement. GitHub Actions ne facture pas le parallélisme sur un repo public ; pour un repo privé sur le plan gratuit, le coût en minutes est marginal vu le nombre de PR.

### 2. SAST = bandit, gated à Medium+

Phase 2. **Choix** : bandit plutôt que semgrep ou pylint security.

| Alternative | Pourquoi rejetée |
|---|---|
| `semgrep` | Plus puissant, mais runtime plus lourd, dépend du registry semgrep en ligne (network failure casse la CI), et le sur-coût n'est pas justifié pour un mono-langage Python à 5k LoC. À reconsidérer si on étend en JS/Go/Rust. |
| `pylint --enable=security` | Couverture security marginale, faux positifs en cascade qui forceraient à maintenir une liste d'exclusions lourde. |
| `ruff` security rules (`S`) | Complémentaire de bandit, pas substitut. À envisager plus tard via `[tool.ruff.lint.select]`, mais double scan = double bruit pour la même classe de problème. |

**Configuration** dans `pyproject.toml` `[tool.bandit]` :

- `exclude_dirs = ["tests", "migrations", ".venv", "build", "dist"]` — les tests font des `assert` légitimes, les migrations contiennent du SQL string par design.
- `skips = ["B101"]` — `assert` est l'ABC de pytest.

**Gating à Medium+** : on a 5 findings Low au baseline (B404/B603/B607 sur les appels `ffmpeg`/`ffprobe` du pipeline transcription — usages corrects, sans `shell=True`, sans input non-trusté). Bloquer dessus serait du bruit. Bloquer sur Medium/High capture les vraies régressions (eval, SQL injection, weak crypto…).

### 3. Dependency scan = pip-audit, non-bloquant

Phase 3. **Choix** : pip-audit plutôt que safety ou snyk.

| Alternative | Pourquoi rejetée |
|---|---|
| `safety` | Version free limitée (DB en retard d'1 semaine + 50 packages max), version pro payante. pip-audit utilise la même source OSV gratuitement et sans cap. |
| `snyk` | Commercial, nécessite un compte Snyk + un token. Sur-dimensionné pour un projet perso. |
| `dependabot` (GitHub) | Complémentaire : Dependabot ouvre des PR de mise à jour, pip-audit échoue la CI ou alerte sur les CVEs en cours. À activer en plus, pas à la place. |

**Non-bloquant (`continue-on-error: true`)** : choix explicite. Un CVE upstream sans patch immédiat (cas du jour : `idna 3.13` → `CVE-2026-45409`, fix prévu `3.15`) ne doit pas bloquer une PR qui n'a aucun lien avec la lib en question. La discipline reste : on relit les logs CI à chaque merge, on ouvre une issue de suivi si nécessaire. Promotion à bloquant uniquement quand on aura un tracker (Linear/GitHub Projects) + une RACI de triage des CVE.

### 4. Secrets scan = gitleaks, bloquant

Phase 4. **Choix** : gitleaks plutôt que trufflehog ou detect-secrets.

| Alternative | Pourquoi rejetée |
|---|---|
| `trufflehog` | Mode `--only-verified` puissant mais plus bruyant en CI publique. Le binaire est aussi plus gros et l'action moins maintenue côté UX. |
| `detect-secrets` | Yelp, bonne lib, mais nécessite de maintenir une `.secrets.baseline` régénérable — friction quotidienne. gitleaks fonctionne stateless. |
| Github Actions secret scanning natif | Couvre le push mais pas le pre-push, et ne scan pas les fichiers non-poussés (gitleaks scan le diff staged via pre-commit). |

**Allowlist `.gitleaks.toml`** :

- `.env.example` — placeholder, pas de vrai secret.
- `tests/**/*.py` — fixtures argon2 calculées dynamiquement, jamais des hashes commitables.
- `scripts/generate_api_key.py` — outil CLI, pas de secret embarqué.

**Bloquant sur finding** : contrairement à pip-audit, ici toute fuite est une vraie urgence. Mieux vaut un faux positif (qu'on allowliste) qu'une vraie fuite qui passe.

### 5. Pre-commit hooks mirrorent la CI

Phase 5. `.pre-commit-config.yaml` à la racine — installation optionnelle (`pre-commit install`), mais documenté dans README et fortement recommandé.

Hooks alignés sur la CI pour éviter "ça passe en local mais pas en CI" :

- `pre-commit-hooks` (v5.0.0) : trailing whitespace, EOF, syntaxe YAML/TOML, large files, `detect-private-key`.
- `ruff-pre-commit` (v0.8.6) : `ruff --fix` + `ruff-format`.
- `bandit` (1.8.0) : même flags que la CI (`--severity-level medium`, scoped à `app/`).
- `gitleaks` (v8.21.2) : scan des staged changes uniquement (rapide).

**Choix pédagogique** : un dev peut commiter sans les hooks (les hooks sont locaux, pas enforced côté serveur), mais la CI repousse de toute façon. Les hooks accélèrent le feedback, ils ne remplacent pas la CI.

## Alternatives rejetées au niveau du jalon

- **Tout sur Pre-commit CI plutôt que GitHub Actions** : Pre-commit CI est limité aux hooks pre-commit, ne sait pas lancer pytest, et ne sait pas non plus exposer un workflow custom (job conditionnel, matrix Python…). On le récupère via les hooks locaux uniquement.
- **Couverture de code (codecov / coverage)** : reportée au Jalon 8+. Mesurer un % sans cible ni budget actionnable serait de la vanity metric. À introduire avec un seuil discuté (genre "≥ 80% sur `app/services/`, libre sur `migrations/` et `app/main.py`").
- **Tests mutationnels (`mutmut`)** : trop lourd à ce stade. À reconsidérer si on lance des refactos massifs.
- **SBOM (CycloneDX, syft)** : utile en contexte enterprise / supply-chain, hors-scope pour un projet perso. À ajouter au Jalon 8 si on signe les artefacts Docker.

## Conséquences

✅ Toute PR vers `main` est désormais filtrée par 5 gates automatisés.
✅ Un nouveau contributeur (ou Claude en session future) ne peut pas casser silencieusement la base.
✅ Les CVE upstream sont visibles dès le merge suivant — plus de surprise au déploiement.
✅ Une fuite accidentelle de clé est rattrapée avant push si pre-commit est installé, sinon avant merge.
⚠️ Le temps total CI passe de "instantané" à ~3-4 min (install + tests + scans). Acceptable pour un projet à faible débit de PR.
⚠️ `pip-audit` non-bloquant suppose une discipline humaine de relecture des logs CI. À revisiter au Jalon 8 quand on aura un système de tracking d'issues.

## Sources

- [12-Factor App — Dev/prod parity](https://12factor.net/dev-prod-parity)
- [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
- [GitHub Docs — About workflows](https://docs.github.com/en/actions/using-workflows/about-workflows)
- [bandit — Configuration](https://bandit.readthedocs.io/en/latest/config.html)
- [pip-audit — README](https://github.com/pypa/pip-audit)
- [gitleaks — Configuration](https://github.com/gitleaks/gitleaks#configuration)
- [pre-commit — Quick start](https://pre-commit.com/#quick-start)
