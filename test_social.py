import unittest
from concurrent.futures import ThreadPoolExecutor
from social import Social, SocialError


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.s = Social(':memory:', clock=lambda: self.now)
        self.users = [self.s.register() for _ in range(4)]

    def call(self, n, action, **data):
        return self.s.dispatch(self.users[n]['token'], action, data)

    def friends(self, n):
        self.call(0, 'friend_add', name=self.users[n]['name'])
        self.call(n, 'friend_accept', id=self.users[0]['id'])

    def test_auth_and_name(self):
        with self.assertRaises(SocialError):
            self.s.dispatch(self.users[0]['id'], 'create')
        self.call(0, 'name', name='TestPlayer')
        with self.assertRaisesRegex(SocialError, 'name_taken'):
            self.call(1, 'name', name='testplayer')
        with self.assertRaisesRegex(SocialError, 'name_locked'):
            self.call(0, 'name', name='NewPlayer')

    def test_expiry_and_recipient(self):
        self.friends(1)
        self.call(0, 'create')
        self.call(0, 'invite', id=self.users[1]['id'])
        iid = self.call(1, 'poll')['invites'][0]['id']
        with self.assertRaises(SocialError): self.call(2, 'accept', id=iid)
        self.now += 10
        with self.assertRaisesRegex(SocialError, 'invite_expired'): self.call(1, 'accept', id=iid)

    def test_capacity_and_disconnect(self):
        self.call(0, 'create')
        for n in (1, 2):
            self.friends(n)
            self.call(0, 'invite', id=self.users[n]['id'])
            iid = self.call(n, 'poll')['invites'][0]['id']
            self.call(n, 'accept', id=iid)
            self.now += 10
            for j in range(4): self.call(j, 'poll')
        self.friends(3)
        with self.assertRaisesRegex(SocialError, 'room_full'): self.call(0, 'invite', id=self.users[3]['id'])
        self.assertEqual(len(self.call(0, 'poll')['room']['members']), 3)
        self.now += 31
        self.assertIsNone(self.call(1, 'poll')['room'])

    def test_decline_and_accept_once(self):
        self.friends(1); self.call(0, 'create'); self.call(0, 'invite', id=self.users[1]['id'])
        iid = self.call(1, 'poll')['invites'][0]['id']
        self.call(1, 'decline', id=iid)
        with self.assertRaises(SocialError): self.call(1, 'accept', id=iid)


if __name__ == '__main__': unittest.main()

