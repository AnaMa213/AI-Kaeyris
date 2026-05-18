# Data Model: Mode `non_diarised`

**Phase 1 du `/speckit-plan`**. Définit les entités, contraintes, invariants et transitions liés à cette feature. Aligné avec `spec.md` (FR-001 à FR-015) et `research.md` (décisions techniques).

> Tables existantes du Jalon 5 (`jdr_api_keys`, `jdr_pjs`, `jdr_sessions`, `jdr_audio_sources`, `jdr_transcriptions`, `jdr_session_pj_mappings`, `jdr_artifacts`, `jdr_jobs`) : voir `specs/001-kaeyris-jdr/data-model.md`. Aucune n'est modifiée par cette feature **sauf** `jdr_sessions` qui gagne 1 colonne.

---

## 1. Vue d'ensemble du schéma (additions)

```
            ┌──────────────────────────────────┐
            │ jdr_sessions (existante)         │
            │  + transcription_mode (NEW)      │
            └──────────┬───────────────────────┘
                       │ 1
                       │
            ┌──────────┴───────────────────────┐
            │ N                          N     │
            │                                  │
   ┌────────▼─────────┐         ┌─────────────▼────────┐
   │ jdr_chunks (NEW) │         │ jdr_session_players  │
   │                  │         │ (NEW)                │
   │ + summary_text   │         │                      │
   └──────────────────┘         └──────────┬───────────┘
                                           │ N → 1
                                           │
                                ┌──────────▼───────────┐
                                │ jdr_pjs (existante)  │
                                └──────────────────────┘
```

Deux nouvelles tables + une colonne. Pas d'index croisé entre les deux nouvelles tables (un PJ "présent" et un chunk de transcription sont des dimensions orthogonales).

---

## 2. `jdr_sessions` (modification d'une table existante)

### Colonne ajoutée

| Colonne | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `transcription_mode` | `VARCHAR(16)` | NOT NULL | `'diarised'` | CHECK applicatif via Enum SQLAlchemy : valeurs autorisées `diarised` / `non_diarised`. |

### Invariants

