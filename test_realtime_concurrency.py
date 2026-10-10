"""Concurrent authoritative state checks; no network or production database."""
import concurrent.futures
import time
import unittest
from multiplayer_ws import MatchHub

class RealtimeConcurrencyTests(unittest.TestCase):
    def test_three_client_actions_return_independent_consistent_snapshots(self):
        hub=MatchHub()
        for uid in ('h','a','b'):
            hub.join('r',uid,host_uid='h',started=True,expected_members=['h','a','b'])
        hub.rooms['r']['next_enemy']=time.monotonic()+60
        original=hub.snapshot('r')
        def send(uid):
            for index in range(50):
                state=hub.action('r',uid,{'action':'input','x':.2+index/100,'y':.2})
                self.assertEqual(set(state['players']),{'h','a','b'})
                self.assertTrue(all(.04<=p['x']<=.96 for p in state['players'].values()))
            return state
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(send,('h','a','b')))
        self.assertEqual([p['x'] for p in original['players'].values()],[.5,.5,.5])
        self.assertEqual([p['x'] for p in hub.snapshot('r')['players'].values()],[.69,.69,.69])

    def test_dead_sender_does_not_create_decoy_or_reset_cooldown(self):
        hub=MatchHub();hub.join('r','h',started=True);hub.join('r','a')
        hub.rooms['r']['players']['a']['hp']=0
        state=hub.action('r','a',{'action':'decoy'})
        self.assertNotIn('a',state['decoys'])
        self.assertNotIn('decoy_ready',state['players']['a'])
        self.assertFalse(state['ended'])

if __name__=='__main__':unittest.main()
