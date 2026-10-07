"""Dodge the Enemies lightweight online leaderboard server.

Run on a PC/VPS:
    py -3.13 online_server.py

Default: http://0.0.0.0:8765
The game client uses DODGE_API_URL, e.g. on another device in the same Wi-Fi:
    DODGE_API_URL=http://192.168.1.50:8765

For public internet play, deploy this file behind HTTPS/reverse proxy on a server.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import json
import sqlite3
from database import connect
import datetime
import os
import threading
import time
from contextlib import contextmanager
from social import Social, SocialError
from multiplayer_ws import MatchHub, websocket_loop
from admin_api import authorize as admin_authorize, get as admin_get, action as admin_action, verify_startup, AdminError

HOST = os.environ.get("DODGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("DODGE_PORT", "8765")))
DB_FILE = os.environ.get("DODGE_DB", "leaderboard.db")
DB_LOCK = threading.Lock()
SOCIAL = None
MATCHES = MatchHub()
PUSH = None


def month_key(dt=None):
    dt = dt or datetime.datetime.now(datetime.timezone.utc)
    return dt.strftime("%Y-%m")


@contextmanager
def db():
    conn = connect(DB_FILE, timeout=10)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with DB_LOCK, db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scores (
            uid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            rank TEXT NOT NULL DEFAULT '-',
            rank_index INTEGER NOT NULL DEFAULT -1,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monthly_rewards (
            month TEXT NOT NULL,
            uid TEXT NOT NULL,
            name TEXT NOT NULL,
            place INTEGER NOT NULL,
            reward INTEGER NOT NULL,
            claimed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (month, uid)
        );
        """)
        row = con.execute("SELECT value FROM meta WHERE key='current_month'").fetchone()
        if not row:
            con.execute("INSERT INTO meta(key,value) VALUES('current_month',?)", (month_key(),))


def reward_for_place(place):
    if place == 1:
        return 2000
    if place == 2:
        return 1500
    if place == 3:
        return 1000
    if 4 <= place <= 100:
        return 700
    return 0


