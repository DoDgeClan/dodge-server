# Owner administration API

Configure `DODGE_ADMIN_KEY` as a random secret of at least 32 characters on the
Render service and the private admin Site. Never send this key to the browser
or the game client. Calls require `Authorization: Bearer <key>` over HTTPS.
Missing configuration denies access with 503; missing or invalid credentials
return 401. Admin responses disable caching and do not enable browser CORS.

Routes:

- `GET /v2/admin/status`: player, online, lobby and match counts; deployed commit.
- `GET /v2/admin/players?limit=25&offset=0&search=`: paginated public player
  identities; no guest tokens, token hashes, chat messages or media.
- `GET /v2/admin/rooms`: up to 200 lobbies with members and match state.
- `POST /v2/admin/action`: JSON with `action`, `id` and, for rename, `name`.
  Supported actions: `rename_player`, `kick_player`, `close_room`.

Rename uses existing persistent guest tables and unique name rules. It keeps
guest authentication tokens and history. Kick only removes the current room
membership; it is not a persistent ban. Closing a room ends its running match
and removes invitations. No API deletes player accounts, progress or messages.
Site actions additionally require owner identity, an active password session
and a same-origin JSON request. Successful mutations enter the Site journal.

No database migration is needed. Startup performs read-only loopback checks
against the running HTTP server and the configured database. The log line
`admin_startup_check ok=true` confirms unauthorized requests receive 401 and
authenticated status, player and room requests receive 200. It never logs the
secret or player response bodies.

Validation: `python -m unittest discover -q`.
For rollback, redeploy commit `282f0654ed3421861bd662cfca80485ad5ee77ee`.
