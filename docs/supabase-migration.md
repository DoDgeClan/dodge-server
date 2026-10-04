# Supabase persistence — staged, not deployed

Project: `ovrhdtdxfxspacmnoqtd`. The project already existed and is on the
user's account. No paid resource was created or upgraded.

## Current production

Render `dodge-server`, branch `main`, commit
`1140db8b2d5e1f10b88fd93db1fdc1b27c89790e`, still runs protocol 3 and SQLite.
The prepared code is based on `feature/social-1.7` at
`0d65e8dfe264c5d848ad202d185c5b806a028660`, protocol 5. Its private chat
routes cannot become available until that backend is deployed.

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

No Actions workflow, Render deploy, new APK build or paid-plan change was
started for this migration. Android delivery, camera/recording, closed-app
FCM notifications and native 1.8 startup remain separate unverified checks.
