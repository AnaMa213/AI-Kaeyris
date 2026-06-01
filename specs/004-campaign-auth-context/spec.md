# Feature Specification: Campaign Auth Context

**Feature Branch**: `004-campaign-auth-context`
**Created**: 2026-05-31
**Status**: Draft
**Input**: User description: "Backend handoff BD-4: Campaigns + memberships + /auth/me. Add campaign as the JDR tenancy unit, attach existing and future users to the default campaign, expose the authenticated current context expected by the web front, and scope existing JDR data by the authenticated user's active campaign."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recuperer le contexte courant apres login (Priority: P1)

Un utilisateur connecte depuis le front veut que l'application sache qui il est, sur quelle campagne JDR il agit, et avec quel role. Le front doit pouvoir initialiser son etat de session sans mock ni valeur codee en dur.

**Why this priority**: Le runtime web est bloque sans un contexte courant fiable. Le login pose deja un cookie de session, mais le front n'a pas encore de source backend pour construire `authId`, `campaignId` et le role JDR courant.

**Independent Test**: Se connecter avec un compte actif, appeler le contrat de contexte courant avec le cookie de session, verifier que la reponse contient l'utilisateur public et la campagne active attendue. Refaire l'appel sans cookie valide et verifier un refus d'authentification.

**Acceptance Scenarios**:

1. **Given** un utilisateur actif connecte et membre d'une campagne, **When** le front demande le contexte courant, **Then** le systeme renvoie son `id`, son `username`, la campagne active, son role dans cette campagne et son personnage si applicable.
2. **Given** une requete sans session valide, **When** le front demande le contexte courant, **Then** le systeme renvoie une erreur d'authentification standard et ne divulgue aucun detail utilisateur.
3. **Given** un utilisateur authentifie mais sans membership de campagne, **When** le front demande le contexte courant, **Then** le systeme renvoie l'utilisateur public avec `active_campaign` a `null`.

---

### User Story 2 - Rattacher les utilisateurs a la campagne V1 (Priority: P2)

Un operateur qui a deja des utilisateurs web veut appliquer la feature sans recreer les comptes ni modifier manuellement chaque profil. Tous les utilisateurs existants doivent rejoindre la campagne V1 par defaut, et chaque nouvel utilisateur cree par un GM doit etre rattache automatiquement.

**Why this priority**: Sans migration et rattachement automatique, la reponse de contexte courant serait vide pour les comptes existants et le front resterait bloque apres login.

**Independent Test**: Partir d'une base contenant des utilisateurs `gm` et `user`, appliquer la feature, verifier qu'une campagne par defaut existe, que chaque utilisateur actif est membre, et qu'un nouvel utilisateur cree ensuite devient membre sans champ de campagne dans la requete.

**Acceptance Scenarios**:

1. **Given** une base avec des utilisateurs existants, **When** la feature est appliquee, **Then** une campagne par defaut existe et chaque utilisateur existant dispose d'un membership sur cette campagne.
2. **Given** un GM connecte dans la campagne par defaut, **When** il cree un nouvel utilisateur, **Then** le nouvel utilisateur est automatiquement membre de cette campagne avec un role derive de son profil.
3. **Given** un utilisateur supprime logiquement, **When** l'historique des memberships est consulte par le systeme, **Then** le rattachement reste disponible pour audit mais l'utilisateur ne peut plus ouvrir de session.

---

### User Story 3 - Isoler les donnees JDR par campagne active (Priority: P2)

Un GM ou joueur membre d'une campagne ne doit voir, creer ou modifier que les donnees JDR rattachees a sa campagne active. Le front ne doit pas pouvoir choisir une autre campagne via le corps de requete ou un parametre libre.

**Why this priority**: La campagne devient l'unite de cloisonnement fonctionnel. Sans isolation, la future evolution multi-campagne serait fragile et des donnees d'une autre table pourraient apparaitre dans l'interface.

**Independent Test**: Creer deux campagnes en base, rattacher l'utilisateur de test a la premiere, placer une session JDR dans chaque campagne, puis verifier que les listes et lectures authentifiees ne retournent que les donnees de la campagne active.

**Acceptance Scenarios**:

1. **Given** un utilisateur membre de la campagne A, **When** il liste les sessions JDR, **Then** seules les sessions de la campagne A sont renvoyees.
2. **Given** un utilisateur membre de la campagne A, **When** il cree une session JDR, **Then** la session est rattachee automatiquement a la campagne A.
3. **Given** une requete qui tente de fournir un autre identifiant de campagne, **When** le systeme traite l'action, **Then** la portee est toujours derivee de la session authentifiee et non de la valeur fournie par le client.

---

### User Story 4 - Preserver les contrats web existants (Priority: P3)

Un utilisateur du front existant veut continuer a se connecter, se deconnecter et gerer les utilisateurs comme aujourd'hui. La feature ajoute le contexte campagne sans casser les formulaires ni les appels deja livres.

**Why this priority**: La valeur de BD-4 est de debloquer le contexte runtime, pas de rouvrir le chantier login/users. Les contrats existants doivent rester stables pour limiter le risque de regression.

**Independent Test**: Executer les scenarios existants de setup, login, logout et CRUD utilisateurs, puis verifier que les nouveaux champs de campagne n'obligent aucun changement dans leurs corps de requete.

**Acceptance Scenarios**:

