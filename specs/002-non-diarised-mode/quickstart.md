# Quickstart: Mode `non_diarised` — scénario E2E

**Phase 1 du `/speckit-plan`**. Décrit le parcours utilisateur complet sur une session en mode `non_diarised`, depuis la création jusqu'aux artefacts dérivés. Sert de référence pour la validation manuelle finale (équivalent T076 du Jalon 5).

> Pré-requis : Jalon 5 livré (service `kaeyris-jdr` fonctionnel), feature `002-non-diarised-mode` implémentée, `alembic upgrade head` exécuté.

---

## 0. Setup local

```powershell
# 1) DB à jour avec la migration 0002
alembic upgrade head

# 2) Avoir une clé MJ active (cf. quickstart Jalon 5 §0 si pas déjà fait)
$gmToken = "<token plaintext d'une clé gm active>"

# 3) Lancer l'API + un worker
uvicorn app.main:app --reload
# Dans un autre terminal :
rq worker default --url $env:REDIS_URL

# 4) Variable d'environnement spécifique à la feature (optionnelle)
$env:KAEYRIS_CHUNK_MAX_CHARS = "30000"   # default si absent
```

---

## 1. Créer une session en mode `non_diarised`

```powershell
$session = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -ContentType "application/json" `
  -Body (@{
      title = "Session test non_diarised"
      recorded_at = (Get-Date).ToString("o")
      transcription_mode = "non_diarised"
  } | ConvertTo-Json)

$session.transcription_mode   # → "non_diarised"
$sessionId = $session.id
```

> Sans le champ `transcription_mode`, la session est créée en `diarised` (défaut, comportement Jalon 5).

---

## 2. Uploader l'audio (inchangé Jalon 5)

```powershell
$audio = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/audio" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -Form @{ file = Get-Item "C:\path\to\session.m4a" }

$jobId = $audio.job_id
```

Le pipeline de transcription se forke automatiquement selon `session.transcription_mode` :
- En `non_diarised` : le worker écrit dans `jdr_chunks` (au lieu de `jdr_transcriptions`).

Polling jusqu'à `succeeded` :

```powershell
do {
  Start-Sleep -Seconds 2
  $job = Invoke-RestMethod -Method GET `
    -Uri "http://localhost:8000/services/jdr/jobs/$jobId" `
    -Headers @{ Authorization = "Bearer $gmToken" }
  $job.status
} while ($job.status -notin @("succeeded", "failed"))
```

---

## 3. Inspecter la transcription chunked

```powershell
$chunks = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/chunks" `
  -Headers @{ Authorization = "Bearer $gmToken" }

$chunks.items.Count         # → nombre de chunks (1 pour une session courte, plusieurs pour 2h+)
$chunks.items[0].text       # → contenu textuel du premier chunk
$chunks.items[0].ordre      # → 0
```

> `summary_text` n'est délibérément pas exposé par cet endpoint (interne au pipeline LLM).

---

## 4. Déclarer les PJ présents à la session

Pré-requis : avoir au moins 2 PJ enregistrés via `POST /pjs` (cf. quickstart Jalon 5). Soit `pj_aragorn_id` et `pj_galadriel_id` deux UUID issus de `GET /pjs`.

```powershell
$players = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/players" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -ContentType "application/json" `
  -Body (@{ pj_ids = @($pj_aragorn_id, $pj_galadriel_id) } | ConvertTo-Json)

$players.pj_ids   # → [aragorn, galadriel]
```

> Cet endpoint remplace `/mapping` du Jalon 5 pour les sessions `non_diarised`. Réservé à ce mode (409 sinon).

---

## 5. Générer le résumé global (map-reduce)

```powershell
$summaryJob = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/summary" `
  -Headers @{ Authorization = "Bearer $gmToken" }

# Poll jusqu'à succès (~5 min pour une session 60k chars)
do {
  Start-Sleep -Seconds 5
  $job = Invoke-RestMethod -Method GET `
    -Uri "http://localhost:8000/services/jdr/jobs/$($summaryJob.id)" `
    -Headers @{ Authorization = "Bearer $gmToken" }
  $job.status
} while ($job.status -notin @("succeeded", "failed"))

# Récupérer le résumé global
$summary = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/summary" `
  -Headers @{ Authorization = "Bearer $gmToken" }

$summary.text         # → résumé global consolidé
$summary.model_used   # → "deepinfra:..."

# En Markdown
$md = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/summary.md" `
  -Headers @{ Authorization = "Bearer $gmToken" }
$md   # texte Markdown avec en-tête de session standard
```

Sous le capot : N appels LLM map (un par chunk) + 1 appel reduce (skippé si N=1). Les `chunks.summary_text` sont peuplés au passage et réutilisables par les jobs dérivés.

---

## 6. Générer les artefacts dérivés (narrative, elements, povs)

Le **contrat HTTP est identique au Jalon 5** — seul l'algorithme interne change selon le mode.

### Narrative

```powershell
$narrJob = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/narrative" `
  -Headers @{ Authorization = "Bearer $gmToken" }
