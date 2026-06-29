# Feature Specification: Epic 8 — Artefacts JDR éditables par le MJ + lectures joueur

**Feature Branch**: `018-artifact-editing`
**Created**: 2026-06-29
**Status**: Draft
**Input**: Handoffs backend BD-23 à BD-27, dérivés de l'ADR frontend `architecture-artifact-editing-epic8.md` (points de décision DP-1 à DP-7, DP-4 = Option B confirmée le 2026-06-29).

## User Scenarios & Testing *(mandatory)*

Les artefacts d'une session (résumé, récit, carte d'éléments, points de vue par PJ) sont aujourd'hui produits par l'IA et **en lecture seule**. Cet epic les rend **modifiables à la main par le MJ** (propriétaire de la campagne), protège ces modifications contre un écrasement accidentel, et ouvre deux lectures supplémentaires aux joueurs. Chaque story ci-dessous correspond à un handoff backend traçable (BD-23 → BD-27) et est indépendamment livrable et testable.

### User Story 1 - Le MJ corrige le résumé, le récit ou un point de vue (Priority: P1) — BD-23

Le MJ relit un artefact texte généré par l'IA et veut corriger une erreur (nom mal transcrit, formulation), sans relancer une génération coûteuse. Il enregistre son texte corrigé et le récupère immédiatement tel quel.

**Why this priority**: C'est la valeur centrale de l'epic — rendre les artefacts éditables. Sans elle, rien d'autre n'a de sens. Livrable seule = MVP utilisable.

**Independent Test**: Sur une session ayant un résumé/récit/POV généré, soumettre un texte corrigé et vérifier qu'une relecture renvoie exactement ce texte ; vérifier qu'un non-MJ ne peut pas éditer et qu'éditer un artefact inexistant est refusé.

**Acceptance Scenarios**:

1. **Given** une session dont le résumé est généré, **When** le MJ enregistre un résumé corrigé, **Then** la lecture du résumé renvoie le texte corrigé et l'opération est immédiate (pas de mise en file d'attente).
2. **Given** une session sans récit généré, **When** le MJ tente de l'éditer, **Then** l'édition est refusée avec la même sémantique « artefact absent » que les lectures existantes.
3. **Given** un utilisateur à rôle lecture seule sur la campagne, **When** il tente d'éditer un artefact, **Then** l'accès est refusé.
4. **Given** un POV existant pour un PJ donné, **When** le MJ enregistre un POV corrigé pour ce PJ, **Then** seul ce POV est modifié.

---

### User Story 2 - Le MJ réorganise la carte d'éléments en catégories libres (Priority: P2) — BD-26

La carte d'éléments générée range les éléments dans 4 buckets fixes (PNJ, Lieux, Objets, Indices). Le MJ veut ajouter, modifier, supprimer des éléments et les classer sous **n'importe quelle catégorie** de son choix, puis enregistrer la carte entière en un seul geste.

**Why this priority**: Forte valeur d'usage mais nécessite une migration du modèle de données et une rupture de contrat ; on la livre après le socle d'édition texte.

**Independent Test**: Générer une carte d'éléments (le système la présente en liste plate taggée par catégorie), la remplacer par une carte comportant une catégorie personnalisée et des descriptions longues, vérifier le round-trip complet.

**Acceptance Scenarios**:

1. **Given** une carte d'éléments fraîchement générée, **When** le MJ la lit, **Then** chaque élément porte une catégorie (les 4 buckets canoniques sont projetés en « PNJ / Lieux / Objets / Indices ») et la structure est une liste plate d'éléments `{catégorie, nom, description}`.
2. **Given** une carte d'éléments existante, **When** le MJ enregistre une carte remplacée contenant une catégorie libre inédite, **Then** la lecture renvoie la carte telle qu'enregistrée, catégorie personnalisée comprise.
3. **Given** un élément édité à la main avec une description de plus de 25 mots, **When** le MJ enregistre, **Then** la description est acceptée (la limite de 25 mots ne s'applique qu'à la génération IA, pas au stockage).
4. **Given** une carte existante, **When** une régénération IA produit les 4 buckets, **Then** ils sont aplatis en éléments taggés par catégorie selon la correspondance npcs→PNJ, locations→Lieux, items→Objets, clues→Indices.

