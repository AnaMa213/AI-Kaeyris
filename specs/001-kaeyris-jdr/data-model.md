# Phase 1 — Data Model : kaeyris-jdr

**Spec** : [`spec.md`](./spec.md)
**Plan** : [`plan.md`](./plan.md)
**Research** : [`research.md`](./research.md)

---

## Vue d'ensemble

```text
api_keys ─┐                pjs ─┐
          │                      │
          │  (1..1)               │  (1..n)
          ▼                      ▼
       gm/player            session_pj_mapping ──── speakers (locuteur)
                                  │
                                  │  (n..1)
                                  ▼
                              sessions ──── audio_sources ─── transcriptions
                                  │
                                  └─── artifacts (narrative | elements | pov:<pj_id>)
                                  └─── jobs (queued via RQ)
```

Toutes les tables sont préfixées par leur **service** : pas de `pjs` à la racine, mais bien dans le schéma logique du service `jdr` (en SQLAlchemy, on peut les nommer `jdr_pjs`, `jdr_sessions`, etc., ou utiliser un schema Postgres dédié `jdr.*`. Décision : noms `jdr_*` en SQLite, schema `jdr` en Postgres si dispo).

---

## Entités

### 1. `api_keys`

Migration : ce qui était un parsing de l'env var `API_KEYS` au Jalon 2 devient une table en DB (cf. R7 / research).

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `id` | UUID | PK | Identifiant interne. |
| `name` | str | UNIQUE NOT NULL | Nom humain ("kenan-laptop", "joueur-aragorn"). |
| `hash` | str | NOT NULL | Hash Argon2 du token Bearer en clair. Vérifié par `argon2.PasswordHasher.verify`. |
| `role` | enum | NOT NULL, in {`gm`, `player`} | Cf. FR-012. |
| `pj_id` | UUID FK→`pjs.id` | NULL | Obligatoire si `role=player`, NULL sinon. Une clé `player` non liée n'a aucun accès (FR-014a). |
| `status` | enum | NOT NULL, in {`active`, `revoked`} | Une clé révoquée est rejetée à l'auth. |
| `created_at` | datetime | NOT NULL | UTC. |
| `revoked_at` | datetime | NULL | Posé à la révocation. |

**Invariants** :
- `(role = 'player') ⇒ pj_id IS NOT NULL`
- `(role = 'gm') ⇒ pj_id IS NULL`
- Si `status = 'revoked'`, l'auth refuse la clé (FR-013).

**Transitions d'état** :
```
active ──[revoke]──▶ revoked   (transition unique, irréversible)
```

---

### 2. `pjs`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `id` | UUID | PK | Identifiant stable. |
| `name` | str | NOT NULL | Nom narratif du personnage ("Galadriel"). |
| `owner_gm_key_id` | UUID FK→`api_keys.id` | NOT NULL | Le MJ qui a créé le PJ. |
| `created_at` | datetime | NOT NULL | UTC. |

**Invariants** :
- `name` est unique par `(owner_gm_key_id)` (un même MJ ne crée pas deux PJ "Aragorn").

