"""Deterministic smoke tests for the authoritative co-op match state."""
import time
import unittest

from multiplayer_ws import MatchHub


class MultiplayerMatchTests(unittest.TestCase):
    def test_defeated_player_can_spectate_while_team_continues(self):
        hub=MatchHub();hub.join('r','h',started=True);hub.join('r','g')
        hub.rooms['r']['players']['h']['hp']=0
        state=hub.action('r','h',{'action':'spectate'})
        self.assertFalse(state['ended'])
        self.assertTrue(state['players']['h']['spectator'])
        hub.rooms['r']['next_enemy']=time.monotonic()-1
        state=hub.snapshot('r')
        self.assertTrue(state['enemies'])

    def test_collision_reaches_player_hitbox(self):
        hub=MatchHub();hub.join('r','h',started=True)
        p=hub.rooms['r']['players']['h'];p['x']=.5;p['y']=.5
        hub.rooms['r']['enemies']=[{'id':1,'x':.58,'y':.5,'type':'normal','hp':1,'max_hp':1}]
        hub.rooms['r']['updated']=time.monotonic()-1
        state=hub.snapshot('r')
        self.assertLess(state['players']['h']['hp'],3)

    def test_start_waits_for_every_expected_connection(self):
        hub=MatchHub()
        state=hub.join('r','h',host_uid='h',started=True,expected_members=['h','g'])
        self.assertFalse(state['started'])
        self.assertFalse(state['enemies'])
        state=hub.join('r','g',host_uid='h',started=True,expected_members=['h','g'])
        self.assertTrue(state['started'])

    def test_attack_does_not_use_teammates_position(self):
        hub=MatchHub();hub.join('r','h',started=True);hub.join('r','g')
        hub.action('r','h',{'action':'input','x':.1,'y':.1})
        hub.rooms['r']['enemies']=[{'id':1,'x':.5,'y':.5,'hp':3,'max_hp':3,'type':'tank'}]
        state=hub.action('r','h',{'action':'attack'})
        self.assertEqual(state['enemies'][0]['hp'],3)
        hub.action('r','g',{'action':'attack'})
        state=hub.action('r','g',{'action':'attack'})
        self.assertEqual(state['enemies'][0]['hp'],2)

    def test_snapshot_is_not_mutated_by_next_input(self):
        hub=MatchHub();original=hub.join('r','h')
        hub.action('r','h',{'action':'input','x':.8,'y':.2})
        self.assertEqual(original['players']['h']['x'],.5)

    def test_three_players_share_one_started_match(self):
        hub = MatchHub()
        hub.join("room", "host", host_uid="host")
        hub.join("room", "p2", host_uid="host")
        state = hub.join("room", "p3", host_uid="host")
        self.assertFalse(state["started"])
        with self.assertRaises(ValueError):
            hub.join("room", "p4", host_uid="host")

        with self.assertRaises(ValueError):
            hub.action("room", "p2", {"action": "start"})
        state = hub.action("room", "host", {"action": "start"})
        self.assertTrue(state["started"])
        self.assertEqual(set(state["players"]), {"host", "p2", "p3"})

        # Force the first server-side spawn without sleeping in the test.
        hub.rooms["room"]["next_enemy"] = time.monotonic() - 1
        state = hub.snapshot("room")
        self.assertGreaterEqual(len(state["enemies"]), 1)
        self.assertEqual(state["wave"], 1)

    def test_input_is_clamped_and_attack_is_shared(self):
        hub = MatchHub()
        hub.join("room", "host", host_uid="host")
        hub.join("room", "p2", host_uid="host")
        hub.action("room", "host", {"action": "start"})
        hub.rooms["room"]["enemies"] = [{
            "id": 1, "x": .5, "y": .5, "type": "normal",
            "hp": 1, "max_hp": 1, "dir": "idle",
        }]
        hub.action("room", "p2", {"action": "input", "x": 4, "y": -2, "dir": "right"})
        state = hub.snapshot("room")
        self.assertEqual(state["players"]["p2"]["x"], .96)
        self.assertEqual(state["players"]["p2"]["y"], .04)
        state = hub.action("room", "host", {"action": "attack"})
        self.assertEqual(state["players"]["host"]["kills"], 1)
        self.assertEqual(state["enemies"], [])


if __name__ == "__main__":
    unittest.main()
