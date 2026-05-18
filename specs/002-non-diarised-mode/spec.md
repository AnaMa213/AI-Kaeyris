# Feature Specification: Mode `non_diarised` (pipeline alternatif sans diarisation)

**Feature Branch**: `002-non-diarised-mode`
**Created**: 2026-05-18
**Status**: Draft
**Input**: User description: "Ajouter un mode `non_diarised` optionnel sur la création de session, qui forke le pipeline sans modifier l'existant. Tag posé à la création de session (défaut = diarised, comportement Jalon 5 inchangé). Mode `non_diarised` : la transcription est stockée en chunks de X caractères dans une nouvelle table `chunks(session_id, chunk_id, ordre, text)`. Nouvel endpoint `POST /sessions/{id}/artifacts/summary` exécute un map-reduce LLM (1 résumé par chunk préservant l'ordre, puis 1 résumé global consolidé). Les endpoints `POST /artifacts/elements` et `POST /artifacts/povs` continuent de fonctionner en consommant les résumés des chunks au lieu des segments diarisés ; le prompt LLM devine qui parle à partir du contexte. Mode `diarised` : inchangé. Map-reduce sur mode diarised hors scope."

## Clarifications

### Session 2026-05-18

- Q: En mode `non_diarised`, comment le MJ déclare-t-il les PJ présents à la session pour permettre la génération `povs` ? → A: Nouvel endpoint dédié `POST /services/jdr/sessions/{id}/players` (rôle `gm`) qui accepte un payload `{"pj_ids": ["uuid1", "uuid2", ...]}` et un `GET` équivalent pour relire la liste. Symétrique de `/mapping` mais sans `speaker_label`. Chaque `pj_id` doit appartenir au MJ courant (`422 invalid-player-list` sinon, cohérent avec `422 invalid-mapping` du Jalon 5).
- Q: Le job `narrative` doit-il être disponible sur les sessions `non_diarised` ou rester exclusif au mode `diarised` ? → A: Disponible sur les deux modes uniformément. En `non_diarised`, `POST /artifacts/narrative` consomme les résumés des chunks (même source que `elements` / `povs`) pour produire un récit chronologique en prose française. Le contrat HTTP reste identique pour le client (même endpoint, même schéma de réponse).
- Q: Comment les résumés partiels par chunk (étape map du job `summary`) sont-ils stockés pour réutilisation par les jobs dérivés (`narrative`, `elements`, `povs`) ? → A: Persistance inline — nouvelle colonne `summary_text` (nullable) ajoutée à la table `chunks`. Alimentée par le job `summary` (1 seul map LLM par session). Les jobs dérivés relisent directement cette colonne. À la régénération de `summary`, les `summary_text` sont remis à `NULL` dans la même transaction que la cascade FR-011, garantissant la cohérence.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — MJ crée une session en mode `non_diarised` (Priority: P1)

Un MJ veut éviter la diarisation pour une session donnée, soit parce qu'il sait que sa source audio s'y prête mal (qualité, locuteurs proches, fond sonore), soit parce qu'il accepte la limitation actuelle du provider cloud (Whisper sans diarisation). À la création de la session, il pose un tag explicite `transcription_mode = "non_diarised"`. Quand il uploade l'audio, le pipeline de transcription écrit la transcription sous forme d'un texte continu découpé en chunks ordonnés dans une nouvelle table, plutôt que sous forme de segments avec speaker labels.

**Why this priority** : c'est le point d'entrée du nouveau mode. Sans cette US, aucune des fonctionnalités en aval (résumé global, elements/povs non-diarised) n'est accessible. C'est aussi ce qui garantit la non-régression du mode `diarised` existant — sans tag explicite, on retombe sur le comportement Jalon 5.

**Independent Test** : créer une session avec `transcription_mode = "non_diarised"`, uploader un M4A court, attendre la fin du job de transcription. Vérifier que la table `chunks` contient bien N rows pour cette session, ordonnées via `ordre`, avec du texte non vide, et que la table `transcriptions` ne contient AUCUNE row pour cette session. Vérifier symétriquement qu'une session créée sans tag (donc en mode `diarised` par défaut) continue d'écrire dans `transcriptions` et pas dans `chunks` — non-régression.

