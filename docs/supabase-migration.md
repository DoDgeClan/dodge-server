# Supabase persistence — separate Free test deployment

Project: `ovrhdtdxfxspacmnoqtd`. The project already existed and is on the
user's account. No paid resource was created or upgraded.

## Current production

Render `dodge-server`, branch `main`, commit
`1140db8b2d5e1f10b88fd93db1fdc1b27c89790e`, still runs protocol 3 and SQLite.
The prepared code is based on `feature/social-1.7` at
`0d65e8dfe264c5d848ad202d185c5b806a028660`, protocol 5. Its private chat
routes are now available on the separate test deployment described below.

## Separate deployment

The candidate now adds `/v2/migrate`. It authenticates a retained guest token
against the fixed original HTTPS service, then copies the verified identity,
accepted friends and incoming requests in one transaction. Verified existing
friend profiles reserve IDs/names until those participants authenticate their
own tokens; the reservation token field cannot authenticate. Repeated migration
with an already imported valid token is idempotent. Conflicts or an unavailable
legacy account fail explicitly, without creating a replacement guest.
This is per-account recovery, not a complete export of historical leaderboard
rows or of users whose tokens/data were already lost by the old Free service.

Render `dodge-supabase-18`, service `srv-db0t26qd0e5s73d39rn0`, runs
`feature/supabase-persistence-1.8` at
`76056ce809d225dc81ef31487a195a5b3d85d275` on the Free plan in Singapore.
URL: https://dodge-supabase-18.onrender.com. Auto-deploy is disabled.
Build: `pip install -r requirements.txt`; start: `python online_server_render.py`.
The session-pooler host was verified in the actual project's Connect dialog:
`aws-0-ap-northeast-2.pooler.supabase.com:5432`.
The dedicated `dodge_writer` login uses a private generated password stored
only in Render environment configuration. It has no superuser, role-creation
or database-creation permissions. The existing Render service was not changed.

Live HTTPS health confirms protocol 5, chat and matches available, and explicit
`Asia/Qyzylorda` streak timezone. `push_available` is false: FCM credentials
are not configured. Android still targets the original server, so installing
the old APK will not gain the new server functions automatically.

## Changes

- Supabase has `dodge_private`: 16 game tables plus a schema-version table.
  Every table has RLS. `anon` and `authenticated` have no schema/table access.
  A restricted `dodge_writer` role has game DML permissions, no schema DDL.
  Game guest-token authentication and participant checks remain on the server.
- `DATABASE_URL` selects PostgreSQL with TLS, disabled named prepared
  statements, finite connection/query/lock timeouts and a transaction lock for
  read/modify/write operations. Use a **session pooler**, not a transaction
  pooler, because search_path and timeouts are session settings.
- SQLite remains usable for local tests. Render fails startup without a
  persistent database instead of silently saving accounts on ephemeral disk.
- Inserts use portable UPSERT syntax. Message IDs use PostgreSQL identity
  sequences. Reactions/preferences update in place; message retries and
  notification events retain their existing uniqueness constraints.
- Leaderboard connections close after each operation. Media HTTP success is
  returned after transaction commit, rather than before commit.
- Duplicate-name checks run before UPDATE under the database transaction
  lock; they do not leave the PostgreSQL transaction in an aborted state.
- The importer refuses a nonempty target, checks source integrity and restored
  row checksums, preserves IDs/token hashes/friendships/settings/media and
  repairs identity sequences. Dry-run is the default. No secrets are logged.
- Media currently uses private PostgreSQL BYTEA, with 32 MiB per-account and
  256 MiB global upload limits. This counts against the free **database** quota.
  Supabase Storage's separate 1 GB allowance is **not integrated yet**.

## Real verification on 2026-10-04

- `scripts/check_live_chat.py` passed against the actual HTTPS Render/Supabase
  deployment with three new test guests. It checked EN/RU and emoji, request
  acceptance/denial, nonce deduplication, delivery ACK with read receipts off,
  replies, sender style, mutual-day streak, editing and owner-only deletion,
  compressed photo delivery, a real AAC/M4A voice file, participant-only media
  access and blocking/unblocking. Four messages and two media files were
  confirmed in the actual Supabase tables. This is backend HTTP verification,
  not interaction with the Android app or push delivery.
