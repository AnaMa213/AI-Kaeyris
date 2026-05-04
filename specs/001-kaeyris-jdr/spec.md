# Feature Specification: kaeyris-jdr — Assistant de session de jeu de rôle

**Feature Branch**: `001-kaeyris-jdr`
**Created**: 2026-05-04
**Status**: Draft
**Input**: User description: "Service kaeyris-jdr : assistant de session de jeu de rôle (Jalon 5). Mode batch (upload audio M4A 2-3h, traitement asynchrone) et mode live (stub documenté seulement). Sorties sur demande : transcription diarisée, résumé narratif, fiche d'éléments structurés (PNJ/lieux/items/indices), résumés point de vue par PJ. Utilisateurs : MJ (priorité haute) et joueurs (lecture). Volume cible : 1 session/semaine, 2-3h, 4-5 locuteurs."

## Clarifications

### Session 2026-05-04

- Q: Comment les joueurs s'authentifient-ils et accèdent-ils à leurs résumés ? → A: Une clé d'API Bearer par joueur, rôle `player`, avec un lien `player → PJ` persisté côté serveur ; les endpoints joueur sont scoppés au PJ associé.
- Q: Comment le mapping locuteur diarisé ↔ PJ est-il établi ? → A: Saisie manuelle a posteriori par le MJ par défaut (le MJ consulte la transcription puis envoie un mapping `{speaker_X: "PJ Y"}`). Une assistance par auto-suggestion contextuelle reste activable plus tard via un flag, sans la livrer au Jalon 5. L'identification par signature vocale est reportée à un jalon ultérieur.
- Q: Sous quels formats les artefacts sont-ils exposés ? → A: JSON par défaut sur toute l'API. En complément, export Markdown sur demande pour les artefacts narratifs (résumé narratif, fiche d'éléments, résumés POV). Pas d'export PDF/DOCX au Jalon 5.
- Q: Quelle est la politique de rétention de l'audio source ? → A: Purge automatique de l'audio source M4A dès que la transcription a été produite avec succès. Les artefacts dérivés (transcription, résumé, fiche, POV) sont conservés ; l'audio brut, lui, ne survit pas à sa transcription.
- Q: Quelle posture pour le provider de transcription ? → A: Hybride dès le Jalon 5. Le `TranscriptionAdapter` expose deux implémentations interchangeables : un provider cloud distant (par défaut, en cohérence avec le LLM cloud du Jalon 4) et un provider local s'exécutant sur un hôte GPU du LAN (PC RTX 4090). Le Pi 5 reste orchestrateur (API, file de jobs, stockage des artefacts) et ne transcrit pas lui-même. Le choix de l'implémentation se fait par configuration.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Le MJ archive et résume une session enregistrée (Priority: P1)

Le maître de jeu (MJ) sort d'une session de 2-3h dont l'audio a été enregistré (téléphone, micro de table, OBS…). Il dépose le fichier M4A via l'API et récupère, une fois le traitement terminé, une transcription diarisée (qui-dit-quoi) et un résumé narratif condensé qu'il peut relire avant la prochaine séance.

**Why this priority** : c'est le besoin fondateur du service. Sans cette boucle "upload → transcription + résumé narratif", le service n'a aucune valeur. C'est aussi le scénario qui matérialise toute la chaîne technique critique (upload, file d'attente, transcription, résumé), donc le bon MVP.

**Independent Test** : déposer un fichier audio M4A de session réelle ou de démonstration, attendre la complétion du job, récupérer la transcription diarisée et le résumé narratif. Le MJ doit pouvoir comprendre les grands événements de la session sans réécouter l'audio.

**Acceptance Scenarios** :

1. **Given** un MJ authentifié et un fichier M4A valide de 2-3h, **When** il soumet le fichier au mode batch, **Then** le service accepte la requête et renvoie un identifiant de session et un identifiant de job traçables.
2. **Given** un job batch en cours, **When** le MJ interroge l'état du job, **Then** il obtient un statut clair (`queued`, `running`, `succeeded`, `failed`) et, en cas d'échec, un motif lisible.
3. **Given** un job batch terminé avec succès, **When** le MJ demande la transcription, **Then** il reçoit un texte segmenté par tour de parole avec un libellé de locuteur (ex. `speaker_1`, `speaker_2`) et des bornes temporelles.
4. **Given** une transcription disponible, **When** le MJ demande le résumé narratif, **Then** il reçoit un texte condensé qui restitue les grandes étapes de la session dans l'ordre chronologique.

