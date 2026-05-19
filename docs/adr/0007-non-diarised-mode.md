# ADR 0007 — Mode `non_diarised` du service `kaeyris-jdr` (sub-jalon 5.5)

- **Statut** : accepté
- **Date** : 2026-05-18
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (monolithe modulaire), ADR 0004 (jobs RQ + retry), ADR 0005 (LLMAdapter), ADR 0006 (service kaeyris-jdr Jalon 5), CLAUDE.md §3 (stack lockée), CLAUDE.md §5 (roadmap)
- **Dérivé de** : [`specs/002-non-diarised-mode/`](../../specs/002-non-diarised-mode/) (spec, clarify, plan, research, data-model, contracts, tasks)

## Contexte

Le Jalon 5 a livré le service `kaeyris-jdr` avec une posture **diarisée** : chaque segment de la transcription porte un `speaker_label` que le MJ mappe ensuite vers un PJ via `PUT /mapping`. Cette posture est solide quand le provider de transcription sait séparer les locuteurs — mais le provider cloud par défaut (DeepInfra Whisper) ne le fait pas (cf. ADR 0006 §2 et `docs/services/jdr.md` §5). En attendant l'hôte GPU local (Jalon 9), tous les segments arrivent avec `speaker_label="unknown"` → un seul PJ mappé possible → POV mono-locuteur.

Ce sub-jalon résout le problème côté pipeline en introduisant un **mode `non_diarised` opt-in à la création de session** qui :

1. Stocke la transcription en **chunks ordonnés** (texte plat, sans speaker labels) dans une nouvelle table `jdr_chunks`.
2. Génère un **résumé global** via map-reduce LLM (1 résumé par chunk, persisté inline ; puis 1 reduce final).
3. Fait consommer ce résumé chunked par les jobs `narrative`, `elements`, `povs` existants, **sans modifier leur contrat HTTP côté client**.
4. Préserve le mode `diarised` (Jalon 5) comme défaut — aucun client existant ne casse, FR-014 explicite.

Quatre décisions structurantes sont actées :

1. **Position du tag** : à la création de session, immuable après.
2. **Stockage des résumés intermédiaires** : inline vs séparé.
3. **Réutilisation des prompts système existants** : un seul jeu pour les deux modes.
4. **Atomicité de la cascade FR-011** : transaction unique au reset, LLM hors transaction.

Spec Kit a produit le détail dans `specs/002-non-diarised-mode/`. Cet ADR consolide les choix structurants pour les retrouver à 6 mois sans plonger dans 90 KB de spec.

## Décision

### 1. Tag posé à la création de session, immuable

`POST /sessions` accepte un champ optionnel `transcription_mode: "diarised" | "non_diarised"` (défaut `"diarised"`). La valeur est :

- **persistée** dans une nouvelle colonne `jdr_sessions.transcription_mode` (`VARCHAR(16)` non-nullable, `server_default='diarised'` pour rétro-compat des sessions Jalon 5).
- **immuable** après création. `PATCH /sessions/{id}` rejette toute occurrence de la clé `transcription_mode` dans le body avec `422 immutable-field` (FR-002).

**Pourquoi pas une bascule ultérieure** : la transcription est l'artefact source de tout le pipeline. Changer de posture après ingestion d'audio impliquerait soit une ré-ingestion (audio purgé après transcription, FR-004 du Jalon 5), soit un mécanisme de conversion `segments → chunks` qui ne préserve pas la fidélité du texte original. Trop de complexité pour zéro gain : on force le MJ à choisir avant l'upload.

**Sessions Jalon 5 existantes** : prennent automatiquement `diarised` via le `server_default`. Aucune migration de données nécessaire, aucun script de backfill.

### 2. Résumés partiels par chunk persistés **inline** (`chunks.summary_text`)

Trois options envisagées (cf. `research.md §5` et `/speckit-clarify Q3`) :

- **A — Transient** (recalculer à chaque artefact) : explose le coût LLM ×N_artefacts. Rejeté.
- **B — Inline** : nouvelle colonne `summary_text TEXT NULL` sur `jdr_chunks`. **Choisi.**
- **C — Artefacts séparés** (`kind="chunk_summary:<ordre>"`) : multiplie les rows, complique les requêtes.

**Avantages de B** :

