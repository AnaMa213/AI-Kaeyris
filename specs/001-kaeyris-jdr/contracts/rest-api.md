# Contract REST API — kaeyris-jdr

**Spec** : [`../spec.md`](../spec.md) · **Plan** : [`../plan.md`](../plan.md) · **Data model** : [`../data-model.md`](../data-model.md)

> Tous les endpoints exigent une **clé API Bearer** (FR-012). Le rôle requis est indiqué pour chaque endpoint. Les erreurs suivent le format Problem Details RFC 9457 (cf. ADR 0002 du projet). Les schémas JSON ci-dessous sont indicatifs (Pydantic v2 sera la source de vérité dans `app/services/jdr/schemas.py`).

---

## Conventions

- Préfixe global : `/services/jdr`
- Codes : `200` lecture, `201` création, `202` accepté pour traitement asynchrone, `400` validation, `401` non authentifié, `403` rôle insuffisant ou hors périmètre, `404` introuvable, `409` état incompatible, `422` corps invalide, `429` rate-limited, `501` non implémenté (live).
- En-tête `Authorization: Bearer <token>` requis sauf mention contraire.
- Réponse Markdown : Content-Type `text/markdown; charset=utf-8`.

---

## Auth & gestion des PJ / joueurs (rôle `gm`)

### `POST /services/jdr/pjs`

Crée un PJ stable rattaché au MJ courant.

```json
// Request
{ "name": "Galadriel" }
// Response 201
{ "id": "uuid", "name": "Galadriel", "created_at": "2026-05-04T10:00:00Z" }
```

### `GET /services/jdr/pjs`

Liste les PJ du MJ.

```json
// Response 200
{ "items": [ { "id": "uuid", "name": "Galadriel", "created_at": "..." } ] }
```

### `POST /services/jdr/players`

Enrôle un joueur en générant une nouvelle clé API liée à un PJ. **Le token en clair n'est renvoyé qu'une seule fois** (FR-014).

```json
// Request
{ "name": "joueur-aragorn", "pj_id": "uuid" }
// Response 201
{
  "id": "uuid",
  "name": "joueur-aragorn",
  "pj_id": "uuid",
  "role": "player",
  "token": "kjdr_3f9a1b...",   // visible une seule fois
  "created_at": "..."
}
```

### `DELETE /services/jdr/players/{player_id}`

Révoque la clé d'un joueur. Le token cesse immédiatement d'être valide.

```http
HTTP/1.1 204 No Content
```

---

## Cycle de vie d'une session (rôle `gm`)

### `POST /services/jdr/sessions`

Crée une session vide (sans audio encore).

```json
// Request
{
  "title": "Donjon des morts-vivants — chapitre 4",
  "recorded_at": "2026-05-03T20:00:00Z"
}
// Response 201
{
  "id": "uuid",
  "title": "...",
  "recorded_at": "...",
  "mode": "batch",
  "state": "created",
  "created_at": "..."
}
```

### `POST /services/jdr/sessions/{session_id}/audio`

Upload du M4A. Le service :
1. Stocke le fichier sur le volume.
2. Calcule sha256, durée.
3. Enqueue un job `transcription` (RQ).
4. Renvoie `202 Accepted` avec `job_id` à poller.

Format : `multipart/form-data` avec champ `file`.

```json
// Response 202
{
  "session_id": "uuid",
  "job_id": "rq:abcd1234",
  "audio": {
    "sha256": "...",
    "size_bytes": 134217728,
    "duration_seconds": 9012
  }
}
```

**Erreurs** :
- `400` si format non-M4A ou intégrité KO (FR-017).
- `409` si la session a déjà un audio uploadé.

### `GET /services/jdr/sessions/{session_id}`

Détail d'une session.

```json
// Response 200
{
  "id": "uuid",
  "title": "...",
  "recorded_at": "...",
  "mode": "batch",
  "state": "transcribed",
  "audio": { "uploaded_at": "...", "purged_at": "..." | null },
  "available_artifacts": ["narrative", "elements", "pov:<pj_id1>", "pov:<pj_id2>"]
}
```

