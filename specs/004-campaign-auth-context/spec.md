# Feature Specification: Campaign Auth Context

**Feature Branch**: `004-campaign-auth-context`
**Created**: 2026-05-30
**Status**: Draft
**Input**: User description: "BD-4 frontend handoff requests campaigns as the JDR multi-tenancy unit, campaign memberships, a current authenticated user context endpoint, and campaign-scoped JDR data access."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Current user campaign context (Priority: P1)

Un utilisateur connecte au front JDR veut obtenir son identite applicative, sa campagne active et son role dans cette campagne afin que l'interface puisse initialiser `useCurrentUser()` sans mock et sans logique metier dupliquee cote front.

**Why this priority**: Sans contexte utilisateur/campagne live, le runtime front ne peut pas connecter les composants JDR a un contrat backend stable.

**Independent Test**: Avec une session web valide pour un MJ et une campagne par defaut existante, appeler `GET /services/jdr/auth/me` et verifier que la reponse contient l'utilisateur, la campagne active, le role `mj` et `character_id: null`. Repeter avec un joueur et verifier `role: player`.

**Acceptance Scenarios**:

1. **Given** un MJ authentifie avec une campagne par defaut, **When** le front appelle `GET /services/jdr/auth/me`, **Then** la reponse contient l'id utilisateur, le username, l'id et le nom de campagne active, le role `mj`, et aucun personnage actif.
2. **Given** un joueur authentifie membre d'une campagne, **When** le front appelle `GET /services/jdr/auth/me`, **Then** la reponse contient le role `player` et le `character_id` associe si disponible.
3. **Given** une requete sans session valide, **When** le front appelle `GET /services/jdr/auth/me`, **Then** le backend retourne `401` au format Problem Details deja utilise par les routes protegees.
4. **Given** un utilisateur authentifie sans campagne accessible, **When** le front appelle `GET /services/jdr/auth/me`, **Then** la reponse authentifiee indique `active_campaign: null` afin que le front bloque les operations JDR.

---

### User Story 2 - Default campaign membership for existing users (Priority: P2)

L'operateur veut que les utilisateurs existants continuent a fonctionner apres la migration, en etant automatiquement rattaches a une campagne JDR par defaut qui represente le mode V1 single-campaign.

**Why this priority**: La nouvelle notion de campagne ne doit pas casser les comptes deja crees ni demander une correction manuelle de la base locale.

**Independent Test**: Sur une base contenant des utilisateurs existants, appliquer la migration/seed puis verifier que chaque utilisateur actif possede une campagne par defaut, un membership, et un role derive de son profil actuel.

**Acceptance Scenarios**:

1. **Given** une base avec des utilisateurs existants, **When** la migration BD-4 est appliquee, **Then** une campagne par defaut existe et chaque utilisateur existant en est membre.
2. **Given** un utilisateur existant avec profil `gm`, **When** les memberships sont crees, **Then** son role de campagne est `mj`.
3. **Given** un utilisateur existant avec profil `user`, **When** les memberships sont crees, **Then** son role de campagne est `player`.
4. **Given** un utilisateur sans campagne par defaut explicite, **When** son contexte courant est resolu, **Then** le backend selectionne de maniere deterministe son premier membership disponible.

---

### User Story 3 - Campaign-scoped JDR data access (Priority: P3)

Un utilisateur JDR veut consulter et modifier uniquement les donnees de sa campagne active, sans envoyer de `campaign_id` depuis le front et sans pouvoir lire les donnees d'une autre campagne.

**Why this priority**: La campagne devient l'unite de multi-tenancy. Meme si la V1 ne contient qu'une campagne en pratique, le contrat doit deja empecher les fuites de donnees quand une deuxieme campagne existe en base.

**Independent Test**: Creer deux campagnes en base, rattacher l'utilisateur connecte a la premiere, creer des donnees JDR dans les deux campagnes, puis verifier que les endpoints JDR ne lisent, creent et modifient que les donnees de la campagne active derivee de la session.

**Acceptance Scenarios**:

1. **Given** un utilisateur membre de la campagne A et des sessions JDR dans les campagnes A et B, **When** il liste les sessions, **Then** seules les sessions de la campagne A sont retournees.
2. **Given** un utilisateur membre de la campagne A, **When** il cree une session JDR, **Then** la session creee est automatiquement rattachee a la campagne A.
3. **Given** une requete de creation JDR venant du front, **When** le body contient des donnees metier valides, **Then** le body n'a pas besoin de contenir `campaign_id`.
4. **Given** un identifiant de ressource appartenant a une autre campagne, **When** un utilisateur tente une operation JDR dessus, **Then** l'operation est refusee ou traitee comme ressource inexistante selon la convention d'erreur existante.

---

### User Story 4 - User management remains campaign-aware (Priority: P4)

Un MJ veut continuer a creer, lister, modifier et desactiver les utilisateurs via les endpoints existants, avec un rattachement automatique a la campagne active pour que la V1 reste simple et que la V2 soit preparee.

**Why this priority**: Les contrats front deja livres pour la gestion d'utilisateurs ne doivent pas changer, mais leur comportement doit respecter le nouveau contexte de campagne.

**Independent Test**: Connecte comme MJ, creer un nouvel utilisateur, verifier qu'il est membre de la campagne active avec un role derive de son profil, puis verifier que la liste des utilisateurs ne retourne que les membres de la campagne active.

**Acceptance Scenarios**:

