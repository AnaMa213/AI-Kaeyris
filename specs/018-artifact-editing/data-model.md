# Data Model — Epic 8 : Artefacts éditables

Référence : table existante `jdr_artifacts` (`app/services/jdr/db/models.py:483`). Cet epic **ajoute des colonnes de provenance** et **transforme la forme JSON des éléments**. Aucune nouvelle table.

## Entité `Artifact` (table `jdr_artifacts`)

| Colonne | Type | Existant ? | Changement |
|---|---|---|---|
| `session_id` | `Uuid` (FK sessions, CASCADE) | oui (PK) | — |
| `kind` | `String(80)` | oui (PK) | — (`narrative` / `elements` / `summary` / `pov:<uuid>`) |
| `content_json` | `JSON` | oui | forme des éléments changée (voir plus bas) |
| `model_used` | `String(255)` | oui | **immuable à l'édition** (BD-24/FR-006) |
| `generated_at` | `DateTime(tz)` | oui | **immuable à l'édition** |
| `is_edited` | `Boolean` not null, default `false` | **NOUVEAU** | posé `true` par l'édition, remis `false` à la (re)génération (BD-24) |
| `edited_at` | `DateTime(tz)` nullable | **NOUVEAU** | `now` à l'édition, `null` à la (re)génération |
| `edited_by` | `String(64)` nullable | **NOUVEAU** | identifiant du MJ éditeur (si peu coûteux ; sinon laissé `null`) |

**Invariants.**
- Édition (BD-23) : met à jour `content_json`, pose `is_edited=true`, `edited_at=now`, `edited_by=gm` ; ne touche ni `model_used` ni `generated_at`.
- (Re)génération (UPSERT) : remplace `content_json`/`model_used`/`generated_at`, remet `is_edited=false`, `edited_at=null`, `edited_by=null`.
- Édition refusée si la ligne n'existe pas (FR-003).

## Forme de `content_json`

### Artefacts texte (`summary`, `narrative`, `pov:<uuid>`)
Inchangée : `{"text": "<markdown>"}`. Non bornée (JSON). BD-25 = vérification + test, pas de changement de schéma.

### Artefact `elements` — AVANT (à migrer)
```json
{
  "npcs":      [{"name": "...", "description": "..."}],
  "locations": [{"name": "...", "description": "..."}],
  "items":     [{"name": "...", "description": "..."}],
  "clues":     [{"name": "...", "description": "..."}]
}
```

### Artefact `elements` — APRÈS (BD-26, Option B)
```json
{
  "elements": [
    {"category": "PNJ",     "name": "...", "description": "..."},
    {"category": "Lieux",   "name": "...", "description": "..."},
    {"category": "Objets",  "name": "...", "description": "..."},
    {"category": "Indices", "name": "...", "description": "..."},
    {"category": "<libre>", "name": "...", "description": "..."}
  ]
}
```
Correspondance de flatten (génération + migration) : `npcs→PNJ`, `locations→Lieux`, `items→Objets`, `clues→Indices`.

## Schémas Pydantic (`schemas.py`)

| Schéma | Changement |
|---|---|
| `Element` | `{name, description}` → `{category: str, name: str, description: str}` ; `category`/`name` non vides, `description` cap de garde généreux (~2000 car.), pas de limite « 25 mots ». |
| `ElementsArtifactOut` | 4 listes → `elements: list[Element]` + champs provenance. |
| `SummaryArtifactOut` / `NarrativeArtifactOut` / `PovArtifactOut` | + `is_edited: bool`, `edited_at: datetime|null` (+ `edited_by` si exposé). |
| `TextEditIn` *(nouveau)* | `{text: str}` (Markdown) — corps des PATCH summary/narrative/povs. |
| `ElementsPutIn` *(nouveau)* | `{elements: list[Element]}` — corps du PUT elements. |

## Migrations Alembic `0019_jdr_artifact_provenance` et `0020_jdr_elements_freeform_category`

**Upgrade.**
1. `0019` : `add_column` `is_edited` (`Boolean`, server_default `false`, not null), `edited_at` (`DateTime(tz)`, nullable), `edited_by` (`String(64)`, nullable) sur `jdr_artifacts`.
2. `0020` : pour chaque ligne `kind='elements'`, lire `content_json`, aplatir les 4 buckets en `{"elements":[...]}` (correspondance ci-dessus), réécrire. Lignes déjà au format `elements` → inchangées (idempotence).

**Downgrade.**
1. `0020` : regrouper `elements[]` par `category` ; PNJ/Lieux/Objets/Indices → buckets respectifs ; catégories libres → bucket de repli `clues` (best-effort, perte de la catégorie libre assumée et documentée).
2. `0019` : `drop_column` `edited_by`, `edited_at`, `is_edited`.

**Tests de migration** : upgrade puis lecture `ElementsArtifactOut` cohérente ; comptage d'éléments avant/après identique (SC-006) ; round-trip upgrade→downgrade sur données de base sans erreur.

## Relations / autorisation

- `Artifact` ↔ `Session` (FK CASCADE) inchangé.
- Lectures joueur (BD-27) : réutilisent l'autorisation « PJ lié au compte a participé à la session » déjà en place pour `/me/.../narrative` et `/me/.../pov`. Pas de nouvelle relation.