### `GET /services/jdr/sessions`

Liste les sessions du MJ.

```json
// Response 200
{ "items": [ { "id": "...", "title": "...", "recorded_at": "...", "state": "..." } ] }
```

---

## Mapping locuteur ↔ PJ (rôle `gm`)

### `PUT /services/jdr/sessions/{session_id}/mapping`

Remplace ou crée le mapping pour la session. Saisie manuelle a posteriori (cf. Q2 du spec).

```json
// Request
{
  "mapping": {
    "speaker_1": "pj_uuid_galadriel",
    "speaker_2": "pj_uuid_aragorn",
    "speaker_3": "pj_uuid_legolas"
  }
}
// Response 200
{ "session_id": "uuid", "mapping": { ... }, "updated_at": "..." }
```

**Erreurs** :
- `409` si la session n'est pas encore en `state = transcribed`.
- `422` si un `pj_uuid` est inconnu ou n'appartient pas au MJ.

> **Side effect** : si des artefacts `pov:<pj_id>` existent déjà, ils sont invalidés (supprimés). Une nouvelle génération sera nécessaire.

### `GET /services/jdr/sessions/{session_id}/mapping`

```json
// Response 200
{ "session_id": "uuid", "mapping": { "speaker_1": "...", ... }, "updated_at": "..." }
```

---

## Production des artefacts (rôle `gm`)

Tous ces endpoints **enqueuent un job RQ** et renvoient `202 Accepted` + `job_id`. Polling via `GET /jobs/{job_id}` (cf. plus bas).

### `POST /services/jdr/sessions/{session_id}/artifacts/narrative`

Enqueue la génération du résumé narratif.

```json
// Response 202
{ "session_id": "uuid", "job_id": "...", "kind": "narrative" }
```

**Erreurs** :
- `409` si la session n'est pas en `state = transcribed`.

### `POST /services/jdr/sessions/{session_id}/artifacts/elements`

Idem pour la fiche PNJ/lieux/items/indices.

### `POST /services/jdr/sessions/{session_id}/artifacts/povs`

Idem pour les POV. Enqueue **un job par PJ mappé** (un seul `job_id` parent renvoyé, dont le statut agrège ses enfants — implémentation via RQ `Group` ou simple loop côté worker).

**Erreurs** :
- `409` si pas de mapping (FR-011) — message d'erreur explicite indiquant l'étape manquante.

### `GET /services/jdr/sessions/{session_id}/artifacts/narrative`

Renvoie le résumé narratif courant en JSON.

```json
// Response 200
{
  "session_id": "uuid",
  "kind": "narrative",
  "content": { "text": "..." },
  "model_used": "deepinfra:meta-llama/Meta-Llama-3.1-8B-Instruct",
  "generated_at": "..."
}
```

**Erreurs** : `404` si pas encore généré.

### `GET /services/jdr/sessions/{session_id}/artifacts/narrative.md`

Idem en Markdown (cf. R6).

```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8

# Session — Donjon des morts-vivants — chapitre 4 (2026-05-03)

## Résumé narratif

Texte du résumé...

---
Généré par kaeyris-jdr le 2026-05-04T10:23:11Z (modèle: deepinfra:Meta-Llama-3.1-8B-Instruct).
```

### `GET /services/jdr/sessions/{session_id}/artifacts/elements[.md]`

Renvoie la fiche d'éléments. JSON suit la structure `data-model.md` §7. Markdown : 4 sections h2.

### `GET /services/jdr/sessions/{session_id}/artifacts/povs/{pj_id}[.md]`

Renvoie le POV pour un PJ donné.

### `GET /services/jdr/sessions/{session_id}/transcription[.md]`

Renvoie la transcription diarisée.

