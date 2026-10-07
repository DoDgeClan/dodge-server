-- Additive migration in the existing private schema; no identities/progress removed.
BEGIN;
CREATE TABLE IF NOT EXISTS dodge_private.account_credentials (
 uid TEXT PRIMARY KEY REFERENCES dodge_private.guests(id),
 salt TEXT NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dodge_private.account_sessions (
 token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES dodge_private.guests(id), expires DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS account_session_expiry ON dodge_private.account_sessions(expires);
CREATE TABLE IF NOT EXISTS dodge_private.account_rate (
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS dodge_private.account_qr (
 id TEXT PRIMARY KEY, poll_hash TEXT NOT NULL, uid TEXT REFERENCES dodge_private.guests(id), expires DOUBLE PRECISION NOT NULL);
ALTER TABLE dodge_private.account_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE dodge_private.account_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dodge_private.account_rate ENABLE ROW LEVEL SECURITY;
ALTER TABLE dodge_private.account_qr ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON dodge_private.account_credentials, dodge_private.account_sessions,
 dodge_private.account_rate, dodge_private.account_qr FROM PUBLIC, anon, authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON dodge_private.account_credentials,
 dodge_private.account_sessions,dodge_private.account_rate,dodge_private.account_qr TO dodge_writer;
CREATE POLICY server_access ON dodge_private.account_credentials TO dodge_writer USING (true) WITH CHECK (true);
CREATE POLICY server_access ON dodge_private.account_sessions TO dodge_writer USING (true) WITH CHECK (true);
CREATE POLICY server_access ON dodge_private.account_rate TO dodge_writer USING (true) WITH CHECK (true);
CREATE POLICY server_access ON dodge_private.account_qr TO dodge_writer USING (true) WITH CHECK (true);
COMMIT;
