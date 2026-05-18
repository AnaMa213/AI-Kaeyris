# Research: Mode `non_diarised` — décisions techno et patterns

**Phase 0 du `/speckit-plan`**. Objectif : résoudre toute zone d'inconnu technique *avant* d'écrire la data-model et les contrats. Aucun `NEEDS CLARIFICATION` ne doit subsister à l'issue de cette phase.

> Note : les 3 clarifications fonctionnelles ont déjà été tranchées dans `/speckit-clarify` (voir `spec.md` §Clarifications). Ce document traite uniquement les décisions techniques d'implémentation.

---

## 1. Stratégie de chunking texte (taille + frontières)

### Décision

- Taille maximale par chunk : **30 000 caractères**, configurable via `KAEYRIS_CHUNK_MAX_CHARS` (env var, default `30000`, validé `> 0` au démarrage).
- Algorithme : découpe gloutonne sur **frontières naturelles** (priorité : double saut de ligne `\n\n` > fin de phrase `[.!?]\s` > espace `\s` > coupe brute en dernier recours).
- Pas d'overlap entre chunks (pas de fenêtre glissante). Le LLM résume chaque chunk indépendamment, le reduce assure la cohérence globale.
- L'`ordre` des chunks est l'entier 0-indexed correspondant à leur position dans la transcription source.

### Rationale

- **30 000 chars ≈ 7 500-10 000 tokens** en français (ratio ~3 chars/token courant pour les corpus français modernes ; source benchmarks tiktoken sur corpus Le Monde). Confortable pour des modèles à 32k tokens de contexte (Llama-3.1, Qwen-2.5) avec marge pour le prompt système + la réponse.
- Découpe sur frontières naturelles : **réduit l'incohérence sémantique** vs coupe brute au milieu d'un mot ou d'une phrase. Algorithme simple (Python stdlib + `re`), pas de dépendance externe.
- Pas d'overlap : sans diarisation, l'ordre temporel est déjà préservé par `ordre`. L'overlap aurait un coût LLM ×1.2-1.5 sans gain de qualité prouvé pour de la summarisation map-reduce (cf. patterns LangChain / LlamaIndex pour `MapReduce` strategy).

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Unité de découpe **tokens** (via `tiktoken`) au lieu de caractères | Tiktoken n'est pas garanti représentatif des modèles non-OpenAI (Llama, Mistral). Ajoute une dépendance pour un gain marginal — le ratio chars/token suffit pour calibrer un seuil sûr. |
| Overlap glissant (ex. 500 chars partagés entre chunks consécutifs) | Coût LLM accru, complexité d'orchestration. Pas justifié pour summary global où la cohérence se construit au reduce. |
| Découpe par segments audio (timestamps) plutôt que caractères texte | Repousse la question : il faudrait quand même limiter la taille du chunk en LLM tokens. Et le mode `non_diarised` se définit justement par l'absence de structure temporelle dans le stockage final. |
| Bibliothèque externe (`semantic_text_splitter`, `langchain.text_splitter.RecursiveCharacterTextSplitter`) | Coût d'ajouter une dépendance pour ~80 lignes de code maison. CLAUDE.md §3 verrouille la stack — toute nouvelle dépendance demande discussion. |

---

## 2. Pattern d'invalidation cascade (FR-011)

### Décision

À la régénération du `summary`, la transaction unique exécute dans l'ordre :

