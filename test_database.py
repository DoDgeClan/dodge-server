import os
import tempfile
import unittest
from unittest.mock import patch
from database import Row, connect, postgres_sql


class DatabaseTests(unittest.TestCase):
    def test_bound_values_keep_unicode_and_quotes(self):
        self.assertEqual(postgres_sql("SELECT '?' AS mark,\"?\" FROM t WHERE text=? AND x LIKE '%'"),
                         "SELECT '?' AS mark,\"?\" FROM t WHERE text=%s AND x LIKE '%%'")
        value = "Привет 'quoted' 😊 ? %"
        with patch.dict(os.environ, {'DATABASE_URL': '', 'RENDER': ''}):
            with tempfile.TemporaryDirectory() as tmp:
                db = connect(tmp + '/db')
                db.execute('CREATE TABLE test(value TEXT)')
                db.execute('INSERT INTO test VALUES(?)', (value,))
                self.assertEqual(db.execute('SELECT value FROM test').fetchone()[0], value)
                db.close()

    def test_render_refuses_ephemeral_database(self):
        with patch.dict(os.environ, {'DATABASE_URL': '', 'RENDER': 'true', 'DODGE_ALLOW_EPHEMERAL': ''}):
            with self.assertRaisesRegex(RuntimeError, 'persistent_database_required'):
                connect(':memory:')

    def test_invalid_dsn_fails_before_connecting(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'http://bad-host'}):
            with self.assertRaisesRegex(RuntimeError, 'invalid_database_url'):
                connect(':memory:')

    def test_row_supports_existing_index_and_dictionary_code(self):
        row = Row(['id', 'text'], [7, 'привет'])
        self.assertEqual(row[0], 7)
        self.assertEqual(row['text'], 'привет')
        self.assertEqual(dict(row), {'id': 7, 'text': 'привет'})
