import io,os,tempfile,unittest,json,struct,threading,urllib.request,urllib.error
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from PIL import Image
from social import Social,SocialError
from chat import TZ,mp4_duration

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.path=os.path.join(self.directory.name,'social.db');self.s=Social(self.path)
        self.users=[self.s.register() for _ in range(3)];self.now=datetime(2026,9,1,12,tzinfo=ZoneInfo(TZ)).timestamp();self.s.chat.clock=lambda:self.now
        self.act(0,'friend_add',name=self.users[1]['name']);self.act(1,'friend_accept',id=self.users[0]['id'])
    def tearDown(self):self.s.db.close();self.directory.cleanup()
    def act(self,user,action,**data):return self.s.dispatch(self.users[user]['token'],action,data)
    def chat(self,user,action,other=1,**data):return self.act(user,action,friend=self.users[other]['id'],**data)
    def send(self,user,nonce,other=1,text='Привет ❤️',**data):return self.chat(user,'chat_send',other,nonce=nonce,text=text,**data)
    def test_delivery_receipts_replies_edits_reactions_delete(self):
        r=self.send(0,'send00001');mid=r['message']['id'];received=self.chat(1,'chat_fetch',0)['messages'][0]
        self.assertEqual(received['text'],'Привет ❤️');self.assertIsNone(received['delivered'])
        self.chat(1,'chat_ack',0,upto=mid);self.assertIsNotNone(self.chat(0,'chat_fetch')['messages'][0]['delivered'])
        self.send(1,'send00002',0,reply=mid);self.chat(1,'chat_react',0,message=mid,emoji='heart')
        self.chat(0,'chat_edit',message=mid,text='Исправлено')
        with self.assertRaisesRegex(SocialError,'owner_only'):self.chat(1,'chat_delete',0,message=mid)
        self.chat(0,'chat_delete',message=mid);self.assertTrue(self.chat(1,'chat_fetch',0)['messages'][0]['deleted'])
    def test_retry_idempotency_search_and_rate_limit(self):
        r=self.send(0,'retry0001');self.assertEqual(r['message']['id'],self.send(0,'retry0001')['message']['id'])
        for i in range(7):self.send(0,'send0010'+str(i),text='needle '+str(i))
        with self.assertRaisesRegex(SocialError,'send_rate_limit'):self.send(0,'ratelimit')
        self.assertEqual(len(self.chat(1,'chat_fetch',0,query='needle')['messages']),7)
    def test_privacy_and_request_denial(self):
        self.act(1,'chat_preferences',requests=False,online=False,receipts=False)
        with self.assertRaisesRegex(SocialError,'requests_disabled'):self.act(2,'friend_add',name=self.users[1]['name'])
        mid=self.send(0,'privacy01')['message']['id'];self.chat(1,'chat_ack',0,upto=mid)
        self.assertIsNone(self.chat(0,'chat_fetch')['messages'][0]['seen'])
        with self.assertRaisesRegex(SocialError,'friend_required'):self.chat(2,'chat_fetch',0)
        self.chat(1,'chat_block',0)
        with self.assertRaisesRegex(SocialError,'blocked'):self.chat(0,'chat_fetch')
        self.act(1,'chat_unblock',friend=self.users[0]['id']);self.chat(0,'chat_fetch')
    def test_server_streak_ten_days_and_once_warning(self):
        for day in range(10):
            self.now+=86400;self.send(0,'dayhost'+str(day));self.assertFalse(self.chat(0,'chat_fetch')['streak']['today_complete']);self.send(1,'dayguest'+str(day),0)
        streak=self.chat(0,'chat_fetch')['streak'];self.assertEqual(streak['days'],10);self.assertTrue(streak['color_unlocked'])
        self.act(0,'chat_preferences',color='blue')
        self.now+=86400+11*3600;self.s.chat.warnings();self.s.chat.warnings()
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM chat_events WHERE kind='streak_notifications'").fetchone()[0],2)
        # Same persistent IDs after restart, and midnight computed in explicit +05 zone.
        self.s.db.commit();self.s.db.close();self.s=Social(self.path);self.s.chat.clock=lambda:self.now;self.s.chat.warnings()
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM chat_events WHERE kind='streak_notifications'").fetchone()[0],2)
        self.now+=86400*2;self.assertEqual(self.chat(0,'chat_fetch')['streak']['days'],0)
    def test_photos_private_compressed_and_size_limited(self):
        out=io.BytesIO();Image.new('RGB',(2400,1200),'white').save(out,'PNG')
        uid=self.users[0]['id'];media=self.s.chat.upload(uid,'photo',out.getvalue());identifier=media['media']
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[1]['id'],identifier)
        mid=self.send(0,'photo0001',text=identifier,kind='photo')['message']['id']
        mime,content=self.s.chat.download(self.users[1]['id'],identifier);self.assertEqual(mime,'image/jpeg');self.assertEqual(Image.open(io.BytesIO(content)).size,(1280,640))
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[2]['id'],identifier)
        self.chat(0,'chat_delete',message=mid)
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.s.chat.download(self.users[1]['id'],identifier)
        with self.assertRaisesRegex(SocialError,'media_too_large'):self.s.chat.upload(uid,'photo',b'x'*2097153)
    def test_sender_style_pin_mute_report_and_restart(self):
        self.act(0,'chat_preferences',style=49);mid=self.send(0,'style0049')['message']['id']
        self.assertEqual(self.chat(1,'chat_fetch',0)['messages'][0]['style'],49)
        self.chat(0,'chat_options',pinned=True,muted=True,background='night')
        self.chat(1,'chat_report',0,message=mid,reason='spam')
        self.s.db.commit();self.s.db.close();self.s=Social(self.path)
        self.assertEqual(self.chat(1,'chat_fetch',0)['messages'][0]['style'],49)
        self.assertTrue(self.act(0,'chat_list')['chats'][0]['options']['muted'])
    def test_streak_color_gate_and_invalid_preferences(self):
        with self.assertRaisesRegex(SocialError,'streak_10_required'):self.act(0,'chat_preferences',color='pink')
        with self.assertRaisesRegex(SocialError,'invalid_style'):self.act(0,'chat_preferences',style=50)
        with self.assertRaisesRegex(SocialError,'invalid_preference'):self.act(0,'chat_preferences',requests='false')

    def test_push_payload_language_privacy_mute_quiet_and_no_repeat(self):
        from push import Push
        class Session:
            def __init__(self):self.payloads=[]
            def post(self,url,json,timeout):
                self.payloads.append(json);return type('Response',(),{'status_code':200})()
        push=Push(self.s);push.credentials=True;push.project='test';push.session=Session()
        push.register(self.users[1]['id'],'x'*30)
        self.act(1,'chat_preferences',language='ru',preview=False)
        self.send(0,'push00001',text='private body');self.s.db.commit();push.process();push.process()
        payloads=push.session.payloads
        self.assertEqual(len(payloads),2) # one original friend request, one message
        self.assertEqual(payloads[-1]['message']['notification']['body'],'Новое сообщение')
        self.assertNotIn('private body',str(payloads[-1]))
        self.chat(1,'chat_options',0,muted=True);self.send(0,'push00002');push.process();self.assertEqual(len(payloads),2)
        self.chat(1,'chat_options',0,muted=False);self.act(1,'chat_preferences',quiet=True,quiet_start=10,quiet_end=15)
        self.send(0,'push00003');push.process();self.assertEqual(len(payloads),2)

if __name__=='__main__':unittest.main()