---

### User Story 3 - Les modifications manuelles ne sont jamais écrasées en silence (Priority: P2) — BD-24

Le MJ a investi du temps à corriger un artefact. S'il (ou un automatisme) relance une génération IA, le travail manuel ne doit pas disparaître sans avertissement. Le système marque l'artefact comme « édité » et bloque une régénération destructive sauf confirmation explicite.

**Why this priority**: Filet de sécurité indispensable dès que l'édition existe ; s'aligne sur la règle « non-destructif tant que pas de succès » de Story 7.4. Livrable après US1 car elle protège ce qu'US1 introduit.

**Independent Test**: Éditer un artefact, vérifier qu'il est marqué « édité » avec une date d'édition ; tenter une régénération et vérifier qu'elle est bloquée tant que la confirmation explicite n'est pas fournie, puis autorisée avec.

**Acceptance Scenarios**:

1. **Given** un artefact généré puis édité à la main, **When** on le lit, **Then** il indique qu'il a été édité et porte une date d'édition, tandis que les informations de la dernière génération IA (modèle utilisé, date de génération) restent inchangées.
2. **Given** un artefact marqué « édité », **When** on demande une régénération sans confirmation, **Then** l'opération est refusée par un conflit invitant à confirmer.
3. **Given** le même artefact édité, **When** on demande une régénération avec confirmation explicite, **Then** la régénération s'exécute et remplace le contenu.
4. **Given** un artefact jamais édité, **When** on demande une régénération, **Then** elle s'exécute sans confirmation supplémentaire (comportement actuel inchangé).

---

### User Story 4 - Les textes longs sont stockés sans troncature (Priority: P3) — BD-25

Un résumé ou un récit édité à la main peut atteindre plusieurs milliers de mots (le besoin produit anticipe ~10 000 mots). Le MJ doit pouvoir enregistrer un texte long et le relire intégralement.

**Why this priority**: Habilitant de faible risque (vérification/migration de type de colonne) ; nécessaire pour qu'US1 tienne ses promesses sur les longs textes, mais sans surface fonctionnelle propre.

**Independent Test**: Enregistrer un artefact texte de plusieurs milliers de mots et vérifier qu'il est relu sans troncature.

**Acceptance Scenarios**:

1. **Given** le MJ édite un résumé de plusieurs milliers de mots, **When** il enregistre, **Then** la relecture renvoie l'intégralité du texte sans coupure.
2. **Given** une longueur de texte importante, **When** l'enregistrement a lieu, **Then** aucune limite haute stricte n'est imposée côté stockage.

---

### User Story 5 - Les joueurs lisent le résumé et les éléments de leurs sessions (Priority: P3) — BD-27

Un joueur (et non le MJ) veut consulter le résumé et la carte d'éléments des sessions auxquelles son personnage a participé, en lecture seule, dans le même esprit que la consultation de son propre point de vue déjà disponible.

**Why this priority**: Extension de surface de lecture, indépendante de l'édition. Réutilise le mécanisme d'autorisation joueur existant.

**Independent Test**: Avec un compte joueur lié à un PJ ayant participé à une session, lire le résumé et les éléments de cette session ; vérifier que les sessions où le PJ n'a pas participé restent inaccessibles.

**Acceptance Scenarios**:

1. **Given** un joueur dont le PJ a participé à une session, **When** il demande le résumé de cette session, **Then** il l'obtient en lecture seule (format structuré et export texte), selon la même projection que les lectures joueur existantes.
2. **Given** ce même joueur, **When** il demande la carte d'éléments de cette session, **Then** il l'obtient en lecture seule.
3. **Given** une session à laquelle le PJ du joueur n'a pas participé, **When** il en demande le résumé ou les éléments, **Then** l'accès est refusé.

---

### Edge Cases

