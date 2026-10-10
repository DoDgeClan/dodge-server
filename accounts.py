"""Password accounts over existing guest IDs; public profile IDs are not credentials.

All mutations use Social's transaction/lock. Password work runs outside SQL locks.
QR approval requires an already authenticated owner and a separate polling secret.
"""
import hashlib
import hmac
import secrets
import time
from social import SocialError

ROUNDS = 600000


def password_hash(password, salt):
    if not isinstance(password, str) or not 6 <= len(password) <= 128:
        raise SocialError('invalid_password')
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ROUNDS).hex()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Accounts:
    def __init__(self, social, clock=time.time):
        self.social, self.clock = social, clock
        with social.lock, social.db:
            social.db.executescript('''
                CREATE TABLE IF NOT EXISTS account_credentials (
                    uid TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_rate (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_qr (
                    id TEXT PRIMARY KEY, poll_hash TEXT NOT NULL, uid TEXT,
                    expires REAL NOT NULL);
            ''')
        self.dummy_salt = secrets.token_hex(16)
        self.dummy_hash = password_hash(secrets.token_urlsafe(24), self.dummy_salt)

    def rate(self, key, limit=10, seconds=600):
        now = self.clock()
        with self.social.lock, self.social.db:
            self.social.db.execute('DELETE FROM account_rate WHERE expires<=?', (now,))
            row = self.social.db.execute('SELECT count FROM account_rate WHERE key=?', (key,)).fetchone()
            if row and row[0] >= limit:
                raise SocialError('auth_rate_limit')
            self.social.db.execute('''INSERT INTO account_rate VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET count=account_rate.count+1''', (key, 1, now+seconds))

    def issue(self, uid):
        token = secrets.token_urlsafe(32)
        self.social.db.execute('DELETE FROM account_sessions WHERE expires<=?', (self.clock(),))
        self.social.db.execute('INSERT INTO account_sessions VALUES (?,?,?)',
                               (digest(token), uid, self.clock()+90*86400))
        profile = self.social.profile(uid)
        return dict(ok=True, id=uid, name=profile['name'], token=token, password_account=True)

    def create(self, data, existing_token, client):
        self.rate('create:'+digest(client), 5, 3600)
        if not isinstance(data.get('name'),str):raise SocialError('invalid_name')
        name, key = self.social.name_key(data.get('name', ''))
        salt = secrets.token_hex(16)
        hashed = password_hash(data.get('password'), salt)
        with self.social.lock, self.social.db:
            uid = self.social.authenticate(existing_token) if existing_token else None
            if uid and self.social.db.execute('SELECT 1 FROM account_credentials WHERE uid=?', (uid,)).fetchone():
                raise SocialError('account_already_exists')
            if self.social.db.execute('SELECT 1 FROM guests WHERE name_key=? AND id<>?', (key, uid or '')).fetchone():
                raise SocialError('name_taken')
            if not uid:
                uid = secrets.token_hex(16)
                # No usable anonymous bearer token is generated for a new account.
                self.social.db.execute('INSERT INTO guests VALUES (?,?,?,?,1)',
                    (uid, digest(secrets.token_urlsafe(48)), name, key))
            else:
                self.social.db.execute('UPDATE guests SET name=?,name_key=?,name_locked=1 WHERE id=?', (name, key, uid))
            self.social.db.execute('INSERT INTO account_credentials VALUES (?,?,?)', (uid, salt, hashed))
            return self.issue(uid)

    def login(self, data, client):
        self.rate('login-ip:'+digest(client), 30)
        try:
            _, key = self.social.name_key(data.get('name', ''))
        except (SocialError, TypeError):
            key = ''
        self.rate('login-name:'+digest(key), 10)
        with self.social.lock:
            row = self.social.db.execute('''SELECT c.uid,c.salt,c.password_hash FROM account_credentials c
                JOIN guests g ON c.uid=g.id WHERE g.name_key=?''', (key,)).fetchone()
            original = tuple(row[key] for key in ('uid','salt','password_hash')) if row else None
        try:
            computed = password_hash(data.get('password'), original[1] if original else self.dummy_salt)
        except SocialError:
            # Perform equal-cost password work for malformed/short inputs as well.
            computed = password_hash('invalid-input', self.dummy_salt)
            original = None
        valid = hmac.compare_digest(computed, original[2] if original else self.dummy_hash)
        if not original or not valid:
            raise SocialError('invalid_credentials')
        with self.social.lock, self.social.db:
            current = self.social.db.execute('SELECT password_hash FROM account_credentials WHERE uid=?', (original[0],)).fetchone()
            if not current or not hmac.compare_digest(current[0], original[2]):
                raise SocialError('invalid_credentials')
            return self.issue(original[0])

    def qr_create(self, client):
        self.rate('qr:'+digest(client), 10)
        ident, poll = secrets.token_hex(24), secrets.token_urlsafe(32)
        with self.social.lock, self.social.db:
            self.social.db.execute('DELETE FROM account_qr WHERE expires<=?', (self.clock(),))
            self.social.db.execute('INSERT INTO account_qr VALUES (?,?,NULL,?)',
                (ident, digest(poll), self.clock()+120))
        return dict(ok=True, id=ident, poll_secret=poll, uri='dodge://login/'+ident, expires_in=120)

    def qr_approve(self, data, token):
        with self.social.lock, self.social.db:
            uid = self.social.authenticate(token)
            if not self.social.db.execute('SELECT 1 FROM account_credentials WHERE uid=?', (uid,)).fetchone():
                raise SocialError('password_account_required')
            ident = data.get('id')
            row = self.social.db.execute('SELECT uid,expires FROM account_qr WHERE id=?', (ident,)).fetchone()
            if not row or row[1] <= self.clock() or row[0]:
                raise SocialError('qr_expired')
            self.social.db.execute('UPDATE account_qr SET uid=? WHERE id=?', (uid, ident))
            return dict(ok=True, approved=True)

    def qr_poll(self, data):
        ident, secret = data.get('id'), data.get('poll_secret')
        if not isinstance(ident, str) or len(ident) != 48 or not isinstance(secret, str) or len(secret) > 128:
            raise SocialError('qr_expired')
        with self.social.lock, self.social.db:
            row = self.social.db.execute('SELECT poll_hash,uid,expires FROM account_qr WHERE id=?', (ident,)).fetchone()
            if not row or row[2] <= self.clock() or not hmac.compare_digest(row[0], digest(secret)):
                raise SocialError('qr_expired')
            if not row[1]:
                return dict(ok=True, pending=True)
            self.social.db.execute('DELETE FROM account_qr WHERE id=?', (ident,))
            return self.issue(row[1])

    def dispatch(self, action, data, token='', client=''):
        if not isinstance(data, dict):
            raise SocialError('invalid_request')
        if action == 'create': return self.create(data, token, client)
        if action == 'login': return self.login(data, client)
        if action == 'qr_create': return self.qr_create(client)
        if action == 'qr_poll': return self.qr_poll(data)
        if action == 'qr_approve': return self.qr_approve(data, token)
        if action == 'status':
            with self.social.lock:
                uid = self.social.authenticate(token)
                enabled = bool(self.social.db.execute('SELECT 1 FROM account_credentials WHERE uid=?', (uid,)).fetchone())
            return dict(ok=True, password_account=enabled, two_factor_available=False)
        # No toggle can falsely enable verification without a delivery provider.
        raise SocialError('unknown_action')
