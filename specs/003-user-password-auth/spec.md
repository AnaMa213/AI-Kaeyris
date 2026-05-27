# Feature Specification: User Password Authentication

**Feature Branch**: `003-user-password-auth`
**Created**: 2026-05-27
**Status**: Draft
**Input**: User description: "Mettre en place un systeme de creation, suppression et modification de users avec un profil entre user/gm et un mot de passe. Remplacer le login basique existant. Le front appelle POST /services/jdr/auth/login avec {profile:'gm', password:'...'}, envoie les cookies avec credentials:'include', attend 200 + cookie HTTP-only en succes, 401 application/problem+json en mauvais identifiants, 403 application/problem+json pour profil non supporte si necessaire."

## Clarifications

### Session 2026-05-27

- Q: Comment identifier plusieurs utilisateurs partageant le meme profil lors du login ? -> A: Plusieurs utilisateurs peuvent partager le meme profil ; le front sera modifie pour envoyer un `username` en plus de `profile` et `password`.
- Q: Quelle semantique appliquer a la suppression d'utilisateur ? -> A: Suppression logique : l'utilisateur est desactive, ne peut plus se connecter, mais reste traçable.
- Q: Quelle duree de vie appliquer aux sessions web ? -> A: Sessions expirables avec duree configurable et `POST /logout` pour invalider explicitement la session.
- Q: Comment creer le premier GM sans script ni modification de `.env` ? -> A: Le front affiche un mode premiere initialisation quand aucun user n'existe ; `POST /services/jdr/auth/setup` cree le premier GM puis se desactive automatiquement.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Creation de profil puis login web (Priority: P1)

Un GM authentifie veut creer un profil applicatif avec `username`, `profile` (`gm` ou `user`) et mot de passe, puis l'utilisateur cree veut se connecter depuis le front. Si les identifiants sont valides, le backend pose un cookie de session HTTP-only utilisable par les appels suivants du front.

**Why this priority**: Sans creation de profil utilisable puis login compatible avec le front, aucune experience web authentifiee de bout en bout ne fonctionne.

**Independent Test**: Avec un GM authentifie (bootstrap ou fixture), appeler `POST /services/jdr/users` pour creer un profil actif avec `username`, `profile` et mot de passe, appeler `POST /services/jdr/auth/login` avec les identifiants crees, verifier `200`, `Set-Cookie: session=...; HttpOnly; Path=/; SameSite=Lax`, puis appeler une route protegee avec le cookie uniquement.

**Acceptance Scenarios**:

1. **Given** un GM authentifie, **When** il cree un profil avec `{"username":"alice","profile":"gm","password":"mot-de-passe-valide"}`, **Then** l'utilisateur `alice` devient actif et aucun hash/secret n'est expose dans la reponse.
2. **Given** l'utilisateur actif `alice` avec profil `gm`, **When** le front appelle `POST /services/jdr/auth/login` avec `{"username":"alice","profile":"gm","password":"mot-de-passe-valide"}`, **Then** le backend retourne `200` et pose un cookie `session` HTTP-only.
3. **Given** un cookie `session` issu du login d'`alice`, **When** le front appelle une route protegee compatible avec son profil, **Then** la requete est acceptee sans header `Authorization`.
4. **Given** un profil supporte mais un mauvais mot de passe, **When** le front appelle le login, **Then** le backend retourne `401` avec `Content-Type: application/problem+json` et le corps exact `{"type":"about:blank","title":"Invalid credentials","status":401}`.
5. **Given** un profil non supporte, **When** le front appelle le login, **Then** le backend retourne `403` avec `Content-Type: application/problem+json` et le corps exact `{"type":"about:blank","title":"Forbidden","status":403}`.

---

### User Story 2 - Gestion complete des utilisateurs par un GM (Priority: P2)

Un GM authentifie veut lister, modifier et supprimer des utilisateurs applicatifs afin de gerer qui peut continuer a acceder a l'interface web et avec quel profil.

**Why this priority**: Apres le MVP de creation + login, l'exploitation durable exige modification, listing et suppression logique sans modifier manuellement la base.

**Independent Test**: Avec une session GM valide et un utilisateur existant, modifier son profil ou son mot de passe, verifier que le nouveau login fonctionne, puis supprimer l'utilisateur et verifier que son login est refuse.

