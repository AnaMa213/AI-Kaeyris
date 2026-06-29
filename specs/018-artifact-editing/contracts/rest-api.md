# REST API Contract — Epic 8 : Artefacts éditables

Préfixe service : `/services/jdr`. Tous les endpoints d'édition exigent un MJ propriétaire de la campagne (`require_gm` + résolution de session). Les lectures `/me/...` exigent un joueur dont le PJ lié a participé à la session.

## BD-23 — Édition synchrone (MJ)

| Méthode | Chemin | Corps | Réponse 200 |
|---|---|---|---|
| `PATCH` | `/sessions/{session_id}/artifacts/summary` | `{ "text": "<md>" }` | `SummaryArtifactOut` |
| `PATCH` | `/sessions/{session_id}/artifacts/narrative` | `{ "text": "<md>" }` | `NarrativeArtifactOut` |
| `PATCH` | `/sessions/{session_id}/artifacts/povs/{pj_id}` | `{ "text": "<md>" }` | `PovArtifactOut` |
| `PUT` | `/sessions/{session_id}/artifacts/elements` | `{ "elements": [ {category,name,description} ] }` | `ElementsArtifactOut` |

**Sémantique.** Écriture synchrone (`200`, pas de `202`/job). Pose `is_edited=true`, `edited_at=now`. Laisse `model_used`/`generated_at` intacts.

**Erreurs.**
- `404`/`422` si l'artefact ciblé n'existe pas (réutiliser la sémantique « artefact absent » existante des GET) — FR-003.
- `403` si l'appelant n'est pas le MJ propriétaire — FR-004.
- `422` si corps invalide (texte vide → rejet ; `category`/`name` vides ; description au-delà du cap de garde) — voir data-model.
- `404` session inconnue / non possédée (sémantique existante).

## BD-24 — Provenance + garde de régénération

**Champs ajoutés** à `SummaryArtifactOut` / `NarrativeArtifactOut` / `PovArtifactOut` / `ElementsArtifactOut` :
```jsonc
{
  "is_edited": false,            // true si édité à la main depuis la dernière génération
  "edited_at": null,             // datetime ISO-8601 tz, ou null
  "edited_by": null              // identifiant MJ, ou null (optionnel)
}
```

**Garde sur la (re)génération** (endpoints POST existants, inchangés sauf ce paramètre) :

| Méthode | Chemin | Param | Comportement |
|---|---|---|---|
| `POST` | `/sessions/{session_id}/artifacts/summary` | `?force=true` | régénère ; **cascade-delete** narrative/elements/pov |
| `POST` | `/sessions/{session_id}/artifacts/narrative` | `?force=true` | régénère le récit |
| `POST` | `/sessions/{session_id}/artifacts/elements` | `?force=true` | régénère les éléments |
| `POST` | `/sessions/{session_id}/artifacts/povs` | `?force=true` | régénère les POV |

- Artefact cible (ou, pour `summary`, **un** artefact aval) `is_edited=true` **et** `force` absent → `409 artifact-edited` (FR-007).
- `?force=true` → procède et écrase (FR-008). Au succès, le contenu (re)généré a `is_edited=false`.
- Artefact non édité → comportement actuel inchangé (pas de `409`).
- Échec du job → l'artefact édité existant n'est pas détruit (non-destructif, FR-009).

## BD-25 — Textes longs

Pas de nouvel endpoint ni changement de contrat. Garantie : `text` accepte ≥ 10 000 mots sans troncature (validé par test, SC-004).

## BD-26 — Éléments free-form

`ElementsArtifactOut` change de forme (rupture de contrat assumée) :
```jsonc
// AVANT
{ "session_id": "...", "npcs": [], "locations": [], "items": [], "clues": [], "model_used": "...", "generated_at": "..." }
// APRÈS
{
  "session_id": "...",
  "elements": [ { "category": "PNJ", "name": "...", "description": "..." } ],
  "model_used": "...", "generated_at": "...",
  "is_edited": false, "edited_at": null
}
```
Génération IA : produit 4 buckets en interne → aplatis en `elements[]` taggés (`npcs→PNJ`, `locations→Lieux`, `items→Objets`, `clues→Indices`).

## BD-27 — Lectures joueur (lecture seule)

| Méthode | Chemin | Réponse |
|---|---|---|
| `GET` | `/me/sessions/{session_id}/summary` | `SummaryArtifactOut` |
| `GET` | `/me/sessions/{session_id}/summary.md` | `text/markdown` |
| `GET` | `/me/sessions/{session_id}/elements` | `ElementsArtifactOut` |
| `GET` | `/me/sessions/{session_id}/elements.md` | `text/markdown` |

- Miroir strict de `/me/sessions/{id}/narrative(.md)` existant : même autorisation (PJ lié ayant participé), mêmes projections.
- `403`/`404` si le PJ du compte n'a pas participé à la session — FR-016 / SC-007.
- POV joueur inchangé (`/me/sessions/{id}/pov` — le sien uniquement).

## Mise à jour du contrat publié

`docs/context/api/openapi.json` doit être régénéré après implémentation (le front régénère ensuite `types/api.ts`). La rupture sur `ElementsArtifactOut` est intentionnelle.