1. **Given** un MJ connecte avec une campagne active, **When** il cree un utilisateur, **Then** l'utilisateur est automatiquement ajoute comme membre de la campagne active.
2. **Given** un utilisateur cree avec profil `gm`, **When** son membership est cree, **Then** son role de campagne est `mj`.
3. **Given** un utilisateur cree avec profil `user`, **When** son membership est cree, **Then** son role de campagne est `player`.
4. **Given** un utilisateur desactive par suppression logique, **When** son compte est desactive, **Then** son membership reste conserve pour tracabilite.

---

### Edge Cases

- Un utilisateur authentifie peut exister sans campagne accessible ; il reste authentifie, mais ne peut pas executer d'operation JDR.
- Plusieurs memberships peuvent exister pour un meme utilisateur, mais la V1 ne cree automatiquement qu'un seul membership par utilisateur.
- Le champ historique `profile` reste disponible en V1 pour compatibilite et sert a deriver le role de campagne lors des creations et migrations.
- Les requetes front ne doivent pas pouvoir choisir arbitrairement une campagne via body ou query param.
- Une deuxieme campagne creee manuellement en base pour les tests ne doit jamais apparaitre dans les resultats d'un utilisateur non membre.
- La suppression logique d'un utilisateur ne doit pas supprimer les traces de membership.
- Le front ne dispose pas d'UI de gestion de campagnes en V1 ; aucune route publique de creation ou modification de campagne n'est requise.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST represent a campaign as the JDR multi-tenancy unit with a stable identity, display name, owner, and creation timestamp.
- **FR-002**: The system MUST represent campaign membership between users and campaigns, including role (`mj` or `player`), optional character identity, and join timestamp.
- **FR-003**: The system MUST keep a user's default campaign reference for resolving the campaign opened after login.
- **FR-004**: The system MUST expose `GET /services/jdr/auth/me` for authenticated web sessions.
- **FR-005**: `GET /services/jdr/auth/me` MUST return the authenticated user's id and username.
- **FR-006**: `GET /services/jdr/auth/me` MUST return `active_campaign` with id, name, role, and nullable `character_id` when a campaign context exists.
- **FR-007**: `GET /services/jdr/auth/me` MUST return `active_campaign: null` for an authenticated user with no campaign context.
- **FR-008**: `GET /services/jdr/auth/me` MUST return `401` Problem Details when no valid authenticated session exists.
- **FR-009**: The active campaign resolution MUST first use the user's default campaign when it is set and accessible.
- **FR-010**: The active campaign resolution MUST fall back to the user's first available membership in deterministic order when no usable default campaign exists.
- **FR-011**: Existing users MUST be assigned to the V1 default campaign during migration or local seed.
- **FR-012**: Existing `gm` profiles MUST map to campaign role `mj`; existing `user` profiles MUST map to campaign role `player`.
- **FR-013**: Newly created users through the existing JDR user management flow MUST be automatically added to the active or default campaign.
- **FR-014**: Soft-deleted users MUST keep their campaign membership records for auditability.
- **FR-015**: JDR data reads MUST be scoped to the active campaign derived from the authenticated session.
- **FR-016**: JDR data writes MUST assign the active campaign derived from the authenticated session.
- **FR-017**: JDR create/update request bodies MUST NOT require the front to provide `campaign_id`.
- **FR-018**: Existing JDR user management API contracts MUST remain stable at the request/response level unless explicitly documented for `/auth/me`.
- **FR-019**: The system MUST NOT expose V1 campaign management endpoints for creating, listing, switching, or editing campaigns.
- **FR-020**: The generated backend API documentation MUST include the new `/services/jdr/auth/me` contract so the frontend can regenerate client types.
- **FR-021**: The local development seed MUST create or preserve the V1 default campaign and memberships required for a working local frontend.
- **FR-022**: Responses for authenticated user context SHOULD prevent shared/browser cache reuse.

### Key Entities

- **Campaign**: A JDR play space and multi-tenancy boundary. Key attributes: stable id, name, owner, creation timestamp.
- **Campaign Membership**: A user's participation in a campaign. Key attributes: user, campaign, role (`mj` or `player`), optional character identity, join timestamp.
- **User**: Existing authenticated web account. It keeps its current profile in V1 and gains a default campaign reference.
- **Active Campaign Context**: The resolved campaign membership used by the backend to answer `/auth/me` and scope JDR data operations.
- **JDR Data Record**: Any campaign-owned business data such as sessions, characters, players, or user-facing JDR resources.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing active users receive a default campaign context after migration or local seed.
- **SC-002**: `GET /services/jdr/auth/me` returns the expected user, campaign, role, and character fields for both one MJ and one player test account.
- **SC-003**: 100% of unauthenticated `/auth/me` calls return the existing protected-route `401` Problem Details format.
- **SC-004**: In a two-campaign test setup, a user sees 0 records from campaigns where they have no active membership.
- **SC-005**: 100% of covered JDR creation flows assign campaign ownership without requiring `campaign_id` in request bodies.
- **SC-006**: Creating a user through the existing user management flow creates exactly one V1 campaign membership for that user.
- **SC-007**: The frontend can replace its `/auth/me` mock with the live backend response without changing consuming JDR components.

## Assumptions

- BD-4 is a backend feature driven by the frontend handoff dated 2026-05-29.
- V1 has exactly one seeded/default campaign for normal usage, even though the data model supports multiple memberships.
- `profile` remains on users for V1 compatibility and is not removed by this feature.
- Campaign creation, campaign switching, membership administration UI, tenant/organization concepts, and user default-campaign editing are out of scope.
- `character_id` may be null for MJ users and for players until character linkage is available.
- The existing session-cookie authentication from the previous auth feature is reused.
- The exact list of JDR data endpoints to scope will be finalized during planning by inspecting the current OpenAPI/routes.
