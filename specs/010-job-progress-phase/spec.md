# Feature Specification: JDR Job Progress Phase

**Feature Branch**: `codex/010-job-progress-phase`
**Created**: 2026-06-03
**Status**: Draft
**Input**: User description: "BD-10 backend handoff: expose real transcription job progress through phase and progress_percent for JDR session transcription jobs."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Voir l'avancement réel d'une transcription (Priority: P1)

En tant que MJ ayant lancé une transcription de session JDR, je veux voir une phase lisible et un pourcentage réel pendant le traitement afin de savoir si le système réduit l'audio, transcrit les fragments, ou a terminé.

**Why this priority**: C'est la valeur principale de BD-10. Le polling actuel indique seulement si le job est en attente, en cours, réussi ou échoué ; il ne permet pas de distinguer une progression réelle d'une estimation d'interface.

**Independent Test**: Peut être testé en lançant une transcription découpée en plusieurs fragments, puis en consultant régulièrement le détail du job pour vérifier que la phase et le pourcentage évoluent pendant le traitement.

**Acceptance Scenarios**:

1. **Given** une transcription en cours sur plusieurs fragments, **When** le MJ consulte le détail du job, **Then** il voit une phase `transcribing` et un `progress_percent` compris entre 0 et 99.
2. **Given** une transcription qui nécessite une réduction audio préalable, **When** le traitement démarre, **Then** le détail du job peut indiquer la phase `reducing` avec un pourcentage initial à 0.
3. **Given** une transcription terminée avec succès, **When** le MJ consulte le détail du job, **Then** il voit `status="succeeded"`, `phase="done"` et `progress_percent=100`.

---

### User Story 2 - Conserver un état fiable malgré les métadonnées absentes (Priority: P2)

En tant que MJ ou client frontend, je veux que le détail d'un job reste disponible même si l'avancement détaillé n'est pas encore connu ou n'est plus disponible, afin que l'interface ne casse pas sur des jobs anciens, purgés ou antérieurs au déploiement.

**Why this priority**: Les champs d'avancement sont décoratifs et temporaires. Le statut métier du job doit rester la source fiable de complétion.

**Independent Test**: Peut être testé en consultant un job dont les métadonnées d'avancement sont absentes tout en vérifiant que la réponse reste valide et que le statut principal est présent.

**Acceptance Scenarios**:

1. **Given** un job dont les détails d'avancement sont absents, **When** le client consulte le détail du job, **Then** la réponse est réussie et expose `phase=null` et `progress_percent=null`.
2. **Given** un job en attente de traitement, **When** le client consulte le détail du job, **Then** le statut principal indique l'attente et les champs d'avancement peuvent rester nuls.
3. **Given** un job terminé dont les détails d'avancement ont expiré, **When** le client consulte le détail du job, **Then** le statut principal reste la source de vérité et aucun échec serveur n'est produit à cause des champs manquants.

---

### User Story 3 - Comprendre un échec sans perdre le dernier avancement connu (Priority: P3)

En tant que MJ, je veux que l'échec d'une transcription conserve la dernière phase et progression utile afin de comprendre que le traitement a réellement commencé et où il s'est arrêté.

**Why this priority**: Cette information améliore le diagnostic utilisateur et évite une impression trompeuse de retour à zéro après un échec.

**Independent Test**: Peut être testé en provoquant un échec pendant ou après une étape de traitement, puis en vérifiant que le détail du job indique l'échec sans réinitialiser le pourcentage.

**Acceptance Scenarios**:

1. **Given** une transcription qui échoue après avoir commencé, **When** le client consulte le détail du job, **Then** le statut principal indique l'échec et la phase peut indiquer `failed`.
2. **Given** une transcription qui échoue après une progression partielle, **When** le client consulte le détail du job, **Then** le pourcentage affiché correspond à la dernière valeur connue et n'est pas remis arbitrairement à 0.

### Edge Cases