---

### User Story 2 — Le MJ extrait une fiche d'éléments structurés (Priority: P2)

Le MJ veut, à partir d'une session déjà transcrite, obtenir une fiche structurée listant les PNJ rencontrés, les lieux visités, les items trouvés ou échangés, et les indices/secrets révélés. Il s'en sert pour préparer la session suivante et tenir son codex de campagne.

**Why this priority** : forte valeur pédagogique pour le MJ, mais elle suppose que la transcription et le résumé narratif (P1) fonctionnent déjà. Indépendamment testable une fois P1 livré.

**Independent Test** : à partir d'une session déjà transcrite, demander la fiche d'éléments. Vérifier que les catégories (PNJ, lieux, items, indices) sont peuplées avec des entrées plausibles tirées du contenu réel de la session.

**Acceptance Scenarios** :

1. **Given** une session disposant d'une transcription, **When** le MJ demande la fiche d'éléments, **Then** il reçoit un objet structuré contenant quatre listes nommées (`npcs`, `locations`, `items`, `clues`).
2. **Given** la fiche est générée, **When** le MJ la consulte, **Then** chaque entrée porte un nom et une courte description ; les entrées sans nom propre dans la session sont étiquetées de manière neutre (ex. "marchand au turban rouge").
3. **Given** une session vide ou sans élément narratif identifiable dans une catégorie, **When** la fiche est générée, **Then** la liste correspondante est renvoyée vide plutôt qu'absente.

---

### User Story 3 — Les joueurs reçoivent des résumés "point de vue" (Priority: P3)

Pour chaque personnage-joueur (PJ) identifié dans la session, le service produit un résumé centré sur ce qu'a vécu/perçu/fait ce personnage. Le MJ peut diffuser ces résumés à chaque joueur entre deux sessions pour qu'il garde son fil narratif.

**Why this priority** : cas d'usage différenciant et fortement apprécié à la table, mais dépend d'une étape supplémentaire (associer un locuteur diarisé à un PJ nommé). Reste indépendamment testable une fois P1 livré.

**Independent Test** : sur une session disposant d'une transcription diarisée et d'un mapping locuteur ↔ PJ déclaré, demander les résumés POV. Vérifier qu'un résumé par PJ est généré, que le contenu privilégie le point de vue du PJ et n'expose pas d'information que ce PJ ne pouvait pas connaître (basé sur sa présence dans les scènes).

**Acceptance Scenarios** :

1. **Given** une session avec un mapping `speaker → PJ` fourni par le MJ, **When** le MJ demande les résumés POV, **Then** un résumé est généré par PJ déclaré.
2. **Given** un PJ absent de plusieurs scènes (silence du locuteur dans la diarisation), **When** son résumé POV est généré, **Then** le résumé reflète qu'il n'était pas témoin direct de ces scènes.
3. **Given** aucun mapping locuteur ↔ PJ n'a encore été fourni, **When** le MJ demande les résumés POV, **Then** le service répond par une erreur claire indiquant l'étape manquante (et non par un résumé inventé).

---

### User Story 4 — Les joueurs consultent en lecture leurs résumés (Priority: P3)

Un joueur veut, entre deux sessions, relire le résumé narratif global et son propre résumé POV pour la session précédente, sans dépendre du MJ pour lui transmettre par messagerie.

**Why this priority** : finalise le cycle d'usage côté table, mais reste secondaire face à la production des contenus eux-mêmes (P1, P2, P3). Peut aussi être livré ultérieurement en s'appuyant temporairement sur un partage hors-API par le MJ.

**Independent Test** : un joueur muni de ses identifiants peut récupérer le résumé narratif d'une session à laquelle il a participé, ainsi que le résumé POV de son propre PJ, sans pouvoir accéder aux résumés POV des autres PJ.

**Acceptance Scenarios** :

