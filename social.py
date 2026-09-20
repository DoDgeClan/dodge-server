"""Authenticated guest lobby service. A single process owns all transient rooms.

Persistent identities/friendships use SQLite. Room expiry is server authoritative.
This module does not yet simulate a multiplayer match.
"""
import hashlib
import secrets
import sqlite3
import threading
import time
import unicodedata


class SocialError(Exception):
    pass


class Social:
    def __init__(self, path, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS guests (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, name_key TEXT UNIQUE NOT NULL,
                name_locked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS friends (
                sender TEXT NOT NULL, recipient TEXT NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(sender, recipient));
        ''')
        self.online = {}
        self.rooms = {}
        self.invites = {}
        self.last_invite = {}

    @staticmethod
    def name_key(name):
        name = unicodedata.normalize('NFKC', name).strip()
        if not 3 <= len(name) <= 16 or not all(c.isalnum() or c == '_' for c in name):
            raise SocialError('invalid_name')
        return name, name.casefold()

    def register(self):
        with self.lock, self.db:
            token = secrets.token_urlsafe(32)
            uid = secrets.token_hex(16)
            while True:
                name = 'Pixel' + secrets.token_hex(5)
                if not self.db.execute('SELECT 1 FROM guests WHERE name_key=?', (name.casefold(),)).fetchone():
                    break
            self.db.execute('INSERT INTO guests VALUES (?,?,?,?,0)',
                            (uid, hashlib.sha256(token.encode()).hexdigest(), name, name.casefold()))
            self.online[uid] = self.clock()
            return {'id': uid, 'name': name, 'token': token}

    def authenticate(self, token):
        if not isinstance(token, str) or not token or len(token) > 256:
            raise SocialError('unauthorized')
        row = self.db.execute('SELECT id FROM guests WHERE token_hash=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:
            raise SocialError('unauthorized')
        return row['id']

    def profile(self, uid):
        row = self.db.execute('SELECT id,name,name_locked FROM guests WHERE id=?', (uid,)).fetchone()
        return dict(row) if row else None

    def connected(self, uid):
        return self.clock() - self.online.get(uid, float('-inf')) < 15

    def cleanup(self):
        now = self.clock()
        self.invites = {k: v for k, v in self.invites.items() if v['expires'] > now}
        for room_id, room in list(self.rooms.items()):
            room['members'] = [u for u in room['members'] if self.connected(u)]
            if not room['members']:
                del self.rooms[room_id]
            elif room['host'] not in room['members']:
                room['host'] = room['members'][0]
        self.online = {u: t for u, t in self.online.items() if now-t < 60}
        self.last_invite = {u: t for u, t in self.last_invite.items() if now-t < 60}

    def room_for(self, uid):
        for rid, room in self.rooms.items():
            if uid in room['members']:
                return rid, room
        return None, None

    def friend_ids(self, uid):
        rows = self.db.execute('SELECT sender,recipient FROM friends WHERE accepted=1 AND (sender=? OR recipient=?)', (uid, uid))
        return [r['recipient'] if r['sender'] == uid else r['sender'] for r in rows]

    def dispatch(self, token, action, data=None):
        data = data or {}
        with self.lock, self.db:
            uid = self.authenticate(token)
            self.cleanup()
            self.online[uid] = self.clock()
            if action == 'name':
                name, key = self.name_key(data.get('name', ''))
                if self.profile(uid)['name_locked']:
                    raise SocialError('name_locked')
                try:
                    self.db.execute('UPDATE guests SET name=?,name_key=?,name_locked=1 WHERE id=?', (name, key, uid))
                except sqlite3.IntegrityError:
                    raise SocialError('name_taken')
            elif action == 'friend_add':
                _, key = self.name_key(data.get('name', ''))
                other = self.db.execute('SELECT id FROM guests WHERE name_key=?', (key,)).fetchone()
                if not other or other['id'] == uid:
                    raise SocialError('player_not_found')
                other = other['id']
                if len(self.friend_ids(uid)) >= 100:
                    raise SocialError('friends_full')
                existing = self.db.execute('SELECT 1 FROM friends WHERE sender=? AND recipient=?', (other, uid)).fetchone()
                if existing:
                    self.db.execute('UPDATE friends SET accepted=1 WHERE sender=? AND recipient=?', (other, uid))
                else:
                    self.db.execute('INSERT OR IGNORE INTO friends VALUES (?,?,0)', (uid, other))
            elif action == 'friend_accept':
                self.db.execute('UPDATE friends SET accepted=1 WHERE sender=? AND recipient=?', (data.get('id'), uid))
            elif action == 'friend_remove':
                other = data.get('id')
                self.db.execute('DELETE FROM friends WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)', (uid, other, other, uid))
            elif action == 'create':
                rid, room = self.room_for(uid)
                if room is None:
                    self.rooms[secrets.token_hex(12)] = {'host': uid, 'members': [uid], 'started': False}
            elif action == 'start_match':
                rid, room = self.room_for(uid)
                if not room:
                    raise SocialError('room_required')
                if room['host'] != uid:
                    raise SocialError('host_only')
                room['started'] = True
            elif action == 'invite':
                rid, room = self.room_for(uid)
                other = data.get('id')
                if not room or room['host'] != uid:
                    raise SocialError('host_only')
                if len(room['members']) >= 3:
                    raise SocialError('room_full')
                if other not in self.friend_ids(uid) or not self.connected(other):
                    raise SocialError('friend_offline')
                if self.room_for(other)[1] is not None:
                    raise SocialError('player_busy')
                if self.clock() - self.last_invite.get(uid, float('-inf')) < 10:
                    raise SocialError('invite_cooldown')
                if any(i['to'] == other for i in self.invites.values()):
                    raise SocialError('player_busy')
                self.last_invite[uid] = self.clock()
                self.invites[secrets.token_hex(12)] = {'from': uid, 'to': other, 'room': rid, 'expires': self.clock()+10}
            elif action in ('accept', 'decline'):
                iid = data.get('id')
                invite = self.invites.get(iid)
                if not invite or invite['to'] != uid:
                    raise SocialError('invite_expired')
                del self.invites[iid]
                if action == 'accept':
                    room = self.rooms.get(invite['room'])
                    if not room or invite['from'] not in room['members']:
                        raise SocialError('invite_expired')
                    if len(room['members']) >= 3:
                        raise SocialError('room_full')
                    if self.room_for(uid)[1] is not None:
                        raise SocialError('player_busy')
                    room['members'].append(uid)
            elif action == 'leave':
                _, room = self.room_for(uid)
                if room:
                    room['members'].remove(uid)
                self.cleanup()
            elif action != 'poll':
                raise SocialError('unknown_action')
            rid, room = self.room_for(uid)
            return {'ok': True, 'profile': self.profile(uid),
                    'friends': [dict(self.profile(u), online=self.connected(u)) for u in self.friend_ids(uid)],
                    'requests': [self.profile(r['sender']) for r in self.db.execute('SELECT sender FROM friends WHERE recipient=? AND accepted=0', (uid,))],
                    'room': None if room is None else {'id': rid, 'host': room['host'], 'started': bool(room.get('started')), 'members': [self.profile(u) for u in room['members']]},
                    'invites': [dict(id=k, name=self.profile(v['from'])['name'], remaining=max(0, v['expires']-self.clock())) for k,v in self.invites.items() if v['to'] == uid]}

