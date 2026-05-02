# ADR 0002 — Structure d'un service et format d'erreur unifié

- **Statut** : accepté
- **Date** : 2026-05-02
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (architecture monolithe modulaire)

## Contexte

Le Jalon 1 concrétise le squelette modulaire posé en Jalon 0. Trois questions structurantes se posent :

1. **Comment organise-t-on le code interne d'un service métier ?** Tout dans un seul fichier ? Séparé en couches ?
2. **Le `_template` est-il un service comme les autres** (donc monté dans l'app) **ou un simple modèle de copie** ?
3. **Quel format d'erreur** l'API renvoie-t-elle, et **comment** l'implémente-t-on ?

Ces choix vont être appliqués à tous les futurs services (résumé JDR au Jalon 5, et tous ceux qui suivront). Une fois pris, en revenir coûtera cher.

## Décision

### 1. Trois fichiers par service : `router.py`, `schemas.py`, `logic.py`

Chaque service dans `app/services/<nom>/` contient au minimum :

| Fichier | Responsabilité | Dépendances autorisées |
|---|---|---|
| `router.py` | Routage HTTP : déclare l'`APIRouter` FastAPI, appelle `logic.py`, sérialise via `schemas.py` | FastAPI, `schemas.py`, `logic.py` |
| `schemas.py` | Modèles Pydantic des inputs/outputs publics du service | Pydantic uniquement |
| `logic.py` | Logique métier pure, ne connaît ni HTTP ni FastAPI | adapters (`app.adapters.*`), core (`app.core.*`), Pydantic |

**Règles** :
- Aucun import croisé entre services (`app.services.foo` ne peut pas importer depuis `app.services.bar`). Si deux services ont besoin d'un même morceau, il remonte dans `app.core/` ou `app.adapters/`.
- `logic.py` n'importe **jamais** `fastapi`. Il doit rester testable sans serveur HTTP.
- `schemas.py` est l'**API publique** du service (au sens : ce que voient les clients). On y soigne les noms et la doc OpenAPI.

### 2. Le `_template` n'est **pas monté** dans l'application principale

`app/main.py` n'inclura **pas** `app.services._template.router`. Le template est un modèle pédagogique de copie, pas un service de production.

Il sera tout de même testé via un mini-app FastAPI créé dans `tests/services/_template/conftest.py` (fixture qui monte uniquement le router du template). Ainsi on vérifie qu'il marche sans polluer la doc OpenAPI publique ni l'API exposée en prod.

### 3. Format d'erreur unifié : **RFC 9457 Problem Details for HTTP APIs**

Toutes les réponses d'erreur de l'API utilisent le format défini par la **RFC 9457** (juillet 2023, succède à la RFC 7807) — https://www.rfc-editor.org/rfc/rfc9457.html

**Structure** :
```json
{
  "type": "https://kaeyris.local/errors/<slug>",
  "title": "Validation error",
  "status": 422,
  "detail": "Field 'message' is required",
  "instance": "/services/_template/echo"
}
```

**Content-Type** de la réponse : `application/problem+json` (et non `application/json`).

**Implémentation** : **fait main** dans `app/core/errors.py`. Pas de dépendance externe.

- Hiérarchie d'exceptions : `AppError(Exception)` racine, sous-classes par catégorie (`ValidationAppError`, `NotFoundAppError`, etc.) ajoutées au fil des besoins.
- Une fonction `register_exception_handlers(app)` appelée depuis `app/main.py` qui enregistre :
  - Un handler pour `AppError` → réponse Problem Details avec le statut HTTP de l'exception.
  - Un handler pour `RequestValidationError` (Pydantic/FastAPI) → réponse 422 Problem Details enrichie d'un champ `errors` listant les champs invalides.
  - Un handler catch-all pour `Exception` → réponse 500 Problem Details générique (pas de stack trace côté client), stack trace loggée côté serveur.

## Alternatives écartées

| Alternative | Raison du rejet |
|---|---|
| **Tout dans un seul fichier `service.py`** | Mélange routing / validation / métier. Impossible de tester `logic.py` sans démarrer FastAPI. Pénible quand le service grossit. |
| **Plus de couches dès le départ** (repository, use_case, controller façon Clean Architecture) | Sur-ingénierie pour un projet à une personne sans DB en Jalon 1. À reconsidérer si la complexité métier le justifie (cf. CLAUDE.md §9 — "DDD strict only if needed"). |
| **Monter `_template` dans l'app principale** | Pollue la doc API, expose un endpoint `_template` en prod sans intérêt fonctionnel, donne l'illusion d'un service réel. La testabilité hors-app suffit. |
| **Format d'erreur maison** (`{"error": {"code": "...", "message": "..."}}`) | Coût d'invention, divergence avec les autres APIs, pas d'outils clients. Cohérence à long terme < gain court terme. |
| **Garder le format par défaut FastAPI** (`{"detail": "..."}`) | Incohérent (HTML pour 500, JSON pour 422), pas de `type` ni `instance` documentés, pas de `Content-Type` standard. Insuffisant pour une API qui vise à grossir. |
| **Lib externe `fastapi-problem-details`** | Dépendance jeune et niche, surdimensionnée pour ~3 types d'erreurs initiaux. Réévaluer si on dépasse 10-15 types d'erreurs ou si on a besoin de fonctionnalités avancées (multi-langue, tracing). Voir https://pypi.org/project/fastapi-problem-details/ |

## Conséquences

**Positives**
- Onboarding nouveau service ultra-rapide : copier `_template/`, renommer, monter. Discipline imposée par la structure.
- `logic.py` testable en pur Python (très rapide, fixtures triviales).
- API parle un langage standard (RFC 9457) : tout client comprend les erreurs sans doc spécifique.
- Aucune dépendance externe ajoutée pour la gestion d'erreurs : surface d'attaque inchangée, pas de risque upstream.

**Négatives / acceptées**
- Trois fichiers par service même si le service est trivial. Coût marginal accepté pour la cohérence.
- L'implémentation maison de la RFC 9457 doit être maintenue manuellement si la spec évolue. Mitigation : les champs sont stables depuis 2016 (RFC 7807), peu probable que ça bouge.
- Test du `_template` nécessite une fixture spéciale (mini-app de test) plutôt qu'utiliser l'app principale. Léger surcoût pédagogique compensé par la propreté de l'API publique.

**Conditions de re-évaluation** (cet ADR sera "superseded" si)
- On dépasse ~15 types d'erreurs distincts → migrer vers `fastapi-problem-details` ou équivalent.
- On a besoin de multi-langue, de tracing distribué dans les erreurs, ou d'un format différent pour des clients non-HTTP.
- Un service grossit assez pour justifier des couches supplémentaires (repository, use case…) → ADR séparé sur ce service précis.

## Références

- RFC 9457 — *Problem Details for HTTP APIs* (2023) — https://www.rfc-editor.org/rfc/rfc9457.html
- Zalando RESTful API Guidelines (recommande Problem Details) — https://opensource.zalando.com/restful-api-guidelines/
- Microsoft REST API Guidelines (recommande Problem Details) — https://github.com/microsoft/api-guidelines
- FastAPI — *Handling Errors* — https://fastapi.tiangolo.com/tutorial/handling-errors/
- ADR 0001 — Architecture de la plateforme (cet ADR en applique les principes au niveau service)
