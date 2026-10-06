"""Owner administration. No guest tokens, chat content or credentials leave this API."""
import hmac
import logging
import os
import sqlite3
import time
from social import SocialError


class AdminError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def authorize(headers):
    expected = os.environ.get('DODGE_ADMIN_KEY', '')
    if len(expected) < 32:
        raise AdminError('admin_not_configured', 503)
    header = headers.get('Authorization', '')
    supplied = header[7:] if header.startswith('Bearer ') else ''
    if len(supplied) > 512 or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise AdminError('admin_unauthorized', 401)


def number(value, maximum):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise AdminError('invalid_pagination')
    if not 0 <= result <= maximum:
        raise AdminError('invalid_pagination')
    return result


def player(social, row):
    uid = row['id']
    room_id, _ = social.room_for(uid)
    return {'id': uid, 'name': row['name'], 'name_locked': bool(row['name_locked']),
            'online': social.connected(uid), 'room_id': room_id}


def get(path, query, social, matches):
    # Lock order matches websocket_loop: social first, match hub second.
    with social.lock, social.db:
        social.cleanup()
        if path == '/v2/admin/status':
            total = social.db.execute('SELECT COUNT(*) AS total FROM guests').fetchone()['total']
            with matches.lock:
                active = sum(bool(r.get('started')) and not r.get('ended', False)
                             for r in matches.rooms.values())
            return {'ok': True, 'admin_api': 1, 'players': total,
                    'online': sum(social.connected(uid) for uid in social.online),
                    'rooms': len(social.rooms), 'matches': active, 'checked_at': int(time.time()),
                    'commit': os.environ.get('RENDER_GIT_COMMIT', '')}
        if path == '/v2/admin/players':
            limit = max(1, number(query.get('limit', ['25'])[0], 100))
            offset = number(query.get('offset', ['0'])[0], 1000000)
            search = query.get('search', [''])[0]
            if len(search) > 64:
                raise AdminError('invalid_search')
            # Literal substring: escape SQL LIKE wildcards, retain parameter binding.
            escaped = search.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            pattern = '%' + escaped + '%'
            where = " WHERE name_key LIKE ? ESCAPE '\\' OR id=?"
            total = social.db.execute('SELECT COUNT(*) AS total FROM guests' + where,
                                      (pattern, search)).fetchone()['total']
            rows = social.db.execute('SELECT id,name,name_locked FROM guests' + where +
                                     ' ORDER BY name_key,id LIMIT ? OFFSET ?',
                                     (pattern, search, limit, offset)).fetchall()
            return {'ok': True, 'players': [player(social, row) for row in rows],
                    'total': total, 'offset': offset, 'limit': limit,
                    'next_offset': offset + limit if offset + limit < total else None}
        if path == '/v2/admin/rooms':
            result = []
            with matches.lock:
                for rid, room in list(social.rooms.items())[:200]:
                    match = matches.rooms.get(rid)
                    members = []
                    for uid in room['members']:
                        row = social.db.execute('SELECT id,name,name_locked FROM guests WHERE id=?',
                                                (uid,)).fetchone()
                        if row:
                            members.append(player(social, row))
                    result.append({'id': rid, 'host': room['host'], 'started': bool(room.get('started')),
                                   'members': members, 'match': None if not match else {
                                       'started': bool(match.get('started')), 'ended': bool(match.get('ended')),
                                       'victory': bool(match.get('victory'))}})
            return {'ok': True, 'rooms': result, 'total': len(social.rooms),
                    'truncated': len(social.rooms) > 200}
    raise AdminError('admin_endpoint_not_found', 404)


def action(data, social, matches):
    if not isinstance(data, dict) or data.get('action') not in ('rename_player', 'kick_player', 'close_room'):
        raise AdminError('unknown_admin_action')
    operation = data['action']
    target = data.get('id')
    if not isinstance(target, str) or not target or len(target) > 64:
        raise AdminError('invalid_target')
    with social.lock, social.db:
        if operation == 'rename_player':
            name = data.get('name')
            if not isinstance(name, str):
                raise AdminError('invalid_name')
            try:
                name, key = social.name_key(name)
            except SocialError:
                raise AdminError('invalid_name') from None
            if not social.db.execute('SELECT id FROM guests WHERE id=?', (target,)).fetchone():
                raise AdminError('player_not_found', 404)
            try:
                social.db.execute('UPDATE guests SET name=?,name_key=?,name_locked=1 WHERE id=?',
                                  (name, key, target))
            except sqlite3.IntegrityError:
                raise AdminError('name_taken', 409) from None
        elif operation == 'kick_player':
            rid, room = social.room_for(target)
            if not room:
                raise AdminError('player_not_in_room', 409)
            # Make the websocket membership check fail; other members retain their sessions.
            room['members'].remove(target)
            room.get('ready', {}).pop(target, None)
            social.invites = {k: v for k, v in social.invites.items()
                             if v['from'] != target and v['to'] != target}
            if room['members']:
                if room['host'] == target:
                    room['host'] = room['members'][0]
            else:
                social.rooms.pop(rid, None)
            with matches.lock:
                match = matches.rooms.get(rid)
                if match:
                    match['players'].pop(target, None)
                    match.get('connections', {}).pop(target, None)
                    if target in match.get('expected', []):
                        match['expected'].remove(target)
                    if match.get('host_uid') == target and room['members']:
                        match['host_uid'] = room['host']
                    if not room['members']:
                        match['ended'] = True
                        match['reason'] = 'admin_closed'
        else:
            room = social.rooms.get(target)
            if not room:
                raise AdminError('room_not_found', 404)
            social.rooms.pop(target)
            social.invites = {k: v for k, v in social.invites.items() if v['room'] != target}
            with matches.lock:
                match = matches.rooms.get(target)
                if match:
                    match['ended'] = True
                    match['reason'] = 'admin_closed'
    logging.info('admin_action action=%s target=%s', operation, target)
    return {'ok': True, 'action': operation, 'id': target}


def verify_startup(port):
    """Read-only loopback checks of the running API and its persistent database."""
    import http.client
    import json
    key = os.environ.get('DODGE_ADMIN_KEY', '')
    if len(key) < 32:
        print('admin_startup_check disabled=true', flush=True)
        return
    try:
        checks = [('/v2/admin/status', False, 401), ('/v2/admin/status', True, 200),
                  ('/v2/admin/players?limit=1', True, 200), ('/v2/admin/rooms', True, 200)]
        for path, authenticated, expected in checks:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=25)
            try:
                conn.request('GET', path, headers={'Authorization': 'Bearer ' + key} if authenticated else {})
                response = conn.getresponse()
                data = json.loads(response.read())
                if response.status != expected or (authenticated and data.get('ok') is not True):
                    raise RuntimeError('unexpected_api_response')
            finally:
                conn.close()
        print('admin_startup_check ok=true unauthorized=401 status=200 players=200 rooms=200', flush=True)
    except Exception as exc:
        print('admin_startup_check ok=false error=' + type(exc).__name__, flush=True)