- Édition concurrente : deux requêtes d'édition du même artefact arrivent quasi simultanément → le dernier écrivain gagne (écriture atomique), pas de fusion ; comportement à confirmer en planification mais pas de garantie de verrouillage optimiste dans ce périmètre.
- Migration des éléments : des lignes existantes sont déjà rangées dans les 4 buckets fixes → elles doivent être converties en éléments taggés par catégorie sans perte lors de la migration.
- Régénération forcée d'un artefact édité : si la nouvelle génération échoue, l'artefact édité existant ne doit pas être perdu (cohérence avec la règle non-destructive de Story 7.4).
- Description vide ou catégorie vide dans une carte d'éléments enregistrée → règle de validation à appliquer (rejet ou normalisation).
- Texte d'édition vide soumis sur un artefact texte → décider rejet vs effacement (par défaut : rejet, l'édition n'est pas une suppression).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (BD-23) : Le MJ propriétaire de la campagne MUST pouvoir remplacer le texte d'un résumé, d'un récit et d'un point de vue de PJ par une écriture **synchrone** (effet immédiat, sans mise en file d'attente).
- **FR-002** (BD-23) : Le MJ MUST pouvoir remplacer la carte d'éléments d'une session en **un seul remplacement atomique** de la carte entière.
- **FR-003** (BD-23) : Une édition MUST être refusée si l'artefact ciblé n'existe pas encore, en réutilisant la sémantique « artefact absent » déjà en vigueur sur les lectures.
- **FR-004** (BD-23) : L'édition d'un artefact MUST être réservée au MJ propriétaire de la campagne ; tout rôle en lecture seule MUST être refusé.
- **FR-005** (BD-24) : Chaque artefact MUST exposer un indicateur « a été édité à la main » et une date de dernière édition (et, si peu coûteux, l'auteur de l'édition).
- **FR-006** (BD-24) : Une édition manuelle MUST NOT altérer l'enregistrement de la dernière génération IA (modèle utilisé, date de génération) ; ces champs restent l'historique immuable de la génération.
- **FR-007** (BD-24) : Une demande de régénération visant un artefact marqué « édité » MUST être refusée par un conflit, sauf si une confirmation explicite est fournie.
- **FR-008** (BD-24) : Avec la confirmation explicite, la régénération MUST s'exécuter et remplacer le contenu ; sur un artefact non édité, la régénération MUST se comporter comme aujourd'hui (sans confirmation).
- **FR-009** (BD-24) : Une régénération forcée qui échoue MUST NOT détruire l'artefact édité existant (non-destructif tant que pas de succès, cohérent avec Story 7.4).
- **FR-010** (BD-25) : Le stockage du texte des artefacts MUST accepter des contenus de plusieurs milliers de mots sans troncature ni limite haute stricte côté stockage.
- **FR-011** (BD-26) : Un élément MUST être modélisé comme `{ catégorie, nom, description }`, et la carte d'éléments MUST être une liste plate d'éléments, chacun rattaché à une catégorie librement choisie par le MJ.
- **FR-012** (BD-26) : La génération IA MUST continuer de produire les 4 buckets canoniques, que le système MUST aplatir en éléments taggés par catégorie selon : npcs→« PNJ », locations→« Lieux », items→« Objets », clues→« Indices ».
- **FR-013** (BD-26) : Les données d'éléments existantes (4 buckets fixes) MUST être migrées une fois vers le modèle taggé par catégorie sans perte.
- **FR-014** (BD-26) : La description d'un élément édité à la main MUST accepter un texte dépassant 25 mots (la limite de 25 mots est une consigne de génération, pas une contrainte de stockage) ; une borne généreuse de validation est admise mais aucune limite stricte ne doit bloquer un usage normal.
- **FR-015** (BD-27) : Un joueur MUST pouvoir lire, en lecture seule, le résumé et la carte d'éléments des sessions auxquelles son PJ lié a participé, via le même mécanisme que les lectures joueur existantes (format structuré + export texte).
- **FR-016** (BD-27) : L'accès joueur au résumé et aux éléments d'une session MUST être refusé si le PJ lié au compte n'a pas participé à cette session.
- **FR-017** (transverse, DP-6) : Le format de stockage et d'échange du texte des artefacts MUST rester le Markdown (parité entre rendu, prévisualisation et export texte) ; cet epic n'introduit pas de format riche alternatif.
- **FR-018** (transverse) : Le *contrat d'appel* des endpoints de génération existants (POST → job asynchrone) MUST rester inchangé, hormis l'ajout du paramètre de confirmation de FR-007. La **forme stockée/projetée** de la carte d'éléments change néanmoins (liste plate taggée par catégorie, FR-011/FR-012) : c'est une évolution attendue de BD-26, pas une exception à cette règle.

### Key Entities *(include if feature involves data)*

- **Artefact** : production rattachée à une session, de l'un des types résumé / récit / carte d'éléments / point de vue. Attributs notables : contenu (Markdown pour les artefacts texte), modèle utilisé et date de génération (immuables, issus de la dernière génération IA), indicateur « édité » + date d'édition (+ éventuel auteur) introduits par cet epic.
- **Élément** : unité de la carte d'éléments, désormais `{ catégorie (texte libre), nom, description }`. Remplace les 4 listes parallèles à buckets fixes.
- **Carte d'éléments** : collection plate d'éléments rattachée à une session, remplacée atomiquement à l'édition.
- **Point de vue (POV)** : artefact texte propre à un PJ d'une session.
- **Lien PJ ↔ compte** : association existante (pré-requis BD-12 / Story 4.16) qui détermine quelles sessions un joueur peut consulter ; réutilisée, non modifiée par cet epic.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** : Un MJ peut corriger un résumé, un récit ou un POV et constater son texte exact à la relecture, en une seule opération immédiate (pas d'attente de traitement asynchrone).
- **SC-002** : 100 % des artefacts édités à la main sont signalés comme tels et conservent intactes les informations de leur dernière génération IA.
- **SC-003** : Aucune modification manuelle n'est perdue sans une confirmation explicite de l'utilisateur (zéro écrasement silencieux).
- **SC-004** : Un artefact texte d'au moins 10 000 mots est enregistré et relu intégralement sans troncature.
- **SC-005** : Le MJ peut classer des éléments sous une catégorie de son choix et les retrouver groupés par cette catégorie après enregistrement.
- **SC-006** : La migration des éléments existants se fait sans perte (le nombre d'éléments et leur contenu avant/après migration concordent).
- **SC-007** : Un joueur accède au résumé et aux éléments de ses sessions et se voit refuser celles auxquelles son PJ n'a pas participé (aucune fuite inter-sessions).

## Assumptions

- Le lien PJ ↔ compte (BD-12 / Story 4.16) est **déjà en place** : les lectures joueur existantes (`/me`, point de vue propre) fonctionnent, donc BD-27 réutilise ce socle sans dépendance bloquante.
- L'édition suit une sémantique « dernier écrivain gagne » : pas de verrouillage optimiste / gestion de conflit d'édition concurrente dans ce périmètre (à confirmer en planification, non requis par les issues).
- La confirmation de régénération destructive est portée par un paramètre explicite côté appelant ; l'UI frontend affiche l'avertissement correspondant (hors périmètre backend).
- La rupture de contrat sur la carte d'éléments est assumée : les clients régénèrent leurs types après livraison ; le périmètre de cet epic n'inclut pas de phase de compatibilité ascendante du contrat des éléments.
- Le besoin produit de ~10 000 mots sert de borne de référence pour les tests, sans constituer une limite imposée.

## Dependencies

- **BD-12 / Story 4.16** (lien PJ ↔ compte) : pré-requis de BD-27, considéré satisfait (voir Assumptions).
- **Story 7.4** (règle non-destructive + validation clé cloud, en cours sur la branche epic-7 servant de base) : BD-24 doit être conçu conjointement pour éviter des sémantiques d'écriture contradictoires.
- **ADR frontend** `architecture-artifact-editing-epic8.md` : source des décisions DP-1 à DP-7 (DP-4 = Option B, catégories libres, confirmée le 2026-06-29).
