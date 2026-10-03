"""Persistent private friend conversations. All methods run under Social.lock.
Streak dates use Asia/Qyzylorda (UTC+05), never client clocks.
"""
import json, time, uuid, io, struct, os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from social import SocialError

TZ='Asia/Qyzylorda'
DEFAULTS={'avatar':0,'requests':True,'online':True,'receipts':True,'messages':True,'friend_notifications':True,'streak_notifications':True,'preview':False,'language':'en','quiet_start':22,'quiet_end':8,'quiet':False,'style':0,'flame':0,'color':'orange','badge':'star'}

class Chat:
    def __init__(self,social,clock=time.time):
        self.social=social;self.db=social.db;self.clock=clock
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS chat_unlocks(uid TEXT,feature TEXT,PRIMARY KEY(uid,feature));
        CREATE TABLE IF NOT EXISTS chat_preferences(uid TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chat_pairs(pair TEXT PRIMARY KEY,a TEXT,b TEXT,settings TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,pair TEXT,sender TEXT,nonce TEXT,kind TEXT,text TEXT,reply INTEGER,style INTEGER,created REAL,edited INTEGER DEFAULT 0,deleted INTEGER DEFAULT 0,seen REAL,delivered REAL,UNIQUE(sender,nonce));
        CREATE INDEX IF NOT EXISTS chat_messages_pair ON chat_messages(pair,id);
        CREATE TABLE IF NOT EXISTS chat_blocks(owner TEXT,target TEXT,PRIMARY KEY(owner,target));
        CREATE TABLE IF NOT EXISTS chat_reactions(message INTEGER,uid TEXT,emoji TEXT,PRIMARY KEY(message,uid));
        CREATE TABLE IF NOT EXISTS chat_media(id TEXT PRIMARY KEY,owner TEXT,kind TEXT,mime TEXT,data BLOB,created REAL);
        CREATE TABLE IF NOT EXISTS chat_reports(id INTEGER PRIMARY KEY,reporter TEXT,message INTEGER,reason TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS chat_days(pair TEXT,day TEXT,sender TEXT,PRIMARY KEY(pair,day,sender));
        CREATE TABLE IF NOT EXISTS chat_events(id TEXT PRIMARY KEY,uid TEXT,kind TEXT,payload TEXT,created REAL,sent INTEGER DEFAULT 0);
        ''')
    def prefs(self,uid):
        row=self.db.execute('SELECT data FROM chat_preferences WHERE uid=?',(uid,)).fetchone()
        result=dict(DEFAULTS,**(json.loads(row[0]) if row else {}))
        result['color_unlocked']=bool(self.db.execute("SELECT 1 FROM chat_unlocks WHERE uid=? AND feature='color'",(uid,)).fetchone())
        return result
    def blocked(self,a,b):
        return bool(self.db.execute('SELECT 1 FROM chat_blocks WHERE (owner=? AND target=?) OR (owner=? AND target=?)',(a,b,b,a)).fetchone())
    def pair(self,uid,other):
        if other not in self.social.friend_ids(uid):raise SocialError('friend_required')
        if self.blocked(uid,other):raise SocialError('blocked')
        a,b=sorted((uid,other));key=a+':'+b
        self.db.execute('INSERT OR IGNORE INTO chat_pairs(pair,a,b) VALUES(?,?,?)',(key,a,b));return key
    def event(self,key,uid,kind,payload):
        self.db.execute('INSERT OR IGNORE INTO chat_events VALUES(?,?,?,?,?,0)',(key,uid,kind,json.dumps(payload),self.clock()))
    def streak(self,key):
        now=datetime.fromtimestamp(self.clock(),ZoneInfo(TZ));today=now.date();a,b=key.split(':')
        rows=self.db.execute('SELECT day,COUNT(*) FROM chat_days WHERE pair=? GROUP BY day HAVING COUNT(*)=2 ORDER BY day DESC LIMIT 36600',(key,)).fetchall()
        completed={r[0] for r in rows};day=today if today.isoformat() in completed else today-timedelta(days=1);count=0
        while day.isoformat() in completed:count+=1;day-=timedelta(days=1)
        midnight=datetime.combine(today+timedelta(days=1),datetime.min.time(),tzinfo=ZoneInfo(TZ))
        participants=[r[0] for r in self.db.execute('SELECT sender FROM chat_days WHERE pair=? AND day=?',(key,today.isoformat()))]
        return {'days':count,'today_complete':len(participants)==2,'remaining':max(0,int(midnight.timestamp()-self.clock())),'timezone':TZ,'color_unlocked':count>=10,'participants_today':participants,'expires':midnight.timestamp()}
    def row(self,uid,mid,key=None):
        r=self.db.execute('SELECT * FROM chat_messages WHERE id=?',(mid,)).fetchone()
        if not r or (key is not None and r['pair']!=key) or uid not in r['pair'].split(':'):raise SocialError('message_not_found')
        a,b=r['pair'].split(':');self.pair(uid,b if uid==a else a);return r
    def message(self,row):
        m=dict(row);m['text']='' if m['deleted'] else m['text'];m.pop('nonce',None)
        m['reactions']=[dict(r) for r in self.db.execute('SELECT uid,emoji FROM chat_reactions WHERE message=?',(m['id'],))]
        m['seen']=m['seen'] if self.prefs(m['sender'])['receipts'] else None
        return m
    def status(self,uid):
        result=[]
        for other in self.social.friend_ids(uid):
            if self.blocked(uid,other):continue
            key=self.pair(uid,other);row=self.db.execute('SELECT settings FROM chat_pairs WHERE pair=?',(key,)).fetchone()
            opts=json.loads(row[0]).get(uid,{})
            result.append({'friend':dict(self.social.profile(other),online=self.social.connected(other) and self.prefs(other)['online']), 'streak':self.streak(key),'options':opts})
        return sorted(result,key=lambda r:not r['options'].get('pinned',False))
    def dispatch(self,uid,action,data):
        if action=='chat_preferences':
            p=self.prefs(uid)
            for key,value in data.items():
                if key not in DEFAULTS:raise SocialError('invalid_preference')
                if type(DEFAULTS[key]) is bool and type(value) is not bool:raise SocialError('invalid_preference')
                if key in ('quiet_start','quiet_end') and (type(value) is not int or not 0<=value<=23):raise SocialError('invalid_preference')
                if key=='language' and value not in ('en','ru'):raise SocialError('invalid_preference')
                if key=='avatar' and (type(value) is not int or not 0<=value<100):raise SocialError('invalid_avatar')
                if key=='style' and (type(value) is not int or not 0<=value<50):raise SocialError('invalid_style')
                if key=='flame' and (type(value) is not int or not 0<=value<12):raise SocialError('invalid_style')
                if key=='color' and value!='orange' and not p.get('color_unlocked') and not any(c['streak']['color_unlocked'] for c in self.status(uid)):raise SocialError('streak_10_required')
                if key=='color' and value not in ('orange','blue','green','purple','pink'):raise SocialError('invalid_color')
                if key=='badge' and value not in ('star','heart','diamond','shield'):raise SocialError('invalid_badge')
                p[key]=value
            self.db.execute('INSERT OR REPLACE INTO chat_preferences VALUES(?,?)',(uid,json.dumps(p)));return {'preferences':p,'chats':self.status(uid)}
        if action=='chat_list':return {'preferences':self.prefs(uid),'chats':self.status(uid),'requests':self.social.dispatch_requests(uid),'blocked':[self.social.profile(r[0]) for r in self.db.execute('SELECT target FROM chat_blocks WHERE owner=?',(uid,))]}
        other=data.get('friend')
        if action=='chat_unblock':
            self.db.execute('DELETE FROM chat_blocks WHERE owner=? AND target=?',(uid,other));return {'unblocked':True}
        key=self.pair(uid,other)
        if action=='chat_block':
            self.db.execute('INSERT OR IGNORE INTO chat_blocks VALUES(?,?)',(uid,other));return {'blocked':True}
        if action=='chat_options':
            row=self.db.execute('SELECT settings FROM chat_pairs WHERE pair=?',(key,)).fetchone();settings=json.loads(row[0]);opts=settings.setdefault(uid,{})
            for name in ('pinned','muted'):
                if name in data:
                    if type(data[name]) is not bool:raise SocialError('invalid_preference')
                    opts[name]=data[name]
            if 'background' in data:
                from re import fullmatch
                if not fullmatch('[a-z_]{1,20}',str(data['background'])):raise SocialError('invalid_preference')
                opts['background']=data['background']
            self.db.execute('UPDATE chat_pairs SET settings=? WHERE pair=?',(json.dumps(settings),key));return {'options':opts}
        if action=='chat_ack':
            upto=int(data.get('upto',0))
            self.db.execute('UPDATE chat_messages SET delivered=COALESCE(delivered,?) WHERE pair=? AND id<=? AND sender<>?',(self.clock(),key,upto,uid))
            if self.prefs(uid)['receipts']:self.db.execute('UPDATE chat_messages SET seen=COALESCE(seen,?) WHERE pair=? AND id<=? AND sender<>?',(self.clock(),key,upto,uid))
            return {'acknowledged':True}
        if action=='chat_fetch':
            before=int(data.get('before',2**62));query=str(data.get('query',''))[:128]
            rows=self.db.execute('SELECT * FROM chat_messages WHERE pair=? AND id<? AND (deleted=1 OR text LIKE ?) ORDER BY id DESC LIMIT 50',(key,before,'%'+query+'%')).fetchall()
            return {'messages':[self.message(r) for r in reversed(rows)],'streak':self.streak(key),'preferences':self.prefs(uid),'peer_cosmetics':{k:self.prefs(other)[k] for k in ('flame','color','badge')},'quest':{'shared_results':self.db.execute("SELECT COUNT(*) FROM chat_messages WHERE pair=? AND kind='result' AND deleted=0",(key,)).fetchone()[0],'target':10}}
        if action=='chat_send':
            nonce=str(data.get('nonce',''))
            if not 8<=len(nonce)<=80:raise SocialError('invalid_nonce')
            duplicate=self.db.execute('SELECT * FROM chat_messages WHERE sender=? AND nonce=?',(uid,nonce)).fetchone()
            if duplicate:
                if duplicate['pair']!=key:raise SocialError('nonce_conflict')
                return {'message':self.message(duplicate),'duplicate':True,'streak':self.streak(key)}
            recent=self.db.execute('SELECT COUNT(*) FROM chat_messages WHERE sender=? AND created>?',(uid,self.clock()-10)).fetchone()[0]
            if recent>=8:raise SocialError('send_rate_limit')
            kind=data.get('kind','text');text=str(data.get('text','')).strip()
            if kind not in ('text','photo','voice','invite','result'):raise SocialError('invalid_kind')
            if not text or len(text)>2000:raise SocialError('invalid_message')
            if kind in ('photo','voice'):
                media=self.db.execute('SELECT kind FROM chat_media WHERE id=? AND owner=?',(text,uid)).fetchone()
                if not media or media[0]!=kind:raise SocialError('invalid_media')
            if kind=='invite':
                rid,room=self.social.room_for(uid)
                if not room or room['started'] or other in room['members']:raise SocialError('room_required')
                self.social.dispatch_by_uid_invite(uid,other);text=rid
            if kind=='result':
                result=json.loads(text)
                if set(result)-{'score','seconds','mode'} or not isinstance(result.get('score'),int) or not 0<=result['score']<=10000000:raise SocialError('invalid_result')
            reply=data.get('reply');reply=int(reply) if reply else None
            if reply:self.row(uid,reply,key)
            style=self.prefs(uid)['style']
            mid=self.db.execute('INSERT INTO chat_messages(pair,sender,nonce,kind,text,reply,style,created) VALUES(?,?,?,?,?,?,?,?)',(key,uid,nonce,kind,text,reply,style,self.clock())).lastrowid
            day=datetime.fromtimestamp(self.clock(),ZoneInfo(TZ)).date().isoformat()
            self.db.execute('INSERT OR IGNORE INTO chat_days VALUES(?,?,?)',(key,day,uid))
            if self.streak(key)['days']>=10:
                for member in key.split(':'):self.db.execute("INSERT OR IGNORE INTO chat_unlocks VALUES(?,'color')",(member,))
            self.event('message:'+str(mid),other,'messages',{'sender':uid,'text':text if kind=='text' else kind,'pair':key})
            return {'message':self.message(self.row(uid,mid,key)),'streak':self.streak(key)}
        mid=int(data.get('message',0));row=self.row(uid,mid,key)
        if action=='chat_report':
            reason=str(data.get('reason','')).strip()
            if not 3<=len(reason)<=300:raise SocialError('invalid_report')
            count=self.db.execute('SELECT COUNT(*) FROM chat_reports WHERE reporter=? AND created>?',(uid,self.clock()-3600)).fetchone()[0]
            if count>=10:raise SocialError('report_rate_limit')
            self.db.execute('INSERT INTO chat_reports(reporter,message,reason,created) VALUES(?,?,?,?)',(uid,mid,reason,self.clock()));return {'reported':True}
        if action=='chat_react':
            emoji=data.get('emoji')
            if emoji not in ('heart','like','laugh','wow','sad','fire'):raise SocialError('invalid_emoji')
            self.db.execute('INSERT OR REPLACE INTO chat_reactions VALUES(?,?,?)',(mid,uid,emoji));return {'message':self.message(self.row(uid,mid,key))}
        if row['sender']!=uid:raise SocialError('owner_only')
        if action=='chat_edit':
            text=str(data.get('text','')).strip()
            if row['kind']!='text' or row['deleted'] or not 1<=len(text)<=2000:raise SocialError('invalid_message')
            self.db.execute('UPDATE chat_messages SET text=?,edited=1 WHERE id=?',(text,mid))
        elif action=='chat_delete':self.db.execute("UPDATE chat_messages SET deleted=1,text='' WHERE id=?",(mid,))
        else:raise SocialError('unknown_action')
        return {'message':self.message(self.row(uid,mid,key))}
    def upload(self,uid,kind,content):
        if self.db.execute('SELECT COALESCE(SUM(length(data)),0) FROM chat_media WHERE owner=?',(uid,)).fetchone()[0]+len(content)>int(os.environ.get('CHAT_MEDIA_QUOTA',33554432)):raise SocialError('media_storage_full')
        if not content or len(content)>2*1024*1024:raise SocialError('media_too_large')
        if self.db.execute('SELECT COUNT(*) FROM chat_media WHERE owner=? AND created>?',(uid,self.clock()-60)).fetchone()[0]>=6:raise SocialError('upload_rate_limit')
        if kind=='photo':
            from PIL import Image,ImageOps
            try:
                with Image.open(io.BytesIO(content)) as im:
                    if im.width*im.height>16000000:raise SocialError('photo_too_large')
                    im=ImageOps.exif_transpose(im);im.thumbnail((1280,1280));im=im.convert('RGB');out=io.BytesIO();im.save(out,'JPEG',quality=88);content=out.getvalue()
            except (OSError,ValueError,Image.DecompressionBombError):raise SocialError('invalid_photo')
            mime='image/jpeg'
        elif kind=='voice':
            duration=mp4_duration(content)
            if not 0<duration<=30:raise SocialError('invalid_voice_duration')
            mime='audio/mp4'
        else:raise SocialError('invalid_media')
        identifier=uuid.uuid4().hex
        self.db.execute('INSERT INTO chat_media VALUES(?,?,?,?,?,?)',(identifier,uid,kind,mime,content,self.clock()));return {'media':identifier,'kind':kind,'bytes':len(content)}
    def download(self,uid,identifier):
        row=self.db.execute('SELECT * FROM chat_media WHERE id=?',(identifier,)).fetchone()
        if not row:raise SocialError('media_not_found')
        if row['owner']!=uid:
            messages=self.db.execute('SELECT * FROM chat_messages WHERE text=? AND deleted=0 AND kind IN (\'photo\',\'voice\')',(identifier,)).fetchall()
            if not any(uid in m['pair'].split(':') and not self.blocked(uid,row['owner']) for m in messages):raise SocialError('unauthorized')
        return row['mime'],row['data']
    def warnings(self):
        with self.social.lock,self.db:
            for row in self.db.execute('SELECT pair,a,b FROM chat_pairs').fetchall():
                state=self.streak(row['pair'])
                if state['days'] and not state['today_complete'] and state['remaining']<=7200:
                    day=datetime.fromtimestamp(self.clock(),ZoneInfo(TZ)).date().isoformat()
                    for uid in (row['a'],row['b']):
                        if not self.blocked(row['a'],row['b']):self.event('streak:'+row['pair']+':'+day+':'+uid,uid,'streak_notifications',{'pair':row['pair'],'days':state['days'],'expires':state['expires']})

def mp4_duration(data):
    """Check actual container time, not client-declared seconds."""
    def boxes(start,end):
        while start+8<=end:
            size,tag=struct.unpack_from('!I4s',data,start)
            if size<8 or start+size>end:raise SocialError('invalid_audio')
            yield tag,start+8,start+size
            start+=size
    if len(data)<24 or data[4:8]!=b'ftyp':raise SocialError('invalid_audio')
    top=list(boxes(0,len(data)))
    if not any(tag==b'mdat' and end-start>0 for tag,start,end in top):raise SocialError('invalid_audio')
    movie=None;audio=None
    for tag,start,end in top:
        if tag==b'moov':
            for child,s,e in boxes(start,end):
                if child==b'mvhd':
                    version=data[s];offset=s+(20 if version==1 else 12)
                    if offset+(12 if version==1 else 8)>e:raise SocialError('invalid_audio')
                    scale=struct.unpack_from('!I',data,offset)[0];duration=struct.unpack_from('!Q' if version==1 else '!I',data,offset+4)[0]
                    if not scale:raise SocialError('invalid_audio')
                    movie=duration/scale
                if child==b'trak':
                    for component,ts,te in boxes(s,e):
                        if component==b'mdia':
                            handler=None;duration=None
                            for mt,ms,me in boxes(ts,te):
                                if mt==b'hdlr':handler=data[ms+8:ms+12]
                                if mt==b'mdhd':
                                    version=data[ms];offset=ms+(20 if version==1 else 12)
                                    if offset+(12 if version==1 else 8)>me:raise SocialError('invalid_audio')
                                    scale=struct.unpack_from('!I',data,offset)[0];value=struct.unpack_from('!Q' if version==1 else '!I',data,offset+4)[0]
                                    if not scale:raise SocialError('invalid_audio')
                                    duration=value/scale
                            if handler!=b'soun':raise SocialError('invalid_audio')
                            audio=duration
    if movie is None or audio is None:raise SocialError('invalid_audio')
    return max(movie,audio)
