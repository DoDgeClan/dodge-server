"""Run existing server tests against a disposable LOCAL PostgreSQL database.

TEST_DATABASE_URL must point to localhost. The script clears game tables in
that local database between tests; it refuses every remote host. PGlite over
its socket server is also supported for environments without a native daemon.
"""
import os
import sys
import unittest
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg import sql

dsn = os.environ.get('TEST_DATABASE_URL', '')
if urlparse(dsn).hostname not in ('127.0.0.1', 'localhost', '::1'):
    raise SystemExit('Only a disposable local PostgreSQL database is allowed')
real_connect = psycopg.connect


def admin():
    connection = real_connect(dsn, autocommit=True, sslmode='disable', prepare_threshold=None)
    connection.execute('RESET ROLE')
    return connection


with admin() as connection:
    connection.execute("DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    connection.execute("DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    exists = connection.execute("SELECT to_regclass('dodge_private.schema_version')").fetchone()[0]
    if not exists:
        migration = next((ROOT / 'supabase/migrations').glob('*_dodge_persistence.sql'))
        connection.execute(migration.read_text(), prepare=False)


def runtime_connect(conninfo='', **kwargs):
    # The loopback test server has no TLS. Production database.py always
    # requires TLS; this patch only affects this explicit local test process.
    kwargs['sslmode'] = 'disable'
    connection = real_connect(conninfo, **kwargs)
    connection.execute('SET ROLE dodge_writer')
    return connection


class LocalResult(unittest.TextTestResult):
    def startTest(self, test):
        with admin() as connection:
            names = [row[0] for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='dodge_private' AND tablename<>'schema_version'")]
            tables = sql.SQL(',').join(sql.Identifier('dodge_private', name) for name in names)
            connection.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY').format(tables))
        super().startTest(test)


with patch.dict(os.environ, {'DATABASE_URL': dsn}), patch('psycopg.connect', runtime_connect):
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in
                               ('test_chat', 'test_social', 'test_release16', 'test_multiplayer'))
    result = unittest.TextTestRunner(verbosity=2, resultclass=LocalResult).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
print('PostgreSQL application scenarios passed with the restricted runtime role')

from test_import import fixture
from migrate_sqlite import import_backups
from social import Social
with tempfile.TemporaryDirectory() as directory:
    social_path, scores_path, a, b, media, content = fixture(directory)
    with admin() as connection:
        names = [row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='dodge_private' AND tablename<>'schema_version'")]
        tables = sql.SQL(',').join(sql.Identifier('dodge_private', name) for name in names)
        connection.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY').format(tables))
        result = import_backups(social_path, scores_path, apply=True, connection=connection)
        assert result['applied'] and result['rows']['guests'] == 2
        try:
            import_backups(social_path, scores_path, apply=True, connection=connection)
        except RuntimeError as exc:
            assert str(exc) == 'target_not_empty'
        else:
            raise AssertionError('Repeated import must refuse existing target data')
    with patch.dict(os.environ, {'DATABASE_URL': dsn}), patch('psycopg.connect', runtime_connect):
        restored = Social(':ignored:')
        assert restored.authenticate(a['token']) == a['id']
        assert restored.authenticate(b['token']) == b['id']
        assert restored.profile(a['id'])['name'] == a['name']
        assert b['id'] in restored.friend_ids(a['id'])
        assert restored.chat.download(a['id'], media['media'])[1] == content
        messages = restored.dispatch(b['token'], 'chat_fetch', {'friend': a['id']})['messages']
        assert messages[0]['text'] == 'Сохранить 😊' and messages[0]['id'] == 99
        sent = restored.dispatch(a['token'], 'chat_send',
                                 {'friend': b['id'], 'nonce': 'after_import_001', 'text': 'После переноса'})
        assert sent['message']['id'] == 100
        restored.db.close()
        restored = Social(':ignored:')
        assert len(restored.dispatch(b['token'], 'chat_fetch', {'friend': a['id']})['messages']) == 2
        restored.db.close()
print('Import, old tokens, names, friends, bytes, ID sequence and restart verified')