- **1 seul map LLM par session**, réutilisé par `narrative`, `elements`, `povs`. Garantit SC-004 (coût LLM dérivés ≤ 60 % du naïf).
- **Reset cascade simple** : `UPDATE jdr_chunks SET summary_text = NULL WHERE session_id = :sid` est atomique à coût constant, idempotent.
- **Pas exposé au client** : `GET /chunks` ne renvoie que `chunk_id`, `ordre`, `text`. Le `summary_text` reste interne au pipeline LLM.

### 3. Prompts système **réutilisés** entre les deux modes

`NARRATIVE_SYSTEM_PROMPT`, `ELEMENTS_SYSTEM_PROMPT`, `POV_SYSTEM_PROMPT` (Jalon 5) sont **utilisés tels quels** sur les deux modes. Ce qui change selon le mode est uniquement le **document source côté user prompt** :

- `diarised` : segments concaténés via `_format_segments_for_narrative` (Jalon 5).
- `non_diarised` : `chunks.summary_text` concaténés via un séparateur explicite `\n\n---\n\n`.

**Pourquoi pas deux jeux de prompts** : le system prompt décrit la **nature** de l'artefact (récit, fiche d'éléments, POV) ; les **contraintes** (fidélité au texte, format de sortie, pas d'invention) sont communes. Dupliquer = risque de divergence au fil des révisions.

Deux nouveaux prompts spécifiques au mode non_diarised : `SUMMARY_MAP_SYSTEM_PROMPT` (résumer un extrait) et `SUMMARY_REDUCE_SYSTEM_PROMPT` (consolider des résumés partiels ordonnés). Aucun rapport avec les prompts artefacts existants.

### 4. Atomicité de la cascade FR-011 : transaction unique au reset, LLM **hors** transaction

À chaque régénération du `summary`, le job ouvre une transaction qui exécute dans l'ordre :

1. `UPDATE jdr_chunks SET summary_text = NULL WHERE session_id = :sid`
2. `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind IN ('narrative', 'elements')`
3. `DELETE FROM jdr_artifacts WHERE session_id = :sid AND kind LIKE 'pov:%'`
4. **Commit** — la transaction se termine ici, AVANT tout appel LLM.

Puis le map (1 appel LLM par chunk, commit par chunk) et le reduce (1 appel LLM, commit final UPSERT `kind="summary"`) tournent **chacun dans sa propre courte transaction**, hors du verrou DB.

**Pourquoi pas une transaction unique englobante** : un appel LLM peut durer plusieurs minutes (SC-001 cible 5 min sur 60 000 chars). Tenir une connexion DB pendant ce temps :

- bloque les autres workers (SQLite single-writer ; PostgreSQL : verrous longs sur les rows ciblées),
- risque de timeout côté pool ou côté Postgres (`statement_timeout`),
- empêche tout retry granulaire (si le LLM fail au map du chunk 5, tout est rollback, on perd le travail des chunks 1-4).

**Compromis assumé** : si le map fail après le reset, l'ancien `summary` global survit (étape 4 pas atteinte) mais les `chunks.summary_text` et les artefacts dérivés ont déjà été supprimés. État dégradé mais cohérent — le MJ relance `POST /artifacts/summary` qui repartira proprement. Documenté dans `data-model.md §6`.

## Décisions de surface (clarify A/A/A)

Trois questions secondaires arbitrées via `/speckit-clarify` (voir `spec.md §Clarifications`) :

- **PJ déclarés en mode non_diarised** : nouvel endpoint dédié `POST /sessions/{id}/players` (avec liste `pj_ids`), symétrique de `/mapping` mais sans `speaker_label`. Choix entre "endpoint dédié", "réutilisation de /mapping avec entrées dégradées", "dérivation automatique des players enrôlés". L'endpoint dédié évite la distorsion sémantique du `/mapping` existant et garantit une déclaration explicite par session (contrairement à la dérivation automatique qui suppose à tort qu'un MJ a une seule campagne active).
- **`narrative` disponible sur les deux modes** : oui, par cohérence UX (un MJ qui passe en non_diarised conserve les 3 artefacts). En non_diarised, `narrative` consomme `chunks.summary_text` comme `elements` et `povs`.
- **Stratégie POV map-reduce** : un seul résumé global commun, nom du PJ injecté dans le prompt user uniquement. Tant que la diarisation n'est pas opérationnelle (Jalon 9), la qualité POV reste limitée par construction — pas de raison d'investir dans un map-reduce POV-aware coûteux qui se justifierait seulement après l'arrivée des speaker labels.

