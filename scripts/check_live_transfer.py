"""Two newly created real legacy guests migrate to the dedicated test service.

Does not change deployments or export another player's data. Never logs tokens.
"""
import requests
import uuid
import time

OLD='https://dodge-server-uf4f.onrender.com'
NEW='https://dodge-supabase-18.onrender.com'


def main():
    with requests.Session() as session:
        # Wake the Free legacy instance using safe GETs before creating users.
        deadline=time.monotonic()+120
        while True:
            try:
                response=session.get(OLD+'/v2/health',timeout=(10,20))
                response.raise_for_status()
                assert response.json().get('ok') is True
                break
            except requests.RequestException:
                if time.monotonic()>=deadline:raise
                print('Waiting for legacy health; no account mutation retried',flush=True)
        def post(base,path,body,user=None):
            response=session.post(base+path,json=body,timeout=(10,30),
                headers={'Authorization':'Bearer '+user['token']} if user else {})
            result=response.json()
            assert response.status_code==200,(path,response.status_code,result.get('error'))
            return result
        def act(base,user,action,**data):
            return post(base,'/v2/action',{'action':action,'data':data},user)
        users=[post(OLD,'/v2/register',{}) for _ in range(2)]
        a,b=users
        act(OLD,a,'friend_add',name=b['name']);act(OLD,b,'friend_accept',id=a['id'])
        for user in users:
            result=post(NEW,'/v2/migrate',{'id':user['id']},user)
            assert result['id']==user['id'] and result['name']==user['name']
        for user,friend in [(a,b),(b,a)]:
            result=act(NEW,user,'poll')
            assert result['profile']['id']==user['id']
            assert [f['id'] for f in result['friends']]==[friend['id']]
            post(NEW,'/v2/migrate',{'id':user['id']},user)
        first=act(NEW,a,'chat_send',friend=b['id'],text='Перенос аккаунта ❤️',nonce=uuid.uuid4().hex)
        received=act(NEW,b,'chat_fetch',friend=a['id'])
        assert received['messages'][0]['id']==first['message']['id']
        assert received['messages'][0]['text']=='Перенос аккаунта ❤️'
    print('PASS: two real legacy accounts retained IDs, names, original tokens and friendship; private chat delivered after transfer')


if __name__=='__main__':main()
