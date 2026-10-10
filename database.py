"""Small DB-API bridge: SQLite for local tests, PostgreSQL for Render.

The API keeps existing guest tokens and wire messages unchanged. PostgreSQL
tables live in dodge_private, outside the public Data API. Migrations are
applied separately; the runtime role cannot create or alter tables.
"""
import logging
import os
import re
import sqlite3
from collections.abc import Mapping

SCHEMA_VERSION = 1
SCHEMA = "dodge_private"
WRITE_LOCK = 79417018


class Row(Mapping):
    def __init__(self, names, values):
        self.names, self.values = names, values
        self.positions = {name: i for i, name in enumerate(names)}

    def __getitem__(self, key):
        return self.values[key if isinstance(key, int) else self.positions[key]]

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)


def row_factory(cursor):
    if cursor.description is None:
        return tuple
    names = [column.name for column in cursor.description]
    return lambda values: Row(names, values)


def postgres_sql(statement):
    # Preserve question marks and percent signs inside quoted literals.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", statement)
    return ''.join(part.replace('%', '%%') if i % 2 else
                   part.replace('%', '%%').replace('?', '%s')
                   for i, part in enumerate(parts))


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self.cursor, self.lastrowid = cursor, lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)


class Postgres:
    def __init__(self, dsn):
        self.dsn = dsn
        self.connection = None
        self.transactions = []
        self.checked_schema = False
        self._connect()

    def _connect(self):
        import psycopg
        # Ignore caller attempts to disable TLS. Use the Supavisor session
        # pooler: transaction pooling does not preserve these session settings.
        # Disable named prepared statements for pooler compatibility.
        try:
            self.connection = psycopg.connect(
                self.dsn, sslmode='require', connect_timeout=10,
                autocommit=True, prepare_threshold=None, row_factory=row_factory,
                application_name='dodge-server-1.8')
            self.connection.execute("SET search_path TO dodge_private, pg_catalog")
            self.connection.execute("SET statement_timeout TO '15s'")
            self.connection.execute("SET lock_timeout TO '10s'")
        except psycopg.Error as exc:
            logging.error('database_connect error=%s', type(exc).__name__)
            raise RuntimeError('database_unavailable') from None

    def _ready(self):
        if self.connection.closed:
            if self.transactions:
                raise RuntimeError('database_transaction_lost')
            self._connect()

    def execute(self, statement, parameters=()):
        import psycopg
        self._ready()
        if re.match(r'\s*(CREATE|ALTER|DROP)\b', statement, re.I):
            self._check_schema()
            # Runtime SQLite initialization is redundant on a migrated database.
            return Cursor(self.connection.execute('SELECT 1 WHERE false'))
        statement = postgres_sql(statement)
        returning_id = bool(re.match(r'\s*INSERT INTO chat_messages\b', statement, re.I))
        if returning_id:
            statement = statement.rstrip().rstrip(';') + ' RETURNING id'
        try:
            cursor = self.connection.execute(statement, parameters)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError('database_integrity_error') from exc
        identifier = cursor.fetchone()[0] if returning_id else None
        return Cursor(cursor, identifier)

    def _check_schema(self):
        if not self.checked_schema:
            row = self.connection.execute(
                'SELECT version FROM dodge_private.schema_version WHERE singleton=1').fetchone()
            if not row or row[0] != SCHEMA_VERSION:
                raise RuntimeError('database_migration_required')
            self.checked_schema = True

    def executescript(self, statement):
        self._check_schema()

    def __enter__(self):
        self._ready()
        savepoint = 'dodge_nested_' + str(len(self.transactions)) if self.transactions else None
        self.connection.execute('SAVEPOINT ' + savepoint if savepoint else 'BEGIN')
        self.transactions.append(savepoint)
        # Serialize read/modify/write operations across processes. A lock is
        # released on commit/rollback, including a dropped connection.
        try:
            self.connection.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
        except Exception:
            self.__exit__(*__import__('sys').exc_info())
            raise
        return self

    def __exit__(self, kind, value, traceback):
        savepoint = self.transactions.pop()
        try:
            if savepoint:
                if kind:
                    self.connection.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
                self.connection.execute('RELEASE SAVEPOINT ' + savepoint)
            else:
                self.connection.execute('ROLLBACK' if kind else 'COMMIT')
        except Exception as exc:
            logging.error('database_transaction error=%s', type(exc).__name__)
            self.connection.close()
            if kind is None:
                raise
        return False

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def connect(path, timeout=10, *, persistent=False):
    dsn = os.environ.get('DATABASE_URL', '').strip()
    if dsn:
        if not dsn.startswith(('postgres://', 'postgresql://')):
            raise RuntimeError('invalid_database_url')
        return Postgres(dsn)
    if os.environ.get('RENDER') == 'true' and os.environ.get('DODGE_ALLOW_EPHEMERAL') != '1':
        raise RuntimeError('persistent_database_required')
    connection = sqlite3.connect(path, timeout=timeout, check_same_thread=not persistent)
    connection.row_factory = sqlite3.Row
    return connection
