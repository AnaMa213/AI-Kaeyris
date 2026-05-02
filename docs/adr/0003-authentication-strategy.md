# ADR 0003 — Stratégie d'authentification de l'API

- **Statut** : accepté
- **Date** : 2026-05-02
- **Décideur** : owner du projet (Kenan)
- **En lien avec** : ADR 0001 (architecture monolithe modulaire), ADR 0002 (format d'erreur RFC 9457)

## Contexte

À ce stade du projet (sortie du Jalon 1), l'API est totalement ouverte : n'importe quel appareil sur le réseau local peut appeler n'importe quel endpoint. Cette ouverture devient un risque dès qu'on aura des services qui :

- Consomment du crédit externe (DeepInfra au Jalon 4) — coût direct si quelqu'un d'autre les appelle
- Stockent ou transcrivent des données potentiellement sensibles (sessions JDR au Jalon 5)
- Modifient un état persistant (DB en Jalon 5+)

Le Jalon 2 doit donc poser une couche d'authentification suffisante pour un sandbox personnel : usage **machine-to-machine** (pas d'humains qui se connectent avec mot de passe), **réseau local** (pas encore d'Internet public), **un seul opérateur** (toi).

OWASP API Security Top 10 (2023) — https://owasp.org/API-Security/ — classe **API1:2023 Broken Authentication** parmi les risques les plus exploités. Cet ADR vise à le couvrir avec un effort minimal mais correct.

Six questions structurantes se posent :

1. Comment identifier le client ?
2. Comment stocker les secrets ?
3. Quel algorithme de hashage ?
4. Comment limiter les abus (rate limiting) ?
5. Quels headers de sécurité poser ?
6. Quelles routes restent publiques, et comment réagir à un échec d'auth ?

## Décision

### 1. Authentification par API key, header `Authorization: Bearer <key>`

L'API exige un header HTTP standard pour toutes les routes protégées :

```
Authorization: Bearer <api_key>
```

Format conforme à la **RFC 6750** (*The OAuth 2.0 Authorization Framework: Bearer Token Usage*) — https://www.rfc-editor.org/rfc/rfc6750. Le token est libre (pas un JWT), mais le mécanisme de transport est standard.

L'API key elle-même est une chaîne aléatoire de **32 octets** (256 bits) générée via `secrets.token_urlsafe(32)`, soit ~43 caractères URL-safe.

### 2. Stockage des clés via variable d'environnement

Provisoirement (pas encore de DB), les clés sont déclarées dans une variable d'env :

```
API_KEYS=<name1>:<argon2_hash1>;<name2>:<argon2_hash2>
```

**Séparateur `;`** entre entrées (et non `,`) car les hashes Argon2 contiennent des virgules dans leur section paramètres (`$argon2id$v=19$m=65536,t=3,p=4$...`).

- `name` = identifiant lisible (`my-laptop`, `pi-monitor`) — sert au logging et à la révocation
- `<argon2_hash>` = hash Argon2id de la clé en clair

La variable est lue par `app/core/config.py` (`pydantic-settings`), parsée en liste typée par `app/core/auth.py`.

**Pas de clé en clair dans le code, ni dans `.env.example`.** Le `.env.example` montre le format mais avec des valeurs d'exemple inoffensives.

À reconsidérer en Jalon 5 quand on aura une DB : passer à un modèle `api_keys(name, hash, status, created_at, last_used_at)` en table SQL.

### 3. Algorithme de hashage : Argon2id

On utilise **Argon2id** via la lib `argon2-cffi` (https://github.com/hynek/argon2-cffi).

Argon2 est le **vainqueur du Password Hashing Competition 2015** et l'algorithme actuellement recommandé par OWASP — https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html. La variante `id` est l'hybride recommandée par défaut (résiste aux attaques side-channel ET aux GPU).

**Paramètres** : valeurs par défaut de `argon2-cffi` (memory_cost=64 MB, time_cost=3, parallelism=4). Valeurs documentées comme "minimum recommandé production" par les mainteneurs de la lib en 2025.

### 4. Rate limiting reporté au Jalon 3

Pas de rate limiting dans ce jalon. Argument :

- Sur réseau local, exposition limitée
- Implémenter en mémoire ne marche pas si on passe en multi-instance
- Le Jalon 3 introduit Redis (queue async) → on aura un store distribué qui supporte naturellement le rate limiting

L'auth bloque déjà 100% des appels non autorisés, ce qui est la première ligne de défense contre l'abus de ressources (OWASP API4).

### 5. Headers de sécurité : middleware fait main

Un middleware Starlette ajoute systématiquement les headers suivants à toute réponse :

| Header | Valeur | Rôle |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | empêche le browser de deviner un type MIME |
| `X-Frame-Options` | `DENY` | empêche le site d'être chargé dans une iframe |
| `Referrer-Policy` | `no-referrer` | ne fuit pas l'URL d'origine vers d'autres sites |
| `Content-Security-Policy` | `default-src 'none'` | API JSON pure, aucun chargement de ressources |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | active uniquement quand on sera derrière HTTPS (Jalon 8) |

Le middleware est dans `app/core/security_headers.py`, environ 30 lignes. Pas de dépendance externe.

Référence : OWASP Secure Headers Project — https://owasp.org/www-project-secure-headers/

### 6. Routes publiques + comportement en cas d'échec

**Routes publiques** (pas d'auth requise) :

- `GET /health` — sondé par les outils d'orchestration externes
- `GET /docs` — Swagger UI pour l'onboarding
- `GET /redoc` — ReDoc
- `GET /openapi.json` — spec OpenAPI brute

Toutes les autres routes (et notamment celles montées dans `app/services/...`) sont protégées par défaut.

**Comportement en cas d'échec** :

- Header `Authorization` manquant ou format invalide → **401 Unauthorized** Problem Details `type=.../unauthorized`
- Clé non reconnue (aucun hash ne matche) → **401 Unauthorized**
- Clé reconnue mais marquée comme révoquée (futur, pas dans ce jalon) → **403 Forbidden** `type=.../forbidden`
- Header `WWW-Authenticate: Bearer realm="ai-kaeyris"` ajouté à toute réponse 401 (RFC 6750 §3)

**Comparaison constant-time** : on vérifie chaque clé candidate via `argon2.verify()` (qui est elle-même constant-time). Pour la comparaison du `name` côté observateur, on utilise `secrets.compare_digest()`. Aucune comparaison brute par `==` sur une donnée sensible dans le code.

## Alternatives écartées

| Alternative | Raison du rejet |
|---|---|
| **OAuth2 / OIDC complet** | Sur-ingénierie absolue pour un projet mono-utilisateur. Zéro client tiers à autoriser. À reconsidérer si l'API devient publique avec plusieurs comptes utilisateurs. |
| **JWT (JSON Web Tokens)** | Utile pour des sessions distribuées ; on a un seul service, un seul opérateur. Le coût (révocation, gestion d'horloge, vérification de signature) dépasse le bénéfice. |
| **mTLS** (mutual TLS, certificats clients) | Très sécurisé mais lourd : génération/distribution/rotation des certificats. Excessif pour un sandbox local. |
| **API key dans une query string** (`?api_key=...`) | Apparait dans logs serveur, browser history, referer. Anti-pattern documenté par RFC 6750 §2.3. |
| **Auth basique HTTP** (`Authorization: Basic <base64>`) | Encode en base64, pas chiffrement. Utilisable mais moins clair que Bearer pour de la machine-to-machine. |
| **Stockage clés en clair en variable d'env** | Si la variable fuit (logs, dump, intrusion shell), toutes les clés sont compromises. Le hashage est trivial à ajouter et change radicalement le risque résiduel. |
| **bcrypt au lieu d'Argon2id** | bcrypt est encore acceptable mais Argon2 est l'état de l'art depuis 2015. Pas de raison de partir sur l'alternative dépassée. |
| **HMAC-SHA256** (sans hash lent) | Techniquement défendable pour des clés de 256 bits d'entropie (pas besoin de "lentir" comme pour des mots de passe humains). Écarté pour la cohérence avec un éventuel ajout futur de mots de passe utilisateur, qui exigerait Argon2 de toute façon. Cycle CPU "gaspillé" reste sub-milliseconde par requête. |
| **SHA-256 simple** | Pas de salt, vulnérable à des rainbow tables si plusieurs clients utilisent la même clé. Anti-pattern. |
| **Rate limiting fait main en mémoire** | Marche en mono-instance, casse en multi-instance. Pas pire que rien mais on vise déjà Redis au Jalon 3. |
| **Lib `slowapi` pour rate limit dès maintenant** | Ajoute une dépendance dont on ne peut tirer parti correctement qu'avec Redis. Mieux d'attendre. |
| **Lib `secweb` ou `starlette-securityheaders` pour les headers** | ~30 lignes économisées contre une dépendance de plus. Cohérent avec le choix Jalon 1 sur RFC 9457 (fait main). |
| **Toutes les routes publiques par défaut, opt-in pour la protection** | Inverse du principe "secure by default" (OWASP). On veut que **oublier** d'ajouter l'auth donne un 401, pas un 200. |

## Conséquences

**Positives**
- Toute requête non authentifiée renvoie un 401 clair, pas un 500 ou un endpoint vulnérable.
- Les clés stockées sont inutilisables même si la variable d'env fuit (il faut la lib Argon2 pour générer les hash, et ce n'est pas réversible).
- Format compatible RFC 6750 → tous les clients HTTP standard savent envoyer `Authorization: Bearer ...`.
- Les routes publiques sont explicitement listées : pas de surprise, pas d'exposition par oubli.
- Aucune dépendance externe ajoutée pour les security headers (juste `argon2-cffi` pour le hash).
- Compatible avec Argon2 standard → migration vers DB en Jalon 5 = simple changement du provider de stockage, pas du format.

**Négatives / acceptées**
- Pas de rate limiting → un attaquant qui devine les premiers caractères pourrait théoriquement essayer en brute-force. Mitigé par : 256 bits d'entropie sur la clé (impossible à brute-forcer), Argon2 lent (~10ms par tentative), et exposition limitée au LAN.
- Stockage en variable d'env limite à ~5 clés en pratique (chaîne longue à manipuler). On vit avec.
- La rotation d'une clé exige un redémarrage du conteneur (variable d'env). Acceptable pour ce jalon.
- Pas de scopes/permissions : toutes les clés ont les mêmes droits sur tous les services protégés. À reconsidérer si on a un service "dangereux" (suppression de données) à isoler.

**Conditions de re-évaluation** (cet ADR sera "superseded" si)
- On dépasse 5 clés actives ou on veut de la rotation sans redémarrage → migration vers stockage DB.
- On expose l'API hors LAN (Internet public, plusieurs clients tiers) → renforcer avec rate limiting + audit log + scopes par clé.
- On ajoute des utilisateurs humains avec mots de passe → revoir le modèle de credentials et probablement introduire OAuth2.
- Argon2id est cassé ou déprécié (peu probable à horizon 10 ans) → migration triviale grâce au format de hash auto-décrivant d'Argon2.

## Références

- RFC 6750 — *The OAuth 2.0 Authorization Framework: Bearer Token Usage* — https://www.rfc-editor.org/rfc/rfc6750
- OWASP API Security Top 10 (2023), particulièrement API1 (Broken Authentication) — https://owasp.org/API-Security/
- OWASP Password Storage Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Secure Headers Project — https://owasp.org/www-project-secure-headers/
- Argon2 RFC 9106 — *Argon2 Memory-Hard Function for Password Hashing and Proof-of-Work Applications* — https://www.rfc-editor.org/rfc/rfc9106
- Lib `argon2-cffi` — https://argon2-cffi.readthedocs.io/
- ADR 0002 — format d'erreur RFC 9457 (toutes les réponses d'erreur d'auth suivent ce format)
