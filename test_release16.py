"""Regressions against the same server sources deployed on Render."""
import unittest,time
from multiplayer_ws import MatchHub
from social import Social,SocialError

class Release16Tests(unittest.TestCase):
    def test_dead_player_cannot_attack_or_move_but_can_spectate(self):
        hub=MatchHub();hub.join('r','h',started=True);hub.join('r','g')
        p=hub.rooms['r']['players']['h'];p['hp']=0
        hub.rooms['r']['enemies']=[{'id':1,'x':.5,'y':.5,'hp':2,'max_hp':2,'type':'tank'}]
        hub.action('r','h',{'action':'attack'});hub.action('r','h',{'action':'input','x':.9,'y':.9})
        self.assertEqual(p['kills'],0);self.assertEqual(p['x'],.5)
        self.assertNotIn('attack_ready',p)
        state=hub.action('r','h',{'action':'spectate'})
        self.assertTrue(state['players']['h']['spectator'])

    def test_reconnect_preserves_hp_kills_and_cooldowns(self):
        hub=MatchHub();hub.join('r','h',started=True);hub.join('r','g')
        p=hub.rooms['r']['players']['g'];p.update(hp=1,kills=4,attack_ready=time.monotonic()+6)
        hub.leave('r','g');self.assertFalse(p['connected'])
        state=hub.join('r','g')
        self.assertEqual(state['players']['g']['hp'],1);self.assertEqual(state['players']['g']['kills'],4)
        self.assertGreater(state['players']['g']['attack_ready'],time.monotonic())

    def test_overlapping_socket_cleanup_cannot_remove_reconnected_player(self):
        hub=MatchHub();hub.join('r','h');hub.join('r','h');hub.leave('r','h')
        self.assertTrue(hub.snapshot('r')['players']['h']['connected'])
        hub.leave('r','h');self.assertFalse(hub.snapshot('r')['players']['h']['connected'])

    def test_join_readiness_three_member_limit_and_leave(self):
        social=Social(':memory:');users=[social.register() for _ in range(4)]
        def action(i,name,**data):return social.dispatch(users[i]['token'],name,data)
        room=action(0,'create')['room'];code=room['code']
        action(1,'join',code=code);action(2,'join',code=code)
        with self.assertRaisesRegex(SocialError,'room_full'):action(3,'join',code=code)
        action(0,'ready',ready=True)
        with self.assertRaisesRegex(SocialError,'team_not_ready'):action(0,'start_match')
        action(1,'ready',ready=True);action(2,'ready',ready=True)
        self.assertTrue(action(0,'start_match')['room']['started'])
        action(2,'leave');self.assertEqual(len(action(0,'poll')['room']['members']),2)
        with self.assertRaisesRegex(SocialError,'room_started'):action(3,'join',code=code)

if __name__=='__main__':unittest.main()