1. **Given** un joueur authentifié et un PJ qui lui est associé, **When** il consulte la liste des sessions, **Then** il ne voit que les sessions auxquelles son PJ a été associé.
2. **Given** un joueur authentifié, **When** il demande son résumé POV pour une session donnée, **Then** il obtient uniquement le résumé de son propre PJ.
3. **Given** un joueur authentifié, **When** il tente d'accéder au résumé POV d'un autre PJ, **Then** la requête est refusée.

---

### User Story 5 — Endpoint live "stub documenté" pour usage futur (Priority: P4)

Un endpoint de mode live est exposé mais non fonctionnel à ce jalon. Il sert à figer le contrat (format de la session live, événements attendus) pour le futur bot Discord qui rebroadcasteura le canal vocal. Toute requête au mode live renvoie une réponse explicite "not implemented yet" mais documentée dans l'OpenAPI.

**Why this priority** : ne livre aucune valeur fonctionnelle immédiate, mais matérialise la décision architecturale (deux modes structurellement distincts) sans coût significatif et évite une refonte future de l'API. À reporter sans regret si la pression de scope augmente.

**Independent Test** : la documentation OpenAPI publique liste les endpoints du mode live ; les appels effectifs au mode live renvoient un statut "non implémenté" avec un message clair pointant vers la documentation.

**Acceptance Scenarios** :

1. **Given** la documentation OpenAPI du service, **When** un développeur la consulte, **Then** il y trouve les endpoints du mode live avec leur schéma de requête/réponse et une mention explicite "stub — not yet implemented".
2. **Given** un client qui appelle un endpoint du mode live, **When** la requête arrive, **Then** le service renvoie un statut indiquant "non implémenté à ce jalon" avec un lien vers la documentation.

---

### Edge Cases

- **Fichier audio corrompu ou format inattendu** : le service refuse l'upload avec un message clair plutôt que de planter le job en cours d'exécution.
- **Fichier très volumineux ou silence prolongé** : le job ne doit ni boucler, ni consommer indéfiniment des ressources ; un timeout raisonné met le job en `failed` avec un motif.
- **Diarisation incertaine** (deux locuteurs très proches, brouhaha) : la transcription rend visible cette incertitude (ex. locuteur `unknown`) plutôt que de l'invisibiliser.
- **Sortie sur demande ré-exécutée** : si le MJ redemande un résumé déjà produit, le service ne refait pas la transcription si elle existe déjà — il réutilise le matériel disponible. La transcription elle-même n'est pas re-générable une fois l'audio source purgé : si elle est jugée erronée, le MJ doit ré-uploader l'audio.
- **Demande de POV pour un PJ non listé dans le mapping** : le service refuse explicitement plutôt que d'inventer un PJ.
- **Job qui échoue en cours de route** (panne LLM, panne transcription) : l'état du job le reflète, le MJ peut le redemander une fois le service rétabli sans réuploader le fichier audio.
- **Volume cumulé** : la rétention durable porte uniquement sur les artefacts dérivés (transcriptions, résumés, fiches, POV) puisque l'audio source est purgé après transcription (cf. FR-004) ; l'empreinte stockage reste maîtrisée à 1 session/semaine.

## Requirements *(mandatory)*

### Functional Requirements

**Mode batch — cycle de vie d'une session enregistrée**