```json
// Response 200 (JSON)
{
  "session_id": "uuid",
  "language": "fr",
  "model_used": "openai:whisper-large-v3",
  "provider": "cloud",
  "completed_at": "...",
  "segments": [
    { "speaker_label": "speaker_1", "start_seconds": 12.34, "end_seconds": 18.91, "text": "..." },
    ...
  ]
}
```

Format Markdown : un paragraphe par tour, préfixe `**[speaker_1 → Galadriel]**` si mapping présent.

---

## Statut de jobs (rôle `gm`)

### `GET /services/jdr/jobs/{job_id}`

```json
// Response 200
{
  "job_id": "rq:abcd1234",
  "kind": "transcription",
  "session_id": "uuid",
  "status": "running",
  "failure_reason": null,
  "queued_at": "...",
  "started_at": "...",
  "ended_at": null
}
```

**Erreurs** : `404` si `job_id` inconnu (TTL RQ expiré : 24h sur succès, 7j sur échec — cf. ADR 0004 §3 ; au-delà, la projection `jobs` reste consultable si elle existe).

---

## Endpoints joueur (rôle `player`)

### `GET /services/jdr/me`

Profil du joueur courant.

```json
// Response 200
{
  "name": "joueur-aragorn",
  "pj": { "id": "uuid", "name": "Aragorn" }
}
```

### `GET /services/jdr/me/sessions`

Liste des sessions où mon PJ est mappé. Aucune autre session n'est visible.

```json
// Response 200
{ "items": [ { "session_id": "...", "title": "...", "recorded_at": "..." } ] }
```

### `GET /services/jdr/me/sessions/{session_id}/narrative[.md]`

Résumé narratif global de la session (en lecture, FR-014).

**Erreurs** :
- `403` si mon PJ n'est pas mappé sur cette session.
- `404` si pas encore généré.

### `GET /services/jdr/me/sessions/{session_id}/pov[.md]`

Mon résumé POV uniquement.

**Erreurs** :
- `403` si pas mappé sur cette session.
- `404` si pas généré.

> Toute tentative d'accéder à `…/me/sessions/{x}/pov` où le mapping ne contient pas mon PJ renvoie `403` (FR-014).
> Aucun endpoint joueur ne donne accès à un POV qui ne m'appartient pas.

---

## Mode live (stub — Jalon 5)

### `POST /services/jdr/live/sessions` — `501 Not Implemented`

```json
// Response 501 (RFC 9457 Problem Details)
{
  "type": "https://kaeyris-jdr.local/errors/live-not-implemented",
  "title": "Live mode not implemented",
  "status": 501,
  "detail": "The live ingestion endpoint contract is published but no implementation is delivered at Jalon 5. See documentation.",
  "documentation_url": "https://kaeyris-jdr.local/docs#live"
}
```

### `WS /services/jdr/live/stream` — fermé immédiatement

À la connexion, le serveur émet une frame de fermeture WS `1011 Internal Error` avec raison `"stub — not yet implemented at Jalon 5"`. Le **schéma des messages futurs** (events `audio.chunk`, `session.end`, `error`) est **documenté en commentaires** dans `app/services/jdr/live/router.py` mais aucun routeur ne les traite. Cette documentation alimente l'OpenAPI (description du WebSocket).

---

## Erreurs (Problem Details, extrait)

| Type URI suffix | HTTP | Quand |
|---|---|---|
| `errors/unauthorized` | 401 | Bearer absent / invalide. |
| `errors/forbidden` | 403 | Rôle insuffisant ou périmètre violé. |
| `errors/not-found` | 404 | Ressource inexistante. |
| `errors/conflict` | 409 | État incompatible (ex : POV demandé sans mapping). |
| `errors/invalid-input` | 422 | Body Pydantic invalide. |
| `errors/rate-limited` | 429 | RFC 9110 §10.2.3 Retry-After (cf. ADR 0004). |
| `errors/live-not-implemented` | 501 | Endpoints du mode live (FR-016). |
