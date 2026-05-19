# Contracts: REST API — Mode `non_diarised`

**Phase 1 du `/speckit-plan`**. Définit la surface REST publique ajoutée et modifiée par cette feature. Aligné avec `spec.md` (FR-001..015), `data-model.md` (entités), `research.md` (décisions). Le contrat HTTP du Jalon 5 sur les sessions `diarised` reste **rigoureusement inchangé** (FR-014).

Toutes les nouvelles routes :
- Sont préfixées `/services/jdr` (cohérent ADR 0002 §1).
- Sont gardées par `require_gm` (rôle MJ) sauf mention contraire.
- Héritent du rate-limiting global (60 req/min par clé, ADR 0004).
- Renvoient des erreurs au format RFC 9457 Problem Details (`Content-Type: application/problem+json`).

---

## 1. Création de session — extension du payload existant

### `POST /services/jdr/sessions` (modifié)

**Body** :

```json
{
  "title": "Session 12 — Le Tombeau de Saruman",
  "recorded_at": "2026-05-18T19:00:00Z",
  "campaign_context": "...",      // optionnel, inchangé Jalon 5
  "transcription_mode": "non_diarised"   // NOUVEAU optionnel, défaut "diarised"
}
```

**Response 201** (champs ajoutés en gras) :

```json
{
  "id": "uuid",
  "title": "...",
  "recorded_at": "...",
  "mode": "batch",
  "state": "created",
  "campaign_context": null,
  "transcription_mode": "non_diarised",   // NOUVEAU — toujours renvoyé
  "created_at": "...",
  "updated_at": "..."
}
```

**Erreurs** :
- `422 invalid-transcription-mode` si la valeur de `transcription_mode` n'est ni `diarised` ni `non_diarised`.

**Compatibilité** : un payload Jalon 5 sans `transcription_mode` est accepté et reçoit le défaut `diarised`. Aucun client existant ne casse.

---

### `PATCH /services/jdr/sessions/{id}` (modifié)

Le champ `transcription_mode` MUST PAS apparaître dans le body. S'il est présent (même avec la valeur courante), la requête est refusée.

**Erreurs ajoutées** :
- `422 immutable-field` si `transcription_mode` figure dans le body. Detail : `"transcription_mode is immutable after session creation."`

Les autres champs `title` et `campaign_context` restent modifiables comme au Jalon 5.

---

## 2. Liste des PJ présents — nouvel endpoint (mode `non_diarised` uniquement)

### `POST /services/jdr/sessions/{session_id}/players`

Déclare la liste des PJ présents à la session (FR-012). Sémantique PUT-like : la liste fournie **remplace intégralement** la précédente.

**Body** :

```json
{
  "pj_ids": ["uuid_pj_aragorn", "uuid_pj_galadriel"]
}
```

**Response 200** :

```json
{
  "session_id": "uuid",
  "pj_ids": ["uuid_pj_aragorn", "uuid_pj_galadriel"],
  "updated_at": "..."
}
```

**Erreurs** :
- `404 session-not-found` si la session n'existe pas ou n'appartient pas au MJ.
- `409 wrong-mode` si la session est en mode `diarised`. Detail : `"Endpoint réservé aux sessions non_diarised ; utiliser PUT /mapping pour les sessions diarised."`
- `422 invalid-player-list` si au moins un `pj_id` est inconnu ou n'appartient pas au MJ courant. Detail liste les `pj_id` fautifs.

---

### `GET /services/jdr/sessions/{session_id}/players`

Relit la liste actuelle.

**Response 200** :

```json
{
  "session_id": "uuid",
  "pj_ids": ["uuid_pj_aragorn", "uuid_pj_galadriel"],
  "updated_at": "..."
}
```

**Erreurs** :
- `404 session-not-found`
- `409 wrong-mode` si session `diarised`

---

## 3. Transcription chunked — nouvel endpoint (mode `non_diarised` uniquement)

### `GET /services/jdr/sessions/{session_id}/chunks`

Liste les chunks de transcription de la session, ordonnés par `ordre`.

**Response 200** :

```json
{
  "session_id": "uuid",
  "items": [
    { "chunk_id": "uuid_1", "ordre": 0, "text": "..." },
    { "chunk_id": "uuid_2", "ordre": 1, "text": "..." },
    { "chunk_id": "uuid_3", "ordre": 2, "text": "..." }
  ]
}
```

Le champ `summary_text` est **délibérément non exposé** dans cet endpoint — il est interne au pipeline LLM, le MJ accède au résumé global via `/artifacts/summary`. Décision §5 de `research.md`.

**Erreurs** :
- `404 session-not-found`
- `404 transcription-not-ready` si la session est `non_diarised` mais le job de transcription n'a pas encore tourné (aucun chunk en DB).
- `409 wrong-mode` si la session est `diarised`. Detail : `"Endpoint réservé aux sessions non_diarised ; utiliser GET /transcription pour les sessions diarised."`

---

## 4. Endpoints `transcription` et `transcription.md` (existants, comportement étendu)

### `GET /services/jdr/sessions/{session_id}/transcription`
### `GET /services/jdr/sessions/{session_id}/transcription.md`

**Comportement Jalon 5** inchangé sur sessions `diarised`.

**Nouvelle erreur** sur sessions `non_diarised` :
- `409 wrong-mode`. Detail : `"Cette session est en mode non_diarised. Utiliser GET /chunks pour la transcription chunked."`

---

## 5. Endpoint `mapping` (existant, restreint au mode `diarised`)

### `PUT /services/jdr/sessions/{session_id}/mapping`
### `GET /services/jdr/sessions/{session_id}/mapping`

**Comportement Jalon 5** inchangé sur sessions `diarised`.