- 31 Python unittest checks pass with SQLite: chat, requests, privacy, media,
  streaks, mock FCM payloads, multiplayer state, DSN validation and import guards.
- 24 existing application scenarios pass through actual psycopg against local
  PostgreSQL in PGlite 0.5.8 / pglite-socket 0.2.11, using the restricted role.
  PGlite is a WASM PostgreSQL test engine, not the live Supabase network path.
  Test-only loopback TLS suppression is confined to the explicit test script;
  production always requires TLS.
- A generated SQLite fixture imports to PostgreSQL with identical old tokens,
  names, friendships, Unicode messages and compressed photo bytes. Imported
  ID 99 is followed by ID 100. Reimport refuses an occupied target. Reopening
  the server DB connection preserves messages and identities.
- Actual Supabase SQL assertions under `dodge_writer` pass for Unicode/emoji,
  message nonce deduplication, ACK, binary bytes, preferences, friendships and
  event deduplication. The SQL test rolls back its temporary data.
- Supabase security advisors return no findings. Performance advisors only
  report currently unused indexes on the empty database; they are retained for
  the existing application query patterns.
- Python AST validation and `git diff --check` pass.

## Blockers before switching the existing Render service

1. Existing `social.db` and `leaderboard.db` must be exported. The currently
   accessible connector/API and protocol-3 HTTP routes do not expose those
   files or a database-export endpoint. Free Render provides no Shell/SSH or
   persistent disk. A code rollback cannot recover overwritten ephemeral data.
   Do not restart, deploy or save deployment-triggering environment changes
   before obtaining a backup. No production records have been copied yet.
2. Render needs a private database connection string. The Supabase connector
   can execute SQL, but does not provide the database password. Do not put it
   in chat, GitHub source, APK or logs. Use Render's secret environment settings
   only after the old data has been protected.
3. Create/enable a dedicated database login with the `dodge_writer` role,
   using a private password. Take the exact session-pooler host from Supabase's
   **Connect** dialog. Do not guess the host. Runtime login needs schema usage,
   table DML and sequence usage; import is performed with an admin connection.
4. Build command must become `pip install -r requirements.txt`; start command
   remains `python online_server_render.py`. Configure `DATABASE_URL` privately.
   Import both verified backups into the empty target before enabling clients.

If a complete backup cannot be obtained, preserve the old service and design
a separate authenticated account-by-account migration. Do not reset accounts
or claim that all old friendships/ratings have been recovered.

## Commands after a backup exists

```sh
python migrate_sqlite.py --social /secure/social.db --leaderboard /secure/leaderboard.db
# DATABASE_URL is supplied privately in the environment, with an admin login:
python migrate_sqlite.py --social /secure/social.db --leaderboard /secure/leaderboard.db --apply
python -m unittest discover -v
```

`scripts/check_postgres.py` is destructive only to a disposable local fixture;
it refuses all remote hosts. Never use it against production. The SQL-only
Supabase verification script rolls back its temporary rows.

The user-authorized APK workflow 37178127200 was started on
`release/android-1.8-supabase`, client commit
`1c6b35bf21f8246ac8c747411f0498f1f90dc983`. Native results are pending.
No paid-plan change was made. The separate Free service was deployed at server
commit `b3b0de68233735f1572a2b87afd51f3d64462d59`.
Two real newly created legacy accounts then passed authenticated transfer:
IDs, names, original tokens and friendship were retained; private Unicode
chat delivered after both transfers. This does not verify recovery of all
historical users or lost legacy sessions. Two-account text/photo/delivery
release checks passed over real HTTPS. Local direct WSS checks reached room
creation but failed with DNS errors; the workflow retains the live WSS gate.
The private database login and session pooler were configured for the new
service; the old deployment remains unchanged. The numbered backup steps
above apply to a future full historical export, not the per-account route.
Android delivery, camera/recording, closed-app FCM notifications and native 1.8
startup remain separate unverified checks. The latest native smoke diagnostic
timed out before DODGE_READY; a successful APK build does not resolve that.