- Un job est encore en file d'attente : les champs `phase` et `progress_percent` restent nuls, car la phase décrit uniquement le travail effectivement démarré.
- Les détails d'avancement sont indisponibles, expirés ou illisibles : le détail du job reste consultable avec un statut principal fiable.
- Une progression atteint la fin des fragments transcrits avant la persistance finale du résultat : le pourcentage ne doit pas annoncer 100 avant que le job soit réellement terminé.
- Une erreur survient pendant la réduction audio ou la transcription : la phase d'échec est visible lorsque disponible et le dernier pourcentage utile est conservé.
- Plusieurs lectures successives d'un job en cours : le pourcentage ne régresse pas entre deux lectures du même traitement.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le détail d'un job JDR MUST exposer un champ optionnel `phase` décrivant l'étape actuelle du traitement lorsqu'elle est connue.
- **FR-002**: Le champ `phase` MUST être limité aux valeurs `reducing`, `transcribing`, `done` et `failed` lorsqu'il est renseigné.
- **FR-003**: Le détail d'un job JDR MUST exposer un champ optionnel `progress_percent` représentant l'avancement réel connu sous forme d'entier de 0 à 100.
- **FR-004**: Pour un job en attente ou sans avancement connu, `phase` et `progress_percent` MUST pouvoir être nuls sans empêcher la consultation du job.
- **FR-005**: Pendant la transcription fragmentée, `progress_percent` MUST être calculé à partir du nombre de fragments terminés par rapport au nombre total de fragments.
- **FR-006**: Pendant une transcription en cours, `progress_percent` MUST rester dans l'intervalle 0 à 99 afin de réserver 100 à un traitement réellement terminé.
- **FR-007**: Lorsqu'un job réussit, le détail du job MUST indiquer `phase="done"` et `progress_percent=100` en complément du statut principal de réussite.
- **FR-008**: Lorsqu'un job échoue après avoir émis une progression, le détail du job MUST conserver le dernier `progress_percent` connu au lieu de le réinitialiser à 0.
- **FR-009**: Le statut principal du job MUST rester la source de vérité pour déterminer si le job est en attente, en cours, réussi ou échoué.
- **FR-010**: Les champs d'avancement détaillé MUST être documentés dans le contrat public consommé par le frontend.
- **FR-011**: Les lectures successives d'un même job en cours MUST présenter une progression monotone, sans diminution du pourcentage déjà observé.
- **FR-012**: La fonctionnalité MUST couvrir le polling existant du détail de job et ne MUST pas exiger de nouveau canal temps réel pour la version initiale.

### Key Entities *(include if feature involves data)*

- **JDR Transcription Job**: Travail asynchrone déclenché après l'upload d'une session audio JDR. Il possède un statut principal et peut exposer une progression détaillée temporaire.
- **Progress Phase**: Étape lisible du traitement, parmi réduction audio, transcription, terminé et échoué.
- **Progress Percent**: Pourcentage entier du traitement réellement observé, dérivé de l'étape courante et de l'avancement des fragments lorsque le découpage est utilisé.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Pour une transcription découpée en au moins 2 fragments, au moins une lecture intermédiaire du job expose `phase="transcribing"` avec un `progress_percent` entre 1 et 99 avant la réussite finale.
- **SC-002**: 100% des jobs terminés avec succès consultés avant expiration des détails d'avancement exposent `phase="done"` et `progress_percent=100`.
- **SC-003**: 100% des jobs sans détails d'avancement disponibles restent consultables sans erreur serveur et retournent des champs d'avancement nuls.
- **SC-004**: Sur un même job en cours, les valeurs observées de `progress_percent` ne diminuent jamais entre deux consultations successives.
- **SC-005**: Le contrat public du détail de job permet au frontend de typer `phase` comme une valeur fermée nullable et `progress_percent` comme un entier nullable.

## Assumptions

- Le besoin porte sur l'amélioration UX de l'avancement d'une transcription batch ; le statut principal du job existe déjà et reste inchangé.
- La version initiale enrichit le détail de job déjà consulté par le frontend ; un flux serveur temps réel reste hors scope tant qu'aucune mesure ne montre que la latence du polling gêne l'usage.
- Les champs d'avancement sont temporaires et best-effort : leur absence ne signifie pas que le job est inconnu ou invalide.
- Le frontend sait retomber sur son comportement actuel lorsque `phase` et `progress_percent` sont nuls.
- Le libellé utilisateur final des phases peut être localisé côté frontend ; le contrat backend expose des valeurs stables et fermées.
