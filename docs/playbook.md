# Playbook — méthodo projet logiciel pro

> Guide général, applicable à n'importe quel projet sérieux (API, portail, app scalable, plateforme).
> Format = questions / commandes / pièges. Ne pas surcharger.
> Pour la vision du projet courant : [`CLAUDE.md`](../CLAUDE.md). Pour le mémo technique (commandes + raisons) : [`memo.md`](./memo.md).

---

## Phase A — Cadrage (avant tout code)

**Questions business**
- Quel **problème** résout-on ? Pour **qui** précisément ?
- Quel est le **résultat mesurable** attendu (KPI, OKR) ?
- Quelle est la **value proposition** vs faire-rien / vs concurrents ?
- Qui sont les **stakeholders** ? Qui décide en cas de conflit ?
- Budget, deadline, équipe : **2 sur 3 sont fixes**, lequel flotte ?

**Questions de scope**
- Quel est le **MVP** (le plus petit truc qui livre de la valeur) ?
- Qu'est-ce qu'on ne fera **pas** ? (le hors-scope est aussi important que le scope)
- Quelles **hypothèses** doit-on valider en premier ? (Lean Startup — Ries 2011)

**Livrables de phase**: Product Brief 1 page, liste de risques, critères de succès.

---

## Phase B — Vision produit & utilisateurs

- Quels sont les **3-5 user journeys** principaux ?
- Quels sont les **jobs-to-be-done** (JTBD, Christensen) ?
- Y a-t-il besoin d'une recherche utilisateur (interviews, analytics existants) ?
- Personas réalistes ou inventées ? (préférer des proto-personas basés sur des vrais users)

**Pièges**: confondre "ce que demande le client" et "ce dont l'utilisateur a besoin" ; designer pour soi-même.

---

## Phase C — Exigences non-fonctionnelles (NFR)

À chiffrer **avant l'archi**, sinon on choisit à l'aveugle.

| Catégorie | Question clé |
|---|---|
| Performance | Latence p95/p99 acceptable ? Throughput attendu ? |
| Disponibilité | SLA visé ? 99% (3.6j/an down) ? 99.9% (8h) ? 99.99% (52min) ? |
| Scalabilité | Combien d'utilisateurs au lancement ? À 1 an ? À 5 ans ? |
| Sécurité | Données sensibles ? RGPD ? PCI-DSS ? HIPAA ? |
| Résilience | RTO (temps de récup) / RPO (données perdues acceptables) ? |
| Observabilité | Logs/metrics/traces requis ? Qui sera oncall ? |
| Conformité | Audit ? Certifs (SOC2, ISO27001) ? |
| Accessibilité | WCAG 2.1 AA exigé ? |
| i18n / l10n | Multi-langue ? Multi-fuseau ? |

**Règle d'or**: pas de chiffre = pas d'exigence. "Rapide" ne veut rien dire ; "p95 < 200ms" oui.

---

## Phase D — Architecture