**Notes** :
- Le PJ est une entité **stable** (vit au-delà d'une session). Le mapping locuteur↔PJ se fait par session via `session_pj_mapping`.

---

### 3. `sessions`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `id` | UUID | PK | Identifiant de la partie. |
| `title` | str | NOT NULL | Titre humain ("Donjon des morts-vivants — chapitre 4"). |
| `recorded_at` | datetime | NOT NULL | Date de la session réelle (jdr-temps), pas de l'upload. |
| `gm_key_id` | UUID FK→`api_keys.id` | NOT NULL | Le MJ propriétaire. |
| `mode` | enum | NOT NULL, in {`batch`, `live`} | `live` réservé au mode futur (Jalon 5 : toujours `batch`). |
| `state` | enum | NOT NULL | Voir transitions ci-dessous. |
| `created_at` | datetime | NOT NULL | UTC. |
| `updated_at` | datetime | NOT NULL | UTC, auto-bumpé. |

**Transitions d'état** (`state`) :
```
created ──[upload audio OK]──▶ audio_uploaded
audio_uploaded ──[transcription queued]──▶ transcribing
transcribing ──[transcription failed]──▶ transcription_failed
transcribing ──[transcription succeeded]──▶ transcribed
transcribed ──[any artefact requested]──▶ transcribed   (état stable, multiples productions)
transcription_failed ──[retry upload]──▶ audio_uploaded
```

L'état n'avance PAS lors de la production d'un résumé / fiche / POV : ce sont des productions à la demande qui ne déforment pas l'état global.

---

### 4. `audio_sources`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `session_id` | UUID FK→`sessions.id` | PK (1-1 avec session) | Une session a au plus un audio source. |
| `path` | str | NOT NULL | Chemin sur le volume (`/data/audios/<session_id>.m4a`). |
| `sha256` | str | NOT NULL | Intégrité ; sert aussi à la dédupe d'upload. |
| `size_bytes` | int | NOT NULL | |
| `duration_seconds` | int | NULL | Calculé à l'upload (via ffprobe ou équivalent). |
| `uploaded_at` | datetime | NOT NULL | UTC. |
| `purged_at` | datetime | NULL | Posé après transcription réussie (cf. FR-004). |

**Invariants** :
- `purged_at IS NOT NULL` ⇒ le fichier sur disque a été supprimé.
- Une session ne peut pas être re-transcrite après purge sans nouvel upload (cf. Edge case "Sortie sur demande ré-exécutée" du spec).

---

### 5. `transcriptions`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `session_id` | UUID FK→`sessions.id` | PK | 1-1 avec session. |
| `segments_json` | JSON | NOT NULL | Liste de segments diarisés (cf. structure ci-dessous). |
| `language` | str(8) | NOT NULL | Code BCP-47 (`fr`, `fr-FR`). |
| `model_used` | str | NOT NULL | Identifie le backend (`openai:whisper-large-v3`, `local:faster-whisper-large-v3`). |
| `provider` | str | NOT NULL | `cloud` ou `local` ; utile pour le diagnostic. |
| `completed_at` | datetime | NOT NULL | UTC. |

**Structure d'un segment** (élément de `segments_json`) :

```json
{
  "speaker_label": "speaker_1 | speaker_2 | unknown",
  "start_seconds": 12.34,
  "end_seconds": 18.91,
  "text": "string"
}
```

Si la diarisation produit des labels génériques (`speaker_1`…), c'est sur ces labels que portera le mapping (cf. `session_pj_mapping`). Si la confiance de diarisation est trop basse pour un segment, le label est `unknown` (cf. Edge case "Diarisation incertaine").

---

### 6. `session_pj_mapping`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `session_id` | UUID FK→`sessions.id` | PK part1 | |
| `speaker_label` | str | PK part2 | Tel que rendu par la transcription (`speaker_1`). |
| `pj_id` | UUID FK→`pjs.id` | NOT NULL | Le PJ associé à ce locuteur. |
| `created_at` | datetime | NOT NULL | UTC. |
| `updated_at` | datetime | NOT NULL | UTC, auto-bumpé. |

**Invariants** :
- Un même `pj_id` peut apparaître plusieurs fois dans une session si deux locuteurs sont en réalité le même PJ (cas de figure non typique mais permis).
- Un même `speaker_label` ne peut pointer que vers un seul `pj_id` par session.
- Le mapping est éditable tant qu'aucun résumé POV n'a été produit (FR-010). Au-delà, les modifications sont autorisées mais doivent invalider les artefacts POV existants (`pov:<pj_id>`) — voir règle dans `artifacts`.

---

### 7. `artifacts`

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `session_id` | UUID FK→`sessions.id` | PK part1 | |
| `kind` | str | PK part2 | `narrative`, `elements`, `pov:<pj_id>`. |
| `content_json` | JSON | NOT NULL | Voir structures par `kind` ci-dessous. |
| `model_used` | str | NOT NULL | LLM utilisé. |
| `generated_at` | datetime | NOT NULL | UTC. Une nouvelle génération **écrase** la précédente (cf. R9). |

**Structure de `content_json` par `kind`** :

- **`kind = "narrative"`** :

  ```json
  { "text": "Texte du résumé narratif chronologique." }
  ```

- **`kind = "elements"`** :

  ```json
  {
    "npcs":      [{"name": "...", "description": "..."}],
    "locations": [{"name": "...", "description": "..."}],
    "items":     [{"name": "...", "description": "..."}],
    "clues":     [{"name": "...", "description": "..."}]
  }
  ```

  (Listes vides plutôt qu'absentes — cf. Acceptance Scenario US 2.3.)

- **`kind = "pov:<pj_id>"`** :

  ```json
  { "pj_id": "uuid", "pj_name": "Galadriel", "text": "Texte du POV." }
  ```

**Invariants** :
- `kind = "pov:<pj_id>"` ⇒ il existe un mapping `(session_id, *, pj_id)` dans `session_pj_mapping`. Sinon FR-011 impose le refus de génération.
- Une mise à jour de `session_pj_mapping` après production d'un POV invalide (`DELETE`) les rows `kind = "pov:<pj_id>"` correspondants pour cette session, forçant régénération explicite.

---

### 8. `jobs` (projection légère pour le statut)

> Note : la source de vérité reste **RQ** (`rq.job.Job.fetch(id, …)` dans Redis). Cette table est une projection optionnelle pour les requêtes croisées `session ↔ jobs`, qui simplifie aussi la gestion d'historique au-delà du TTL RQ (24h sur succès, 7j sur échec, cf. ADR 0004).

| Champ | Type | Contraintes | Notes |
|---|---|---|---|
| `id` | str | PK | `rq.Job.id`. |
| `kind` | enum | NOT NULL, in {`transcription`, `narrative`, `elements`, `povs`} | |
| `session_id` | UUID FK→`sessions.id` | NOT NULL | |
| `status` | enum | NOT NULL, in {`queued`, `running`, `succeeded`, `failed`} | Synchronisé à la fin du job par un hook on_success / on_failure. |
| `failure_reason` | text | NULL | Posé en cas de failure ; message lisible (FR-007 sur les erreurs). |
| `queued_at` | datetime | NOT NULL | |
| `started_at` | datetime | NULL | |
| `ended_at` | datetime | NULL | |

**Invariants** :
- Un job `kind=transcription` réussi déclenche : (a) UPSERT dans `transcriptions`, (b) UPDATE `audio_sources.purged_at` + suppression du fichier, (c) UPDATE `sessions.state = 'transcribed'`.
- Un job `kind ∈ {narrative, elements, povs}` ne peut être enqueué que si la session est en `state = 'transcribed'`.

---

## Vue d'ensemble des contraintes inter-tables (résumé)

1. **Auth ↔ rôle** : `api_keys.role = 'player'` ⇔ `pj_id IS NOT NULL`.
2. **Audio ↔ purge** : `transcriptions.session_id` exists ⇒ `audio_sources.purged_at IS NOT NULL` (FR-004).
3. **Mapping ↔ POV** : `artifacts.kind LIKE 'pov:%'` requires un row dans `session_pj_mapping` correspondant ; modification du mapping invalide les POV concernés.
4. **Visibilité joueur** : un joueur de `pj_id = X` voit les `sessions` où il existe un mapping `(session_id, *, pj_id=X)` ; il voit les artefacts `narrative` de ces sessions et les `pov:X` (jamais d'autres `pov:Y`).
5. **Cascade** : la suppression d'une `session` cascade vers ses `audio_sources`, `transcriptions`, `session_pj_mapping`, `artifacts`, `jobs` (mais pas `pjs` — un PJ vit au-delà d'une session).

## Migration depuis l'existant

- L'env var `API_KEYS` (Jalon 2, format `name1:hash1;name2:hash2`) reste lue par `app/core/auth.py`. Au démarrage de l'API, si la table `api_keys` est vide, **import des entrées de l'env var** avec `role='gm'` (les rôles n'existaient pas avant). Une fois la table peuplée, l'env var devient un fallback de bootstrap.
- Aucune autre donnée existante à migrer (Jalons 0-4 ne persistent rien de business).