- **Immuabilité** : la valeur de `transcription_mode` est définie à la création via `POST /services/jdr/sessions` (FR-001) et **ne peut plus être modifiée** ensuite (FR-002). Enforcé en business code (le handler `PATCH /sessions/{id}` rejette `transcription_mode` même s'il apparaît dans le body, retourne 422).
- **Rétro-compatibilité** : les sessions créées au Jalon 5 (avant la migration `0002`) prennent automatiquement la valeur `diarised` via le `server_default` Alembic. Aucune session ne reste sans mode.
- Aucune modification des autres champs de `jdr_sessions` ni des relations existantes (`audio_source`, `transcription`, `mappings`, `artifacts`, `jobs`).

### Impact sur les relations

- `Session.mappings` (vers `SessionPjMapping`) reste valide mais **n'est plus alimentée** en mode `non_diarised` (FR-014 logique : `/mapping` est exclusivement utilisable sur les sessions `diarised`).
- `Session.transcription` (vers `Transcription`) reste valide mais **n'est plus alimentée** en mode `non_diarised` (la transcription est stockée dans `jdr_chunks` à la place).
- `Session.artifacts` (vers `Artifact`) reste valide et **accueille un nouveau `kind="summary"`** en mode `non_diarised`. Les autres `kind` (`narrative`, `elements`, `pov:<pj_id>`) sont produits dans les deux modes.

---

## 3. `jdr_chunks` (nouvelle table)

Stocke la transcription d'une session `non_diarised` sous forme d'une séquence ordonnée de chunks texte. Chaque chunk porte aussi le résumé partiel produit par l'étape map du job `summary` (cf. décision §1 et §4 de `research.md`).

### Schéma

| Colonne | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `id` | `UUID` | NOT NULL | `uuid4()` | PK |
| `session_id` | `UUID` | NOT NULL | — | FK → `jdr_sessions(id)` ON DELETE CASCADE, INDEX |
| `ordre` | `INTEGER` | NOT NULL | — | Position 0-indexée dans la séquence de la session. Unique avec `session_id`. |
| `text` | `TEXT` | NOT NULL | — | Contenu textuel brut du chunk. |
| `summary_text` | `TEXT` | NULL | NULL | Résumé partiel produit par l'étape map du job `summary` (FR-007). NULL tant que le map n'a pas tourné. |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

### Contraintes & index

- PK : `id`
- Unique : `(session_id, ordre)` — index composite, prévient les doublons d'ordre dans une session
- INDEX `session_id` (déjà couvert par l'unique composite, mais explicite pour les filtres simples)
- FK CASCADE sur `session_id` → suppression de session → suppression des chunks
- Aucune contrainte d'`ordre` séquentiel sans trou au niveau SQL : le job de transcription doit garantir 0, 1, 2, …, N-1 mais le schéma reste tolérant.

### Invariants applicatifs

- Une session n'a des rows `jdr_chunks` **que si** `session.transcription_mode = 'non_diarised'`.
- À l'inverse, une session `non_diarised` en état `transcribed` doit avoir au moins 1 chunk (sinon état dégénéré, signalé par `409` côté API).
- `summary_text` est NULL pour tous les chunks tant que `POST /artifacts/summary` n'a pas tourné avec succès. À la régénération, tous les `summary_text` de la session sont remis à NULL **atomiquement** avec la suppression cascade des artefacts dérivés (FR-011).
- Pas de modification possible de `text` ou `ordre` après création — c'est le pendant logique de l'immuabilité de la transcription Jalon 5.

### Transitions d'état

```
[création par _transcribe_session]
  → summary_text = NULL
  ↓ (POST /artifacts/summary)
[map du job summary, chunk par chunk]
  → summary_text = "<résumé partiel>"
  ↓ (re-POST /artifacts/summary)
[reset transactionnel]
  → summary_text = NULL (+ cascade DELETE narrative/elements/pov:*)
  → puis ré-application du map
```

---

## 4. `jdr_session_players` (nouvelle table)

Déclare la liste des PJ présents à une session `non_diarised`. Utilisée par le job `povs` pour savoir pour quels PJ produire un POV (FR-012). C'est l'équivalent en mode `non_diarised` de `jdr_session_pj_mappings` du mode `diarised`, mais sans `speaker_label` (qui n'a pas de sens sans diarisation).

### Schéma

| Colonne | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `session_id` | `UUID` | NOT NULL | — | PK part 1, FK → `jdr_sessions(id)` ON DELETE CASCADE |
| `pj_id` | `UUID` | NOT NULL | — | PK part 2, FK → `jdr_pjs(id)` ON DELETE RESTRICT |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | — |

### Contraintes & index

- PK composite : `(session_id, pj_id)` — empêche d'inscrire deux fois le même PJ sur la même session.
- FK CASCADE sur `session_id` (suppression de session → désinscription auto).
- FK RESTRICT sur `pj_id` (un PJ référencé par une session ne peut pas être supprimé tant qu'il l'est).
- INDEX implicite sur `session_id` via la PK composite.
- INDEX dédié sur `pj_id` (pas strictement nécessaire au jalon courant mais utile pour de futures requêtes type "toutes les sessions où ce PJ est présent" — symétrique du `pj_id` INDEX sur `jdr_session_pj_mappings`).

### Invariants applicatifs

- Une session n'a des rows `jdr_session_players` **que si** `session.transcription_mode = 'non_diarised'`.
- Chaque `pj_id` listé MUST appartenir au MJ propriétaire de la session — validation côté `logic.set_session_players()` (FR-012), équivalent à la validation `jdr_session_pj_mappings` du Jalon 5.
- Le `POST /sessions/{id}/players` est **idempotent à payload identique** et **destructif sur l'ancien contenu** : il remplace intégralement la liste (semantique PUT-like, similar à `replace_for_session` de `MappingRepository`).

### Transitions d'état

```
[création de session non_diarised] → 0 row
  ↓ (POST /sessions/{id}/players body {"pj_ids": [A, B]})
2 rows : (sid, A), (sid, B)
  ↓ (POST /sessions/{id}/players body {"pj_ids": [B, C]})
[remplacement atomique] : DELETE (sid, A), KEEP (sid, B), INSERT (sid, C)
  → 2 rows : (sid, B), (sid, C)
```

Note : l'INSERT/DELETE peut être implémenté comme un DELETE+INSERT global plutôt qu'un diff fin — c'est plus simple et atomique.

---

## 5. Artefact `kind="summary"` (nouveau dans table existante)

`jdr_artifacts` (existante du Jalon 5) accueille un nouveau `kind` applicatif sans modification de schéma. La PK composite reste `(session_id, kind)`.

| Champ | Valeur en mode summary |
|---|---|
| `session_id` | UUID de la session (FK CASCADE) |
| `kind` | `"summary"` |
| `content_json` | `{"text": "<résumé global consolidé>"}` |
| `model_used` | Identifiant du modèle utilisé pour le **reduce final** (ex. `"deepinfra:meta-llama/Meta-Llama-3.1-70B-Instruct"`) |
| `generated_at` | TIMESTAMPTZ du reduce final (pas de chaque map intermédiaire) |

### Invariants

- Une session a au plus 1 row avec `kind="summary"` (PK composite).
- Cohérent avec la sémantique UPSERT existante de `ArtifactRepository.upsert` (Jalon 5).
- L'artefact `summary` n'est créé que sur des sessions `non_diarised` — il n'a pas de sens en `diarised` (et l'endpoint refuse 409 dans ce cas, FR-006).
- À la régénération, l'ancien `summary` est UPSERT (écrit par-dessus). Voir §6 pour la séquence atomique complète.

---

## 6. Atomicité de la régénération du `summary` (FR-011)

Le job `_generate_summary` ouvre une transaction unique qui exécute dans l'ordre :

1. **Reset des résumés partiels** : `UPDATE jdr_chunks SET summary_text = NULL WHERE session_id = :sid`
2. **Cascade delete des artefacts dérivés** :
   - `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind = 'narrative'`
   - `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind = 'elements'`
   - `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind LIKE 'pov:%'`
3. (Commit du reset — la transaction se termine ici)
4. **Phase map** : pour chaque chunk dans `ordre` ASC, appel LLM, `UPDATE jdr_chunks SET summary_text = :s WHERE id = :cid`. Commit par chunk (chaque map est une mini-transaction).
5. **Phase reduce** : si > 1 chunk, concatène les `summary_text` et appelle le LLM une dernière fois. Sinon, le seul `summary_text` est le résumé global.
6. **UPSERT** `artifacts(session_id, kind="summary")` avec le texte produit. Commit.

### Garanties

- **Échec pendant le reset (étapes 1-2)** : rollback. L'ancien état (anciens `summary_text` + anciens artefacts dérivés) est préservé.
- **Échec entre les commits du reset et la fin du map (étapes 3-4)** : les `summary_text` sont à NULL, les artefacts dérivés ont été supprimés. C'est un état dégradé mais cohérent : un re-POST relance le job en mode "rebuild" (au pire on perd les anciens artefacts dérivés que le MJ devra régénérer, ce qui est l'intention). Le `summary` global ancien est conservé (étape 6 pas encore atteinte).
- **Échec pendant le reduce (étape 5)** : `summary_text` à jour, anciens artefacts dérivés supprimés, ancien `summary` global préservé. État reconstruction-cohérent : la `POST /artifacts/summary` suivante reprend depuis le reduce (les `summary_text` sont déjà calculés, on saute le map). Heuristique de re-prise : si un chunk a déjà un `summary_text` non NULL au début du job, on skip son map. C'est une optimisation à valider à l'implémentation — non bloquante.

### Trade-off documenté

Le découpage en deux transactions (reset court + map/reduce long) est **délibéré** : tenir une seule transaction pendant les appels LLM (5 min pour 60k chars selon SC-001) tiendrait des verrous DB trop longtemps. C'est cohérent avec `research.md §2`.

---

## 7. Relations entre entités et autorisation

| Entité | Propriétaire | Visibilité |
|---|---|---|
| `Session.transcription_mode` | Le MJ qui a créé la session | MJ uniquement |
| `Chunk` (row) | Indirect via `session.gm_key_id` | MJ propriétaire de la session uniquement (`require_gm` + filtre `Session.gm_key_id = current.id`) |
| `SessionPlayer` (row) | Indirect via `session.gm_key_id` | MJ propriétaire uniquement. Chaque `pj_id` doit appartenir à ce même MJ (FR-012). |
| `Artifact(kind="summary")` | Indirect via session | MJ propriétaire uniquement. Pas d'accès joueur au jalon courant. |

Aucune extension de la visibilité joueur (`/me/*`) au jalon courant. Les endpoints `/me/sessions/{id}/narrative` et `/me/sessions/{id}/pov` du Jalon 5 restent **exclusivement utilisables sur des sessions `diarised`** — un joueur dont le MJ a opté pour le mode `non_diarised` ne verra rien via `/me/*`. Cette restriction est documentée dans `spec.md §Assumptions` et `quickstart.md §6`.

---

## 8. Validation Pydantic côté API (résumé)

| Endpoint | Champ | Validation |
|---|---|---|
| `POST /services/jdr/sessions` | `transcription_mode` | optionnel, default `diarised`, valeurs autorisées `diarised` ou `non_diarised`, sinon 422 |
| `POST /services/jdr/sessions/{id}/players` | `pj_ids` | array non vide ; chaque élément doit être un UUID valide ; déduplication serveur ; max 50 items (limite arbitraire prudente — au-delà, cas non prévu) |
| `POST /services/jdr/sessions/{id}/artifacts/summary` | (pas de body) | session doit être `non_diarised` ET en état `transcribed` ET avoir ≥ 1 chunk |
| `POST /services/jdr/sessions/{id}/artifacts/{narrative\|elements\|povs}` | (pas de body — endpoints Jalon 5 réutilisés) | si session `non_diarised`, exiger `summary` généré (sinon 409 `no-summary`). Si session `diarised`, exiger `transcription` (déjà géré au Jalon 5). |

---

## 9. Volumétrie attendue

Estimations pour calibrer les index et l'absence de pagination explicite :

- Une session JDR longue (2h-3h) : 50 000 à 100 000 caractères de transcription → 2 à 4 chunks à 30 000 chars. **Petit nombre de chunks par session** (≤ 10 dans 99 % des cas).
- Un MJ a typiquement < 100 sessions actives. Total `jdr_chunks` rows : < 1 000 typique, < 10 000 en croissance.
- `jdr_session_players` : ≤ 8 PJ par session × 100 sessions × N MJ. Négligeable.

Les index PK composite suffisent. Pas de besoin d'index supplémentaire ni de pagination dans les listings (`GET /chunks`, `GET /players`).

---

## 10. Synthèse

| Table | Statut | Lignes attendues |
|---|---|---|
| `jdr_sessions` | Modifiée (+1 colonne) | Inchangé (~100/MJ) |
| `jdr_chunks` | Nouvelle | ~1 000-10 000 (croît avec usage) |
| `jdr_session_players` | Nouvelle | Négligeable (≤ 8/session × #sessions) |
| `jdr_artifacts` | Réutilisée, +1 kind | +1 row `summary` par session non_diarised |

Schéma stable, pas de cycle FK, pas de divergence entre SQLite (dev) et PostgreSQL (cible Jalon 8).
