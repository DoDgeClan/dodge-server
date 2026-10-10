"""Import backups without changing guest IDs/tokens. Dry-run is the default.

Use DATABASE_URL for a Supabase session-pooler connection. Never pass a DSN
on the command line or commit database backups. A nonempty target is refused.
"""
import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

TABLES = ('guests', 'friends', 'chat_unlocks', 'chat_preferences', 'chat_pairs',
          'chat_messages', 'chat_blocks', 'chat_reactions', 'chat_media',
          'chat_reports', 'chat_days', 'chat_events', 'push_devices',
          'meta', 'scores', 'monthly_rewards')


def read_backup(path):
    source = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('sqlite_backup_corrupt')
        names = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        for name in TABLES:
            if name in names:
                columns = [row[1] for row in source.execute(f'PRAGMA table_info("{name}")')]
                rows = source.execute(f'SELECT * FROM "{name}"').fetchall()
                result[name] = (columns, rows)
        return result
    finally:
        source.close()


def checksum(rows):
    def encode(value):
        if isinstance(value, (bytes, memoryview)):
            return {'binary_sha256': hashlib.sha256(bytes(value)).hexdigest()}
        return value
    encoded = [json.dumps([encode(v) for v in row], ensure_ascii=False,
                          separators=(',', ':')) for row in rows]
    return hashlib.sha256('\n'.join(sorted(encoded)).encode()).hexdigest()


def import_backups(social, leaderboard, *, apply=False, connection=None):
    snapshots = read_backup(social)
    for name, table in read_backup(leaderboard).items():
        if name in snapshots:
            raise RuntimeError('overlapping_backup_tables')
        snapshots[name] = table
    if not {'guests', 'friends', 'scores', 'meta', 'monthly_rewards'} <= snapshots.keys():
        raise RuntimeError('missing_backup_tables')
    counts = {name: len(table[1]) for name, table in snapshots.items()}
    if not apply:
        return {'applied': False, 'rows': counts}
    import psycopg
    from psycopg import sql
    from database import WRITE_LOCK
    own_connection = connection is None
    if own_connection:
        dsn = os.environ.get('DATABASE_URL')
        if not dsn:
            raise RuntimeError('database_url_required')
        connection = psycopg.connect(dsn, sslmode='require', connect_timeout=10,
                                    autocommit=True, prepare_threshold=None)
    try:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
            version = connection.execute(
                'SELECT version FROM dodge_private.schema_version WHERE singleton=1').fetchone()
            if version is None or version[0] != 1:
                raise RuntimeError('database_migration_required')
            for name in TABLES:
                table = sql.Identifier('dodge_private', name)
                if connection.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(table)).fetchone():
                    raise RuntimeError('target_not_empty')
            for name, (columns, rows) in snapshots.items():
                table = sql.Identifier('dodge_private', name)
                selected = sql.SQL(',').join(map(sql.Identifier, columns))
                placeholders = sql.SQL(',').join(sql.Placeholder() for _ in columns)
                query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(table, selected, placeholders)
                with connection.cursor() as cursor:
                    cursor.executemany(query, rows)
                restored = connection.execute(sql.SQL('SELECT {} FROM {}').format(selected, table)).fetchall()
                if checksum(restored) != checksum(rows):
                    raise RuntimeError('import_verification_failed')
            for name in ('chat_messages', 'chat_reports'):
                connection.execute(sql.SQL(
                    "SELECT setval(pg_get_serial_sequence(%s,'id'),"
                    'COALESCE((SELECT max(id) FROM {}),1),'
                    '(SELECT count(*)>0 FROM {}))').format(
                        sql.Identifier('dodge_private', name),
                        sql.Identifier('dodge_private', name)), ('dodge_private.' + name,))
        return {'applied': True, 'rows': counts}
    finally:
        if own_connection:
            connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--social', required=True)
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(import_backups(args.social, args.leaderboard, apply=args.apply)))
    except Exception as exc:
        # Drivers can include the DSN and server detail in exception messages.
        print(json.dumps({'ok': False, 'error_type': type(exc).__name__}))
        raise SystemExit(1)
