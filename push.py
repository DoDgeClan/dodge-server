"""Real FCM HTTP v1 sender. Credentials are server-only, never bundled in APK."""
import json,os,threading,time,logging
from datetime import datetime
from zoneinfo import ZoneInfo
from chat import TZ

class Push:
    def __init__(self,social):
        self.social=social;self.db=social.db;self.stopped=threading.Event()
        self.db.execute('CREATE TABLE IF NOT EXISTS push_devices(uid TEXT,token TEXT UNIQUE,PRIMARY KEY(uid,token))')
        self.credentials=None;self.session=None;self.project=None
        path=os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if path:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            self.credentials=service_account.Credentials.from_service_account_file(path,scopes=['https://www.googleapis.com/auth/firebase.messaging'])
            self.session=AuthorizedSession(self.credentials);self.project=self.credentials.project_id
    @property
    def configured(self):return self.credentials is not None
    def register(self,uid,token):
        if not self.configured:raise ValueError('push_not_configured')
        if not isinstance(token,str) or not 20<=len(token)<=4096:raise ValueError('invalid_push_token')
        with self.social.lock,self.db:
            self.db.execute('DELETE FROM push_devices WHERE token=?',(token,))
            self.db.execute('INSERT INTO push_devices VALUES(?,?)',(uid,token))
    def start(self):
        if self.configured:threading.Thread(target=self.run,daemon=True).start()
    def run(self):
        while not self.stopped.is_set():
            try:self.process()
            except Exception as exc:logging.error('push_worker error=%s',type(exc).__name__)
            self.stopped.wait(60)
    def process(self):
        self.social.chat.warnings()
        with self.social.lock:
            events=self.db.execute('SELECT * FROM chat_events WHERE sent=0 ORDER BY created LIMIT 100').fetchall()
        for event in events:
            with self.social.lock,self.db:
                uid=event['uid'];prefs=self.social.chat.prefs(uid);body=json.loads(event['payload']);enabled=prefs[event['kind']]
                hour=datetime.fromtimestamp(self.social.chat.clock(),ZoneInfo(TZ)).hour
                quiet=prefs['quiet'] and ((prefs['quiet_start']<=hour<prefs['quiet_end']) if prefs['quiet_start']<prefs['quiet_end'] else (hour>=prefs['quiet_start'] or hour<prefs['quiet_end']))
                if 'pair' in body:
                    pair=self.db.execute('SELECT settings FROM chat_pairs WHERE pair=?',(body['pair'],)).fetchone()
                    enabled=enabled and not (pair and json.loads(pair[0]).get(uid,{}).get('muted'))
                if body.get('sender') and self.social.chat.blocked(uid,body['sender']):enabled=False
                if event['created']<self.social.chat.clock()-3600:enabled=False
                if event['kind']=='streak_notifications':
                    if body.get('expires',0)<=self.social.chat.clock() or self.social.chat.streak(body['pair'])['today_complete']:enabled=False
                # A unique persistent event has one delivery attempt. In particular
                # streak warnings cannot repeat after process restart/HTTP timeout.
                self.db.execute('UPDATE chat_events SET sent=1 WHERE id=? AND sent=0',(event['id'],))
                devices=self.db.execute('SELECT token FROM push_devices WHERE uid=?',(uid,)).fetchall()
            if not enabled or quiet:continue
            ru=prefs['language']=='ru';kind=event['kind']
            text={'messages':('Новое сообщение','New message'),'friend_notifications':('Новая заявка в друзья','New friend request'),'streak_notifications':('Серия скоро закончится','Your streak ends soon')}[kind][0 if ru else 1]
            if kind=='messages' and prefs['preview']:text=str(body.get('text',text))[:120]
            for device in devices:
                payload={'message':{'token':device[0],'notification':{'title':'Dodge The Enemies','body':text},'data':{'event_id':event['id'],'kind':kind,'pair':body.get('pair','')},'android':{'priority':'high','ttl':'3600s','notification':{'tag':event['id'],'channel_id':'dodge_social','visibility':'PRIVATE'}}}}
                r=self.session.post('https://fcm.googleapis.com/v1/projects/'+self.project+'/messages:send',json=payload,timeout=15)
                if r.status_code==404:
                    with self.social.lock,self.db:self.db.execute('DELETE FROM push_devices WHERE token=?',(device[0],))
                elif r.status_code>=400:logging.error('push_delivery status=%d',r.status_code)
