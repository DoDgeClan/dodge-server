"""Exercise an explicitly selected test deployment; creates three test guests.

No tokens or message bodies are logged. Leaves test records for persistence
verification. This checks HTTP/backend behavior, not Android UI or FCM.
"""
import io
import sys
import subprocess
import tempfile
from pathlib import Path

import requests
from PIL import Image


def main():
    base = sys.argv[1].rstrip('/')
    if base != 'https://dodge-supabase-18.onrender.com':
        raise SystemExit('Only the dedicated test deployment is allowed')
    health = requests.get(base + '/v2/health', timeout=60).json()
    assert health['protocol'] == 5 and health['chat_available']
    users = [requests.post(base + '/v2/register', json={}, timeout=30).json()
             for _ in range(3)]
    assert all('token' in u and 'id' in u for u in users)

    def act(n, action, error=None, **data):
        response = requests.post(base + '/v2/action',
            headers={'Authorization': 'Bearer ' + users[n]['token']},
            json={'action': action, 'data': data}, timeout=30)
        body = response.json()
        if error:
            assert body.get('error') == error, (action, response.status_code, body.get('error'))
        else:
            assert response.status_code == 200 and body.get('ok'), (action, response.status_code, body.get('error'))
        return body

    act(0, 'friend_add', name=users[1]['name'])
    act(1, 'friend_accept', id=users[0]['id'])
    act(1, 'chat_preferences', requests=False, receipts=False, language='ru')
    act(2, 'friend_add', error='requests_disabled', name=users[1]['name'])
    act(0, 'chat_preferences', style=49, language='en')
    first = act(0, 'chat_send', friend=users[1]['id'], nonce='live_text_0001', text='Проверка EN/RU ❤️')['message']
    duplicate = act(0, 'chat_send', friend=users[1]['id'], nonce='live_text_0001', text='Проверка EN/RU ❤️')['message']
    assert first['id'] == duplicate['id']
    incoming = act(1, 'chat_fetch', friend=users[0]['id'])
    assert incoming['messages'][0]['text'] == 'Проверка EN/RU ❤️'
    assert incoming['messages'][0]['style'] == 49
    assert incoming['streak']['timezone'] == 'Asia/Qyzylorda'
    act(1, 'chat_ack', friend=users[0]['id'], upto=first['id'])
    receipt = act(0, 'chat_fetch', friend=users[1]['id'])['messages'][0]
    assert receipt['delivered'] is not None and receipt['seen'] is None
    act(1, 'chat_send', friend=users[0]['id'], nonce='live_reply_0001', text='Reply', reply=first['id'])
    assert act(0, 'chat_fetch', friend=users[1]['id'])['streak']['today_complete']
    act(2, 'chat_fetch', error='friend_required', friend=users[0]['id'])
    act(0, 'chat_edit', friend=users[1]['id'], message=first['id'], text='Edited')
    act(1, 'chat_delete', error='owner_only', friend=users[0]['id'], message=first['id'])

    image = io.BytesIO()
    Image.new('RGB', (2400,1200), 'white').save(image, 'PNG')
    with tempfile.TemporaryDirectory() as directory:
        audio = Path(directory) / 'voice.m4a'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1',
                        '-c:a','aac','-y',str(audio)],check=True)
        for kind, content in [('photo', image.getvalue()), ('voice', audio.read_bytes())]:
            response = requests.post(base + '/v2/upload/' + kind, data=content,
                headers={'Authorization':'Bearer ' + users[0]['token']}, timeout=30)
            assert response.status_code == 200, (kind,response.status_code)
            identifier = response.json()['media']
            act(0, 'chat_send', friend=users[1]['id'], nonce='live_media_' + kind, kind=kind, text=identifier)
            for n, expected in [(0,200),(1,200),(2,401)]:
                download = requests.get(base + '/v2/media/' + identifier,
                    headers={'Authorization':'Bearer ' + users[n]['token']},timeout=30)
                assert download.status_code == expected, (kind,n,download.status_code)
                if n == 1:
                    if kind == 'photo':
                        assert Image.open(io.BytesIO(download.content)).size == (1280,640)
                    else:
                        assert download.content == content
    act(1,'chat_block',friend=users[0]['id'])
    act(0,'chat_fetch',error='blocked',friend=users[1]['id'])
    act(1,'chat_unblock',friend=users[0]['id'])
    act(0,'chat_fetch',friend=users[1]['id'])
    print('PASS: live HTTPS chat, three accounts, EN/RU, nonce, receipts, reply, style, streak, edit, private photo/voice, blocking')
    print('Push configured:', health['push_available'])


if __name__ == '__main__':
    main()
