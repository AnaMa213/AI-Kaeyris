# ADR 0013 - Separation des roles systeme et scoping campagne des PJ

## Statut

Accepte - 2026-06-01

## Contexte

BD-6 a ajoute le contexte de campagne, mais l'identite web melangeait encore deux notions : le profil global du portail (`gm`/`user`) et le role d'un membre dans une campagne (`gm`/`player`). Les PJ restaient aussi utilisables comme objets globaux du MJ, ce qui bloquait les prochains ecrans front de gestion par campagne.

## Decision

Nous separons explicitement les roles :

- `core_users.system_role` porte l'autorite globale du portail : `admin` ou `user`.
- `jdr_campaign_members.role` porte l'autorite locale a une campagne : `gm` ou `pj`.
- Les routes `/services/jdr/users` sont reservees aux admins globaux.
- Tout utilisateur web authentifie peut creer une campagne et devient GM de cette campagne.
- `jdr_pjs.campaign_id` devient obligatoire et `jdr_pjs.user_id` optionnel permet de rattacher un PJ a un compte web.
- Le role API-key legacy `player` reste preserve pour les tokens joueur et les routes `/me/*`.

## Consequences

Le front peut deduire les permissions depuis `/services/jdr/auth/me` sans confondre administration globale et pouvoir de GM. Les endpoints PJ deviennent compatibles avec des vues par campagne, tout en gardant le fallback V1 sur la campagne par defaut quand un GM web cree un PJ sans `campaign_id`.

La migration BD-7 est volontairement orientee purge local/staging : elle backfill `system_role`, convertit les memberships `player` vers `pj`, puis rend `jdr_pjs.campaign_id` non nullable. Les environnements contenant encore des PJ sans campagne doivent etre purges ou reseedes avant upgrade.

## Alternatives rejetees

- Garder `profile=gm|user` et ajouter un champ de campagne separe : trop ambigu pour le front et pour les tests d'autorisation.
- Renommer le role API-key `player` partout : trop risque, car les tokens joueur `/me/*` ont encore un contrat separe des memberships web.
- Laisser les PJ globaux et filtrer seulement cote front : insuffisant pour l'autorisation serveur.