- **FR-001** : Le service MUST permettre à un MJ authentifié de soumettre un fichier audio M4A représentant une session de jeu et MUST renvoyer un identifiant de session et un identifiant de job pour le suivi.
- **FR-002** : Le service MUST traiter la session de manière asynchrone (file d'attente de jobs) afin de ne pas bloquer la requête HTTP d'upload, qui doit répondre rapidement avec un accusé de réception.
- **FR-003** : Le service MUST exposer un endpoint d'interrogation de l'état d'un job, renvoyant au minimum les statuts `queued`, `running`, `succeeded`, `failed`, et un motif lisible en cas d'échec.
- **FR-004** : Le service MUST persister la session et les artefacts produits (transcription, résumé narratif, fiche, résumés POV) durablement. L'audio source, lui, MUST être supprimé automatiquement dès que la transcription correspondante a atteint l'état `succeeded` ; le service MUST tracer cet événement de purge avec un horodatage. La session, sa transcription et ses artefacts dérivés MUST rester accessibles après purge de l'audio.

**Capacités de sortie sur demande**

- **FR-005** : Le service MUST produire, pour chaque session traitée, une transcription complète segmentée par tour de parole, chaque segment portant un libellé de locuteur et des bornes temporelles.
- **FR-006** : Le service MUST produire, à la demande, un résumé narratif condensé d'une session disposant d'une transcription, restituant les grandes étapes dans l'ordre chronologique.
- **FR-007** : Le service MUST produire, à la demande, une fiche d'éléments structurés extraits de la session, organisée en quatre catégories nommées : PNJ, lieux, items, indices.
- **FR-008** : Le service MUST produire, à la demande, un résumé "point de vue" par PJ déclaré pour la session, centré sur ce que ce PJ a perçu ou fait.
- **FR-009** : Le service MUST permettre de re-déclencher la production de tout artefact (résumé, fiche, POV) sans relancer la transcription, dès lors que la transcription est déjà disponible.
- **FR-009a** : Le service MUST exposer chaque artefact (transcription, résumé narratif, fiche d'éléments, résumé POV) au format JSON sur l'API. Pour le résumé narratif, la fiche d'éléments et chaque résumé POV, le service MUST aussi proposer un export Markdown à la demande, dérivé du même contenu sans dégrader l'information. Aucun autre format de sortie (PDF, DOCX) n'est livré au Jalon 5.

**Mapping locuteur ↔ PJ**

- **FR-010** : Le service MUST permettre au MJ de fournir explicitement, après diarisation, un mapping `locuteur diarisé → PJ` pour une session donnée (saisie manuelle a posteriori), et MUST conserver ce mapping pour les générations ultérieures. Le service MUST permettre au MJ de remplacer ou compléter ce mapping tant qu'aucun résumé POV n'a été produit, et MUST tracer l'horodatage de la dernière mise à jour.
- **FR-010a** : Le service MUST NOT, au Jalon 5, tenter d'auto-suggérer le mapping ni d'identifier les locuteurs par signature vocale. Une auto-suggestion contextuelle reste possible derrière un flag explicitement désactivé par défaut, à activer dans un jalon ultérieur sans casser le contrat de FR-010.
- **FR-011** : Le service MUST refuser de produire des résumés POV tant qu'aucun mapping n'a été fourni pour la session concernée, en renvoyant un message d'erreur indiquant clairement l'étape manquante.

**Authentification et accès**

- **FR-012** : Le service MUST exiger une authentification par clé d'API Bearer sur tous ses endpoints, conformément à l'auth introduite au Jalon 2. Chaque clé porte un rôle parmi `gm` (maître de jeu) ou `player` (joueur).
- **FR-013** : Le MJ (rôle `gm`) MUST être l'unique acteur autorisé à uploader un audio, à fournir un mapping locuteur ↔ PJ, à déclencher la production des artefacts pour une session, et à enrôler/révoquer les clés joueur associées à ses PJ.
- **FR-014** : Un joueur (rôle `player`) authentifié MUST pouvoir consulter en lecture (a) le résumé narratif d'une session à laquelle son PJ est associé et (b) le résumé POV de son propre PJ ; il MUST NOT pouvoir accéder aux résumés POV d'autres PJ, ni aux endpoints d'écriture.
- **FR-014a** : Le service MUST persister, pour chaque clé `player`, le lien `player → PJ` qui définit son périmètre de lecture. Une clé `player` non liée à un PJ ne donne accès à aucune ressource.

**Mode live (stub documenté)**

- **FR-015** : Le service MUST exposer dans sa documentation OpenAPI le contrat des endpoints du mode live (initiation de session live, réception de flux audio, événements de fin) avec une mention explicite "stub — not yet implemented".
- **FR-016** : Toute requête réelle au mode live MUST renvoyer une réponse "non implémenté à ce jalon" sans simuler un comportement partiel.

**Fiabilité, ressources, observabilité fonctionnelle**

- **FR-017** : Le service MUST refuser un upload dont le format ou l'intégrité est invalide, sans déclencher de job, en renvoyant un message d'erreur clair.
- **FR-018** : Le service MUST appliquer un timeout raisonné aux jobs longs (transcription, génération LLM) afin de garantir qu'un job ne reste pas indéfiniment en `running`.
- **FR-019** : Le service MUST tracer les événements clés du cycle de vie d'une session (upload accepté, job démarré, transcription produite, audio source purgé, artefact demandé, échec) à des fins de diagnostic.

**Provider de transcription**

- **FR-020** : Le service MUST encapsuler la transcription derrière un `TranscriptionAdapter` agnostique, conformément au pattern d'adaptation déjà appliqué au LLM. La logique métier MUST NOT référencer un fournisseur concret.
- **FR-021** : Le service MUST exposer au moins deux implémentations interchangeables de `TranscriptionAdapter` : (a) un provider cloud distant et (b) un provider local s'exécutant sur un hôte GPU joignable depuis le réseau local (typiquement une station équipée d'un GPU performant, distincte du Pi 5). Le choix de l'implémentation active MUST se faire par configuration (variable d'environnement), sans modification du code métier.
- **FR-022** : Le Pi 5 MUST rester orchestrateur du service (API, file de jobs, stockage des artefacts) et MUST NOT exécuter lui-même la transcription, quelle que soit l'implémentation active.