## Conséquences

### Positives

- **Service utilisable end-to-end sur sessions longues** sans attendre l'hôte GPU local. Le map-reduce permet de digérer des sessions de 2-3h (50-90k chars de transcription) en restant sous le seuil de saturation du contexte LLM.
- **Zéro régression Jalon 5** (FR-014, garantie par 248 tests Jalon 5 verts sans modification après livraison du sub-jalon).
- **Coût LLM optimisé** : la phase map du `summary` est faite une fois et réutilisée par `narrative`, `elements`, `povs` — diviseur ~10 sur le coût total comparé à un naïf "chaque artefact ingère la transcription complète".
- **Surface API additive uniquement** : aucun client Jalon 5 ne casse, les nouveaux endpoints (`/chunks`, `/players`, `/artifacts/summary[.md]`) sont opt-in via le tag à la création.
- **Symétrie /mapping ↔ /players** : un MJ familier du Jalon 5 retrouve une API parallèle sans surprise.

### Négatives (assumées)

- **Qualité dégradée des POV en non_diarised** : sans speaker labels, le LLM doit "deviner" qui agit depuis le contexte narratif des résumés chunked. Limite documentée explicitement dans le system prompt POV et dans `quickstart.md §6`.
- **Cascade plus stricte qu'au Jalon 5** : régénérer le `summary` supprime aussi `narrative` et `elements`, alors que le Jalon 5 ne supprimait que `pov:*` au changement de mapping. Plus brutal mais plus honnête (un changement de résumé invalide vraiment tous les artefacts dérivés).
- **Choix immutable du mode** : le MJ qui se trompe à la création doit créer une nouvelle session. Trade-off assumé (cf. §1).
- **+2 tables + 1 colonne en DB** : modeste mais non nul. Modèles ORM bien encapsulés dans `jdr_chunks` et `jdr_session_players`.

### Neutres

- **Pas d'extension `/me/*` joueur au sub-jalon courant** : un joueur dont le MJ a opté pour le mode non_diarised ne voit rien via `/me/sessions/{id}/{narrative|pov}` (409 wrong-mode). À reconsidérer si la première vraie session non_diarised montre un besoin UX joueur.
- **Validation manuelle E2E (T059) reste hors CI** : comme T076 du Jalon 5, à exécuter avant la clôture formelle du sub-jalon avec une vraie clé DeepInfra et un audio M4A réel.

## Alternatives rejetées

| Alternative | Pourquoi rejetée |
|---|---|
| Map-reduce uniforme sur les deux modes (`diarised` inclus) | Hors scope du sub-jalon 5.5 — coût d'implémentation et de validation × 2 sans intérêt immédiat (les sessions diarisées n'ont pas encore la masse critique pour saturer le contexte). À reconsidérer Jalon 9 quand la diarisation locale arrivera. |
| Stratégie POV-aware en non_diarised (map-reduce par PJ) | Coût LLM × N_pj sans gain qualitatif en l'absence de diarisation (le texte source est le même pour tous les PJ). À reconsidérer post-Jalon 9. |
| Endpoint `/players` réutilisant `/mapping` avec entrées dégradées | Distorsion sémantique : `/mapping` est conçu pour `{speaker_label: pj_id}`, autoriser des entrées sans speaker confond le schéma et les tests. |
| Tag mode posé à l'upload audio plutôt qu'à la création | Risque d'incohérence si plusieurs uploads (FR-017 du Jalon 5 — un seul upload par session, mais le pattern reste fragile). Création = source unique de vérité. |
| Tag mutable post-création | Implique migration des données existantes (segments → chunks ou inverse), perte de fidélité, complexité orchestration. YAGNI strict. |

## Suivi et révision

À revisiter après :

- la première vraie session JDR en mode non_diarised (validation T059 + benchmark coût/qualité réel),
- l'arrivée de la diarisation locale (Jalon 9) — réévaluer la stratégie POV-aware et l'éventuelle promotion du map-reduce sur le mode diarised.

Cet ADR ne sera amendé qu'en cas de **superseding** par un futur ADR (convention immutabilité ADR, cf. ADR 0001).