**Acceptance Scenarios**:

1. **Given** un GM authentifie, **When** il liste les utilisateurs, **Then** les utilisateurs sont renvoyes sans hash ni secret.
2. **Given** un GM authentifie et un utilisateur existant, **When** il modifie le mot de passe de cet utilisateur, **Then** l'ancien mot de passe est refuse et le nouveau est accepte.
3. **Given** un GM authentifie et un utilisateur existant, **When** il modifie le profil de cet utilisateur, **Then** les droits de l'utilisateur refletent le nouveau profil au prochain login.
4. **Given** un GM authentifie et un utilisateur existant, **When** il supprime cet utilisateur, **Then** l'utilisateur est desactive et ses futures tentatives de login sont refusees.

---

### User Story 3 - Premiere initialisation via le front (Priority: P3)

L'utilisateur installe l'application et veut pouvoir creer le premier compte GM depuis le front, sans lancer de script local et sans modifier `.env`.

**Why this priority**: Une installation sans utilisateur initial serait bloquee. Le setup front evite le piege du mot de passe par defaut tout en gardant une experience produit simple.

**Independent Test**: Demarrer avec une base vide, verifier que `GET /services/jdr/auth/setup/status` annonce qu'un setup est requis, appeler `POST /services/jdr/auth/setup` avec `username` et `password`, verifier qu'un GM actif est cree et qu'une deuxieme tentative de setup est refusee.

**Acceptance Scenarios**:

1. **Given** une base sans utilisateur, **When** le front appelle `GET /services/jdr/auth/setup/status`, **Then** le backend indique que la premiere initialisation est requise.
2. **Given** une base sans utilisateur, **When** le front appelle `POST /services/jdr/auth/setup` avec `{"username":"admin","password":"mot-de-passe-choisi"}`, **Then** un premier GM actif est cree et une session GM est ouverte via cookie HTTP-only.
3. **Given** au moins un utilisateur existant, **When** le front appelle `POST /services/jdr/auth/setup`, **Then** le backend refuse la requete et ne cree aucun nouveau GM.
4. **Given** le nouveau modele utilisateur actif, **When** le login web recoit un ancien token API comme `password`, **Then** il est refuse sauf s'il correspond explicitement au mot de passe d'un utilisateur.

---

### User Story 4 - Logout et expiration de session (Priority: P4)

Un utilisateur connecte veut pouvoir fermer sa session web, et l'operateur veut que les sessions expirent automatiquement apres une duree controlee.

**Why this priority**: Le cookie HTTP-only protege mieux le secret, mais une session sans expiration ni revocation explicite reste trop durable si un navigateur est partage ou compromis.

**Independent Test**: Se connecter, verifier qu'une route protegee accepte le cookie, appeler `POST /services/jdr/auth/logout`, puis verifier que le meme cookie ne donne plus acces. Simuler une session expiree et verifier qu'elle est refusee.

**Acceptance Scenarios**:

1. **Given** un utilisateur connecte, **When** il appelle `POST /services/jdr/auth/logout`, **Then** sa session courante est invalidee et le cookie ne permet plus d'acceder aux routes protegees.
2. **Given** une session dont la duree de vie configurable est depassee, **When** le front appelle une route protegee avec son cookie, **Then** la requete est refusee comme non authentifiee.

---

### Edge Cases