def rollover_if_needed():
    current = month_key()
    with DB_LOCK, db() as con:
        row = con.execute("SELECT value FROM meta WHERE key='current_month'").fetchone()
        stored = row["value"] if row else current
        if stored == current:
            return

        players = con.execute(
            "SELECT uid,name,score,rank,rank_index FROM scores "
            "ORDER BY rank_index DESC, score DESC, updated_at ASC, uid ASC LIMIT 100"
        ).fetchall()
        for place, player in enumerate(players, 1):
            reward = reward_for_place(place)
            if reward:
                con.execute(
                    "INSERT INTO monthly_rewards(month,uid,name,place,reward,claimed) VALUES(?,?,?,?,?,0) ON CONFLICT DO NOTHING",
                    (stored, player["uid"], player["name"], place, reward),
                )
        con.execute("DELETE FROM scores")
        con.execute("INSERT INTO meta(key,value) VALUES('current_month',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (current,))


def leaderboard(limit=100):
    rollover_if_needed()
    limit = max(1, min(100, int(limit)))
    with DB_LOCK, db() as con:
        rows = con.execute(
            "SELECT uid,name,score,rank,rank_index FROM scores "
            "ORDER BY rank_index DESC, score DESC, updated_at ASC, uid ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def personal_ranking(uid, connection=None):
    """Read the existing monthly scoreboard for the authenticated social UID.

    Never accept a client-provided device UID as an identity mapping. Legacy
    submissions remain device-reported, not an authoritative anti-cheat score.
    """
    if connection is not None:
        # Social.dispatch already owns the PostgreSQL advisory transaction lock.
        # A second DB connection would block on that same lock until timeout.
        return _personal_ranking_read(uid,connection)
    with DB_LOCK, db() as con:
        return _personal_ranking_read(uid,con)


def _personal_ranking_read(uid,con):
    month=con.execute("SELECT value FROM meta WHERE key='current_month'").fetchone()
    month=month[0] if month else month_key()
    # Read-only: avoid serving the previous month's position during rollover.
    if month!=month_key():return {'position':None,'participating':False,'month':month_key(),'source':'legacy_leaderboard'}
    row=con.execute('SELECT uid,score,rank,rank_index,updated_at FROM scores WHERE uid=?',(uid,)).fetchone()
    if not row:return {'position':None,'participating':False,'month':month,'source':'legacy_leaderboard'}
    ahead=con.execute('SELECT COUNT(*) FROM scores WHERE rank_index>? OR (rank_index=? AND score>?) OR (rank_index=? AND score=? AND updated_at<?) OR (rank_index=? AND score=? AND updated_at=? AND uid<?)',(row['rank_index'],row['rank_index'],row['score'],row['rank_index'],row['score'],row['updated_at'],row['rank_index'],row['score'],row['updated_at'],uid)).fetchone()[0]
    return {'position':ahead+1,'participating':True,'month':month,'score':row['score'],'rank':row['rank'],'rank_index':row['rank_index'],'source':'legacy_leaderboard'}


def submit(payload):
    rollover_if_needed()
    uid = str(payload.get("uid", ""))[:80].strip()
    name = str(payload.get("name", "PLAYER"))[:16].strip() or "PLAYER"
    score = max(0, min(999999, int(payload.get("score", 0))))
    rank = str(payload.get("rank", "-"))[:20]
    rank_index = max(-1, min(100, int(payload.get("rank_index", -1))))
    if not uid:
        raise ValueError("uid required")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    with DB_LOCK, db() as con:
        old = con.execute("SELECT score,rank_index FROM scores WHERE uid=?", (uid,)).fetchone()
        # Keep the strongest result: higher rank first, then higher survival time.
        should_update = old is None or rank_index > old["rank_index"] or (
            rank_index == old["rank_index"] and score >= old["score"]
        )
        if should_update:
            con.execute(
                "INSERT INTO scores(uid,name,score,rank,rank_index,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET name=excluded.name,score=excluded.score,rank=excluded.rank,rank_index=excluded.rank_index,updated_at=excluded.updated_at",
                (uid, name, score, rank, rank_index, now),
            )
        else:
            con.execute("UPDATE scores SET name=?,updated_at=? WHERE uid=?", (name, now, uid))
    return {"ok": True, "month": month_key()}


def claim(payload):
    rollover_if_needed()
    uid = str(payload.get("uid", ""))[:80].strip()
    if not uid:
        raise ValueError("uid required")
    with DB_LOCK, db() as con:
        row = con.execute(
            "SELECT month,place,reward,claimed FROM monthly_rewards WHERE uid=? ORDER BY month DESC LIMIT 1",
            (uid,),
        ).fetchone()
        if not row:
            return {"ok": True, "reward": 0, "message": "NO REWARD"}
        if row["claimed"]:
            return {"ok": True, "reward": 0, "month": row["month"], "message": "ALREADY CLAIMED"}
        con.execute("UPDATE monthly_rewards SET claimed=1 WHERE month=? AND uid=?", (row["month"], uid))
        return {
            "ok": True,
            "reward": int(row["reward"]),
            "place": int(row["place"]),
            "month": row["month"],
            "message": "CLAIMED",
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "DodgeLeaderboard/1.0"

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if not urlparse(self.path).path.startswith('/v2/admin/'):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith('/v2/admin/'):
                admin_authorize(self.headers)
                return self.send_json(200, admin_get(parsed.path, parse_qs(parsed.query), SOCIAL, MATCHES))
            if parsed.path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
                websocket_loop(self, MATCHES, SOCIAL)
                return
            if parsed.path.startswith('/v2/media/'):
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise SocialError('unauthorized')
                with SOCIAL.lock:
                    uid=SOCIAL.authenticate(auth[7:]);mime,content=SOCIAL.chat.download(uid,parsed.path.rsplit('/',1)[-1])
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(content);return
            if parsed.path == "/v2/health":
                return self.send_json(200, {"ok": True, "service": "dodge-server", "protocol": 5, "chat_available": True, "streak_timezone": "Asia/Qyzylorda", "push_available": bool(PUSH and PUSH.configured), "match_available": True, "combat_features": ["boomerang", "reflect"], "combat_revision": 10801, "websocket": "/ws", "reconnect_grace": 20})
            if parsed.path == "/health":
                rollover_if_needed()
                return self.send_json(200, {"ok": True, "month": month_key()})
            if parsed.path == "/leaderboard":
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", [100])[0])
                return self.send_json(200, {"ok": True, "month": month_key(), "players": leaderboard(limit)})
            return self.send_json(404, {"ok": False, "error": "not found"})
        except AdminError as e:
            self.send_json(e.status, {'ok': False, 'error': str(e)})
        except SocialError as e:
            self.send_json(401 if str(e)=='unauthorized' else 409,{'ok':False,'error':str(e)})
        except Exception as e:
            import logging
            logging.error('http_error type=%s',type(e).__name__)
            self.send_json(500, {'ok':False,'error':'server_error'})

    def do_POST(self):
        try:
            parsed=urlparse(self.path)
            if parsed.path.startswith('/v2/admin/'):
                admin_authorize(self.headers)
                if parsed.path != '/v2/admin/action':
                    raise AdminError('admin_endpoint_not_found', 404)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise AdminError('invalid_admin_payload')
                if not self.headers.get('Content-Type', '').startswith('application/json'):
                    raise AdminError('json_required', 415)
                self.connection.settimeout(10)
                try:
                    payload = json.loads(self.rfile.read(length).decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    raise AdminError('invalid_admin_payload') from None
                return self.send_json(200, admin_action(payload, SOCIAL, MATCHES))
            length=int(self.headers.get('Content-Length','0'))
            if parsed.path in ('/v2/upload/photo','/v2/upload/voice'):
                if not 0<length<=2097152:raise SocialError('media_too_large')
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise SocialError('unauthorized')
                with SOCIAL.lock,SOCIAL.db:
                    uid=SOCIAL.authenticate(auth[7:]);content=self.rfile.read(length)
                    if len(content)!=length:raise SocialError('invalid_media')
                    result=dict(ok=True,**SOCIAL.chat.upload(uid,parsed.path.rsplit('/',1)[-1],content))
                return self.send_json(200,result)
            if not 0<=length<=65536:return self.send_json(413,{'ok':False,'error':'request_too_large'})
            if length < 0:
                return self.send_json(400, {"ok": False, "error": "invalid length"})
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(payload,dict):raise SocialError('invalid_request')
            parsed = urlparse(self.path)
            if parsed.path == '/v2/account':
                auth=self.headers.get('Authorization','')
                token=auth[7:] if auth.startswith('Bearer ') else ''
                return self.send_json(200,SOCIAL.accounts.dispatch(payload.get('action'),payload.get('data',{}),token,self.client_address[0]))
            if parsed.path == "/v2/register":
                with SOCIAL.lock:
                    return self.send_json(200, SOCIAL.register())
            if parsed.path == '/v2/migrate':
                from legacy import migrate
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise SocialError('unauthorized')
                return self.send_json(200,migrate(SOCIAL,auth[7:],payload.get('id')))
            if parsed.path == '/v2/push':
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '):raise SocialError('unauthorized')
                with SOCIAL.lock:
                    uid=SOCIAL.authenticate(auth[7:])
                    if not PUSH or not PUSH.configured:raise SocialError('push_not_configured')
                    PUSH.register(uid,payload.get('token'))
                return self.send_json(200,{'ok':True})
            if parsed.path == "/v2/action":
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    raise SocialError("unauthorized")
                action, data = payload.get("action"), payload.get("data", {})
                if not isinstance(action, str) or not isinstance(data, dict):
                    return self.send_json(400, {"ok": False, "error": "invalid request"})
                return self.send_json(200, SOCIAL.dispatch(auth[7:], action, data))
            if parsed.path == "/submit":
                return self.send_json(200, submit(payload))
            if parsed.path == "/claim":
                return self.send_json(200, claim(payload))
            return self.send_json(404, {"ok": False, "error": "not found"})
        except AdminError as e:
            self.send_json(e.status, {'ok': False, 'error': str(e)})
        except SocialError as e:
            self.send_json(401 if str(e) == "unauthorized" else 409, {"ok": False, "error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            import logging
            logging.error('http_error type=%s',type(e).__name__)
            self.send_json(500, {'ok':False,'error':'server_error'})

    def log_message(self, fmt, *args):
        import re
        message=re.sub(r"([?&]token=)[^&\s]+",r"\1[redacted]",fmt % args)
        print("[%s] %s" % (self.log_date_time_string(),message),flush=True)


if __name__ == "__main__":
    init_db()
    SOCIAL = Social(os.environ.get("DODGE_SOCIAL_DB", "social.db"))
    from push import Push
    PUSH=Push(SOCIAL);PUSH.start()
    print(f"Dodge leaderboard server: http://{HOST}:{PORT}")
    print("Top 100 monthly rewards: #1=2000, #2=1500, #3=1000, #4-100=700")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=verify_startup, args=(httpd.server_port,), daemon=True).start()
    httpd.serve_forever()
