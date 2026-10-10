import hashlib
import unittest
from social import Social, SocialError
from legacy import migrate, PENDING


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.s=Social(':memory:')
        self.a={'id':'a'*32,'name':'OldAlpha','name_locked':1}
        self.b={'id':'b'*32,'name':'OldBeta','name_locked':0}
        self.ta='legacy_token_alpha_'+'x'*25
        self.tb='legacy_token_beta_'+'y'*25
    def tearDown(self): self.s.db.close()
    def snapshot(self, owner, friend):
        return {'ok':True,'profile':owner,'friends':[friend],'requests':[]}
    def test_identity_friends_and_second_account_claim(self):
        migrate(self.s,self.ta,self.a['id'],lambda _:self.snapshot(self.a,self.b))
        self.assertEqual(self.s.authenticate(self.ta),self.a['id'])
        self.assertEqual(self.s.friend_ids(self.a['id']),[self.b['id']])
        with self.assertRaises(SocialError):self.s.authenticate(PENDING+self.b['id'])
        migrate(self.s,self.tb,self.b['id'],lambda _:self.snapshot(self.b,self.a))
        self.assertEqual(self.s.authenticate(self.tb),self.b['id'])
        self.assertEqual(self.s.friend_ids(self.a['id']),[self.b['id']])
        result=migrate(self.s,self.ta,self.a['id'],lambda _:self.fail('Retry must not contact legacy'))
        self.assertEqual(result['name'],'OldAlpha')
        self.s.dispatch(self.ta,'chat_send',{'friend':self.b['id'],'nonce':'legacychat001','text':'Migrated'})
        self.assertEqual(self.s.dispatch(self.tb,'chat_fetch',{'friend':self.a['id']})['messages'][0]['text'],'Migrated')
    def test_mismatch_rejects_without_writing(self):
        with self.assertRaisesRegex(SocialError,'legacy_identity_mismatch'):
            migrate(self.s,self.ta,'c'*32,lambda _:self.snapshot(self.a,self.b))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM guests').fetchone()[0],0)
    def test_name_collision_rolls_back_every_row(self):
        user=self.s.register();self.s.dispatch(user['token'],'name',{'name':'OldBeta'})
        with self.assertRaisesRegex(SocialError,'legacy_name_conflict'):
            migrate(self.s,self.ta,self.a['id'],lambda _:self.snapshot(self.a,self.b))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM guests').fetchone()[0],1)
    def test_active_identity_cannot_be_taken_over(self):
        self.s.db.execute('INSERT INTO guests VALUES(?,?,?,?,1)',(self.a['id'],hashlib.sha256(b'other_token').hexdigest(),'OldAlpha','oldalpha'))
        self.s.db.commit()
        with self.assertRaisesRegex(SocialError,'legacy_identity_conflict'):
            migrate(self.s,self.ta,self.a['id'],lambda _:self.snapshot(self.a,self.b))
    def test_legacy_unavailable_does_not_create_guest(self):
        def unavailable(_):raise SocialError('legacy_server_unavailable')
        with self.assertRaisesRegex(SocialError,'legacy_server_unavailable'):
            migrate(self.s,self.ta,self.a['id'],unavailable)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM guests').fetchone()[0],0)


if __name__=='__main__':unittest.main()
