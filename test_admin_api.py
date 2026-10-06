import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from social import Social
from multiplayer_ws import MatchHub
import online_server_render as server


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATABASE_URL': '', 'RENDER': '',
                                          'DODGE_ADMIN_KEY': 'test-key-' + 'x'*48})
        self.env.start()
        self.social = Social(self.temp.name + '/social.db')
        self.matches = MatchHub()
        self.a, self.b = self.social.register(), self.social.register()
        self.patches = [patch.object(server, 'SOCIAL', self.social),
                        patch.object(server, 'MATCHES', self.matches)]
        for p in self.patches:
            p.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        for p in reversed(self.patches):
            p.stop()
        self.social.db.close(); self.env.stop(); self.temp.cleanup()

    def request(self, path, data=None, key=True):
        headers = {}
        if key:
            headers['Authorization'] = 'Bearer ' + (os.environ['DODGE_ADMIN_KEY'] if key is True else key)
        if data is not None:
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        conn.request('POST' if data is not None else 'GET', path,
                     json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse(); result = response.status, dict(response.headers), json.loads(response.read())
        conn.close()
        return result

    def make_room(self, started=False):
        self.social.dispatch(self.a['token'], 'create')
        rid, room = self.social.room_for(self.a['id'])
        self.social.dispatch(self.b['token'], 'join', {'code': rid})
        if started:
            self.social.dispatch(self.a['token'], 'start_match')
            self.matches.join(rid, self.a['id'], host_uid=self.a['id'], started=True,
                              expected_members=room['members'])
            self.matches.join(rid, self.b['id'], host_uid=self.a['id'], started=True,
                              expected_members=room['members'])
        return rid

    def test_admin_key_required_and_no_guest_token_privilege(self):
        for key in (False, 'wrong', self.a['token']):
            status, headers, body = self.request('/v2/admin/players', key=key)
            self.assertEqual(status, 401); self.assertNotIn('players', body)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request('/v2/health', key=False)[0], 200)
        with patch.dict(os.environ, {'DODGE_ADMIN_KEY': ''}):
            self.assertEqual(self.request('/v2/admin/status', key=False)[0], 503)

    def test_players_pagination_search_and_no_credentials(self):
        status, headers, data = self.request('/v2/admin/players?limit=1')
        self.assertEqual(status, 200); self.assertEqual(data['total'], 2)
        self.assertEqual(len(data['players']), 1); self.assertEqual(data['next_offset'], 1)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(set(data['players'][0]), {'id', 'name', 'name_locked', 'online', 'room_id'})
        self.assertEqual(self.request('/v2/admin/players?search=%25')[2]['total'], 0)
        self.assertEqual(self.request('/v2/admin/players?limit=999')[0], 400)
        self.assertEqual(self.request('/v2/admin/players?offset=-1')[0], 400)

    def test_rename_persists_without_rotating_guest_token_and_duplicate_rejected(self):
        self.assertEqual(self.request('/v2/admin/action', {'action': 'rename_player', 'id': self.a['id'], 'name': 'Creator_New'})[0], 200)
        self.assertEqual(self.social.authenticate(self.a['token']), self.a['id'])
        self.assertEqual(self.social.profile(self.a['id'])['name'], 'Creator_New')
        self.assertEqual(self.request('/v2/admin/action', {'action': 'rename_player', 'id': self.b['id'], 'name': 'Creator_New'})[0], 409)
        self.assertEqual(self.request('/v2/admin/action', {'action': 'rename_player', 'id': self.a['id'], 'name': 'a'})[0], 400)

    def test_rooms_and_kick_host_preserve_other_player_and_transfer_host(self):
        rid = self.make_room(started=True)
        rooms = self.request('/v2/admin/rooms')[2]['rooms']
        self.assertEqual(len(rooms[0]['members']), 2)
        self.assertTrue(rooms[0]['match']['started'])
        self.assertEqual(self.request('/v2/admin/action', {'action': 'kick_player', 'id': self.a['id']})[0], 200)
        self.assertIsNone(self.social.room_for(self.a['id'])[0])
        self.assertEqual(self.social.room_for(self.b['id'])[0], rid)
        self.assertEqual(self.social.rooms[rid]['host'], self.b['id'])
        self.assertNotIn(self.a['id'], self.matches.rooms[rid]['players'])
        self.assertIn(self.b['id'], self.matches.rooms[rid]['players'])
        self.assertEqual(self.matches.rooms[rid]['host_uid'], self.b['id'])

    def test_close_room_ends_match_and_invalidates_invites(self):
        rid = self.make_room(started=True)
        self.social.invites['invite'] = {'room': rid, 'from': self.a['id'], 'to': self.b['id'], 'expires': self.social.clock()+10}
        self.assertEqual(self.request('/v2/admin/action', {'action': 'close_room', 'id': rid})[0], 200)
        self.assertNotIn(rid, self.social.rooms)
        self.assertFalse(self.social.invites)
        self.assertTrue(self.matches.rooms[rid]['ended'])
        self.assertEqual(self.request('/v2/admin/action', {'action': 'close_room', 'id': rid})[0], 404)

    def test_unauthorized_mutation_and_unknown_action_cannot_change_state(self):
        rid = self.make_room()
        self.assertEqual(self.request('/v2/admin/action', {'action': 'close_room', 'id': rid}, key=False)[0], 401)
        self.assertIn(rid, self.social.rooms)
        self.assertEqual(self.request('/v2/admin/action', {'action': 'delete_all', 'id': rid})[0], 400)
        self.assertIn(rid, self.social.rooms)
        self.assertEqual(self.request('/v2/admin/action', [1, 2, 3])[0], 400)


if __name__ == '__main__':
    unittest.main()
