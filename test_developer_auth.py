import io,json,os,unittest,urllib.error
from unittest.mock import patch
import developer_auth as auth
class Response(io.BytesIO):
    status=200
class Opener:
    def __init__(self,data=None,error=None):self.data=data;self.error=error;self.request=None
    def open(self,r,timeout):
        self.request=r
        if self.error:raise self.error
        return Response(json.dumps(self.data).encode())
class Tests(unittest.TestCase):
    def test_credentials_not_configured(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(auth.login({'email':'x','password':'p'})[0],503)
    def test_scope_and_private_relay(self):
        with patch.dict(os.environ,{'DODGE_DEV_SITE_TOKEN':'test-service-token','DODGE_ADMIN_KEY':'k'*40}):
            o=Opener({'ok':True,'scope':'autoplay','expires_in':900});self.assertEqual(auth.login({'email':'owner@test','password':'test-password'},o)[0],200)
            self.assertEqual(o.request.full_url,auth.SITE+'/api/developer/login')
            self.assertIn('Oai-sites-authorization',o.request.headers)
            o=Opener({'ok':True,'scope':'admin','expires_in':900});self.assertEqual(auth.login({'email':'x','password':'p'},o)[0],503)
    def test_wrong_credentials_and_rate_limit(self):
        with patch.dict(os.environ,{'DODGE_DEV_SITE_TOKEN':'test','DODGE_ADMIN_KEY':'k'*40}):
            for code in (401,429):
                o=Opener(error=urllib.error.HTTPError(auth.SITE,code,'',{},None));self.assertEqual(auth.login({'email':'x','password':'p'},o)[0],code)
if __name__=='__main__':unittest.main()