### Key Entities *(include if feature involves data)*

- **Session** : représente une partie de jeu de rôle enregistrée et identifiée. Attributs notables : identifiant, date de la session, mode (batch / live), propriétaire (le MJ), état global, références vers son audio source et ses artefacts.
- **Job** : représente une exécution asynchrone de traitement (transcription, génération de résumé, génération de fiche, génération POV). Attributs notables : identifiant, type, état, motif d'échec éventuel, horodatages, lien vers la session concernée.
- **Audio source** : le fichier M4A original déposé par le MJ. Attributs notables : durée, taille, intégrité validée, propriétaire.
- **Transcription** : la sortie diarisée. Attributs notables : segments (locuteur, bornes temporelles, texte), score de confiance global éventuel.
- **Résumé narratif** : texte condensé associé à une session.
- **Fiche d'éléments structurés** : objet à quatre listes (PNJ, lieux, items, indices) ; chaque élément porte au minimum un nom et une description.
- **PJ (personnage-joueur)** : entité narrative déclarée par le MJ et associée à un locuteur diarisé via le mapping. Attributs notables : nom, joueur associé (clé `player`) optionnel.
- **Mapping locuteur ↔ PJ** : association explicite, propre à une session, entre un identifiant de locuteur diarisé et un PJ.
- **Résumé POV** : texte centré sur un PJ, dérivé d'une transcription et d'un mapping.
- **Clé d'API** : credential Bearer authentifiant un acteur. Attributs notables : identifiant, rôle (`gm` ou `player`), lien vers un PJ pour les clés `player`, état (active / révoquée).
- **Joueur (utilisateur)** : porteur d'une clé `player`, en lecture seule, associé à exactement un PJ. Périmètre de lecture limité aux sessions où ce PJ est mappé.
- **MJ (utilisateur)** : porteur d'une clé `gm`, propriétaire des sessions, autorisé à uploader, à déclarer le mapping, à déclencher toutes les productions et à gérer les clés `player`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** : pour 100 % des sessions M4A valides de 2-3h soumises en mode batch, le MJ reçoit une transcription diarisée et un résumé narratif exploitable sans avoir à réécouter l'audio.
- **SC-002** : sur une session de référence (audio de démonstration), le MJ identifie correctement le déroulé chronologique de la séance en lisant uniquement le résumé narratif, en moins de 5 minutes de lecture.
- **SC-003** : pour une session disposant d'une transcription, la production d'un résumé narratif, d'une fiche d'éléments ou d'un set de résumés POV est obtenue à la demande sans que le MJ ait à relancer la transcription (l'étape coûteuse est faite une seule fois).
- **SC-004** : la latence perçue par le MJ pour soumettre un upload est inférieure à 5 secondes (le job lourd s'exécute ensuite en arrière-plan).
- **SC-005** : un job batch standard (session 2-3h, 4-5 locuteurs) atteint l'état `succeeded` ou `failed` en moins de 60 minutes en régime nominal ; au-delà, un timeout raisonné déclenche un échec contrôlé.
- **SC-006** : un joueur identifié peut accéder à son résumé POV en moins de 2 clics/appels (lister mes sessions → demander mon résumé POV pour cette session) et ne peut accéder à aucun résumé POV qui ne le concerne pas (taux d'incidents d'accès = 0).
- **SC-007** : 100 % des erreurs renvoyées par le service portent un message lisible (cause + étape suggérée) plutôt qu'une trace technique brute, permettant au MJ de comprendre comment corriger sans support.
- **SC-008** : le contrat REST/WS du mode live est consultable dans la documentation publique du service avant le début du Jalon 6 ; aucun appel client réel au mode live ne renvoie de comportement partiel inventé.
- **SC-009** : un changement de provider de transcription (cloud ↔ local LAN) s'effectue par modification de configuration uniquement, sans toucher le code de `app/services/jdr/`. Le test de bascule est validé en environnement de dev en moins de 5 minutes (variable d'environnement + redémarrage du worker).