**Nouvelle erreur** sur sessions `non_diarised` :
- `409 wrong-mode`. Detail : `"Cette session est en mode non_diarised. Utiliser POST /players pour déclarer les PJ présents."`

---

## 6. Résumé global de session — nouvel endpoint (mode `non_diarised` uniquement)

### `POST /services/jdr/sessions/{session_id}/artifacts/summary`

Enqueue le job map-reduce de résumé global (FR-006, FR-007). Effet de bord : à chaque appel, les `chunks.summary_text` de la session sont remis à NULL et les artefacts dérivés (`narrative`, `elements`, `pov:*`) sont supprimés en cascade (FR-011), dans la même transaction.

**Response 202** :

```json
{
  "id": "rq-job-id",
  "kind": "summary",
  "session_id": "uuid",
  "status": "queued",
  "queued_at": "..."
}
```

**Erreurs** :
- `404 session-not-found`
- `409 wrong-mode` si session `diarised` (au Jalon courant, le map-reduce sur diarised est hors scope).
- `409 session-not-transcribed` si state ≠ `transcribed`.
- `409 no-chunks` si state = `transcribed` mais aucun chunk en DB (cas dégénéré : audio vide).

### `GET /services/jdr/sessions/{session_id}/artifacts/summary`

Lit l'artefact persisté.

**Response 200** :

```json
{
  "session_id": "uuid",
  "text": "<résumé global consolidé>",
  "model_used": "deepinfra:meta-llama/Meta-Llama-3.1-70B-Instruct",
  "generated_at": "..."
}
```

**Erreurs** :
- `404 session-not-found`
- `404 artifact-not-ready` si le résumé n'a pas (encore) été généré.
- `409 wrong-mode` si session `diarised`.

### `GET /services/jdr/sessions/{session_id}/artifacts/summary.md`

Rend le résumé global en Markdown avec l'en-tête de session standard (cf. `render_session_header` du Jalon 5). `Content-Type: text/markdown; charset=utf-8`.

**Erreurs** : mêmes que la version JSON.

---

## 7. Endpoints `narrative` / `elements` / `povs` (existants, contrat HTTP inchangé)

### `POST /services/jdr/sessions/{session_id}/artifacts/narrative`
### `POST /services/jdr/sessions/{session_id}/artifacts/elements`
### `POST /services/jdr/sessions/{session_id}/artifacts/povs`

**Contrat HTTP rigoureusement identique au Jalon 5**. La structure du body, de la response 202 (`JobQueuedOut`), du JSON et du Markdown sur les GET est inchangée.

**Comportement interne** :
- En mode `diarised` : pipeline Jalon 5 inchangé, segments diarisés consommés.
- En mode `non_diarised` : le job lit `chunks.summary_text` ordonnés (FR-009) au lieu des segments. Le format de sortie reste identique du point de vue du client.

**Nouvelle erreur** spécifique au mode `non_diarised` :
- `409 no-summary` si la session est `non_diarised` mais `chunks.summary_text` sont NULL (le job `summary` n'a pas (encore) tourné). Detail : `"Aucun résumé global disponible. POST /artifacts/summary d'abord."`

Les erreurs existantes du Jalon 5 (`session-not-transcribed`, `no-mapping` côté povs) restent valides en mode `diarised`. En mode `non_diarised`, `no-mapping` est remplacé par `no-summary` (FR-010).

---

## 8. Endpoints `/me/*` (joueur)

**Inchangés**. Toujours utilisables uniquement sur les sessions `diarised` au jalon courant (cf. `spec.md §Assumptions` et `data-model.md §7`).

Si un joueur fait `GET /me/sessions/{id}/{narrative|pov}[.md]` sur une session `non_diarised` :
- `409 wrong-mode`. Detail : `"Cette session est en mode non_diarised. L'accès joueur aux résumés non_diarised n'est pas disponible dans cette version."`

---

## 9. Tableau récapitulatif des nouveaux codes d'erreur

| `type` URI suffix | HTTP | Quand |
|---|---|---|
| `errors/invalid-transcription-mode` | 422 | Valeur de `transcription_mode` à la création hors enum |
| `errors/immutable-field` | 422 | Tentative de `PATCH` sur `transcription_mode` |
| `errors/wrong-mode` | 409 | Endpoint appelé sur un mode incompatible (mapping sur non_diarised, /players sur diarised, /chunks sur diarised, /transcription sur non_diarised, /summary sur diarised, /me/* sur non_diarised) |
| `errors/invalid-player-list` | 422 | `POST /players` avec un `pj_id` inconnu ou non-owned |
| `errors/no-chunks` | 409 | `POST /artifacts/summary` sur une session `non_diarised` sans chunks en DB |
| `errors/no-summary` | 409 | `POST /artifacts/{narrative\|elements\|povs}` sur `non_diarised` sans summary généré |

Codes d'erreur existants du Jalon 5 (`session-not-found`, `session-not-transcribed`, `no-mapping`, `artifact-not-ready`, `transcription-not-ready`, etc.) restent valides et sont réutilisés tels quels.

---

## 10. Compatibilité ascendante

Garanties pour les clients du Jalon 5 :

- Un client qui ignore `transcription_mode` dans le payload `POST /sessions` continue de marcher (défaut `diarised`).
- Un client qui ignore `transcription_mode` dans la response `GET /sessions/{id}` continue de marcher (champ supplémentaire, parsing JSON tolérant côté client recommandé).
- Aucun endpoint existant ne change de path, de méthode, ni de structure de body.
- Toutes les routes Jalon 5 sur une session `diarised` retournent strictement les mêmes statuts et bodies qu'au Jalon 5.

Pas de breaking change pour les clients existants. La feature est **additive uniquement**.
