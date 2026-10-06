"""Opt-in real HTTP integration check with two disposable authenticated accounts.

No fake server, credentials, existing-player edits or APK build. Cleanup revokes
sharing, deletes test messages and leaves rooms/friendships. Guest records remain
because the production API has no account deletion endpoint.
"""
import argparse
import io
import secrets
import time
import requests
from PIL import Image


def check(base):
    base=base.rstrip('/');session=requests.Session();users=[];messages=[]
    def request(path, user=None, payload=None, content=None, expected_error=None, operation=None):
        headers={'Authorization':'Bearer '+user['token']} if user else {}
        if content is not None:
            response=session.post(base+path,data=content,headers=headers,timeout=(10,30))
        elif payload is not None:
            response=session.post(base+path,json=payload,headers=headers,timeout=(10,30))
        else:response=session.get(base+path,headers=headers,timeout=(10,30))
        if expected_error:
            assert response.status_code in (401,409), 'Expected authorization rejection, received HTTP '+str(response.status_code)
            body=response.json();assert body.get('error')==expected_error, 'Unexpected error category'
            return body
        assert response.status_code==200,'HTTP '+str(response.status_code)+' for '+(operation or path)
        if path.startswith('/v2/media/'):
            assert response.headers.get('Content-Type','').startswith('image/jpeg'),'Invalid avatar MIME'
            return response.content
        return response.json()
    def action(i, operation, expected_error=None, **data):
        return request('/v2/action',users[i],{'action':operation,'data':data},expected_error=expected_error,operation=operation)
    def passed(name):print('PASS '+name,flush=True)
    try:
        health=request('/v2/health');assert health.get('protocol',0)>=5,'Unsupported protocol'
        for _ in range(2):
            user=request('/v2/register',payload={});assert user.get('id') and user.get('token'),'Registration failed';users.append(user)
        prefix='T20_'+secrets.token_hex(3)
        for i in range(2):action(i,'name',name=prefix+str(i))
        stats={'stats_runs':12,'stats_victories':3,'stats_enemies_defeated':40,'stats_playtime_seconds':78.5,'level':2}
        action(0,'profile_stats',stats=stats)
        profile=action(1,'profile_lookup',id=users[0]['id'])['profile'];assert 'stats' not in profile,'Stranger stats leaked'
        action(1,'chat_preferences',requests=False)
        action(0,'friend_add',expected_error='requests_disabled',name=prefix+'1')
        action(1,'chat_preferences',requests=True)
        action(0,'friend_add',name=prefix+'1');action(1,'friend_accept',id=users[0]['id'])
        action(0,'chat_preferences',status='Проверка EN/RU',online=False)
        profile=action(1,'profile_lookup',id=users[0]['id'])['profile']
        assert profile['status']=='Проверка EN/RU' and not profile['online'],'Status privacy broken'
        assert profile['stats']==stats and profile['stats_source']=='device','Stats contract broken'
        action(1,'profile_stats',expected_error='invalid_stats',id=users[0]['id'],stats=stats)
        passed('friend requests, public status, online privacy and scoped device statistics')
        action(1,'chat_preferences',writes='nobody',invites='nobody')
        action(0,'chat_send',expected_error='messages_disabled',friend=users[1]['id'],text='blocked',nonce=secrets.token_hex(8))
        action(0,'create');action(0,'invite',expected_error='invites_disabled',id=users[1]['id'])
        action(1,'chat_preferences',writes='friends',invites='friends',receipts=False)
        sent=action(0,'chat_send',friend=users[1]['id'],text='Привет — real HTTP',nonce=secrets.token_hex(8))['message'];mid=sent['id'];messages.append(mid)
        fetched=action(1,'chat_fetch',friend=users[0]['id'])['messages'];assert any(m['id']==mid and m['text']==sent['text'] for m in fetched),'Message not delivered'
        action(1,'chat_mark_all_read');fetched=action(0,'chat_fetch',friend=users[1]['id'])['messages'];received=next(m for m in fetched if m['id']==mid)
        assert received['delivered'] is not None and received['seen'] is None,'Receipt preference violated'
        action(1,'chat_preferences',receipts=True);action(1,'chat_mark_all_read')
        action(1,'chat_save',friend=users[0]['id'],message=mid,saved=True)
        assert any(m['id']==mid and m['saved'] for m in action(1,'chat_saved')['messages']),'Saved message missing'
        assert action(0,'chat_saved')['messages']==[],'Other participant save leaked'
        action(1,'chat_save',friend=users[0]['id'],message=mid,saved=False)
        assert action(1,'chat_saved')['messages']==[],'Unsave failed'
        passed('real text delivery, receipt settings, saved messages and server send/invite restrictions')
        image=io.BytesIO();Image.new('RGB',(64,64),'blue').save(image,'PNG')
        media=request('/v2/upload/photo',users[0],content=image.getvalue())['media']
        request('/v2/media/'+media,users[1],expected_error='unauthorized')
        action(0,'profile_avatar',media=media)
        assert action(1,'profile_lookup',id=users[0]['id'])['profile']['avatar_media']==media,'Profile avatar absent'
        request('/v2/media/'+media,users[1]);action(0,'profile_avatar',media=None)
        request('/v2/media/'+media,users[1],expected_error='unauthorized')
        passed('actual PNG upload, explicit avatar sharing and revoked download')
        room=action(0,'poll')['room'];assert room and room['code'],'Room creation failed'
        action(1,'join',code=room['code']);action(0,'start_match')
        recent=action(0,'recent_players')['players'];assert any(p['id']==users[1]['id'] for p in recent),'Recent match participant missing'
        ranking=action(0,'my_ranking')['ranking'];assert ranking['participating'] is False and ranking['position'] is None,'New account fabricated rank'
        action(0,'my_ranking',expected_error='invalid_request',uid=users[1]['id'])
        passed('actual room start history and authenticated ranking absence/identity rejection')
        action(0,'name_rename',name=prefix+'R');action(0,'name_rename',expected_error='rename_cooldown',name=prefix+'X')
        passed('confirmed name rename and server cooldown')
        print('PASS social20 live HTTP: two accounts; no claim of phone/FCM/WSS verification',flush=True)
    finally:
        for mid in messages:
            try:action(0,'chat_delete',friend=users[1]['id'],message=mid)
            except Exception:print('Cleanup message failed; disposable account only',flush=True)
        for i in range(len(users)):
            for name,data in [('profile_avatar',{'media':None}),('leave',{})]:
                try:action(i,name,**data)
                except Exception:print('Cleanup '+name+' failed for disposable account',flush=True)
        if len(users)==2:
            try:action(0,'friend_remove',id=users[1]['id'])
            except Exception:print('Cleanup friendship failed for disposable accounts',flush=True)
        session.close()
        print('Cleanup attempted: rooms, friendship, messages, avatar sharing; guest identities retained (no account-delete API)',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);args=parser.parse_args()
    try:check(args.url)
    except Exception as exc:
        # Never include request headers/body or tokens in diagnostics.
        print('FAIL social20 '+type(exc).__name__+': '+str(exc),flush=True)
        raise SystemExit(1)
