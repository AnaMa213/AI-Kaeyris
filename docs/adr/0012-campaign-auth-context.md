# ADR 0012 — Campaign Auth Context

Date: 2026-05-30

## Statut

Accepté.

## Contexte

Le frontend BD-4 a besoin d'un contrat backend stable pour connaître l'utilisateur courant et son contexte de campagne via `GET /services/jdr/auth/me`. Le projet reste en V1 mono-campagne dans l'usage normal, mais les données JDR doivent déjà être isolables par campagne pour éviter de faire confiance à un `campaign_id` envoyé par le client.

## Décision

La campagne devient la frontière de multi-tenancy du service JDR.

- Seed V1 d'une campagne fixe : `00000000-0000-0000-0000-000000000001`, `Campagne par defaut`.
- Ajout de `campaigns`, `campaign_members`, `core_users.default_campaign_id`, `jdr_sessions.campaign_id` et `jdr_pjs.campaign_id`.
- Résolution active : `default_campaign_id` valide, sinon premier membership, sinon `active_campaign: null`.
- Les routes JDR dérivent `campaign_id` côté serveur depuis l'auth ; les champs `campaign_id` dans les payloads front sont refusés.
- Les API keys Bearer restent compatibles et utilisent la campagne V1 quand elle existe.

## Alternatives rejetées

- Ajouter une couche tenant/organisation : trop large pour BD-4 et contraire au YAGNI du projet.
- Laisser le frontend passer `campaign_id` : risque d'autorisation cassée, car le client ne doit pas choisir son périmètre de données.
- Ajouter des endpoints CRUD de campagnes en V1 : le besoin frontend porte sur le contexte courant, pas sur l'administration de campagnes.

## Conséquences

- `/services/jdr/auth/me` est publié dans l'OpenAPI avec `Cache-Control: no-store`.
- Les users existants et créés via `/services/jdr/users` reçoivent un membership de campagne.
- Les tests doivent couvrir la résolution active, le seed idempotent, le rejet de `campaign_id` client et l'isolation session/PJ.

Références : 12-Factor state in backing services https://12factor.net/processes ; OWASP API Security 2023 broken authorization https://owasp.org/API-Security/editions/2023/en/0xa5-broken-function-level-authorization/