1. `UPDATE jdr_chunks SET summary_text = NULL WHERE session_id = :sid`
2. `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind IN ('narrative', 'elements') OR kind LIKE 'pov:%'`
3. (l'ancien `summary` est UPSERT à la fin du nouveau job, hors de cette transaction de reset)

Mécanisme : la transaction de reset s'ouvre **au début** du job `_generate_summary`, **avant** la phase map. Si une erreur LLM survient pendant le map ou le reduce, la transaction est rollback, l'état antérieur est préservé (y compris l'ancien `summary` qui survit donc à un échec de régénération). Le job RQ tombe en `failed` et le MJ peut relancer.

### Rationale

- **Atomique = FR-011 strict**. Une régénération partiellement appliquée laisserait `summary_text=NULL` sans summary global remis à jour, état incohérent.
- Calqué sur le pattern d'invalidation `pov:*` du Jalon 5 (`data-model.md §6` du Jalon 5, implémenté via `MappingRepository.replace_for_session` + `ArtifactRepository.invalidate_pov_artifacts` dans la même session SQLAlchemy).
- L'ancien `summary` survit à un échec : c'est une garantie utile (le MJ ne perd pas le contenu existant si l'API LLM échoue transitoirement).

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Reset + delete dans la **même** transaction que les nouvelles écritures (map+reduce+UPSERT summary) | Transaction trop longue (peut durer 5 min, cf. SC-001). Verrous DB tenus pendant les appels LLM → risque de timeout PostgreSQL. SQLite single-writer aussi handicapé. |
| Pas de reset, juste DELETE des artefacts dérivés | Laisse `summary_text` obsolète en place. Si le nouveau job échoue après le reduce mais avant l'UPSERT, on a un état partiellement réécrit (certains `summary_text` mis à jour, d'autres non) selon l'ordre du map. |
| Soft-delete avec flag `is_stale=true` sur les artefacts | Complexifie la lecture (chaque GET doit filtrer). Pas d'avantage clair vs cascade delete. Rejeté en clarify (Option C de Q2). |

---

## 3. Prompts LLM en mode `non_diarised`

### Décision

Trois nouveaux prompts système dans `app/services/jdr/prompts.py` :

- `SUMMARY_MAP_SYSTEM_PROMPT` : résume un segment isolé, en français, fidèle aux faits, sans inventer, sans ajouter "ce segment est le N-ième". Format : prose courte (5-15 phrases).
- `SUMMARY_REDUCE_SYSTEM_PROMPT` : consolide une séquence ordonnée de résumés partiels en un résumé global cohérent. Préserver la chronologie. Pas de méta-commentaire ("ce résumé regroupe..."). Style : récit fluide proche du `NARRATIVE_SYSTEM_PROMPT`.
- Pour `narrative`, `elements`, `povs` en mode `non_diarised` : on **réutilise les prompts système existants** (`NARRATIVE_SYSTEM_PROMPT`, `ELEMENTS_SYSTEM_PROMPT`, `POV_SYSTEM_PROMPT`) mais on **modifie le user prompt** côté job pour :
  - Indiquer que l'input n'est pas une transcription brute mais un résumé déjà consolidé (mention en en-tête du user prompt).
  - Préciser au LLM "tu n'as pas d'étiquette de locuteur ; déduis qui parle à partir du contexte et des noms de PJ fournis".
  - Pour `povs`, lister les PJ présents par leur `name` dans l'en-tête (issus de `jdr_session_players`).

### Rationale

- **Réutiliser les prompts système** plutôt que créer des variantes `_NON_DIARISED` :
  - Le prompt système définit la *nature* de l'artefact (récit, fiche d'éléments, POV), pas la *forme* de l'input. Les contraintes (fidélité, format, style) sont identiques entre les deux modes.
  - Évite la duplication et le risque de divergence non intentionnelle entre les deux variantes (drift de qualité au fil des itérations sur les prompts).
- **User prompt modifié** : le contexte (résumé vs transcript) et les instructions opérationnelles (deviner le locuteur) appartiennent légitimement au user prompt côté job — c'est ce qui change selon la situation.
- Pour `summary`, deux prompts distincts (`MAP` et `REDUCE`) sont nécessaires : la tâche elle-même est différente.

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Deux variantes par artefact (`NARRATIVE_SYSTEM_PROMPT_DIARISED` + `NARRATIVE_SYSTEM_PROMPT_NON_DIARISED`) | Duplication. Risque de divergence sur les contraintes communes (fidélité au texte, pas d'invention, etc.) au fil des révisions. |
| Un seul prompt unifié avec condition `if/else` interne au LLM | Anti-pattern. Le LLM ne comprend pas bien les conditionnels imbriqués. Plus de tokens consommés pour rien. |
| Prompts générés dynamiquement (string templating côté code) | Casse la centralisation prompts.py (CLAUDE.md §2.4 et ADR 0006 §2). |

---

## 4. Lecture de `chunks.summary_text` par les jobs dérivés

### Décision

Quand `_generate_narrative` / `_generate_elements` / `_generate_povs` détectent `session.transcription_mode == 'non_diarised'` :

1. Charger les rows de `jdr_chunks` ordonnées par `ordre` ASC pour la session.
2. **Pré-condition** : tous les `summary_text` doivent être non-NULL. Si au moins un est NULL → `PermanentJobError("Session summary not generated")` côté job, mappé en `409 no-summary` côté route (FR-010).
3. Concaténer les `summary_text` avec un séparateur simple (`\n\n---\n\n`) dans l'ordre de `ordre`. Le résultat sert de "transcript équivalent" pour le user prompt.
4. Appeler le LLM une seule fois (pas de map-reduce supplémentaire — le map a déjà été fait par `_generate_summary`).

### Rationale

- **Une seule lecture DB** (`SELECT * FROM jdr_chunks WHERE session_id = :sid ORDER BY ordre`), un seul appel LLM par artefact. Aligné SC-004 (coût LLM ≤ 60 % du naïf).
- Pré-condition stricte = même UX que le `409 no-mapping` du Jalon 5 (cohérent).
- Séparateur explicite (`---`) plutôt que jonction silencieuse : aide le LLM à comprendre la structure chunked du document même s'il est invité à le lire comme un texte continu.

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Charger uniquement les `summary_text` (sans `text`) pour économiser la bande passante DB | Optimisation prématurée. Les rows `chunks` sont petites (~5-30 KB), une seule session entière tient en mémoire largement. |
| Auto-trigger du job `summary` si un `summary_text` est NULL | Rejeté en clarify (Q1 — pré-condition stricte). |
| Reduce LLM "léger" (passe les `summary_text` chunked au LLM pour relancer un reduce avant chaque artefact) | Double coût LLM. Pas justifié — le reduce a déjà été fait par `_generate_summary` et son output existe via l'artefact `summary`. Les jobs dérivés peuvent même réutiliser `summary` global si plus pertinent. Voir §5. |

---

## 5. Réutilisation du `summary` global vs des `chunks.summary_text` par les jobs dérivés

### Décision

Les jobs dérivés (`narrative`, `elements`, `povs`) consomment **les `chunks.summary_text`** (résumés partiels par chunk, ordonnés), **pas** l'artefact `summary` global.

### Rationale

- **Plus de signal granulaire**. Le `summary` global est par construction *plus condensé* que la somme des résumés partiels (compression supplémentaire au reduce). Les jobs dérivés ont besoin de plus de matière pour produire `elements` (extraction de PNJ/lieux/items) et `povs` (POV par PJ avec détails) — utiliser le résumé global perdrait des détails à valeur extraite.
- **Cohérent avec FR-009** : le spec dit explicitement "consomment les résumés des chunks". Pas le summary global.
- **Le `summary` reste l'artefact final destiné au MJ pour relecture** — c'est son rôle. Les jobs dérivés ont leur source distincte.

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Consommer le `summary` global comme source des jobs dérivés | Sur-compression. Perte d'information. Risque que `elements` rate des PNJ mineurs présents dans les `summary_text` mais effacés au reduce. |
| Consommer les `chunks.text` originaux (skip les résumés intermédiaires) | Annule le gain SC-004. Re-saturation du contexte LLM. |
| Choix configurable par env var (le MJ décide entre summary global et chunks.summary_text) | Sur-ingénierie YAGNI. Une seule décision raisonnable, on l'acte. |

---

## 6. Test pyramid pour cette feature

### Décision

- **Unit** (>50 % des nouveaux tests) : `text_chunker` (boundaries, taille respectée, edge cases vide/géant), pure logic functions (`logic.list_session_chunks`, `logic.set_session_players`, parsing du user prompt non-diarisé).
- **Integration côté DB** (~30 %) : repositories + cascade invalidation (un test critique sur le rollback transactionnel).
- **End-to-end via httpx** (~20 %) : POST /sessions avec mode, POST /artifacts/summary avec stub LLM (3 chunks → vérifier 4 appels LLM dans l'ordre attendu), cross-mode isolation 409.
- **Pas de E2E avec vraie clé DeepInfra** dans la suite automatisée — c'est la validation manuelle finale (équivalent T076 du Jalon 5).

### Rationale

- Aligné CLAUDE.md §2.5 (test pyramid : beaucoup d'unit, moins d'integration, très peu d'E2E).
- Le `text_chunker` est facilement unit-testable et a beaucoup d'edge cases — c'est là qu'on investit massivement.
- Les appels LLM sont mockés via `_StubLLM` (pattern Jalon 5 dans `tests/services/jdr/test_narrative.py`).

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| Tester `_generate_summary` avec vraie API DeepInfra en CI | Coût, flakiness, latence. La validation E2E reste manuelle. |
| Skip les tests d'isolation cross-mode | Risque de régression silencieuse sur FR-014 (le `/mapping` accidentellement accepté sur non_diarised, par ex.). C'est la check de sécurité critique. |

---

## 7. Schéma de migration Alembic

### Décision

Une seule migration `migrations/versions/0002_non_diarised_mode.py` qui :

1. `ALTER TABLE jdr_sessions ADD COLUMN transcription_mode VARCHAR(16) NOT NULL DEFAULT 'diarised'` (avec `server_default` pour rétro-compat des sessions existantes).
2. `CREATE TABLE jdr_chunks (...)` avec colonnes : `id UUID PK`, `session_id UUID FK CASCADE`, `ordre INT NOT NULL`, `text TEXT NOT NULL`, `summary_text TEXT NULL`, `created_at TIMESTAMPTZ NOT NULL`. Index unique `(session_id, ordre)`.
3. `CREATE TABLE jdr_session_players (...)` : PK composite `(session_id, pj_id)`, FK CASCADE vers `jdr_sessions(id)` et `jdr_pjs(id)`, `created_at TIMESTAMPTZ NOT NULL`.
4. Downgrade complet (DROP TABLE + DROP COLUMN), testable via `alembic downgrade -1` puis `alembic upgrade head` (smoke test obligatoire avant commit).

### Rationale

- Une seule migration parce que les 3 changements sont logiquement liés (mode `non_diarised` complet). Plus simple à reviewer, atomique au sens DDL.
- `server_default='diarised'` sur l'ALTER : les rows existantes (sessions Jalon 5) se retrouvent en mode `diarised`, comportement attendu (cf. assumptions §spec.md).
- `VARCHAR(16)` plutôt qu'enum SQL natif : SQLAlchemy + SQLite gèrent mieux les enums en VARCHAR + CHECK constraint applicatif. Cohérent avec les autres enums du Jalon 5 (Role, ApiKeyStatus, SessionState, etc.) qui sont stockés en strings.
- Alembic en mode `--autogenerate` ne couvre pas le `server_default`, je l'écris à la main par-dessus pour ce point précis.

### Alternatives considérées

| Alternative | Pourquoi rejetée |
|---|---|
| 3 migrations séparées (mode → chunks → session_players) | Surcoût d'orchestration, pas de gain. Un downgrade partiel laisserait un état incohérent (mode `non_diarised` sans table `chunks`). |
| Migration sans `server_default` → forcer le code applicatif à mettre `diarised` au create | Casse les sessions Jalon 5 existantes : la colonne `NOT NULL` sans default échouerait à l'`ALTER`. |
| Enum SQL natif (`CREATE TYPE`) côté PostgreSQL | Divergence schema SQLite vs PostgreSQL. Le pattern Jalon 5 stocke tous les enums en VARCHAR pour parité dev/prod. |

---

## 8. Performance et observabilité

### Décision

- Logs `structlog` ajoutés sur les nouvelles routes/jobs avec des champs structurés cohérents avec le Jalon 5 : `event`, `session_id`, `chunk_count`, `llm_calls`, `model_used`, `duration_ms`.
- Aucune métrique Prometheus à ce jalon (cohérent : le Jalon 6 observability arrive après, pas le moment d'introduire `prometheus-client` ici).
- Les durées sont calculées localement et logguées, prêtes à être branchées sur des métriques au Jalon 6.

### Rationale

- L'observabilité formelle (métriques Prometheus, traces OpenTelemetry) est explicitement le **Jalon 6** dans CLAUDE.md §5. Anticiper ici violerait YAGNI (§2.3).
- Les logs structurés couvrent suffisamment le besoin de debug pour la première session réelle, sans dépendance nouvelle.

---

## 9. Stratégie de migration des sessions Jalon 5

### Décision

- Les sessions créées au Jalon 5 (sans `transcription_mode`) prennent automatiquement `diarised` via le `server_default` Alembic. Aucune action manuelle.
- Aucune session existante ne bascule en `non_diarised` après-coup. Si un MJ veut tester le nouveau mode, il crée une **nouvelle** session avec le tag — c'est ce que l'immutabilité de `transcription_mode` (FR-002) impose de toute façon.

### Rationale

- Migration zéro-clic. Pas de script de backfill. Pas de risque de casser des sessions productives.
- L'immutabilité simplifie : on n'a pas à gérer un état "session partiellement migrée".

---

## 10. Mode `live` et joueurs

### Décision

Hors scope, comme posé dans `spec.md §Assumptions`. Le mode live (US5 du Jalon 5) reste un stub. Les endpoints `/me/*` joueur restent exclusivement utilisables sur des sessions `diarised` au jalon courant.

### Rationale

YAGNI. Le besoin n'est pas formulé. À reconsidérer si les premiers utilisateurs réels demandent de partager le résumé avec les joueurs.

---

## Synthèse

Toutes les zones d'inconnu technique sont résolues. Aucun `NEEDS CLARIFICATION` ne subsiste pour le passage en Phase 1. Le plan d'implémentation peut être détaillé dans `data-model.md` et `contracts/rest-api.md`.
