"""Import an existing identity only after the fixed legacy server authenticates it.

Verified friend profiles reserve their real IDs/names until their own token is
authenticated. Reservations cannot log in: their token field is not a SHA256.
No caller-supplied profile or friendship is trusted. Legacy service is unchanged.
"""
import hashlib
import re
import requests
from social import SocialError

SOURCE = 'https://dodge-server-uf4f.onrender.com'
PENDING = 'legacy-pending:'


def fetch_profile(token):
    try:
        response = requests.post(SOURCE + '/v2/action',
            headers={'Authorization': 'Bearer ' + token},
            json={'action': 'poll', 'data': {}}, timeout=(5, 15),
            allow_redirects=False)
        if response.status_code == 401:
            raise SocialError('legacy_session_unavailable')
        if response.status_code != 200 or len(response.content) > 65536:
            raise SocialError('legacy_server_unavailable')
        return response.json()
    except (requests.RequestException, ValueError):
        raise SocialError('legacy_server_unavailable') from None


def profile(social, value):
    if not isinstance(value, dict) or not re.fullmatch('[a-f0-9]{32}', str(value.get('id', ''))):
        raise SocialError('legacy_invalid_response')
    try:
        name, key = social.name_key(value.get('name', ''))
    except (SocialError, TypeError):
        raise SocialError('legacy_invalid_response') from None
    locked = value.get('name_locked')
    if locked not in (0, 1, False, True):
        raise SocialError('legacy_invalid_response')
    return value['id'], name, key, int(locked)


def migrate(social, token, expected_id=None, fetch=fetch_profile):
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        raise SocialError('legacy_session_unavailable')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with social.lock:
        try:
            uid = social.authenticate(token)
        except SocialError:
            uid = None
        if uid:
            if expected_id and uid != expected_id:
                raise SocialError('legacy_identity_mismatch')
            return dict(ok=True, **social.profile(uid))
    # Do not hold the DB write lock while contacting the legacy service.
    snapshot = fetch(token)
    if not isinstance(snapshot, dict) or snapshot.get('ok') is not True:
        raise SocialError('legacy_invalid_response')
    owner = profile(social, snapshot.get('profile'))
    uid = owner[0]
    if expected_id and uid != expected_id:
        raise SocialError('legacy_identity_mismatch')
    groups = []
    for field in ('friends', 'requests'):
        values = snapshot.get(field)
        if not isinstance(values, list) or len(values) > 100:
            raise SocialError('legacy_invalid_response')
        groups.append([profile(social, item) for item in values])
    friends, requests_ = groups
    with social.lock, social.db:
        for item in [owner, *friends, *requests_]:
            identifier, name, key, locked = item
            if item is not owner and identifier == uid:
                raise SocialError('legacy_invalid_response')
            current = social.db.execute('SELECT * FROM guests WHERE id=?', (identifier,)).fetchone()
            collision = social.db.execute('SELECT id FROM guests WHERE name_key=? AND id<>?', (key, identifier)).fetchone()
            if collision:
                raise SocialError('legacy_name_conflict')
            if not current:
                social.db.execute('INSERT INTO guests VALUES(?,?,?,?,?)',
                    (identifier, digest if identifier == uid else PENDING + identifier, name, key, locked))
            elif identifier == uid:
                if current['token_hash'] not in (digest, PENDING + identifier):
                    raise SocialError('legacy_identity_conflict')
                if current['token_hash'].startswith(PENDING):
                    social.db.execute('UPDATE guests SET token_hash=?,name=?,name_key=?,name_locked=? WHERE id=?',
                        (digest,name,key,locked,identifier))
        for identifier, *_ in friends:
            reverse=social.db.execute('SELECT 1 FROM friends WHERE sender=? AND recipient=?',(identifier,uid)).fetchone()
            if reverse:
                social.db.execute('UPDATE friends SET accepted=1 WHERE sender=? AND recipient=?',(identifier,uid))
            else:
                social.db.execute('INSERT INTO friends VALUES(?,?,1) ON CONFLICT(sender,recipient) DO UPDATE SET accepted=1', (uid,identifier))
        for identifier, *_ in requests_:
            social.db.execute('INSERT INTO friends VALUES(?,?,0) ON CONFLICT DO NOTHING', (identifier,uid))
    return dict(ok=True, **social.profile(uid))