**Questions de structure**
- Monolithe, monolithe modulaire, microservices, serverless ? → par défaut **monolithe modulaire** (Fowler 2015 — https://martinfowler.com/bliki/MonolithFirst.html), microservices seulement si scaling indépendant ou équipes >2 pizzas.
- Synchrone (REST/gRPC) ou asynchrone (queue/events) ? Mix ?
- Stateful ou stateless ? Où vit l'état ? (DB, cache, queue, blob)
- Multi-tenant ou single-tenant ? Isolation par schéma, par DB, par cluster ?

**Diagrammes** : modèle **C4** (Brown — https://c4model.com) — Contexte / Container / Component / Code. Au minimum les 2 premiers niveaux.

**Décisions structurantes** → 1 ADR (Architecture Decision Record) par décision. Format MADR (https://adr.github.io/madr/) : Contexte / Décision / Alternatives / Conséquences.

**Pièges**: "microservices par défaut" (complexité prématurée) ; choisir une techno parce qu'elle est cool (recency bias) ; ignorer la conway's law (l'archi reflète l'org).

---

## Phase E — Choix de stack

Pour chaque brique, se poser :
1. **Maturité** : âge, taille communauté, fréquence releases
2. **Compétences équipe** : on sait l'opérer en prod ?
3. **Écosystème** : libs, intégrations, hosting
4. **Coût total** : licence + infra + apprentissage + maintenance
5. **Sortie de secours** : comment migrer si ça tourne mal ?

**Règle**: choisir **ennuyeux** par défaut (Boring Technology — McKinley — https://boringtechnology.club). L'innovation se concentre sur le métier, pas l'infra.

**Briques typiques à trancher**: langage / framework web / DB / cache / queue / objet storage / observability / CI/CD / hosting.

---

## Phase F — Structure repo & conventions

- **Monorepo ou polyrepo** ? Monorepo simplifie les refactors transverses ; polyrepo isole les cycles de vie.
- **Layout** : `app/` (code), `tests/`, `docs/`, `infra/` (IaC), `scripts/`. Convention > préférence personnelle.
- **Naming** : kebab-case pour repos/dossiers publics, snake_case pour Python, camelCase pour JS/TS. Cohérent partout.
- **Conventions de commit** : Conventional Commits (https://www.conventionalcommits.org) — `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`.
- **Branching** : trunk-based (main + feature branches courtes) > git-flow pour la plupart des projets modernes (DORA — https://dora.dev).

---

## Phase G — Qualité (tests, sécu, perf)

**Tests — pyramide** (Cohn 2009)
- Beaucoup d'**unitaires** (rapides, isolés, sans I/O)
- Quelques **intégration** (DB réelle, container)
- Très peu d'**E2E** (lents, fragiles, mais valident le contrat user)
- Couverture cible : **70-80%** sur le code métier (pas un dogme, c'est un signal).

**Sécurité — questions à se poser**
- OWASP API Top 10 (https://owasp.org/API-Security/) couvert ?
- Secrets via env vars uniquement, jamais en dur ?
- Validation systématique des inputs (Pydantic, zod, joi) ?
- Auth + Authz séparées et testées ?
- Dépendances scannées (Dependabot, Snyk, pip-audit) ?
- Logs ne contiennent pas de données sensibles (PII, tokens) ?

**Performance** : profiler **avant** d'optimiser (Knuth — "premature optimization is the root of all evil"). Mesurer avec un outil (k6, locust, ab) sur un environnement représentatif.

---

## Phase H — Observabilité & ops

**Les 3 piliers**: logs structurés (JSON), metrics (Prometheus/OpenMetrics), traces (OpenTelemetry — https://opentelemetry.io).

- Chaque requête a un **correlation ID** propagé partout.
- Définir des **SLI/SLO** (Google SRE — https://sre.google/sre-book/) et alerter sur le SLO budget, pas sur des seuils arbitraires.
- **Health endpoints** : `/health` (liveness, je tourne) et `/ready` (readiness, je peux servir).
- **Runbook** opérationnel : que faire quand X tombe ? Doit exister AVANT le go-live.
- Métriques DORA (deploy frequency, lead time, MTTR, change failure rate) pour mesurer la santé delivery.

---

## Phase I — Delivery (CI/CD)

- **CI**: à chaque PR → lint + tests + build + scan sécu. Doit tourner < 10 min sinon les devs contournent.
- **CD**: au moins **dev → staging → prod**. Promotion automatique ou manuelle selon criticité.
- **Stratégies de release** : blue/green, canary, feature flags. Choisir selon le risque.
- **Migrations DB** : versionnées, idempotentes, **réversibles** quand possible. Jamais directement en prod à la main.
- **Rollback** : doit être testé, pas juste "documenté".

**Pièges**: déployer le vendredi sans plan ; tests qui ne tournent qu'en local ; "ça marche sur ma machine".

---

## Phase J — Documentation vivante

Trois niveaux qui ne se confondent pas :
- **README** : "comment je lance ce truc en 5 min" (onboarding développeur).
- **ADR** : pourquoi on a fait tel choix structurant (`docs/adr/NNNN-titre.md`).
- **Runbook / Ops** : que faire en cas d'incident (`docs/runbook.md`).
- **API doc** : OpenAPI auto-générée tant que possible (FastAPI le fait, Express via swagger).

**Règle**: si la doc n'est pas mise à jour dans la même PR que le code, elle ment dans les 3 mois.

---

## Phase K — Évolution & dette

- **Refacto continu** > grand refacto. Boy-scout rule : laisse le code plus propre que tu l'as trouvé.
- **Dette technique tracée** : un backlog visible, pas des post-its dans la tête. Étiquette `tech-debt`.
- **Refacto piloté par le besoin** : on ne refactor pas pour le plaisir, on refactor pour débloquer une feature.
- **Bouger sa position** : ce qui était bon il y a 2 ans peut ne plus l'être. Réévaluer périodiquement les choix structurants (ADR superseded).

---

## Anti-patterns universels à refuser

- **Big bang rewrite** : tuer le projet. Préférer Strangler Fig (Fowler — https://martinfowler.com/bliki/StranglerFigApplication.html).
- **Premature optimization** : Knuth.
- **Premature abstraction** : DRY appliqué trop tôt → accouplement faux. Préférer WET (Write Everything Twice) puis abstraire à la 3ᵉ occurrence.
- **God object / God service** : une chose qui fait tout. Splitter par responsabilité (SRP — Martin).
- **Cargo cult** : copier une pratique d'un autre contexte sans comprendre pourquoi (ex. microservices "comme Netflix").
- **Vendor lock-in non assumé** : OK si conscient et documenté, dangereux si subi.
- **Tests qui testent l'implémentation** au lieu du comportement — fragiles, freinent le refacto.

---

## Frameworks de référence à connaître

| Framework | Utilité | Lien |
|---|---|---|
| 12-Factor App | Apps cloud-natives | https://12factor.net |
| OWASP Top 10 | Sécu web | https://owasp.org/Top10/ |
| OWASP API Top 10 | Sécu API | https://owasp.org/API-Security/ |
| C4 Model | Diagrammes archi | https://c4model.com |
| MADR | Format ADR | https://adr.github.io/madr/ |
| DORA | Métriques delivery | https://dora.dev |
| Google SRE | Ops, SLO | https://sre.google/sre-book/ |
| Conventional Commits | Format commits | https://www.conventionalcommits.org |
| Test Pyramid | Cohn 2009 | https://martinfowler.com/articles/practical-test-pyramid.html |
| Strangler Fig | Migration legacy | https://martinfowler.com/bliki/StranglerFigApplication.html |

---

## Checklist générique avant un go-live

- [ ] NFR chiffrés et validés (latence, dispo, sécu)
- [ ] Tests unitaires + intégration verts en CI
- [ ] Scan sécu (deps + SAST) clean
- [ ] Logs structurés + metrics + alertes en place
- [ ] Health + ready endpoints exposés
- [ ] Runbook écrit, oncall identifié
- [ ] Migration DB testée + rollback testé
- [ ] Backup + plan de restore vérifiés
- [ ] Doc à jour (README + ADR récents + runbook)
- [ ] Charge testée à 2-3× le pic attendu
- [ ] Plan de communication en cas d'incident
