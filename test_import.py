import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image
from social import Social
import online_server_render as leaderboard
from migrate_sqlite import checksum, import_backups, read_backup


def fixture(directory):
    social_path = str(Path(directory) / 'social.db')
    scores_path = str(Path(directory) / 'leaderboard.db')
    with patch.dict(os.environ, {'DATABASE_URL': '', 'RENDER': ''}):
        social = Social(social_path)
        a, b = social.register(), social.register()
        social.dispatch(a['token'], 'friend_add', {'name': b['name']})
        social.dispatch(b['token'], 'friend_accept', {'id': a['id']})
        social.dispatch(a['token'], 'chat_send',
                        {'friend': b['id'], 'nonce': 'import_nonce_001', 'text': 'Сохранить 😊'})
        image = io.BytesIO()
        Image.new('RGB', (10, 12), 'blue').save(image, 'PNG')
        with social.db:
            media = social.chat.upload(a['id'], 'photo', image.getvalue())
            social.db.execute('UPDATE chat_messages SET id=99')
        compressed = social.chat.download(a['id'], media['media'])[1]
        social.db.close()
        with patch.object(leaderboard, 'DB_FILE', scores_path):
            leaderboard.init_db()
            leaderboard.submit({'uid': a['id'], 'name': a['name'], 'score': 77})
    return social_path, scores_path, a, b, media, compressed


class ImportTests(unittest.TestCase):
    def test_dry_run_keeps_existing_files_and_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            social, scores, a, b, media, content = fixture(tmp)
            before = checksum(read_backup(social)['guests'][1])
            result = import_backups(social, scores)
            self.assertFalse(result['applied'])
            self.assertEqual(result['rows']['guests'], 2)
            self.assertEqual(result['rows']['chat_messages'], 1)
            self.assertEqual(result['rows']['scores'], 1)
            self.assertEqual(before, checksum(read_backup(social)['guests'][1]))

    def test_corrupt_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'broken.db'
            path.write_bytes(b'not a database')
            with self.assertRaises(sqlite3.DatabaseError):
                read_backup(path)

    def test_missing_backup_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing.db'
            with self.assertRaises(sqlite3.OperationalError):
                read_backup(path)
            self.assertFalse(path.exists())