- Si aucun utilisateur n'existe, le systeme entre en mode premiere initialisation et permet de creer le premier GM depuis le front.
- Si au moins un utilisateur existe, le mode premiere initialisation est ferme et `POST /services/jdr/auth/setup` est refuse.
- Si un utilisateur supprime ou desactive tente de se connecter, le login retourne `401 Invalid credentials` sans reveler que le compte existe.
- Si une session est expiree ou invalidee par logout, elle ne donne plus acces aux routes protegees meme si le cookie est encore envoye par le navigateur.
- Si plusieurs utilisateurs partagent le meme profil, le champ `username` distingue explicitement le compte a authentifier. Le front doit donc envoyer `username`, `profile` et `password`.
- Si une tentative de suppression vise le dernier GM actif, le systeme doit refuser l'operation pour eviter un verrouillage administratif.
- Si le mot de passe fourni est vide ou invalide, la requete est rejetee sans creer ni modifier d'utilisateur.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide `POST /services/jdr/auth/login` with the request body accepted by the updated front: `{"username":"string","profile":"gm","password":"string"}` or `{"username":"string","profile":"user","password":"string"}`.
- **FR-002**: The login endpoint MUST return `200` on success and set `Set-Cookie: session=...; HttpOnly; Path=/; SameSite=Lax`. The response body MAY be empty.
- **FR-003**: The login endpoint MUST return `401` with `Content-Type: application/problem+json` and body `{"type":"about:blank","title":"Invalid credentials","status":401}` for invalid credentials.
- **FR-004**: The login endpoint MUST return `403` with `Content-Type: application/problem+json` and body `{"type":"about:blank","title":"Forbidden","status":403}` for unsupported profiles or forbidden profile use.
- **FR-005**: The system MUST persist users with at least: unique identity, profile (`gm` or `user`), password hash, status, creation timestamp, and last update timestamp.
- **FR-006**: The system MUST store only password hashes, never plaintext passwords.
- **FR-007**: The system MUST allow an authenticated GM to create users with profile and password.
- **FR-008**: The system MUST allow an authenticated GM to list users without exposing password hashes or secrets.
- **FR-009**: The system MUST allow an authenticated GM to modify a user's profile and/or password.
- **FR-010**: The system MUST allow an authenticated GM to delete users by logical deletion only: the user becomes inactive/deleted, cannot log in anymore, and remains retained for traceability.
- **FR-011**: The system MUST prevent deleting or deactivating the last active GM.
- **FR-012**: The system MUST ensure deleted, deactivated, or unknown users cannot log in.
- **FR-013**: The system MUST keep the front integration stable except for the explicit addition of `username`: base URL remains controlled by the front, cookies are usable with `credentials: "include"`, and no response body is required on successful login.
- **FR-014**: The system MUST replace the previous login behavior that treated the GM API token as the web password.
- **FR-015**: The system MUST expose a first-run setup flow that lets the front create the first GM user only when no user exists.
- **FR-016**: The system MUST issue web sessions with a configurable expiration duration.
- **FR-017**: The system MUST provide `POST /services/jdr/auth/logout` to invalidate the current session and make the current cookie unusable for future authenticated requests.
- **FR-018**: The system MUST reject expired or logged-out sessions on every protected web request.
- **FR-019**: The system MUST reject first-run setup once at least one user exists.

### Key Entities

- **User**: A human or web-facing account. Key attributes: unique username, profile (`gm` or `user`), password hash, status, timestamps. Multiple users may share the same profile, but usernames are unique. Deletion is logical through status, not physical removal.
- **Session Cookie**: A browser credential issued after successful login and sent by the front on subsequent requests. It must not expose plaintext passwords.
- **Web Session**: Server-side session state associated with a user and an expiration timestamp. A web session can be invalidated explicitly by logout or implicitly by expiration.
- **Profile**: Authorization posture for the web account. `gm` can administer users and access GM workflows; `user` receives non-admin access.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A GM can create a new profile and that profile can complete login from the front contract in under 2 minutes.
- **SC-002**: 100% of invalid login attempts in the defined negative scenarios return the exact Problem Details bodies specified by the front contract.
- **SC-003**: After a password change, the old password is rejected and the new password succeeds on the next login attempt.
- **SC-004**: After logical deletion or deactivation, the user cannot obtain a new session cookie while the account remains traceable.
- **SC-005**: The system cannot reach a state with zero active GM users through the user management endpoints.
- **SC-006**: After logout, the previous session cookie is rejected on the next protected request.
- **SC-007**: After the configured session duration elapses, the session is rejected without requiring a server restart.

## Assumptions

- The feature targets the existing local web front and must preserve `POST /services/jdr/auth/login`, while requiring the front to add `username` to the login body.
- `gm` and `user` are the only profiles in scope for this feature.
- Password reset by email, invitation emails, OAuth/OIDC, and public self-service signup are out of scope.
- Existing Bearer/API-key authentication may remain for API clients, but the web login must stop treating API tokens as user passwords.
- Session duration is configurable by the operator; the exact default value can be selected during planning.
