# Quickstart — Validation Epic 8 : Artefacts éditables

Procédure de validation manuelle (en complément des tests pytest). Suppose une session non_diarised `transcribed` avec résumé + éléments générés, et un compte MJ (clé API) propriétaire.

## Pré-requis

```bash
# Appliquer la migration
.venv/Scripts/python.exe -m alembic upgrade head      # applique 0019 (provenance) puis 0020 (flatten éléments)

# Lancer l'API
docker compose up   # ou: uvicorn app.main:app --reload
```

Variables : `BASE=http://localhost:8000/services/jdr`, `H="-H 'Authorization: Bearer <clé MJ>'"`, `SID=<session uuid>`.

## BD-23 — Édition synchrone

```bash
# Éditer le résumé (réponse immédiate, 200, pas de job)
curl -X PATCH "$BASE/sessions/$SID/artifacts/summary" $H \
  -H 'Content-Type: application/json' -d '{"text":"# Résumé corrigé\n..."}'

# Relire : le texte corrigé revient tel quel, is_edited=true, edited_at renseigné
curl "$BASE/sessions/$SID/artifacts/summary" $H

# Artefact inexistant → 404/422 ; rôle non-MJ → 403 ; texte vide → 422
```

## BD-26 — Éléments free-form

```bash
# Lire les éléments : structure plate, chaque item a une category (PNJ/Lieux/Objets/Indices)
curl "$BASE/sessions/$SID/artifacts/elements" $H

# Remplacer la carte avec une catégorie libre + description longue
curl -X PUT "$BASE/sessions/$SID/artifacts/elements" $H \
  -H 'Content-Type: application/json' \
  -d '{"elements":[{"category":"Factions","name":"La Main Noire","description":"<description de plus de 25 mots ...>"}]}'

# Relire : la catégorie libre et la description longue sont conservées
```

## BD-24 — Garde de régénération

```bash
# Sur un artefact is_edited=true, régénérer sans force → 409 artifact-edited
curl -X POST "$BASE/sessions/$SID/artifacts/summary" $H        # attendu: 409

# Avec force → la régénération procède ; au succès is_edited repasse à false
curl -X POST "$BASE/sessions/$SID/artifacts/summary?force=true" $H   # attendu: 202 job

# Sur un artefact non édité → comportement actuel (pas de 409)
```

## BD-25 — Texte long

```bash
# Générer ~10 000 mots et éditer ; relecture intégrale sans troncature
python - <<'PY'
import json; print(json.dumps({"text":"mot "*10000}))
PY
# → utiliser ce corps dans un PATCH summary, puis GET et vérifier la longueur
```

## BD-27 — Lectures joueur

```bash
# Avec une clé JOUEUR dont le PJ a participé à la session
curl "$BASE/me/sessions/$SID/summary" -H 'Authorization: Bearer <clé joueur>'
curl "$BASE/me/sessions/$SID/elements" -H 'Authorization: Bearer <clé joueur>'

# Session non jouée par le PJ → 403/404 (aucune fuite inter-sessions)
```

## Definition of Done (rappel CLAUDE.md §7)

- [ ] `ruff check .` clean
- [ ] `pytest` vert (édition, provenance, flatten, migration, lectures joueur)
- [ ] `alembic upgrade head` + `downgrade` OK
- [ ] `docker compose up` démarre sans crash
- [ ] `curl` manuels ci-dessus conformes
- [ ] `docs/context/api/openapi.json` régénéré
- [ ] README + `docs/journal.md` mis à jour ; ADR si décision notable
- [ ] Issues BD-23→27 fermées via la PR
