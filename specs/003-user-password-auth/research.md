# Research: User Password Authentication

**Phase 0 du `/speckit-plan`**. Objectif : verrouiller les decisions techniques avant les contrats et le modele de donnees.

## 1. Identite de login

### Decision

Le login web utilise `username + profile + password`. `username` est unique globalement. Plusieurs utilisateurs peuvent partager le meme `profile` (`gm` ou `user`).

### Rationale

Le contrat initial `profile + password` ne permet pas de distinguer plusieurs comptes ayant le meme profil. Ajouter `username` garde une UX simple, evite une unicite artificielle "un compte par profil", et permet de tester proprement les cas de suppression, rotation de mot de passe et changement de profil.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Un seul utilisateur par profil | Trop limite des que plusieurs GM ou plusieurs users existent. |
| Email comme identifiant | Plus lourd : validation email, normalisation, et future recuperation de compte hors scope. |
| Mot de passe seul comme discriminant | Mauvais modele mental et mauvais pour l'audit. |

## 2. Stockage des mots de passe

### Decision

Stocker uniquement des hashes Argon2id via `argon2-cffi`, comme l'auth API-key existante.

### Rationale

Le projet utilise deja Argon2id. OWASP recommande Argon2id comme choix moderne pour le stockage de mots de passe lorsque disponible (https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). Reutiliser la meme librairie evite une nouvelle dependance et garde le format de hash auto-descriptif.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| bcrypt | Acceptable, mais ajoute une decision et une dependance alors qu'Argon2id existe deja. |
| HMAC/SHA-256 | Inadapte aux mots de passe humains car trop rapide pour resister au brute-force offline. |
| Stocker le mot de passe chiffre | Inutile ici : il ne doit jamais etre recupere, seulement verifie. |

## 3. Type de session web

### Decision

Emettre un cookie HTTP-only contenant un token opaque aleatoire. Stocker cote serveur une session avec hash du token, `user_id`, `expires_at`, `revoked_at`.

### Rationale

Un token opaque cote serveur rend la revocation simple (`POST /logout`) et evite de mettre des claims ou secrets dans le cookie. RFC 6265 definit `HttpOnly` comme un attribut limitant l'acces au cookie par les APIs non HTTP, utile contre l'exposition par script cote navigateur (https://www.rfc-editor.org/rfc/rfc6265). La session reste un backing service state, coherent avec 12-Factor "stateless process" : le process ne garde pas la session en memoire (https://12factor.net/processes).

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| JWT signe dans le cookie | Revocation plus complexe, horloge et rotation de cle a gerer. Surdimensionne pour LAN/sandbox. |
| Cookie contenant le mot de passe ou API token | Secret long-vivant expose au navigateur ; c'est justement ce que la feature remplace. |
| Session en memoire | Perdue au restart, non compatible avec plusieurs process. |

## 4. Expiration et logout

### Decision

Ajouter une duree de vie configurable, avec default propose a 8 heures, et `POST /services/jdr/auth/logout` qui revoque la session courante et expire le cookie cote client.

### Rationale

Une session bornee reduit l'impact d'un cookie vole ou d'un navigateur partage. Le logout est indispensable pour rendre la revocation immediate. La duree exacte reste une configuration operateur pour respecter 12-Factor config (https://12factor.net/config).

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Session sans expiration serveur | Trop durable ; pas de borne claire au risque. |
| Expiration uniquement cote navigateur | Le serveur accepterait encore un ancien cookie si renvoye manuellement. |
| Refresh tokens | Complexite inutile pour un front local. |

## 5. Suppression utilisateur

### Decision

Suppression logique : `status` passe a `deleted` ou `inactive`, le login est refuse, la row reste traçable. Interdire la suppression/desactivation du dernier GM actif.

### Rationale

La suppression logique garde l'audit et evite de casser les references futures aux sessions, jobs ou logs. Le garde-fou "dernier GM" evite un lock-out administratif.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| DELETE physique | Perte de trace et risque d'incoherence si des lignes referencent l'utilisateur. |
| Suppression physique uniquement pour admins | YAGNI pour le v1. |

## 6. Coexistence API-key et users

### Decision

Conserver l'auth API-key existante pour les clients machine et les ownerships JDR existants. Ajouter un chemin de verification web-session qui produit une identite authentifiee compatible avec les checks `gm`/`user`.

### Rationale

La spec demande de remplacer le login web, pas de supprimer les API keys. Les tables JDR existantes utilisent `gm_key_id` comme owner ; les casser agrandirait le scope. Cette feature doit donc introduire le login user sans refactorer tout le modele JDR.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Migrer toute propriete JDR de `api_key_id` vers `user_id` | Changement massif, risque fort, hors scope. |
| Mapper chaque user GM vers une API key automatique | Confus et recree le melange user/password vs API token. |

## 7. Premiere initialisation du premier GM

### Decision

Prevoir un mode premiere initialisation pilote par le front :

- `GET /services/jdr/auth/setup/status` indique si aucun user n'existe.
- `POST /services/jdr/auth/setup` cree le premier utilisateur `gm` avec `username` et mot de passe choisis par l'utilisateur.
- L'endpoint setup est disponible uniquement quand `core_users` est vide. Des qu'un user existe, il refuse toute creation.
- Le mot de passe est hashé immediatement ; aucun mot de passe par defaut n'est code dans l'application.

### Rationale

Le besoin utilisateur est de pouvoir installer et utiliser l'application depuis le front sans script ni edition `.env`. Un mot de passe par defaut commun (`admin/admin`) serait plus simple mais cree un risque de credentials connus. OWASP documente les credentials hardcodes/default comme une faiblesse a eviter et recommande plutot un mode de premiere initialisation avec mot de passe unique (https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password).

Le setup first-run garde l'UX produit simple tout en fermant automatiquement la surface d'attaque apres creation du premier compte.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Mot de passe par defaut `admin` | Credential connu, risque d'oubli apres installation. |
| Bootstrap env avec hash | Securise, mais ne respecte pas le besoin de tout faire depuis le front. |
| Endpoint setup toujours ouvert | Surface d'attaque dangereuse ; le setup doit se fermer des qu'un user existe. |
| Seed SQL manuel obligatoire | Moins ergonomique, augmente les erreurs operateur. |

## 8. Rate limiting du login

### Decision

Le login doit avoir une protection anti-bruteforce minimale. Reutiliser Redis si disponible via le rate limiter existant, mais bucket par `username + profile + IP` ou fallback par IP si username absent.

### Rationale

OWASP API Security Top 10 2023 inclut Broken Authentication et Unrestricted Resource Consumption parmi les risques majeurs (https://owasp.org/API-Security/editions/2023/en/0x11-t10/). Le login n'a pas encore d'identite authentifiee, donc le rate limit par key existant ne suffit pas.

### Alternatives considered

| Alternative | Pourquoi rejetee |
|---|---|
| Aucun rate limit car LAN | Trop fragile des que l'API passe derriere Caddy ou est exposee a plus d'appareils. |
| CAPTCHA | Hors scope et inutile pour une API locale. |
