"""Deterministic smoke tests for the authoritative co-op match state."""
import time
import unittest

from multiplayer_ws import MatchHub


class MultiplayerMatchTests(unittest.TestCase):
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