**Acceptance Scenarios** :

1. **Given** un MJ authentifié, **When** il `POST /services/jdr/sessions` avec `{"title": "...", "recorded_at": "...", "transcription_mode": "non_diarised"}`, **Then** la session est créée avec ce mode visible dans `GET /sessions/{id}`.
2. **Given** une session en mode `non_diarised` avec un audio uploadé, **When** le job de transcription se termine, **Then** la table `chunks` contient une ou plusieurs rows pour la session, ordonnées par `ordre`, somme des `text` couvre toute la transcription source ; la table `transcriptions` est vide pour cette session.
3. **Given** une session créée sans `transcription_mode` (défaut), **When** le job de transcription se termine, **Then** la table `transcriptions` contient une row (segments diarisés comme aujourd'hui), la table `chunks` est vide pour cette session.
4. **Given** une session en mode `non_diarised`, **When** le MJ fait `GET /services/jdr/sessions/{id}/transcription`, **Then** la requête est refusée avec un code et un message qui indique qu'il faut utiliser le nouvel endpoint chunks-based (ou retourne un format dégradé adapté — voir Edge Cases).
5. **Given** une session en mode `non_diarised`, **When** le MJ fait `GET /services/jdr/sessions/{id}/chunks`, **Then** la réponse liste les chunks ordonnés (`ordre`, `text`) pour cette session.

---

### User Story 2 — MJ obtient un résumé global de session via map-reduce (Priority: P2)

Une fois la transcription stockée en chunks, le MJ déclenche un job qui (a) appelle le LLM une fois par chunk pour produire un résumé partiel (en préservant l'`ordre`), puis (b) appelle le LLM une fois de plus pour consolider les résumés partiels en un résumé global cohérent. Le résultat est persisté comme artefact `summary` de la session, consultable en JSON et en Markdown.

**Why this priority** : c'est l'output utilisateur principal du mode `non_diarised`. La transcription par chunks n'a pas de valeur en soi pour le MJ — il veut un récit lisible. C'est ce qui justifie l'effort de US1.

**Independent Test** : sur une session non-diarised avec 5 chunks déjà stockés, mocker le `LLMAdapter` pour qu'il renvoie `"résumé du chunk N"` au premier appel et `"résumé global consolidé"` au dernier. Déclencher `POST /artifacts/summary`. Vérifier qu'il y a exactement 5 + 1 = 6 appels LLM, dans l'ordre des `ordre` croissants pour les 5 premiers, et que la row `artifacts(kind="summary")` contient bien `"résumé global consolidé"`.

**Acceptance Scenarios** :

1. **Given** une session `non_diarised` avec 5 chunks stockés, **When** le MJ `POST /services/jdr/sessions/{id}/artifacts/summary`, **Then** un job est enqueué (202 + JobQueuedOut) et termine en `succeeded` avec une row `artifacts(kind="summary")`.
2. **Given** la même session, **When** le job tourne, **Then** le LLM est appelé exactement (nombre de chunks + 1) fois : N appels "map" (un par chunk, dans l'ordre `ordre` croissant) puis 1 appel "reduce" consolidant les résumés partiels.
3. **Given** une session `non_diarised` avec 1 seul chunk (transcription courte), **When** le job tourne, **Then** le LLM est appelé exactement 1 fois (l'étape reduce est skippée), et le résumé partiel est utilisé directement comme résumé global.
4. **Given** une session `diarised` (mode par défaut), **When** le MJ `POST /services/jdr/sessions/{id}/artifacts/summary`, **Then** la requête est refusée avec un code et un message qui explicite que cet endpoint est réservé au mode `non_diarised` (hors scope du jalon courant pour le mode `diarised`).
5. **Given** un résumé global déjà généré, **When** le MJ rejoue `POST /artifacts/summary`, **Then** le résumé est régénéré (UPSERT) ; les artefacts `narrative`, `elements`, `pov:*` éventuellement existants sur la session sont supprimés en cascade pour forcer leur régénération sur la nouvelle base.

---

### User Story 3 — MJ génère les artefacts dérivés (narrative, elements, povs) sur une session `non_diarised` (Priority: P3)

Les endpoints existants `POST /sessions/{id}/artifacts/narrative`, `POST /sessions/{id}/artifacts/elements` et `POST /sessions/{id}/artifacts/povs` continuent de fonctionner sur les sessions en mode `non_diarised`, mais en interne ils ne consomment plus la transcription segmentée (qui n'existe pas) : ils consomment les **résumés intermédiaires de chaque chunk** (étape map du job summary) comme document source, puis demandent au LLM de produire respectivement le récit chronologique / la fiche d'éléments / les POV par PJ en devinant qui parle à partir du contexte narratif.

**Why this priority** : c'est ce qui rend le mode `non_diarised` réellement utilisable pour le scénario JDR complet — sans cette US, on n'a qu'un résumé global mais pas les artefacts dérivés que le MJ et les joueurs consomment au quotidien.

**Independent Test** : sur une session `non_diarised` avec un résumé global déjà généré (donc les résumés partiels par chunk sont aussi en DB ou ré-extractibles), déclencher `POST /artifacts/elements` puis `POST /artifacts/povs`. Vérifier que le prompt user envoyé au LLM contient bien les résumés des chunks (et pas une transcription segmentée), et que les artefacts résultants ont la même structure JSON que ceux produits sur une session `diarised` (`{npcs, locations, items, clues}` pour elements ; `pov:<pj_id>` rows pour povs).

**Acceptance Scenarios** :

1. **Given** une session `non_diarised` avec ses chunks et le résumé global générés, **When** le MJ `POST /artifacts/elements`, **Then** le job produit une row `artifacts(kind="elements")` avec un JSON `{npcs, locations, items, clues}` valide.
2. **Given** la même session avec une liste de PJ présents déclarée via `POST /sessions/{id}/players` (par exemple 2 PJ : Aragorn et Galadriel), **When** le MJ `POST /artifacts/povs`, **Then** une row `artifacts(kind="pov:<pj_id>")` est produite par PJ listé (2 rows ici), à partir des résumés des chunks et de l'indication "deviner qui parle à partir du contexte".
3. **Given** une session `non_diarised` **sans** résumé global encore généré, **When** le MJ `POST /artifacts/elements` ou `POST /artifacts/povs`, **Then** la requête est refusée avec un message clair qui indique d'appeler `POST /artifacts/summary` d'abord (cohérent avec le pattern `409 no-mapping` du Jalon 5).
4. **Given** une session `non_diarised` avec son résumé global déjà généré, **When** le MJ `POST /artifacts/narrative`, **Then** le job produit une row `artifacts(kind="narrative")` à partir des résumés des chunks (et non d'une transcription segmentée), avec le même format de sortie qu'en mode `diarised`. La requête `GET /artifacts/narrative` (JSON) et `GET /artifacts/narrative.md` (Markdown) renvoient le résultat avec un contrat HTTP identique au mode `diarised`.

---

### Edge Cases

- **Tag invalide à la création** : si le MJ envoie `{"transcription_mode": "autre_chose"}`, la requête est refusée 422 avec la liste des valeurs autorisées (`diarised`, `non_diarised`).
- **Modification du tag après création** : le `transcription_mode` est immuable une fois la session créée. Tentative de modifier via `PATCH /sessions/{id}` → 422 avec un message explicite.
- **Session `non_diarised` sans aucun chunk** (cas dégénéré : audio vide ou transcription vide) : `POST /artifacts/summary` retourne 409 avec un message qui pointe vers l'état du job de transcription. Aucun appel LLM n'est effectué.
- **Régénération du résumé global** : à chaque `POST /artifacts/summary` ré-exécuté, l'ancien `summary` est UPSERT, et les artefacts `narrative`, `elements`, `pov:*` éventuellement existants sur cette session sont supprimés atomiquement, forçant une régénération explicite (miroir du pattern d'invalidation `pov:*` du Jalon 5).
- **Appel d'un endpoint exclusif au mode non choisi** : ex. `POST /artifacts/summary` sur session `diarised`, ou `GET /transcription` (format segmenté) sur session `non_diarised` → 409 avec message qui indique l'endpoint approprié pour ce mode.
- **Echec partiel du map-reduce** : si un appel LLM échoue (transient ou permanent) sur n'importe quel chunk ou sur le reduce, le job échoue globalement et aucun résumé n'est persisté. Pas de résultat partiel.
- **Chunk dépassant les capacités du modèle après découpage** : si la taille en caractères est respectée mais que le contenu tokenise au-delà du contexte du modèle, le job échoue en `permanent` avec un message actionnable (réduire le seuil de chunking ou changer de modèle).
- **Joueur tente d'accéder à la transcription chunks ou au résumé global** : `chunks` et `summary` sont des artefacts MJ-only au jalon courant ; pas d'endpoint `/me/sessions/{id}/summary`. À reconsidérer plus tard si une UX joueur l'exige.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** : Le système MUST accepter un champ optionnel `transcription_mode` dans le payload de création de session, avec deux valeurs autorisées : `"diarised"` (défaut, comportement Jalon 5 inchangé) et `"non_diarised"`. Toute autre valeur retourne `422`.
- **FR-002** : Le `transcription_mode` d'une session MUST être immuable après création. Toute tentative de modification via les endpoints existants (`PATCH /sessions/{id}`) retourne `422` avec un message explicite.
- **FR-003** : Le pipeline de transcription MUST se forker sur la valeur de `transcription_mode` : en mode `diarised`, écriture inchangée dans la table existante des transcriptions (segments diarisés) ; en mode `non_diarised`, écriture dans la nouvelle table de chunks (`chunks(session_id, chunk_id, ordre, text)`) en N rows ordonnées par `ordre`, somme des `text` couvrant l'intégralité de la transcription source.
- **FR-004** : La taille maximale en caractères d'un chunk MUST être configurable par variable d'environnement, avec un default raisonnable. Le découpage MUST viser à ne PAS couper en milieu de mot ni de phrase quand possible (découpage sur les frontières naturelles : ponctuation forte, sinon espace).
- **FR-005** : Le système MUST exposer une route `GET /services/jdr/sessions/{id}/chunks` (rôle `gm`) qui retourne la liste des chunks de la session, ordonnés par `ordre`, avec `chunk_id`, `ordre`, `text`. Disponible uniquement sur les sessions `non_diarised` (sinon 409 avec message d'orientation).
- **FR-006** : Le système MUST exposer une route `POST /services/jdr/sessions/{id}/artifacts/summary` (rôle `gm`) qui enqueue un job de génération du résumé global. Disponible uniquement sur les sessions `non_diarised` en état approprié (chunks présents). Retourne `202` + `JobQueuedOut`.
- **FR-007** : Le job de résumé global MUST procéder en deux étapes : (a) **map** — un appel LLM par chunk dans l'ordre `ordre` croissant, qui produit un résumé partiel de ce chunk, **persisté inline dans `chunks.summary_text`** au fil de l'eau ; (b) **reduce** — un appel LLM consolidant les résumés partiels dans l'ordre, qui produit le résumé global persisté comme `artifacts(kind="summary")`. Si la session ne contient qu'un seul chunk, l'étape reduce est omise et le résumé partiel sert directement de résumé global. Les `chunks.summary_text` produits par l'étape map sont conservés en DB après la fin du job pour permettre aux jobs dérivés (`narrative`, `elements`, `povs`) de les relire sans relancer le map (cf. FR-009).
- **FR-008** : Le système MUST exposer une route `GET /services/jdr/sessions/{id}/artifacts/summary` (rôle `gm`) qui retourne le résumé global persisté, et une variante `GET .../summary.md` qui rend la même donnée en Markdown avec en-tête de session standard.
- **FR-009** : Les jobs existants `narrative`, `elements` et `povs` MUST consommer les **résumés partiels des chunks** (lus directement dans la colonne `chunks.summary_text` produite par FR-007) comme document source quand la session est en mode `non_diarised`, au lieu de la transcription segmentée. Aucun map LLM n'est ré-exécuté par ces jobs — la phase map est faite une seule fois par le job `summary` et réutilisée par tous les jobs dérivés. Le prompt système MUST instruire le LLM d'inférer les rôles à partir du contexte narratif (puisque pas de speaker labels). Le format de sortie de chaque artefact (`{"text": "..."}` pour narrative, `{npcs, locations, items, clues}` pour elements, `pov:<pj_id>` rows pour povs) reste identique au mode `diarised`.
- **FR-010** : Les endpoints `POST /artifacts/narrative`, `POST /artifacts/elements` et `POST /artifacts/povs` sur une session `non_diarised` MUST refuser avec `409 no-summary` si le résumé global n'a pas encore été généré, pointant vers `POST /artifacts/summary` comme étape préalable (cohérent avec le pattern `409 no-mapping` du Jalon 5).
- **FR-011** : Régénérer `summary` MUST supprimer en cascade et atomiquement les artefacts `narrative`, `elements` et `pov:*` existants pour la session, ET remettre à `NULL` les `chunks.summary_text` de tous les chunks de cette session avant la nouvelle phase map. Toute la régénération (reset des `summary_text`, nouveau map, nouveau reduce, UPSERT du `summary`, suppression cascade des artefacts dérivés) MUST se faire dans une transaction unique — soit tout réussit, soit on rollback l'état antérieur. Miroir de FR-008 du Jalon 5 sur l'invalidation `pov:*` au changement de mapping.
- **FR-012** : Le système MUST exposer un nouvel endpoint `POST /services/jdr/sessions/{id}/players` (rôle `gm`) qui accepte `{"pj_ids": ["uuid1", "uuid2", ...]}` pour déclarer la liste des PJ présents à une session `non_diarised`. Un `GET` équivalent permet de relire la liste. Cet endpoint MUST être réservé aux sessions `non_diarised` (`409 wrong-mode` sur une session `diarised`, qui utilise `/mapping` à la place). Chaque `pj_id` du payload MUST appartenir au MJ courant, sinon `422 invalid-player-list` (symétrique du `422 invalid-mapping` du Jalon 5). La liste MUST pouvoir être remplacée intégralement par un nouveau `POST` (semantique PUT-like : réécriture complète).
- **FR-013** : Le système MUST exposer `POST /services/jdr/sessions/{id}/artifacts/narrative` uniformément sur les deux modes. En mode `diarised`, le comportement Jalon 5 est inchangé (consommation des segments diarisés). En mode `non_diarised`, le job consomme les résumés partiels des chunks (étape map de FR-007) comme document source et produit un récit chronologique en prose française. Le format de sortie (`{"text": "..."}`), le rendu Markdown via `narrative.md`, et le contrat HTTP côté client restent rigoureusement identiques entre les deux modes.
- **FR-014** : Les endpoints existants du Jalon 5 (création/list/get session, upload audio, `GET /transcription`, `GET /transcription.md`, `PUT /mapping`, `POST /artifacts/narrative`, `POST /artifacts/elements` sur diarised, `POST /artifacts/povs` sur diarised, `/me/*`) MUST rester fonctionnels et inchangés en comportement sur les sessions `diarised`. La suite de tests existante doit rester verte sans modification.
- **FR-015** : Les jobs map-reduce MUST mapper les erreurs `TransientLLMError` / `PermanentLLMError` du `LLMAdapter` sur les exceptions `TransientJobError` / `PermanentJobError` cohérentes avec la retry policy RQ existante (ADR 0004 §3). Un échec sur n'importe quel appel LLM (map ou reduce) fait échouer le job globalement.

### Key Entities

- **Session (augmentée)** : la session existante du Jalon 5 gagne un attribut `transcription_mode` (énumération `diarised` / `non_diarised`), immuable après création. Aucune autre modification de la session existante.
- **Chunk** : nouvelle entité applicative liée 1-N à une session. Attributs : `session_id` (FK), `chunk_id` (identifiant local au chunk), `ordre` (entier croissant qui détermine la séquence), `text` (contenu textuel du chunk), `summary_text` (résumé partiel produit par l'étape map du job `summary`, nullable). Unicité par `(session_id, ordre)`. Pas de cycle.
- **Artifact (kind="summary")** : nouvel usage de la table des artefacts existante. Clé composite reste `(session_id, kind)` ; `content_json` stocke le résumé global produit. Aucune migration schema nécessaire pour l'artefact lui-même — c'est un nouveau `kind` au sens applicatif. La seule migration schema requise par la feature concerne la nouvelle table `chunks` (cf. ci-dessus) et sa colonne `summary_text`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** : Sur une session `non_diarised` dont la transcription source dépasse 60 000 caractères, le pipeline `transcription → chunks → summary` produit un résumé global d'au plus 5 000 caractères en moins de 5 minutes (mesure : durée du job RQ de bout en bout, avec modèle cloud raisonnable).
- **SC-002** : Sur une session `non_diarised` tenant en un seul chunk (≤ X caractères), le job `summary` effectue exactement 1 appel LLM (reduce skippé) et termine en moins de 60 secondes.
- **SC-003** : Aucune régression sur les sessions `diarised` : la suite `pytest` du Jalon 5 reste verte sans modification, le scénario `quickstart.md` du Jalon 5 reste exécutable de bout en bout.
- **SC-004** : Pour une session `non_diarised` de 80 000 caractères avec 4 PJ, le coût total LLM (en tokens facturés) de `summary + elements + povs` est inférieur de **au moins 40%** au coût d'une exécution naïve où les 3 jobs ingéreraient chacun la transcription brute complète.
- **SC-005** : Les artefacts `elements` produits sur une session `non_diarised` référencent au moins **80%** des PNJ, lieux, items et indices qu'un humain identifie comme présents dans la session de référence (mesure manuelle sur 3 sessions de test, single évaluateur).
- **SC-006** : Le MJ peut créer une session `non_diarised`, attendre la transcription, déclencher `summary`, puis `elements` et `povs`, en moins de 6 minutes de bout en bout pour une session source de 30 minutes (cible : 2× temps réel de la session).
- **SC-007** : Le mode `non_diarised` est activable / désactivable via un seul champ dans le payload de création — aucun changement de configuration globale ni redémarrage de service requis.

## Assumptions

- La taille maximale d'un chunk est par défaut 30 000 caractères (≈ 7 500 à 10 000 tokens en français, confortable pour un contexte 32k tokens avec marge prompt + sortie). Configurable par variable d'environnement. La valeur exacte sera affinée par benchmarks empiriques après la première session réelle, sans bloquer cette feature.
- Le découpage en chunks est calculé à la fin du job de transcription, à partir du texte concaténé issu du provider de transcription. Il n'y a pas d'invariant fort sur l'alignement temporel chunks ↔ secondes audio — l'ordre du `ordre` reflète l'ordre du texte, pas un timestamp absolu.
- En mode `non_diarised`, le provider de transcription cloud actuel (Whisper OpenAI-compatible via DeepInfra) reste l'unique source. Aucun changement de provider ni d'appel à un service de diarisation séparé.
- Les artefacts `chunks`, `summary`, `elements`, `povs` produits sur une session `non_diarised` sont visibles uniquement par le MJ propriétaire au jalon courant. L'extension aux joueurs (`/me/sessions/{id}/summary` par exemple) est explicitement hors scope, à reconsidérer ultérieurement.
- Le pipeline de transcription côté provider (étape audio → texte) est inchangé : c'est uniquement le **stockage et l'exposition** du résultat qui se forke selon le `transcription_mode`. Le découpage en chunks intervient en aval du provider, côté job.
- Le mode `diarised` reste le défaut explicite et documenté. Toute session existante créée avant ce jalon est implicitement `diarised` — pas de migration nécessaire.
- Aucune nouvelle dépendance externe (pas de nouveau provider LLM, pas de service tiers). On continue d'utiliser le `LLMAdapter` existant et la queue RQ existante.
- Les invariants d'autorisation existants (sessions scoppées au MJ propriétaire, isolation joueur FR-014 du Jalon 5) s'appliquent identiquement aux nouvelles routes.
- Aucune modification de l'audio source n'est requise — la purge automatique post-transcription du Jalon 5 reste en vigueur quel que soit le mode.
- Le mode live (stub Jalon 5) n'est pas concerné par cette feature.
