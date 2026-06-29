# Research — Epic 8 : Artefacts éditables

Décisions techniques résolvant les zones d'incertitude du plan. Tout est ancré dans une lecture du code existant (`app/services/jdr/`), pas dans les suppositions de l'ADR frontend.

## §1 — Stockage réel des artefacts (impacte BD-23, BD-25, BD-26)

**Constat (vérifié).** Une seule table `jdr_artifacts` (`db/models.py:483`) :
- PK composite `(session_id, kind)` ; `kind ∈ {"narrative", "elements", "summary", "pov:<uuid>"}` (`String(80)`).
- Contenu dans **`content_json` (colonne `JSON`)**, pas de colonne texte typée.
- `model_used` (`String(255)`) + `generated_at` (`DateTime tz`).
- Sémantique UPSERT via `ArtifactRepository.upsert` (`repositories.py:639`) : une régénération écrase `content_json`/`model_used`/`generated_at`.
- Les artefacts texte sérialisent `content_json = {"text": "<markdown>"}` (router `get_summary`/`get_narrative`/`get_pov` lisent `content_json.get("text","")`).

**Décision.** Les endpoints d'édition (BD-23) écrivent dans `content_json` via une nouvelle méthode `ArtifactRepository.update_content(session_id, kind, content_json)` qui **exige une ligne existante** (sinon retourne `None` → 404/422 côté router, FR-003) et **ne touche pas** `model_used`/`generated_at`.

**Rationale.** Réutilise la table et l'index PK existants ; aucune nouvelle entité de stockage (YAGNI). L'édition est un `UPDATE` indexé → écriture synchrone naturelle (DP-1).

## §2 — BD-25 : colonnes texte non bornées

**Constat.** Le texte des artefacts n'est jamais dans un `VARCHAR(n)` : il est imbriqué dans `content_json` (`JSON`). En SQLite le type `JSON` est stocké en `TEXT` (non borné) ; en Postgres `JSON`/`JSONB` est non borné.

**Décision.** **Aucune migration `VARCHAR→TEXT`.** BD-25 se réduit à : (a) confirmer qu'aucun chemin n'introduit de cap, (b) un test de non-régression round-trip d'un texte ~10 000 mots (FR-010 / SC-004). On documente explicitement que l'hypothèse de l'ADR (« colonne `text` possiblement bornée ») ne s'applique pas à ce backend.

