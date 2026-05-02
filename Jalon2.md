# Jalon 2 — Authentication (walkthrough pédagogique)

> Document explicatif détaillé : étapes, **pourquoi**, alternatives écartées, normes respectées.
> Public : toi qui apprends. À relire dans 6 mois.

---

## Sommaire

1. [Objectif et menaces couvertes](#1-objectif-et-menaces-couvertes)
2. [Étape 0 — ADR 0003 avant le code](#2-étape-0--adr-0003-avant-le-code)
3. [Étape 1 — Dépendance `argon2-cffi`](#3-étape-1--dépendance-argon2-cffi)
4. [Étape 2 — Extension de `AppError` avec headers HTTP](#4-étape-2--extension-de-apperror-avec-headers-http)
5. [Étape 3 — `app/core/auth.py`](#5-étape-3--appcoreauthpy)
6. [Étape 4 — `app/core/security_headers.py`](#6-étape-4--appcoresecurity_headerspy)
7. [Étape 5 — `app/core/config.py` et `.env.example`](#7-étape-5--appcoreconfigpy-et-envexample)
8. [Étape 6 — `scripts/generate_api_key.py`](#8-étape-6--scriptsgenerate_api_keypy)
9. [Étape 7 — Câblage dans `app/main.py`](#9-étape-7--câblage-dans-appmainpy)
10. [Étape 8 — Tests](#10-étape-8--tests)
11. [Normes et bonnes pratiques respectées](#11-normes-et-bonnes-pratiques-respectées)
12. [Choix alternatifs envisagés et écartés](#12-choix-alternatifs-envisagés-et-écartés)
13. [Limitations acceptées](#13-limitations-acceptées)
14. [Ce que ce jalon prépare pour la suite](#14-ce-que-ce-jalon-prépare-pour-la-suite)

---

## 1. Objectif et menaces couvertes

### Selon CLAUDE.md §5

> Jalon 2 : **API key auth, hashed storage, rate limiting, security headers**

### Menaces que l'on adresse

| Menace OWASP API (2023) | Comment on s'en protège |
|---|---|
| **API1:2023** Broken Authentication | API key obligatoire sur toutes les routes hors liste publique, hash Argon2id |
| **API2:2023** Broken Object Level Authorization | (partiellement — pas de scopes par clé encore) |
| **API4:2023** Unrestricted Resource Consumption | Auth bloque déjà les appels anonymes ; rate limiting au Jalon 3 |
| **API7:2023** SSRF | Headers `Referrer-Policy: no-referrer` |
| **API8:2023** Security Misconfiguration | Security headers OWASP standards posés systématiquement |
| **API10:2023** Unsafe Consumption of APIs | (hors scope ; concerne les apps qui consomment des tiers) |

Référence : OWASP API Security Top 10 — https://owasp.org/API-Security/

### Ce qu'on ne fait pas dans ce jalon

- ❌ OAuth2 / OIDC / SSO
- ❌ JWT (sessions)
- ❌ Mot de passe utilisateur (modèle "humain")
- ❌ Scopes / permissions par clé (toutes les clés sont égales)
- ❌ Rotation automatique de clés
- ❌ Rate limiting (reporté Jalon 3 avec Redis)
- ❌ Audit log des authentifications (reporté Jalon 6)

---

## 2. Étape 0 — ADR 0003 avant le code

### Ce qui a été fait

Rédaction de [`docs/adr/0003-authentication-strategy.md`](./docs/adr/0003-authentication-strategy.md). 6 décisions structurantes :

1. Bearer token (RFC 6750)
2. Stockage env var (à reconsidérer Jalon 5 pour DB)
3. Argon2id via `argon2-cffi`
4. Rate limiting reporté
5. Security headers fait main
6. Routes publiques explicites + 401/403 + constant-time

### Pourquoi avant le code

Même raison qu'au Jalon 1 : un ADR rédigé après le code devient une justification a posteriori, pas une vraie décision réfléchie. Les "alternatives écartées" sont d'autant plus précieuses qu'elles capturent **ce qu'on n'a pas fait et pourquoi**, ce que le code ne dit pas.

### Détail technique attrapé en route

Pendant la rédaction, on a réalisé que les hashes Argon2 contiennent des virgules (`m=65536,t=3,p=4`). Le format env var initialement prévu (`API_KEYS=name1:hash1,name2:hash2`) aurait été cassé. **Changement** : séparateur `;` au lieu de `,`. Documenté dans l'ADR.

C'est typiquement le genre de détail qui surgit pendant la conception et qu'on est content d'avoir capturé avant de coder.

---

## 3. Étape 1 — Dépendance `argon2-cffi`

### Ce qui a été fait

Ajout dans [`pyproject.toml`](./pyproject.toml) :

```toml
dependencies = [
    ...
    "argon2-cffi",
]
```

Puis `pip install -e ".[dev]"` qui télécharge `argon2-cffi 25.1.0` + ses dépendances transitives (`argon2-cffi-bindings`, `cffi`, `pycparser`).

### Pourquoi cette lib

`argon2-cffi` (https://argon2-cffi.readthedocs.io/) est :
- Maintenue par **Hynek Schlawack** (auteur de `attrs`, `structlog`, autorité reconnue dans l'écosystème Python)
- Wrap la **lib C de référence** d'Argon2 (https://github.com/P-H-C/phc-winner-argon2)
- Présente une API haut niveau (`PasswordHasher`) qui choisit des paramètres safe par défaut
- 1700+ étoiles GitHub, releases régulières depuis 2015

C'est l'option canonique en Python.

### Pourquoi Argon2 (le format)

Argon2 a gagné le **Password Hashing Competition** en 2015 (https://www.password-hashing.net). C'est le standard moderne, supplantant bcrypt/scrypt/PBKDF2. Variante `id` = hybride résistant aux attaques GPU et side-channel.

OWASP le recommande comme premier choix pour 2024+ : https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html.

### Alternatives écartées

- **`bcrypt`** : encore acceptable mais 2x plus ancien, plus lent à mémoriser, pas de protection memory-hard. Pas de raison de partir sur l'alternative dépassée pour un projet neuf.
- **`passlib`** : abstraction sur plusieurs algos, beaucoup plus large que ce qu'on a besoin. La maintenance est plus lente que `argon2-cffi`.
- **`hashlib.scrypt`** stdlib : moins moderne qu'Argon2.
- **Hash SHA-256 simple** : pas de salt, vulnérable rainbow tables, anti-pattern.
- **HMAC-SHA256 avec un secret** : techniquement valide pour des API keys 256-bit, mais incompatible avec un futur ajout de mots de passe utilisateur (qui exigera de toute façon un hash lent type Argon2).

---

## 4. Étape 2 — Extension de `AppError` avec headers HTTP

### Ce qui a été fait

Modification de [`app/core/errors.py`](./app/core/errors.py) :

- Ajout d'un attribut de classe `default_headers: tuple[tuple[str, str], ...] = ()` à `AppError`
- Le constructeur copie ces defaults dans un attribut d'instance `self.headers: dict[str, str]`, mergé avec un éventuel paramètre `headers=...`
- `_problem_response` et `_handle_app_error` propagent ces headers à la `JSONResponse`

### Pourquoi un tuple de tuples et pas un dict

```python
default_headers: dict[str, str] = {}   # ← DANGER
```

Les attributs de classe **mutables** (dict, list, set) sont partagés entre **toutes les instances** et **toutes les sous-classes**. Si quelqu'un fait `instance.headers["X"] = "Y"`, ça contamine le dict de classe. Bug subtil et reproductible 1 fois sur 50.

```python
default_headers: tuple[tuple[str, str], ...] = ()   # ← OK
```

Les tuples sont immuables, donc impossible à muter par accident. On copie en dict dans `__init__` pour avoir un dict d'instance privé.

C'est un piège Python classique, documenté par exemple ici : https://docs.python-guide.org/writing/gotchas/#mutable-default-arguments

### Pourquoi cette extension

Pour pouvoir ajouter `WWW-Authenticate: Bearer realm="ai-kaeyris"` automatiquement à toute réponse 401. Exigé par RFC 6750 §3 :

> If the protected resource request does not include authentication credentials [...], the resource server MUST include the HTTP "WWW-Authenticate" response header field

Sans ce header, certains clients HTTP refusent même de retenter l'authentification.

### Alternative écartée

- **Ajouter `WWW-Authenticate` directement dans le handler** sans toucher à `AppError` : marche pour ce cas mais n'est pas extensible. La prochaine fois qu'on aura besoin d'un header custom, on devra rouvrir le handler. Mieux d'avoir le mécanisme générique une fois pour toutes.

---

## 5. Étape 3 — `app/core/auth.py`

### Ce qui a été fait

Création de [`app/core/auth.py`](./app/core/auth.py). Composants :

#### Exceptions
```python
class UnauthorizedError(AppError):  # 401 + WWW-Authenticate
class ForbiddenError(AppError):      # 403, pour révocations futures
```

#### Dataclasses
```python
@dataclass(frozen=True, slots=True)
class APIKeyEntry:
    name: str
    hash: str

@dataclass(frozen=True, slots=True)
class AuthenticatedKey:
    name: str
```

#### Fonctions
- `parse_api_keys(raw: str) -> list[APIKeyEntry]` : parse le format `name1:hash1;name2:hash2`, lève `ValueError` si malformé.
- `get_registered_keys() -> list[APIKeyEntry]` : lit `settings.API_KEYS` et le parse. **Overridable en tests** via `app.dependency_overrides`.
- `require_api_key(request, registered_keys=Depends(get_registered_keys))` : la dépendance FastAPI à monter sur les routers protégés.

### Pourquoi `frozen=True, slots=True` sur les dataclasses

- `frozen=True` : l'instance est immuable. On ne peut pas faire `entry.hash = "..."` après création. Sécurité par défaut.
- `slots=True` : utilise `__slots__` au lieu d'un `__dict__`. Légèrement plus rapide, légèrement moins gourmand en mémoire. Surtout : empêche d'ajouter des attributs dynamiques (encore plus de discipline).

Référence : https://docs.python.org/3/library/dataclasses.html#dataclasses.dataclass.

### Pourquoi un `dataclass APIKeyEntry` et pas un tuple `(name, hash)`

Lisibilité et type safety. `entry.name` est plus parlant que `entry[0]`. Et si demain on ajoute un champ `created_at`, on n'a pas à toucher à tous les sites d'usage.

### Pourquoi `get_registered_keys()` séparé du `require_api_key()`

Pour permettre l'injection en tests. FastAPI a un mécanisme `app.dependency_overrides[func] = replacement` qui ne marche que sur des fonctions appelées via `Depends()`. En faisant `require_api_key` dépendre de `Depends(get_registered_keys)`, on peut substituer le store de clés dans les tests sans monkey-patcher `settings`.

```python
app.dependency_overrides[get_registered_keys] = lambda: [test_entry]
```

C'est le pattern canonique FastAPI pour des dépendances configurables en tests : https://fastapi.tiangolo.com/advanced/testing-dependencies/

### Pourquoi la boucle `_verify_against_registry` ne short-circuit pas

Naïvement on ferait :
```python
for entry in entries:
    if hasher.verify(entry.hash, token):
        return AuthenticatedKey(name=entry.name)  # ← break implicite
```

Mais le temps total devient alors **proportionnel au nombre de tentatives jusqu'au match**. Un attaquant peut donc déduire si ses essais sont "proches" d'une vraie clé en mesurant le temps de réponse.

Notre version :
```python
matched = None
for entry in entries:
    if hasher.verify(entry.hash, token):
        if matched is None:
            matched = AuthenticatedKey(name=entry.name)
        # pas de break — on continue
return matched
```

Le temps total est constant pour un registre de N entrées (à `argon2.verify` lui-même constant-time près). C'est un best-effort — la taille du registre fuit toujours, mais c'est négligeable.

### Pourquoi rejeter même quand `API_KEYS=""`

Principe **secure by default**. Sans cette vérification, un opérateur qui oublie de remplir `.env` aurait un endpoint qui n'accepterait personne... mais qui resterait techniquement "accessible". Le code refuse explicitement et logge un `error`.

### Alternatives écartées

- **`fastapi.security.HTTPBearer`** (l'équivalent built-in FastAPI) : marche mais ajoute des comportements (auto-doc OpenAPI, gestion fine des erreurs) que je voulais contrôler entièrement. Refait en 5 lignes plus claires.
- **Comparaison brute par `==`** des tokens : timing attack potentielle. Argon2 nous protège, mais le pattern est mauvais en général.
- **Lire `settings.API_KEYS` directement dans `require_api_key`** au lieu de passer par `get_registered_keys` : casse la testabilité.

---

## 6. Étape 4 — `app/core/security_headers.py`

### Ce qui a été fait

Middleware Starlette dans [`app/core/security_headers.py`](./app/core/security_headers.py) qui ajoute :

| Header | Valeur | Rôle |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | empêche le browser de "deviner" un type MIME ; bloque les attaques de type MIME confusion |
| `X-Frame-Options` | `DENY` | bloque le chargement dans une iframe ; empêche les attaques de clickjacking |
| `Referrer-Policy` | `no-referrer` | ne fuit pas l'URL d'origine vers d'autres sites |
| `Content-Security-Policy` | `default-src 'none'` | API JSON pure ; aucun chargement de ressources permis |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | force HTTPS (effectif quand on sera derrière Caddy au Jalon 8) |

### Pourquoi `setdefault` et pas `__setitem__`

```python
response.headers.setdefault(header, value)
```

`setdefault` ne met la valeur que si la clé n'existe pas déjà. Permet à un endpoint spécifique de poser une politique CSP plus stricte sans que le middleware global ne l'écrase.

C'est un détail mais ça évite plus tard une frustration : "pourquoi mon endpoint ne respecte pas ma CSP custom ?"

### Pourquoi ces 5 headers et pas d'autres

D'autres headers existent (`X-XSS-Protection`, `Permissions-Policy`, `Cross-Origin-*`...). On a sélectionné les 5 standards et utiles pour une **API JSON** :

- `X-XSS-Protection` : déprécié et même contre-productif sur les browsers modernes.
- `Permissions-Policy` : utile pour les apps avec front (caméra, micro, etc.) ; pas pour une API.
- `Cross-Origin-*` : utiles pour des frontends ; on n'a pas de front.

Référence : OWASP Secure Headers Project — https://owasp.org/www-project-secure-headers/

### Alternatives écartées

- **Lib `secweb`** ou `starlette-securityheaders` : ajoute une dépendance pour 30 lignes qu'on maîtrise. Cohérent avec le choix RFC 9457 fait main du Jalon 1.
- **Headers configurables via env var** : YAGNI. Si on a besoin de différencier dev/prod un jour, on ajoutera.

---

## 7. Étape 5 — `app/core/config.py` et `.env.example`

### Ce qui a été fait

Ajout d'un champ Pydantic Settings dans [`app/core/config.py`](./app/core/config.py) :

```python
API_KEYS: str = ""
```

Et dans [`.env.example`](./.env.example) :

```
API_KEYS=
```

### Pourquoi un seul champ string et pas un objet structuré

Pydantic Settings supporte de parser du JSON depuis une env var. On aurait pu faire :

```python
API_KEYS: list[APIKeyEntry] = []
```

Mais ça aurait exigé que l'env var soit du JSON, qui est pénible à éditer à la main et qui n'est pas la convention dans la plupart des stacks.

On garde une string brute et on parse dans `auth.py`. Sépare les concerns : config = transport, auth = sémantique.

### Pourquoi un default `""` et pas `None`

Pour que `parse_api_keys()` puisse l'accepter sans `None` checks supplémentaires. Une chaîne vide = "pas de clés configurées", comportement explicite et sûr.

---

## 8. Étape 6 — `scripts/generate_api_key.py`

### Ce qui a été fait

Script CLI dans [`scripts/generate_api_key.py`](./scripts/generate_api_key.py) :

```bash
python scripts/generate_api_key.py mon-laptop
```

Sortie :
```
Name:      mon-laptop
Plain key: u27iPX5zjfpAceHyuQ9laXstbxr1FtXLGNWNlnh-Nrw

Send the plain key to the client. NEVER store it server-side.

Append (or replace) in your .env, separating entries with ';' :

  API_KEYS=mon-laptop:$argon2id$v=19$m=65536,t=3,p=4$wkZJ3D0N4HKeo/aBk1dRvg$+Awbk8Z5HTQOjhmG4Or2AXTM45RBrnpI5DZZ5jxO/9c
```

### Pourquoi un script séparé et pas un endpoint

Sécurité : un endpoint qui génère des API keys serait lui-même une cible. Préférable d'avoir une commande CLI locale qu'on exécute manuellement, qui produit le hash, puis qu'on insère dans `.env`. Le serveur n'a **jamais** la clé en clair.

### Pourquoi `secrets.token_urlsafe(32)`

- `secrets` (pas `random`) : module conçu pour les usages crypto (https://docs.python.org/3/library/secrets.html)
- `token_urlsafe` : encode en base64 URL-safe (caractères `A-Z a-z 0-9 - _`), pas de `+`, `/`, `=` qui poseraient problème dans des headers HTTP
- `32` octets = 256 bits d'entropie — irrécupérable par brute-force

### Validation du `name`

```python
if ";" in args.name or ":" in args.name:
    parser.error("name must not contain ';' or ':'.")
```

Ces deux caractères sont les séparateurs du format env var. Si on les autorisait dans le nom, on casserait le parsing.

### Alternatives écartées

- **Endpoint REST `POST /admin/api-keys`** : danger sécu (qui peut l'appeler ? avec quelle clé ?). Et inutile pour un sandbox personnel.
- **Lib `click` ou `typer`** : `argparse` (stdlib) suffit pour 2 arguments. Pas de dépendance ajoutée.

---

## 9. Étape 7 — Câblage dans `app/main.py`

### Ce qui a été fait

Ajout de 2 lignes dans [`app/main.py`](./app/main.py) :

```python
from app.core.security_headers import SecurityHeadersMiddleware
...
app.add_middleware(SecurityHeadersMiddleware)
```

### Pourquoi pas d'auth globale "par défaut"

FastAPI permet de poser des dépendances globales (`FastAPI(dependencies=[...])`). On aurait pu y mettre `Depends(require_api_key)` pour protéger toutes les routes par défaut.

**Pourquoi on ne le fait pas** : on devrait alors **désactiver** explicitement l'auth sur `/health`, `/docs`, `/redoc`, `/openapi.json`. FastAPI ne propose pas un mécanisme propre pour ça (il faudrait une dépendance qui détecte le path et skip — fragile).

À la place, le pattern recommandé est : auth attachée **au router** au moment de `include_router(...)`. Comme `_template` n'est pas monté en prod, il n'y a actuellement aucun service à protéger dans `main.py`. La machinerie est prête, le premier service réel (Jalon 5) la déclenchera.

### Risque connu

Si quelqu'un (ou toi dans 6 mois) monte un router en oubliant `dependencies=[Depends(require_api_key)]`, l'endpoint sera servi sans auth. C'est documenté dans le journal et le memo. Mitigation : revue de code, et pourquoi pas un test au Jalon 5+ qui parcourt l'OpenAPI et vérifie qu'aucune route hors liste publique n'est sans auth.

---

## 10. Étape 8 — Tests

### Vue d'ensemble

11 nouveaux tests, total **17 verts** :

```
tests/core/test_auth.py            9 tests (4 parse + 5 require)
tests/core/test_security_headers.py 2 tests
tests/core/test_errors.py           2 tests (déjà existants)
tests/services/_template/...        3 tests (déjà existants)
tests/test_health.py                1 test (déjà existant)
```

### Décomposition `test_auth.py`

**4 tests sur `parse_api_keys`** :
- string vide / None / espaces → 0 entrées
- une entrée → liste de 1
- plusieurs entrées séparées par `;` → liste correctement parsée
- entrée malformée (pas de `:`, name vide, hash vide) → `ValueError`

**5 tests sur `require_api_key`** (via mini-app FastAPI avec `dependency_overrides`) :
- header absent → 401 + `WWW-Authenticate`
- header malformé (`Authorization: Basic ...`) → 401
- clé inconnue → 401
- clé valide → 200 + payload `{"hello": <name>}`
- registre vide même avec un token → 401 (secure by default)

### Pourquoi un fixture `known_key`

```python
@pytest.fixture
def known_key():
    key = "test-secret-key-do-not-use-in-prod"
    hashed = PasswordHasher().hash(key)
    return key, [APIKeyEntry(name="test", hash=hashed)]
```

Hasher est **lent** (10ms par hash). Si on hashait la clé dans chaque test, on perdrait du temps. Le fixture le fait une fois et tous les tests qui en dépendent réutilisent.

### Décomposition `test_security_headers.py`

- 1 test sur réponse 200 → tous les 5 headers présents
- 1 test sur réponse 404 (route inexistante) → tous les 5 headers présents

Tester sur 404 est important : le middleware doit s'appliquer même quand la route n'existe pas (sinon un attaquant pourrait scanner les routes en distinguant celles qui existent par l'absence de header).

### Norme respectée

- **CLAUDE.md §2.5** : tout endpoint a au moins un test ; logique de sécurité testée explicitement.
- **AAA pattern** (Arrange / Act / Assert) appliqué.
- **Test pyramid** : ce sont des tests d'intégration légers (router + middleware + dépendance), rapides (~150ms total pour 11 tests dont les hash Argon2).

### Alternatives écartées

- **Mocker Argon2** pour aller plus vite : on perdrait la valeur du test. Mieux d'accepter 100ms de latence et tester réellement.
- **Tester sur l'app principale `app.main`** : impossible sans monter une route protégée. Mieux d'avoir un mini-app dédié dans le test.

---

## 11. Normes et bonnes pratiques respectées

| Norme | Application |
|---|---|
| **OWASP API Top 10 2023** | API1 (auth obligatoire), API4 (auth bloque l'abus), API8 (security headers) |
| **OWASP Password Storage Cheat Sheet** | Argon2id avec paramètres recommandés |
| **OWASP Secure Headers Project** | 5 headers recommandés posés |
| **RFC 6750** (Bearer Token) | Format `Authorization: Bearer ...` + `WWW-Authenticate` sur 401 |
| **RFC 9457** (Problem Details) | Réponses d'erreur d'auth conformes (héritées du Jalon 1) |
| **RFC 9106** (Argon2) | Format de hash standard, auto-décrivant |
| **12-Factor §III** | Toutes les clés via env var, jamais dans le code |
| **Secure by default** | Tout protégé sauf liste publique explicite ; refus si `API_KEYS=""` |
| **Constant-time comparison** | `argon2.verify` (constant-time) + boucle complète sans short-circuit |
| **Single Responsibility** | `errors.py` / `auth.py` / `security_headers.py` séparés |
| **YAGNI** (CLAUDE.md §2.3) | Pas d'OAuth, pas de JWT, pas de DB, pas de scopes, pas de rotation |
| **Dependency injection testable** | `get_registered_keys` overridable via `app.dependency_overrides` |

---

## 12. Choix alternatifs envisagés et écartés

### Au niveau mécanisme d'auth

| Alternative | Pourquoi écartée |
|---|---|
| OAuth2 / OIDC | Sur-ingénierie pour mono-utilisateur ; aucun tiers à autoriser |
| JWT | Inutile sans sessions distribuées ; révocation coûteuse |
| mTLS (certificats clients) | Très sécurisé mais lourd : génération/distribution/rotation |
| API key dans query string | Apparait dans logs/history/referer, anti-pattern RFC 6750 §2.3 |
| Auth Basic HTTP | Encode base64, pas de hash ; moins clair pour M2M |

### Au niveau hash

| Alternative | Pourquoi écartée |
|---|---|
| bcrypt | Acceptable mais dépassé ; pas de raison de partir là-dessus pour un projet neuf |
| scrypt stdlib | Moins moderne qu'Argon2 |
| SHA-256 simple | Pas de salt, vulnérable rainbow tables |
| HMAC-SHA256 | Défendable techniquement (256-bit entropie côté key) mais incompatible avec un futur ajout de mots de passe humains |
| Hash en clair (??) | (n'a même pas été envisagé sérieusement) |

### Au niveau stockage

| Alternative | Pourquoi écartée |
|---|---|
| Fichier JSON `api_keys.json` | Plus de cérémonie qu'env var pour 1-3 clés |
| DB SQL | Pas encore de DB (Jalon 5+) |
| Service externe (Vault, KMS) | Excessif pour un sandbox local |
| Hash secret en dur dans le code | Le code va sur GitHub publique |

### Au niveau security headers

| Alternative | Pourquoi écartée |
|---|---|
| Lib `secweb` | 30 lignes économisées contre une dépendance ; cohérent avec choix Jalon 1 |
| Headers configurables par route | YAGNI ; `setdefault` permet déjà l'override |
| Headers via décorateur sur la route | Plus invasif, moins systématique |

### Au niveau rate limiting

| Alternative | Pourquoi écartée |
|---|---|
| `slowapi` maintenant | Sans Redis, in-memory ne marche pas en multi-instance ; mieux d'attendre Jalon 3 |
| Fait main avec dict + timestamps | Réinvente la roue, mauvais à scale |
| `nginx`/Caddy rate limit côté reverse-proxy | Logique : on fera ça en complément au Jalon 8, pas en remplacement |

---

## 13. Limitations acceptées

| Limitation | Pourquoi acceptée | À reprendre quand |
|---|---|---|
| Pas de rate limiting | Reporté Jalon 3 (avec Redis) | Jalon 3 |
| Pas de scopes par clé | YAGNI ; toutes les clés ont les mêmes droits | Si un service "dangereux" doit être isolé |
| Rotation = redémarrage du conteneur | Pas de hot-reload pour env vars | Jalon 5 (DB) |
| Stockage limité à ~5 clés en pratique | Chaîne env var devient pénible à éditer | Jalon 5 (DB) |
| Pas d'audit log auth | Reporté Jalon 6 (observabilité) | Jalon 6 |
| Timing-attack inter-entrées non parfait | Le temps total révèle la **taille** du registre, pas son contenu | Acceptable à notre échelle |
| Pas de handler `HTTPException` FastAPI | Pas utilisé en interne | Si on adopte le pattern |
| Risque "oubli de `Depends(require_api_key)`" | FastAPI n'a pas de "auth par défaut" propre | Test au Jalon 5+ qui scanne l'OpenAPI |

---

## 14. Ce que ce jalon prépare pour la suite

### Jalon 3 (async queue)

- Le `require_api_key` peut être réutilisé tel quel pour protéger les endpoints qui poussent des jobs.
- Redis débloque le rate limiting (in-memory devient OK en mono-instance + Redis pour multi).

### Jalon 4 (adapters LLM)

- L'API key authentifiée → `AuthenticatedKey.name` → on peut tracer "qui a consommé combien de tokens DeepInfra".
- Cohérent avec le besoin d'auditer la consommation par client.

### Jalon 5 (premier service réel — JDR)

- Pattern à suivre : `app.include_router(jdr.router, dependencies=[Depends(require_api_key)])`.
- Migration possible vers un store DB pour les clés (table `api_keys(name, hash, status, created_at, last_used_at)`).
- Possibilité d'ajouter des scopes : "cette clé peut appeler `/services/jdr/*` mais pas `/services/admin/*`".

### Jalon 6 (observabilité)

- Audit log : structlog logera chaque `require_api_key` avec `auth.name` et le résultat.
- Métriques Prometheus : `api_auth_attempts_total{result="success|failure"}`.

### Jalon 7 (CI/CD)

- Tests d'auth font partie de la suite ; ils tourneront en CI.
- Scan secrets (gitleaks) : alertera si un hash apparait par erreur dans un commit.

### Jalon 8 (déploiement Pi)

- HSTS deviendra effectif (Caddy fournit le HTTPS).
- Caddy peut faire un rate limiting complémentaire au niveau reverse-proxy.

---

## Référence rapide — checklist DoD du Jalon 2

| Critère CLAUDE.md §7 | État |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `pytest` | ✅ 17 passed |
| `docker compose up --build` | 🟡 à tester (rebuild requis : `pyproject.toml` modifié) |
| `curl /health` + auth | 🟡 à tester avec une clé générée |
| README à jour | ✅ section Authentification ajoutée |
| Entrée journal | ✅ |
| ADR | ✅ ADR 0003 |
| Commit pushed | 🟡 reste à faire |
