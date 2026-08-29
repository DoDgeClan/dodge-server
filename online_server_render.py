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
import datetime
import os
import threading

HOST = os.environ.get("DODGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", os.environ.get("DODGE_PORT", "8765")))
DB_FILE = os.environ.get("DODGE_DB", "leaderboard.db")
DB_LOCK = threading.Lock()


def month_key(dt=None):
    dt = dt or datetime.datetime.utcnow()
    return dt.strftime("%Y-%m")


def db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


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
            "ORDER BY rank_index DESC, score DESC, updated_at ASC LIMIT 100"
        ).fetchall()
        for place, player in enumerate(players, 1):
            reward = reward_for_place(place)
            if reward:
                con.execute(
                    "INSERT OR IGNORE INTO monthly_rewards(month,uid,name,place,reward,claimed) VALUES(?,?,?,?,?,0)",
                    (stored, player["uid"], player["name"], place, reward),
                )
        con.execute("DELETE FROM scores")
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('current_month',?)", (current,))


def leaderboard(limit=100):
    rollover_if_needed()
    limit = max(1, min(100, int(limit)))
    with DB_LOCK, db() as con:
        rows = con.execute(
            "SELECT uid,name,score,rank,rank_index FROM scores "
            "ORDER BY rank_index DESC, score DESC, updated_at ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def submit(payload):
    rollover_if_needed()
    uid = str(payload.get("uid", ""))[:80].strip()
    name = str(payload.get("name", "PLAYER"))[:16].strip() or "PLAYER"
    score = max(0, min(999999, int(payload.get("score", 0))))
    rank = str(payload.get("rank", "-"))[:20]
    rank_index = max(-1, min(100, int(payload.get("rank_index", -1))))
    if not uid:
        raise ValueError("uid required")
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with DB_LOCK, db() as con:
        old = con.execute("SELECT score,rank_index FROM scores WHERE uid=?", (uid,)).fetchone()
        # Keep the strongest result: higher rank first, then higher survival time.
        should_update = old is None or rank_index > old["rank_index"] or (
            rank_index == old["rank_index"] and score >= old["score"]
        )
        if should_update:
            con.execute(
                "INSERT OR REPLACE INTO scores(uid,name,score,rank,rank_index,updated_at) VALUES(?,?,?,?,?,?)",
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
        self.send_header("Access-Control-Allow-Origin", "*")
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
            if parsed.path == "/health":
                rollover_if_needed()
                return self.send_json(200, {"ok": True, "month": month_key()})
            if parsed.path == "/leaderboard":
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", [100])[0])
                return self.send_json(200, {"ok": True, "month": month_key(), "players": leaderboard(limit)})
            return self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self.send_json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        try:
            length = min(65536, int(self.headers.get("Content-Length", "0")))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            parsed = urlparse(self.path)
            if parsed.path == "/submit":
                return self.send_json(200, submit(payload))
            if parsed.path == "/claim":
                return self.send_json(200, claim(payload))
            return self.send_json(404, {"ok": False, "error": "not found"})
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self.send_json(500, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


if __name__ == "__main__":
    init_db()
    print(f"Dodge leaderboard server: http://{HOST}:{PORT}")
    print("Top 100 monthly rewards: #1=2000, #2=1500, #3=1000, #4-100=700")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