## Assumptions

- **Volume et fréquence** : la cible est 1 session/semaine, 2-3h, 4-5 locuteurs variables. Le dimensionnement (CPU/RAM/stockage, choix d'éventuels modèles locaux ou distants pour la transcription) est calé sur ce profil ; un usage très supérieur déclencherait une réévaluation.
- **Stockage et rétention** : l'audio source M4A est purgé automatiquement après production réussie de la transcription (cf. FR-004). Les artefacts dérivés (transcription, résumé narratif, fiche, POV) sont conservés durablement. Conséquence : la transcription n'est pas re-générable a posteriori sans nouvel upload — décision assumée pour limiter le coût de stockage et la surface privacy (voix des joueurs).
- **Format de sortie** : JSON sur l'API pour tous les artefacts (texte plein pour la transcription, objet structuré pour la fiche, prose pour résumés et POV). Export Markdown disponible à la demande pour le résumé narratif, la fiche d'éléments et les résumés POV. Le téléchargement PDF/DOCX et tout autre format binaire restent hors scope du jalon.
- **Provider de transcription** : posture hybride dès ce jalon, derrière un `TranscriptionAdapter` agnostique (cf. FR-020/021/022). Deux implémentations interchangeables coexistent : un provider cloud distant (par défaut, cohérent avec le LLM cloud du Jalon 4) et un provider local exécuté sur un hôte GPU du LAN (typiquement une station avec GPU performant, p. ex. RTX 4090, distincte du Pi 5). Le Pi 5 reste orchestrateur uniquement. Le choix de l'implémentation active se fait par configuration ; le choix du fournisseur cloud concret et du backend local concret (Whisper, faster-whisper, autre) sera arbitré dans le plan.
- **Surface privacy de la transcription** : avec le provider cloud, l'audio source quitte le réseau local le temps de la transcription, puis est purgé localement (cf. FR-004) ; avec le provider local, l'audio ne quitte jamais le LAN. Ce trade-off est documenté pour permettre au MJ de basculer en connaissance de cause.
- **Déclenchement des artefacts hors transcription** (résumé narratif, fiche, POV) : ils sont déclenchés explicitement par le MJ ("sortie sur demande"), pas systématiquement à la fin du job de transcription, afin de maîtriser les coûts LLM.
- **Identité du MJ** : un seul MJ utilisateur de la plateforme à ce jalon ; le multi-MJ avec isolation stricte n'est pas requis et reste hors scope.
- **Live mode** : le mode live n'est pas branché à Discord à ce jalon ; le contrat exposé sert à dérisquer la phase d'intégration future, sans engagement de stabilité d'API au-delà de ce qui est nécessaire à un jalon "stub".

## Outstanding Clarifications

> Aucune. Toutes les questions à scope-impactant ouvertes au moment du `/speckit-specify` ont été tranchées dans la session 2026-05-04 (cf. section "Clarifications" en haut du document). Le spec est prêt pour `/speckit-plan`.