**Alternatives écartées.** Ajouter une colonne `text` dédiée par artefact : refusé (réécriture inutile du modèle, casse l'UPSERT générique, viole YAGNI).

## §3 — BD-26 : éléments free-form (Option B, décidée)

**Constat.** `ElementsArtifactOut` (`schemas.py:529`) expose 4 listes parallèles `npcs/locations/items/clues`, chacune de `Element{name, description}`. Le `content_json` d'un artefact `kind='elements'` a la forme `{"npcs":[...], "locations":[...], "items":[...], "clues":[...]}`.

**Décision.**
1. **Schéma** : `Element` devient `{category: str, name: str, description: str}` ; `ElementsArtifactOut` devient `{session_id, elements: list[Element], model_used, generated_at, + provenance}`.
2. **Flatten génération** : le job de génération continue de produire 4 buckets ; un helper `logic.flatten_elements(buckets) -> list[Element]` les aplatit avec la correspondance fixe `npcs→"PNJ"`, `locations→"Lieux"`, `items→"Objets"`, `clues→"Indices"` (FR-012).
3. **Migration de données** (et non DDL) : la migration `0019` réécrit le `content_json` de chaque ligne `kind='elements'` du même format buckets vers `{"elements":[{category,name,description}, ...]}` via la même correspondance, sans perte (FR-013 / SC-006). Downgrade : regroupe par `category` connue vers buckets, catégories inconnues → bucket de repli documenté (best-effort, perte de catégories libres assumée au downgrade).
4. **Validation** : `description` non bornée fonctionnellement (FR-014) ; borne de garde généreuse (ex. 2000 caractères) au niveau Pydantic pour éviter un abus, pas la limite « 25 mots » qui reste une consigne de génération (DP-5). `category`/`name` non vides (trim).

**Rationale.** Une liste plate taggée est plus simple que 4 tableaux parallèles, colle au modèle mental « catégorie arbitraire », et l'aplatissement isole la génération LLM (toujours 4 buckets) du contrat public (catégories libres). Rupture de contrat typé assumée (clients régénèrent leurs types).

## §4 — BD-24 : provenance + garde de régénération

**Constat.** Deux chemins de régénération destructifs :
- Chaque `POST .../artifacts/{narrative|elements|povs}` ré-enfile un job qui réécrit l'artefact.
- `POST .../artifacts/summary` (`router.py:1714`) est **doublement destructif** : il reset `chunks.summary_text` et **cascade-delete** les artefacts narrative/elements/pov (documenté FR-011 du service).

**Décision.**
1. **Colonnes** (migration `0019`, DDL) : `is_edited: bool` (default `false`, not null), `edited_at: datetime|null`, `edited_by: str|null`.
2. **Pose de provenance** : seuls les endpoints d'édition (BD-23) posent `is_edited=true`, `edited_at=now`, `edited_by=<gm>`. L'UPSERT de régénération **réinitialise** `is_edited=false`, `edited_at=null` (un contenu fraîchement (re)généré n'est plus « édité »).
3. **Garde `?force`** : chaque `POST` de (re)génération vérifie, **avant d'enfiler le job**, si l'artefact cible (ou, pour `summary`, un artefact aval `is_edited`) existe et est `is_edited`. Si oui et `force` absent → `409 artifact-edited` (FR-007). Avec `?force=true` → procède (FR-008). Artefact non édité → comportement actuel inchangé.
4. **Non-destructif tant que pas de succès** (FR-009, alignement Story 7.4) : la suppression/écrasement des artefacts édités ne survient qu'au succès du job, jamais en pré-suppression. Le code Story 7.4 (déjà sur la branche de base epic-7) sert de référence pour la même sémantique de write.

**Rationale.** La garde isole un investissement manuel d'un écrasement IA, sans bloquer le flux normal (artefacts non édités). Reset de provenance à la régénération : un contenu IA n'est pas « édité main », sinon `is_edited` deviendrait collant et fausserait l'UI.

**Point d'attention (à confirmer en tasks).** Périmètre exact de la garde du `summary` cascade : MVP = bloquer si **n'importe quel** artefact aval (`narrative`/`elements`/`pov:*`) est `is_edited` ; le `?force` lève la garde pour toute la cascade. Documenté ici comme décision par défaut.

## §5 — BD-27 : lectures joueur

**Constat.** Le pattern `/me` existe (`router.py:2113+`) : `GET /me`, `/me/sessions`, `/me/sessions/{id}/narrative(.md)`, `/me/sessions/{id}/pov(.md)`. L'autorisation joueur (PJ lié au compte ayant participé à la session) est donc déjà implémentée et réutilisable — **BD-12 / Story 4.16 satisfaite** (hypothèse de la spec confirmée).

**Décision.** Ajouter `GET /me/sessions/{id}/summary(.md)` et `GET /me/sessions/{id}/elements(.md)` en miroir strict des lectures `narrative`/`pov` existantes : même dépendance d'autorisation, mêmes schémas de projection (`SummaryArtifactOut`, `ElementsArtifactOut` post-BD-26). Le POV joueur reste inchangé (le sien uniquement). Refus si le PJ n'a pas participé (FR-016 / SC-007).

**Rationale.** Réutilisation maximale ; aucune nouvelle logique d'authz. Risque de fuite inter-sessions couvert par un test dédié.

## §6 — Concurrence d'édition

**Décision.** « Dernier écrivain gagne » (UPDATE atomique sur PK), pas de verrouillage optimiste (pas de colonne `version`/ETag). Hors périmètre des issues.

**Rationale.** Usage personnel mono-MJ ; le coût d'un contrôle de concurrence optimiste (ETag/If-Match + 412) n'est pas justifié (YAGNI). Réévaluable à la Rule of Three si un usage multi-MJ apparaît.

## Résumé des écarts ADR ↔ backend réel

| Sujet | Hypothèse ADR (frontend) | Réalité backend | Conséquence |
|---|---|---|---|
| BD-25 | colonne `text` peut-être `VARCHAR(n)` à migrer | texte en `content_json` (JSON, non borné) | migration supprimée, simple vérif + test |
| BD-26 | migration de modèle | migration de **données JSON**, pas DDL | plus léger ; downgrade best-effort |
| BD-24 | ajouter colonnes provenance | OK + garde doit couvrir le **cascade-delete** du `summary` | garde plus large que par-artefact |