# poll → succeeded

$narr = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/narrative" `
  -Headers @{ Authorization = "Bearer $gmToken" }
$narr.text   # récit chronologique, prose, devine qui parle depuis le contexte
```

### Elements

```powershell
$elJob = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/elements" `
  -Headers @{ Authorization = "Bearer $gmToken" }
# poll → succeeded

$el = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/elements" `
  -Headers @{ Authorization = "Bearer $gmToken" }
$el.npcs        # liste de PNJ
$el.locations   # liste de lieux
$el.items       # liste d'items
$el.clues       # liste d'indices
```

### POVs

```powershell
$povsJob = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/povs" `
  -Headers @{ Authorization = "Bearer $gmToken" }
# poll → succeeded (un appel LLM par PJ déclaré au §4)

# POV d'Aragorn
$pov = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/povs/$pj_aragorn_id" `
  -Headers @{ Authorization = "Bearer $gmToken" }
$pov.text   # POV scoppé sur Aragorn, le LLM a "deviné" qu'il agissait depuis le contexte
```

> Tous ces jobs lisent `chunks.summary_text` (rempli au §5) — pas de nouveau map LLM. Coût LLM ≈ 1 appel par artefact, pas N+1.

---

## 7. Régénérer le résumé global (cascade invalidation)

Si le MJ veut affiner (changer le `campaign_context`, ou simplement re-shooter) :

```powershell
# Re-POST → reset des chunks.summary_text + DELETE cascade des artefacts dérivés, atomique
Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/summary" `
  -Headers @{ Authorization = "Bearer $gmToken" }

# Tout artefact dérivé est désormais 404
$el = Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/elements" `
  -Headers @{ Authorization = "Bearer $gmToken" }
# → 404 artifact-not-ready : à régénérer
```

Pas de notification temps réel pour les joueurs — au prochain `GET /me/...` ils verront simplement un 404. C'est cohérent avec le pattern d'invalidation `pov:*` du Jalon 5.

---

## 8. Erreurs typiques (validation cross-mode)

```powershell
# Tentative de modifier le mode après création : 422 immutable-field
Invoke-RestMethod -Method PATCH `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -ContentType "application/json" `
  -Body '{"transcription_mode": "diarised"}'

# Tentative d'utiliser /mapping sur une session non_diarised : 409 wrong-mode
Invoke-RestMethod -Method PUT `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/mapping" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -ContentType "application/json" `
  -Body '{"mapping": {"speaker_1": "..."}}'

# Tentative de POST /artifacts/narrative sans avoir généré summary d'abord : 409 no-summary
Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions/$sessionId/artifacts/narrative" `
  -Headers @{ Authorization = "Bearer $gmToken" }
```

---

## 9. Comparaison avec une session `diarised` (non-régression)

Sur une session créée sans `transcription_mode` (donc en mode `diarised`), **toutes** les routes du Jalon 5 fonctionnent strictement comme avant. À titre de smoke test post-livraison :

```powershell
$sessionDiarised = Invoke-RestMethod -Method POST `
  -Uri "http://localhost:8000/services/jdr/sessions" `
  -Headers @{ Authorization = "Bearer $gmToken" } `
  -ContentType "application/json" `
  -Body '{"title":"Session diarised", "recorded_at":"2026-05-18T20:00:00Z"}'

$sessionDiarised.transcription_mode   # → "diarised"

# Uploader audio, mapping, narrative, elements, povs, /me/* : tout doit fonctionner
# comme au Jalon 5. La suite pytest correspondante reste verte sans modification (FR-014).

# Tentative d'utiliser /chunks sur diarised : 409 wrong-mode
Invoke-RestMethod -Method GET `
  -Uri "http://localhost:8000/services/jdr/sessions/$($sessionDiarised.id)/chunks" `
  -Headers @{ Authorization = "Bearer $gmToken" }
```

---

## 10. Synthèse

Le scénario E2E complet d'une session `non_diarised` :

1. `POST /sessions` avec `transcription_mode: "non_diarised"` → session créée
2. `POST /sessions/{id}/audio` → job de transcription qui écrit dans `jdr_chunks`
3. `GET /sessions/{id}/chunks` → inspecter le texte par chunks
4. `POST /sessions/{id}/players` → déclarer les PJ présents
5. `POST /artifacts/summary` → map-reduce, peuple `chunks.summary_text` + crée artefact `summary`
6. `POST /artifacts/{narrative,elements,povs}` → lisent `chunks.summary_text`, produisent les artefacts du Jalon 5

Le mode `diarised` (défaut) garde le pipeline Jalon 5 strictement inchangé.

Cette procédure est la cible de la validation manuelle finale après livraison de la feature (l'équivalent de T076 du Jalon 5).