1. **Given** le formulaire de login existant, **When** un utilisateur envoie `username`, `profile` et `password`, **Then** le login conserve son comportement actuel et pose le cookie de session.
2. **Given** le CRUD utilisateur existant, **When** un GM cree, liste, modifie ou supprime un utilisateur, **Then** les corps de requete restent compatibles et aucun secret n'est expose.
3. **Given** le front consomme le contexte courant, **When** un utilisateur GM est connecte, **Then** le role renvoye est compatible avec le vocabulaire runtime actuel du front.

### Edge Cases

- L'utilisateur a un `default_campaign_id` renseigne mais n'est plus membre de cette campagne : le systeme doit ignorer ce defaut invalide et chercher un membership valide.
- L'utilisateur a plusieurs memberships : la campagne active V1 est son defaut si valide, sinon le premier membership deterministe.
- L'utilisateur n'a aucun membership : le contexte courant reste authentifie cote compte, mais `active_campaign` vaut `null`.
- Une session expiree, revoquee ou appartenant a un utilisateur inactif doit etre refusee avant toute resolution de campagne.
- Un joueur sans personnage rattache ne doit pas recevoir un `character_id` invente.
- Une deuxieme campagne creee uniquement pour test ne doit jamais apparaitre dans les resultats d'un utilisateur membre de la campagne par defaut.
- Les donnees historiques creees avant cette feature doivent etre rattachees a la campagne V1 pour rester visibles apres migration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST model a campaign as the current JDR grouping used to scope users and JDR data.
- **FR-002**: The system MUST expose an authenticated current-context contract for the web front containing public user identity and the active campaign context.
- **FR-003**: The current-context response MUST include `user.id`, `user.username`, and either `active_campaign` with `id`, `name`, `role`, `character_id`, or `active_campaign: null`.
- **FR-004**: The system MUST reject missing, expired, revoked, or otherwise invalid sessions before returning any current-context data.
- **FR-005**: The system MUST resolve the active campaign from the user's valid default campaign when available.
- **FR-006**: If the default campaign is absent or invalid, the system MUST resolve the active campaign from a deterministic valid membership for that user.
- **FR-007**: If the user has no valid campaign membership, the system MUST return a successful current-context response with `active_campaign: null`.
- **FR-008**: The system MUST create or identify a single default campaign for V1 deployments.
- **FR-009**: The system MUST attach existing users to the V1 default campaign during adoption of this feature.
- **FR-010**: The system MUST attach newly created users to the creator's active campaign, or to the V1 default campaign when no other active campaign exists.
- **FR-011**: The system MUST derive campaign membership role from the existing profile model for V1: `gm` users become campaign GMs, `user` users become campaign players.
- **FR-012**: The current-context role vocabulary MUST be compatible with the currently typed web runtime: `gm` or `player`.
- **FR-013**: The system MUST retain existing login, logout, first-run setup, and user-management request bodies.
- **FR-014**: The system MUST NOT require clients to send a campaign identifier when creating JDR data in V1.
- **FR-015**: The system MUST derive the write scope for newly created JDR data from the authenticated user's active campaign.
- **FR-016**: The system MUST filter existing JDR read, list, update, delete, and generation workflows by the authenticated user's active campaign.
- **FR-017**: The system MUST prevent a client-provided campaign identifier from widening or changing the authenticated campaign scope.
- **FR-018**: The system MUST preserve membership records for logically deleted users for traceability while preventing those users from opening or using web sessions.
- **FR-019**: The system MUST avoid exposing password hashes, session tokens, internal API-key hashes, or unrelated membership details in current-context and user-management responses.
- **FR-020**: The system MUST provide a validation path proving that data from a second campaign is invisible to a user whose active campaign is the default campaign.

### Key Entities *(include if feature involves data)*

- **Campaign**: A JDR grouping owned by a user and used as the unit of data visibility. Key attributes: identifier, display name, owner, creation timestamp.
- **Campaign Membership**: A user's relationship to a campaign. Key attributes: user, campaign, role, optional character, join timestamp.
- **Active Campaign Context**: The campaign membership selected for the current authenticated session. It is used by the front for navigation state and by backend workflows for data scope.
- **User**: Existing web account with username, profile and status. This feature adds campaign membership semantics without replacing the login identity.
- **JDR Data**: Existing sessions, characters, mappings, artifacts and related resources that must be associated with and filtered by campaign.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid logged-in users can retrieve a current-context response immediately after login without a frontend mock.
- **SC-002**: 100% of existing active users in a pre-feature database are members of the V1 default campaign after adoption.
- **SC-003**: In an isolation test with two campaigns, 0 records from the other campaign appear in list or detail views for a user scoped to the default campaign.
- **SC-004**: Existing login, logout, setup and user-management acceptance tests continue to pass without changing their request bodies.
- **SC-005**: 100% of unauthenticated, expired-session, revoked-session and deleted-user current-context attempts are rejected before user or campaign data is returned.
- **SC-006**: No response introduced by this feature exposes password hashes, session token hashes, plaintext session tokens or internal API-key hashes.

## Assumptions

- The feature is BD-4 from the frontend handoff dated 2026-05-29.
- V1 remains effectively single-campaign in product usage, even though the data model allows multiple campaign memberships for later evolution.
- No campaign-management UI or public campaign CRUD is included in this feature.
- No tenant or organization layer above campaign is included in this feature.
- The existing `profile` field remains for V1 compatibility; campaign membership can become the stronger authorization source later.
- The handoff text uses `mj` in places, but the currently typed web runtime expects `gm | player`; this spec chooses `gm | player` for V1 compatibility.
- Player `character_id` may remain `null` until a character binding exists; the system must not invent one.
- The frontend continues to send cookies with credentials included and continues to treat `active_campaign: null` as no usable JDR context.
