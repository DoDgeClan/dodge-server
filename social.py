"""Authenticated guest lobby service. A single process owns all transient rooms.

Persistent identities/friendships use SQLite. Room expiry is server authoritative.
This module does not yet simulate a multiplayer match.
"""
from database import connect, Postgres
import hashlib
import json
import math
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
        # SQL/lobby operations own self.lock. Match frames must never wait for SQL.
        # Lock order: social.lock -> realtime_lock -> MatchHub.lock.
        self.realtime_lock = threading.RLock()
        self.realtime_membership = {}
        self.realtime_online = {}
        self.db = connect(path, persistent=True)
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
        from chat import Chat
        self.chat=Chat(self)

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
        if not row:return None
        prefs=self.chat.prefs(uid)
        return dict(row,avatar=prefs['avatar'],status=prefs['status'])

    def connected(self, uid):
        with self.realtime_lock:
            seen = max(self.online.get(uid, float('-inf')),
                       self.realtime_online.get(uid, float('-inf')))
        return self.clock() - seen < 30

    def publish_realtime_membership(self):
        """Caller owns social.lock; publish immutable authorization data without SQL."""
        membership = {}
        for rid, room in self.rooms.items():
            members = tuple(room['members'])
            access = (rid, room['host'], bool(room.get('started')), members)
            for uid in members:
                membership[uid] = access
        with self.realtime_lock:
            self.realtime_membership = membership
            self.realtime_online = {u:t for u,t in self.realtime_online.items()
                                    if u in membership}

    def realtime_access(self, uid):
        # WSS owns realtime_lock across validation plus authoritative mutation.
        return self.realtime_membership.get(uid)

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
        self.publish_realtime_membership()

    def room_for(self, uid):
        for rid, room in self.rooms.items():
            if uid in room['members']:
                return rid, room
        return None, None

    def friend_ids(self, uid):
        rows = self.db.execute('SELECT sender,recipient FROM friends WHERE accepted=1 AND (sender=? OR recipient=?)', (uid, uid))
        return [r['recipient'] if r['sender'] == uid else r['sender'] for r in rows]

    def dispatch_requests(self,uid):
        return [self.profile(r['sender']) for r in self.db.execute('SELECT sender FROM friends WHERE recipient=? AND accepted=0',(uid,))]

    def allow_invite(self, uid, other):
        if not self.profile(other): raise SocialError('player_not_found')
        if self.chat.blocked(uid,other): raise SocialError('blocked')
        policy=self.chat.prefs(other)['invites']
        if policy=='nobody' or (policy=='friends' and other not in self.friend_ids(uid)):
            raise SocialError('invites_disabled')

    def dispatch_by_uid_invite(self,uid,other):
        self.allow_invite(uid,other)
        rid,room=self.room_for(uid)
        if not room or room['host']!=uid or room.get('started') or len(room['members'])>=3:raise SocialError('room_required')
        if self.clock()-self.last_invite.get(uid,float('-inf'))<10:raise SocialError('invite_cooldown')
        self.last_invite[uid]=self.clock();self.invites[secrets.token_hex(12)]={'from':uid,'to':other,'room':rid,'expires':self.clock()+10}

    def dispatch(self, token, action, data=None):
        data = data or {}
        with self.lock, self.db:
            uid = self.authenticate(token)
            self.cleanup()
            self.online[uid] = self.clock()
            if action.startswith('chat_'):
                return dict(ok=True,**self.chat.dispatch(uid,action,data))
            if action == 'profile_avatar':
                if set(data)!={'media'}:raise SocialError('invalid_avatar')
                media=data.get('media')
                if media is not None:
                    if not isinstance(media,str) or len(media)!=32:raise SocialError('invalid_avatar')
                    row=self.db.execute('SELECT owner,kind FROM chat_media WHERE id=?',(media,)).fetchone()
                    if not row or row['owner']!=uid or row['kind']!='photo':raise SocialError('invalid_avatar')
                prefs=self.chat.prefs(uid);prefs['avatar_media']=media
                self.db.execute('INSERT INTO chat_preferences VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data',(uid,json.dumps(prefs)))
                return {'ok':True,'avatar_media':media,'visibility':'friends'}
            elif action == 'profile_stats':
                allowed={'stats_runs':10000000,'stats_victories':10000000,'stats_enemies_defeated':1000000000,'stats_playtime_seconds':315360000,'level':10000}
                stats=data.get('stats')
                if set(data)!={'stats'} or not isinstance(stats,dict) or not stats or not set(stats)<=set(allowed):raise SocialError('invalid_stats')
                for key,value in stats.items():
                    if type(value) not in ((int,float) if key=='stats_playtime_seconds' else (int,)) or not (1 if key=='level' else 0)<=value<=allowed[key] or not math.isfinite(value):raise SocialError('invalid_stats')
                prefs=self.chat.prefs(uid)
                prefs['profile_stats']=dict(prefs.get('profile_stats',{}),**stats)
                prefs['stats_updated_at']=self.chat.clock()
                self.db.execute('INSERT INTO chat_preferences VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data',(uid,json.dumps(prefs)))
                return {'ok':True,'stats':prefs['profile_stats'],'stats_source':'device','stats_updated_at':prefs['stats_updated_at']}
            elif action == 'my_ranking':
                if data:raise SocialError('invalid_request')
                from online_server_render import personal_ranking
                return {'ok':True,'ranking':personal_ranking(uid,connection=self.db if isinstance(self.db,Postgres) else None)}
            elif action == 'profile_lookup':
                other=data.get('id')
                if not other:
                    _, key=self.name_key(data.get('name',''))
                    row=self.db.execute('SELECT id FROM guests WHERE name_key=?',(key,)).fetchone()
                    other=row[0] if row else None
                profile=self.profile(other)
                if not profile or self.chat.blocked(uid,other):raise SocialError('player_not_found')
                prefs=self.chat.prefs(other)
                result=dict(profile,online=self.connected(other) and prefs['online'])
                if other==uid or other in self.friend_ids(uid):
                    result.update(stats=prefs.get('profile_stats',{}),stats_source='device',stats_updated_at=prefs.get('stats_updated_at'),avatar_media=prefs.get('avatar_media'))
                return {'ok':True,'profile':result}
            elif action == 'recent_players':
                peers={}
                for row in self.db.execute("SELECT payload,created FROM chat_events WHERE uid=? AND kind='recent_match' ORDER BY created DESC LIMIT 100",(uid,)):
                    for other in json.loads(row[0])['players']:
                        if other!=uid and other not in peers and not self.chat.blocked(uid,other):
                            peers[other]=dict(self.profile(other),played_at=row[1])
                return {'ok':True,'players':list(peers.values())[:50]}
            elif action in ('name','name_rename'):
                name, key = self.name_key(data.get('name', ''))
                if action=='name' and self.profile(uid)['name_locked']:
                    raise SocialError('name_locked')
                if action=='name_rename' and self.db.execute("SELECT 1 FROM chat_events WHERE uid=? AND kind='name_rename' AND created>?",(uid,self.chat.clock()-86400)).fetchone():raise SocialError('rename_cooldown')
                if self.db.execute('SELECT 1 FROM guests WHERE name_key=? AND id<>?', (key, uid)).fetchone():
                    raise SocialError('name_taken')
                try:
                    self.db.execute('UPDATE guests SET name=?,name_key=?,name_locked=1 WHERE id=?', (name, key, uid))
                except sqlite3.IntegrityError:
                    raise SocialError('name_taken')
                if action=='name_rename':self.chat.event('rename:'+secrets.token_hex(12),uid,'name_rename',{'name':name},notify=False)
            elif action == 'friend_add':
                _, key = self.name_key(data.get('name', ''))
                other = self.db.execute('SELECT id FROM guests WHERE name_key=?', (key,)).fetchone()
                if not other or other['id'] == uid:
                    raise SocialError('player_not_found')
                other = other['id']
                if not self.chat.prefs(other)['requests']:raise SocialError('requests_disabled')
                if self.chat.blocked(uid,other):raise SocialError('blocked')
                if self.db.execute("SELECT COUNT(*) FROM chat_events WHERE kind='friend_notifications' AND payload LIKE ? AND created>?",('%'+uid+'%',self.chat.clock()-3600)).fetchone()[0]>=30:raise SocialError('request_rate_limit')
                if len(self.friend_ids(uid)) >= 100:
                    raise SocialError('friends_full')
                existing = self.db.execute('SELECT 1 FROM friends WHERE sender=? AND recipient=?', (other, uid)).fetchone()
                if existing:
                    self.db.execute('UPDATE friends SET accepted=1 WHERE sender=? AND recipient=?', (other, uid))
                else:
                    inserted=self.db.execute('INSERT INTO friends VALUES (?,?,0) ON CONFLICT DO NOTHING', (uid, other))
                    if inserted.rowcount:self.chat.event('friend:'+secrets.token_hex(12),other,'friend_notifications',{'sender':uid})
            elif action == 'friend_accept':
                if self.chat.blocked(uid,data.get('id')):raise SocialError('blocked')
                self.db.execute('UPDATE friends SET accepted=1 WHERE sender=? AND recipient=?', (data.get('id'), uid))
            elif action == 'friend_decline':
                self.db.execute('DELETE FROM friends WHERE sender=? AND recipient=? AND accepted=0',(data.get('id'),uid))
            elif action == 'friend_remove':
                other = data.get('id')
                self.db.execute('DELETE FROM friends WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)', (uid, other, other, uid))
            elif action == 'create':
                rid, room = self.room_for(uid)
                if room is None:
                    self.rooms[secrets.token_hex(12)] = {'host': uid, 'members': [uid], 'started': False, 'ready': {uid: False}}
            elif action == 'join':
                code=str(data.get('code','')).strip()
                room=self.rooms.get(code)
                if room is None:raise SocialError('room_not_found')
                current_id,current=self.room_for(uid)
                if current_id==code:pass
                elif current is not None:raise SocialError('player_busy')
                elif room.get('started'):raise SocialError('room_started')
                elif len(room['members'])>=3:raise SocialError('room_full')
                else:
                    room['members'].append(uid);room.setdefault('ready',{})[uid]=False
            elif action == 'ready':
                _,room=self.room_for(uid)
                if not room:raise SocialError('room_required')
                if room.get('started'):raise SocialError('room_started')
                if not isinstance(data.get('ready'),bool):raise SocialError('invalid_request')
                room.setdefault('ready',{})[uid]=data['ready'];room['ready_required']=True
            elif action == 'start_match':
                rid, room = self.room_for(uid)
                if not room:
                    raise SocialError('room_required')
                if room['host'] != uid:
                    raise SocialError('host_only')
                if len(room['members']) < 2:
                    raise SocialError('need_teammate')
                # Older protocol-3 clients have no readiness button. Enforce
                # readiness only after a player explicitly opts into that flow.
                if room.get('ready_required') and not all(room['ready'].get(u,False) for u in room['members']):
                    raise SocialError('team_not_ready')
                if not room.get('started'):
                    event=secrets.token_hex(12)
                    for member in room['members']:
                        self.chat.event('recent:'+event+':'+member,member,'recent_match',{'players':list(room['members'])},notify=False)
                room['started'] = True
            elif action == 'invite':
                rid, room = self.room_for(uid)
                other = data.get('id')
                self.allow_invite(uid,other)
                if not room or room['host'] != uid:
                    raise SocialError('host_only')
                if room.get('started'):raise SocialError('room_started')
                if len(room['members']) >= 3:
                    raise SocialError('room_full')
                if not self.connected(other):
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
                    if room.get('started'):raise SocialError('room_started')
                    if len(room['members']) >= 3:
                        raise SocialError('room_full')
                    if self.room_for(uid)[1] is not None:
                        raise SocialError('player_busy')
                    room['members'].append(uid);room.setdefault('ready',{})[uid]=False
            elif action == 'leave':
                _, room = self.room_for(uid)
                if room:
                    room['members'].remove(uid);room.get('ready',{}).pop(uid,None)
                self.cleanup()
            elif action != 'poll':
                raise SocialError('unknown_action')
            self.publish_realtime_membership()
            rid, room = self.room_for(uid)
            return {'ok': True, 'profile': self.profile(uid),
                    'friends': [dict(self.profile(u), online=self.connected(u) and self.chat.prefs(u)['online']) for u in self.friend_ids(uid)],
                    'requests': [self.profile(r['sender']) for r in self.db.execute('SELECT sender FROM friends WHERE recipient=? AND accepted=0', (uid,))],
                    'room': None if room is None else {'id': rid, 'host': room['host'], 'started': bool(room.get('started')), 'code':rid,'ready':{u:room.get('ready',{}).get(u,False) for u in room['members']},'members': [self.profile(u) for u in room['members']]},
                    'invites': [dict(id=k, name=self.profile(v['from'])['name'], remaining=max(0, v['expires']-self.clock())) for k,v in self.invites.items() if v['to'] == uid]}
