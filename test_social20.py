"""Real authorization, persistence and settings tests using isolated identities."""
import os,tempfile,unittest
from social import Social,SocialError

class Social20Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=os.path.join(self.tmp.name,'social.db')
        self.s=Social(self.path);self.users=[self.s.register() for _ in range(3)]
        self.now=1800000000;self.s.chat.clock=lambda:self.now
        self.act(0,'friend_add',name=self.users[1]['name']);self.act(1,'friend_accept',id=self.users[0]['id'])
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def act(self,i,action,**data):return self.s.dispatch(self.users[i]['token'],action,data)
    def send(self):return self.act(0,'chat_send',friend=self.users[1]['id'],nonce='nonce-social20',text='hello')['message']['id']
    def test_privacy_invites_and_text_are_server_enforced(self):
        self.act(0,'create')
        self.act(1,'chat_preferences',invites='nobody',writes='nobody',online=False,status='Привет')
        for action in ('invite','chat_send'):
            with self.assertRaisesRegex(SocialError,'invites_disabled|messages_disabled'):
                self.act(0,action,id=self.users[1]['id'],friend=self.users[1]['id'],nonce='privacy-nonce',text='hello')
        profile=self.act(2,'profile_lookup',id=self.users[1]['id'])['profile']
        self.assertFalse(profile['online']);self.assertEqual(profile['status'],'Привет');self.assertNotIn('token',profile)
        self.act(1,'chat_preferences',writes='friends')
        with self.assertRaisesRegex(SocialError,'friend_required'):
            self.act(2,'chat_send',friend=self.users[1]['id'],nonce='notfriends',text='hello')
        with self.assertRaisesRegex(SocialError,'invites_disabled'):
            self.act(0,'chat_send',friend=self.users[1]['id'],nonce='invite-message',text='invite',kind='invite')
    def test_saved_messages_scoped_persistent_and_revocable(self):
        mid=self.send()
        self.act(1,'chat_save',friend=self.users[0]['id'],message=mid,saved=True)
        self.assertEqual(len(self.act(1,'chat_saved')['messages']),1)
        self.assertTrue(self.act(1,'chat_fetch',friend=self.users[0]['id'])['messages'][0]['saved'])
        self.assertFalse(self.act(0,'chat_fetch',friend=self.users[1]['id'])['messages'][0]['saved'])
        self.assertEqual(self.act(0,'chat_saved')['messages'],[])
        with self.assertRaisesRegex(SocialError,'friend_required'):
            self.act(2,'chat_save',friend=self.users[0]['id'],message=mid,saved=True)
        self.s.db.close();self.s=Social(self.path)
        self.assertEqual(len(self.act(1,'chat_saved')['messages']),1)
        self.act(0,'chat_delete',friend=self.users[1]['id'],message=mid)
        self.assertEqual(self.act(1,'chat_saved')['messages'],[])
    def test_read_all_honors_receipts_and_excludes_unrelated(self):
        mid=self.send();self.act(1,'chat_preferences',receipts=False)
        self.act(1,'chat_mark_all_read');row=self.s.chat.row(self.users[0]['id'],mid)
        self.assertIsNone(row['seen']);self.assertIsNotNone(row['delivered'])
        self.act(1,'chat_preferences',receipts=True);self.act(1,'chat_mark_all_read')
        self.assertIsNotNone(self.s.chat.row(self.users[0]['id'],mid)['seen'])
    def test_name_rename_unique_cooldown_and_profile_lookup(self):
        self.act(0,'name',name='First_Name')
        self.act(0,'name_rename',name='New_Name')
        self.assertEqual(self.act(1,'profile_lookup',name='new_name')['profile']['id'],self.users[0]['id'])
        with self.assertRaisesRegex(SocialError,'rename_cooldown'):self.act(0,'name_rename',name='Again_Name')
        with self.assertRaisesRegex(SocialError,'name_taken'):self.act(1,'name_rename',name='New_Name')
        self.now+=86401;self.act(0,'name_rename',name='Again_Name')
    def test_recent_players_record_match_not_lobby_and_friend_removal(self):
        room=self.act(0,'create')['room']['code'];self.act(1,'join',code=room)
        self.assertEqual(self.act(0,'recent_players')['players'],[])
        self.act(0,'start_match');self.act(0,'start_match')
        players=self.act(0,'recent_players')['players'];self.assertEqual([p['id'] for p in players],[self.users[1]['id']])
        self.assertEqual(self.act(2,'recent_players')['players'],[])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM chat_events WHERE kind='recent_match'").fetchone()[0],2)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM chat_events WHERE kind IN ('recent_match','name_rename') AND sent=0").fetchone()[0],0)
        self.act(0,'friend_remove',id=self.users[1]['id'])
        self.assertEqual(self.s.friend_ids(self.users[1]['id']),[])
        with self.assertRaisesRegex(SocialError,'friend_required'):self.send()
    def test_everyone_invitation_policy_permits_nonfriend_only_when_enabled(self):
        self.act(2,'create')
        with self.assertRaisesRegex(SocialError,'invites_disabled'):
            self.act(2,'invite',id=self.users[1]['id'])
        self.act(1,'chat_preferences',invites='all')
        self.act(2,'invite',id=self.users[1]['id'])
        self.assertEqual(len(self.act(1,'poll')['invites']),1)

    def test_invalid_new_preferences_rollback(self):
        for data in ({'invites':'anyone'},{'writes':'all'},{'status':'x'*81},{'status':'bad\nstatus'}):
            with self.assertRaisesRegex(SocialError,'invalid_preference'):self.act(0,'chat_preferences',**data)
        self.assertEqual(self.s.chat.prefs(self.users[0]['id'])['status'],'')


