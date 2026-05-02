# ADR 0001 — Architecture de la plateforme : monolithe modulaire FastAPI

- **Statut** : accepté
- **Date** : 2026-05-02
- **Décideur** : owner du projet (Kenan)

## Contexte

Le projet AI-Kaeyris est une plateforme AI personnelle hébergée à terme sur un Raspberry Pi 5, accessible en REST sur le réseau local. Elle doit pouvoir héberger plusieurs services métier indépendants (résumé audio JDR en Jalon 5, autres services à venir) derrière une API unique, avec une seule personne pour la développer et l'opérer.

Trois axes de décision se posent au démarrage :

1. **Style architectural** : monolithe, monolithe modulaire, microservices, ou serverless ?
2. **Framework web** : FastAPI, Flask, Django, ou autre ?
3. **Organisation interne** : comment séparer le code métier, les intégrations externes et les concerns transverses ?

## Décision

### 1. Monolithe modulaire ("Monolith First")

Le projet adopte un **monolithe modulaire** : un seul processus, un seul déploiement, mais découpé en modules métier indépendants (`app/services/<feature>/`) qui ne s'importent pas entre eux. Chaque service peut être extrait plus tard en service séparé sans réécriture.

### 2. Framework : FastAPI

**FastAPI** (https://fastapi.tiangolo.com) est retenu comme framework web :

- Génération OpenAPI native (DoD facilitée, doc auto à `/docs` et `/redoc`)
- Pydantic v2 first-class pour validation et schémas
- Async natif (utile dès qu'on appellera des LLM en streaming)
- Dépendances injection légère, sans framework DI lourd

### 3. Organisation : séparation `core / services / adapters`

Trois dossiers fonctionnellement disjoints :

- `app/core/` : concerns transverses (config, auth, logging, errors)
- `app/services/<feature>/` : un dossier par feature métier, sans cross-import
- `app/adapters/` : intégrations externes derrière des interfaces (LLMAdapter, TranscriptionAdapter…) ; le code métier ne référence jamais un vendor

## Alternatives écartées

| Alternative | Raison du rejet |
|---|---|
| **Microservices d'emblée** | Complexité prématurée (Fowler 2015 — https://martinfowler.com/bliki/MonolithFirst.html). Une seule personne, pas de besoin de scaling indépendant, pas d'équipes parallèles à découpler. À reconsidérer si scaling ou ownership le justifient. |
| **Monolithe non modulaire** | Aucun coût d'avoir des frontières claires dès le début ; éviterait des refactos pénibles plus tard. Conway's Law ne joue pas ici (pas d'équipe), mais la discipline architecturale aide à apprendre. |
| **Serverless (AWS Lambda, etc.)** | Cible Raspberry Pi local, pas de cloud. Disqualifié par le contexte. |
| **Flask** | Pas d'async natif, pas d'OpenAPI auto, écosystème vieillissant pour les API modernes. |
| **Django / DRF** | Trop lourd pour une API REST sans front intégré ; ORM imposé alors qu'on n'a pas encore de DB en Jalon 0 (YAGNI). |
| **Litestar / Starlette pur** | Plus minimaliste mais moins de batteries incluses ; FastAPI fait le bon compromis ergonomie/contrôle. |
| **Pas de séparation `adapters/`** | Risque de coupler le métier à un vendor (DeepInfra aujourd'hui, autre demain). L'adapter pattern (Gamma et al., GoF 1994) protège la portabilité. |

## Conséquences

**Positives**
- Dette de complexité minimale au départ ; productivité élevée pour démarrer.
- Frontières claires (`services/` vs `core/` vs `adapters/`) qui forcent la discipline et facilitent une éventuelle extraction future en services séparés (Strangler Fig — Fowler).
- OpenAPI gratuite : test manuel via `/docs`, contrat formalisé.

**Négatives / acceptées**
- Un seul point de déploiement : si un service plante violemment (memory leak), il fait tomber tout le processus. Mitigation : observabilité (Jalon 6) + redémarrage auto via Docker.
- Pas de scaling indépendant par service. Si un service devient gros consommateur, il faudra alors envisager une extraction.
- Le pattern `adapters/` introduit une indirection qui peut sembler over-engineered au Jalon 0 (un seul vendor). Justifié par l'inflexion attendue dès le Jalon 4 (multi-provider).

**Conditions de re-évaluation** (cet ADR sera "superseded" si)
- Un service nécessite un cycle de release indépendant.
- Le scaling devient hétérogène (un service gourmand, les autres non).
- Plusieurs personnes travaillent en parallèle et se gênent dans le même repo.

## Références

- Martin Fowler, *MonolithFirst* (2015) — https://martinfowler.com/bliki/MonolithFirst.html
- Sam Newman, *Building Microservices* (O'Reilly, 2021) — chapitres sur le timing du split.
- *12-Factor App* — https://12factor.net (factors III, X, XI appliqués ici).
- Gamma, Helm, Johnson, Vlissides, *Design Patterns* (1994) — chapitre Adapter.
- C4 model pour la documentation visuelle de l'archi — https://c4model.com (à introduire au Jalon 1).
