"""Exercise the real WSS loop under a blocked SQL lock and membership revocation."""
import json
import queue
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from social import Social
from multiplayer_ws import MatchHub, websocket_loop
from admin_api import action as admin_action

class RealtimeAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.social=Social(':memory:');self.hub=MatchHub()
        self.host=self.social.register();self.guest=self.social.register()
        self.call(self.host,'create');self.rid=self.social.room_for(self.host['id'])[0]
        self.call(self.guest,'join',code=self.rid);self.call(self.host,'start_match')
        self.messages=queue.Queue();self.snapshots=[];self.first=threading.Event()
    def tearDown(self):
        self.social.db.close()
    def call(self,user,action,**data):return self.social.dispatch(user['token'],action,data)
    def run_socket(self,operation):
        owner=self
        class Reader:
            def read(self,conn):
                try:return owner.messages.get(timeout=.02)
                except queue.Empty:return ''
        class Handler:
            path='/ws?token='+owner.host['token']+'&room='+owner.rid
            headers={'Sec-WebSocket-Key':'dGhlIHNhbXBsZSBub25jZQ=='}
            connection=SimpleNamespace(settimeout=lambda _:None)
            def send_response(self,*args):pass
            def send_header(self,*args):pass
            def end_headers(self):pass
        def write(conn,payload,*args):
            self.snapshots.append(json.loads(payload));self.first.set()
        with patch('multiplayer_ws.FrameReader',Reader),patch('multiplayer_ws.write_frame',write):
            worker=threading.Thread(target=websocket_loop,args=(Handler(),self.hub,self.social))
            worker.start()
            try:
                self.assertTrue(self.first.wait(1),'WSS did not produce initial snapshot')
                operation()
            finally:
                self.messages.put(None);worker.join(1)
                self.assertFalse(worker.is_alive(),'WSS shutdown blocked')
    def test_sql_lock_does_not_block_match_frames_or_decoy(self):
        def operation():
            with self.social.lock:
                before=len(self.snapshots)
                self.messages.put(json.dumps({'action':'decoy'}))
                deadline=time.monotonic()+.5
                while time.monotonic()<deadline and len(self.snapshots)<before+3:time.sleep(.01)
                self.assertGreaterEqual(len(self.snapshots),before+3)
                self.assertIn(self.host['id'],self.snapshots[-1]['decoys'])
        self.run_socket(operation)
    def test_leave_revokes_existing_wss_before_next_action(self):
        def operation():
            self.call(self.host,'leave')
            self.messages.put(json.dumps({'action':'decoy'}));time.sleep(.08)
            self.assertNotIn(self.host['id'],self.hub.rooms[self.rid]['players'])
            self.assertNotIn(self.host['id'],self.hub.rooms[self.rid]['decoys'])
        self.run_socket(operation)
    def test_admin_kick_revokes_existing_socket_and_keeps_guest_access(self):
        def operation():
            admin_action({'action':'kick_player','id':self.host['id']},self.social,self.hub)
            self.messages.put(json.dumps({'action':'decoy'}));time.sleep(.08)
            self.assertIsNone(self.social.realtime_access(self.host['id']))
            self.assertEqual(self.social.realtime_access(self.guest['id'])[1],self.guest['id'])
            self.assertNotIn(self.host['id'],self.hub.rooms[self.rid]['decoys'])
        self.run_socket(operation)
    def test_admin_close_revokes_all_members(self):
        admin_action({'action':'close_room','id':self.rid},self.social,self.hub)
        self.assertIsNone(self.social.realtime_access(self.host['id']))
        self.assertIsNone(self.social.realtime_access(self.guest['id']))
    def test_cleanup_revokes_expired_members_but_keeps_live_socket(self):
        with self.social.lock:
            self.social.online[self.host['id']]=self.social.clock()-40
            self.social.online[self.guest['id']]=self.social.clock()-40
            with self.social.realtime_lock:self.social.realtime_online[self.host['id']]=self.social.clock()
            self.social.cleanup()
        self.assertIsNotNone(self.social.realtime_access(self.host['id']))
        self.assertIsNone(self.social.realtime_access(self.guest['id']))
    def test_invalid_token_never_upgrades_or_joins_match(self):
        from social import SocialError
        handler=SimpleNamespace(path='/ws?token=invalid&room='+self.rid)
        with self.assertRaisesRegex(SocialError,'unauthorized'):
            websocket_loop(handler,self.hub,self.social)
        self.assertFalse(self.hub.rooms)

    def test_not_started_room_cannot_be_authorized_for_wss(self):
        self.call(self.host,'leave');self.call(self.host,'create')
        self.assertFalse(self.social.realtime_access(self.host['id'])[2])

if __name__=='__main__':unittest.main()