class ProfileData20Tests(unittest.TestCase):
    setUp=Social20Tests.setUp
    tearDown=Social20Tests.tearDown
    act=Social20Tests.act
    def test_device_statistics_scoped_bounded_and_persistent(self):
        stats={'stats_runs':12,'stats_victories':4,'stats_enemies_defeated':99,'stats_playtime_seconds':123.5,'level':3}
        self.assertEqual(self.act(0,'profile_stats',stats=stats)['stats_source'],'device')
        self.assertEqual(self.act(1,'profile_lookup',id=self.users[0]['id'])['profile']['stats'],stats)
        self.assertNotIn('stats',self.act(2,'profile_lookup',id=self.users[0]['id'])['profile'])
        for data in ({'stats':{'level':False}},{'stats':{'stats_runs':10**1000}},{'stats':{'stats_runs':-1}},{'stats':{'stats_playtime_seconds':float('nan')}},{'stats':{'unknown':7}},{'stats':stats,'id':self.users[1]['id']}):
            with self.assertRaisesRegex(SocialError,'invalid_stats'):self.act(0,'profile_stats',**data)
        self.s.db.close();self.s=Social(self.path)
        self.assertEqual(self.act(0,'profile_lookup',id=self.users[0]['id'])['profile']['stats'],stats)
        self.act(0,'friend_remove',id=self.users[1]['id'])
        self.assertNotIn('stats',self.act(1,'profile_lookup',id=self.users[0]['id'])['profile'])
    def test_profile_avatar_explicit_optin_friend_scoped_and_revoked(self):
        import io
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(128,128),'blue').save(image,'PNG')
        with self.s.db:media=self.s.chat.upload(self.users[0]['id'],'photo',image.getvalue())['media']
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[1]['id'],media)
        with self.assertRaisesRegex(SocialError,'invalid_avatar'):self.act(1,'profile_avatar',media=media)
        self.act(0,'profile_avatar',media=media)
        self.assertEqual(self.act(1,'profile_lookup',id=self.users[0]['id'])['profile']['avatar_media'],media)
        self.assertNotIn('avatar_media',self.act(2,'profile_lookup',id=self.users[0]['id'])['profile'])
        self.assertEqual(self.s.chat.download(self.users[1]['id'],media)[0],'image/jpeg')
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[2]['id'],media)
        self.s.db.close();self.s=Social(self.path)
        self.assertEqual(self.act(1,'profile_lookup',id=self.users[0]['id'])['profile']['avatar_media'],media)
        self.act(0,'profile_avatar',media=None)
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[1]['id'],media)
        self.act(0,'profile_avatar',media=media)
        self.act(0,'friend_remove',id=self.users[1]['id'])
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[1]['id'],media)

    def test_ranking_reads_authenticated_identity_actual_rows(self):
        import online_server_render as server
        old=server.DB_FILE;server.DB_FILE=os.path.join(self.tmp.name,'scores.db')
        try:
            server.init_db()
            self.assertFalse(self.act(0,'my_ranking')['ranking']['participating'])
            server.submit({'uid':self.users[1]['id'],'name':'Other','rank':'Silver','rank_index':1,'score':100})
            server.submit({'uid':self.users[0]['id'],'name':'Me','rank':'Bronze','rank_index':0,'score':500})
            rank=self.act(0,'my_ranking')['ranking']
            self.assertEqual(rank['position'],2);self.assertEqual(rank['score'],500)
            with self.assertRaisesRegex(SocialError,'invalid_request'):self.act(2,'my_ranking',uid=self.users[0]['id'])
            self.assertFalse(self.act(2,'my_ranking')['ranking']['participating'])
            with server.db() as con:con.execute("UPDATE meta SET value='2000-01' WHERE key='current_month'")
            self.assertFalse(self.act(0,'my_ranking')['ranking']['participating'])
            with server.db() as con:self.assertEqual(con.execute("SELECT COUNT(*) FROM scores").fetchone()[0],2)
        finally:server.DB_FILE=old

if __name__=='__main__':unittest.main()
