import tempfile
import unittest
from social import Social, SocialError


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.social=Social(self.temp.name+'/test.db')
        self.accounts=self.social.accounts

    def tearDown(self):
        self.social.db.close();self.temp.cleanup()

    def create(self,name='Creator',password='sixletters',token=''):
        return self.accounts.dispatch('create',{'name':name,'password':password},token,'local')

    def test_login_password_and_no_plaintext(self):
        user=self.create()
        login=self.accounts.dispatch('login',{'name':'creator','password':'sixletters'},client='local')
        self.assertEqual(login['id'],user['id'])
        self.assertEqual(self.social.authenticate(login['token']),user['id'])
        self.assertEqual(self.social.authenticate(user['token']),user['id'])
        for name,password in [('Creator','wrongpassword'),('NotFound','sixletters'),('Creator','x')]:
            with self.assertRaisesRegex(SocialError,'invalid_credentials'):
                self.accounts.dispatch('login',{'name':name,'password':password},client='local')
        row=self.social.db.execute('SELECT salt,password_hash FROM account_credentials').fetchone()
        self.assertNotEqual(row[1],'sixletters');self.assertEqual(len(row[0]),32)

    def test_existing_guest_claim_preserves_id_and_friends(self):
        guest=self.social.register();friend=self.social.register()
        self.social.dispatch(guest['token'],'friend_add',{'name':friend['name']})
        self.social.dispatch(friend['token'],'friend_accept',{'id':guest['id']})
        user=self.create(token=guest['token'])
        self.assertEqual(user['id'],guest['id'])
        self.assertEqual(self.social.friend_ids(user['id']),[friend['id']])
        with self.assertRaisesRegex(SocialError,'account_already_exists'):self.create('Other',token=guest['token'])

    def test_short_password_duplicate_and_unauthorized_claim(self):
        with self.assertRaisesRegex(SocialError,'invalid_password'):self.create(password='12345')
        user=self.create()
        with self.assertRaisesRegex(SocialError,'name_taken'):self.create()
        with self.assertRaisesRegex(SocialError,'unauthorized'):self.create('Other',token=user['id'])

    def test_qr_needs_owner_approval_poll_secret_and_single_use(self):
        user=self.create();qr=self.accounts.dispatch('qr_create',{},client='qr')
        self.assertNotIn(qr['poll_secret'],qr['uri'])
        self.assertTrue(self.accounts.dispatch('qr_poll',qr)['pending'])
        with self.assertRaisesRegex(SocialError,'unauthorized'):
            self.accounts.dispatch('qr_approve',qr,token=user['id'])
        self.accounts.dispatch('qr_approve',qr,token=user['token'])
        with self.assertRaisesRegex(SocialError,'qr_expired'):
            self.accounts.dispatch('qr_poll',dict(qr,poll_secret='wrong'))
        login=self.accounts.dispatch('qr_poll',qr)
        self.assertEqual(login['id'],user['id'])
        with self.assertRaisesRegex(SocialError,'qr_expired'):self.accounts.dispatch('qr_poll',qr)

    def test_expiry_and_rate_limit(self):
        qr=self.accounts.dispatch('qr_create',{},client='qr')
        with self.social.db:self.social.db.execute('UPDATE account_qr SET expires=0')
        with self.assertRaisesRegex(SocialError,'qr_expired'):self.accounts.dispatch('qr_poll',qr)
        for _ in range(10):self.accounts.rate('test')
        with self.assertRaisesRegex(SocialError,'auth_rate_limit'):self.accounts.rate('test')

    def test_new_device_database_restart(self):
        user=self.create();other=Social(self.temp.name+'/test.db')
        try:
            self.assertEqual(other.authenticate(user['token']),user['id'])
            self.assertEqual(other.accounts.login({'name':'Creator','password':'sixletters'},'device2')['id'],user['id'])
        finally:other.db.close()

    def test_real_http_login_and_qr(self):
        import threading,json,urllib.request,urllib.error
        from http.server import ThreadingHTTPServer
        import online_server_render as server
        previous=server.SOCIAL;server.SOCIAL=self.social
        http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def request(action,data,token=''):
            req=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+'/v2/account',
                data=json.dumps({'action':action,'data':data}).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as response:return response.code,json.load(response)
        try:
            status,user=request('create',{'name':'HttpUser','password':'sixletters'})
            self.assertEqual(status,200)
            status,result=request('login',{'name':'HttpUser','password':'wrongpass'})
            self.assertEqual(status,409);self.assertEqual(result['error'],'invalid_credentials')
            status,result=request('login',{'name':'HttpUser','password':'sixletters'})
            self.assertEqual(status,200);self.assertEqual(result['id'],user['id'])
            _,qr=request('qr_create',{})
            self.assertTrue(request('qr_poll',qr)[1]['pending'])
            self.assertEqual(request('qr_approve',qr,user['token'])[0],200)
            self.assertEqual(request('qr_poll',qr)[1]['id'],user['id'])
        finally:http.shutdown();http.server_close();server.SOCIAL=previous


if __name__=='__main__':unittest.main()
