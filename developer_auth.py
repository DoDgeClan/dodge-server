"""Narrow relay to the private owner's existing credential store; no APK secrets."""
import json
import os
import threading
import urllib.error
import urllib.request

SITE = 'https://dodge-enemies-admin.mrdoxer-kz.chatgpt.site'
SLOTS = threading.BoundedSemaphore(3)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def login(payload, opener=None):
    email, password = payload.get('email'), payload.get('password')
    if not isinstance(email,str) or not isinstance(password,str) or not 0<len(email)<=254 or not 0<len(password)<=128:
        return 400, {'ok':False,'error':'invalid_credentials'}
    token, key = os.environ.get('DODGE_DEV_SITE_TOKEN',''), os.environ.get('DODGE_ADMIN_KEY','')
    if not token or len(key)<32:
        return 503, {'ok':False,'error':'developer_auth_unavailable'}
    if not SLOTS.acquire(blocking=False):
        return 503, {'ok':False,'error':'developer_auth_busy'}
    try:
        req=urllib.request.Request(SITE+'/api/developer/login',data=json.dumps({'email':email,'password':password}).encode(),method='POST',headers={'Content-Type':'application/json','Authorization':'Bearer '+key,'OAI-Sites-Authorization':'Bearer '+token})
        opener=opener or urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(req,timeout=15) as r:
                if r.status!=200:return 503,{'ok':False,'error':'developer_auth_unavailable'}
                raw=r.read(4097)
                if len(raw)>4096:raise ValueError('oversized')
                data=json.loads(raw)
                if data.get('ok') is not True or data.get('scope')!='autoplay' or data.get('expires_in')!=900:
                    raise ValueError('invalid_scope')
                return 200,{'ok':True,'scope':'autoplay','expires_in':900}
        except urllib.error.HTTPError as exc:
            if exc.code==401:return 401,{'ok':False,'error':'invalid_credentials'}
            if exc.code==429:return 429,{'ok':False,'error':'developer_auth_rate_limit'}
            return 503,{'ok':False,'error':'developer_auth_unavailable'}
        except Exception:
            return 503,{'ok':False,'error':'developer_auth_unavailable'}
    finally:
        SLOTS.release()
