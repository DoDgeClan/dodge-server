"""Small dependency-free WebSocket match layer for the Render service.

The existing REST lobby remains unchanged. This module only owns transient
match state and a conservative text-frame WebSocket loop, so it can run in the
same ThreadingHTTPServer process without adding native dependencies.
"""
import base64
import hashlib
import json
import math
import random
import socket
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class MatchHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.rooms = {}

    def join(self, room_id, uid, host_uid=None, started=False):
        with self.lock:
            room = self.rooms.setdefault(room_id, {
                "started": False, "updated": time.monotonic(),
                "started_at": 0.0, "players": {}, "enemies": [], "next_enemy": 0,
                "ended": False, "victory": False, "reason": "", "host_uid": host_uid or uid,
            })
            if host_uid:
                room["host_uid"] = host_uid
            if uid not in room["players"] and len(room["players"]) >= 3:
                raise ValueError("room_full")
            room["players"].setdefault(uid, {"x": 0.5, "y": 0.5, "dir": "idle", "hp": 3, "max_hp": 3, "kills": 0, "invulnerable_until": 0.0})
            if started and not room["started"]:
                room["started"] = True
                room["started_at"] = time.monotonic()
                room["next_enemy"] = room["started_at"] + .8
            return self.snapshot(room_id)

    def leave(self, room_id, uid):
        with self.lock:
            room = self.rooms.get(room_id)
            if not room:
                return
            room["players"].pop(uid, None)
            if not room["players"]:
                self.rooms.pop(room_id, None)

    def action(self, room_id, uid, message):
        with self.lock:
            room = self.rooms.get(room_id)
            if not room or uid not in room["players"]:
                raise ValueError("not_in_match")
            player = room["players"][uid]
            action = message.get("action")
            if action == "start":
                if uid != room.get("host_uid"):
                    raise ValueError("host_only")
                if not room["started"]:
                    room["started"] = True
                    room["started_at"] = time.monotonic()
                    room["next_enemy"] = room["started_at"] + .8
            elif action == "input":
                for key in ("x", "y"):
                    if isinstance(message.get(key), (int, float)):
                        player[key] = max(0.04, min(0.96, float(message[key])))
                if isinstance(message.get("dir"), str):
                    player["dir"] = message["dir"][:8]
            elif action == "attack":
                killed = 0
                for enemy in room["enemies"][:]:
                    for other in room["players"].values():
                        if math.hypot(enemy["x"]-other["x"], enemy["y"]-other["y"]) <= .13:
                            room["enemies"].remove(enemy); killed += 1; break
                player["kills"] += killed
            elif action == "shield":
                player["invulnerable_until"] = max(player.get("invulnerable_until", 0.0), time.monotonic() + 3.0)
            elif action == "dash":
                player["invulnerable_until"] = max(player.get("invulnerable_until", 0.0), time.monotonic() + .4)
            self._tick(room)
            return self.snapshot(room_id)

    def _tick(self, room):
        now = time.monotonic()
        dt = min(.2, max(0, now-room["updated"])); room["updated"] = now
        if not room["started"] or room["ended"] or not room["players"]:
            return
        elapsed = max(0.0, now-room["started_at"])
        if elapsed >= 180:
            room["ended"] = True; room["victory"] = True; room["reason"] = "Team survived"
            return
        if now >= room["next_enemy"]:
            room["next_enemy"] = now + max(.65, 1.45-min(.55, elapsed/180))
            angle = random.random()*math.tau
            etype = "tank" if elapsed >= 45 and random.random() < .18 else "normal"
            hp = 3 if etype == "tank" else 1
            room["enemies"].append({"id": random.randrange(1_000_000_000), "x": .5+math.cos(angle)*.48, "y": .5+math.sin(angle)*.42, "type": etype, "hp": hp, "max_hp": hp, "dir": "idle"})
        targets = list(room["players"].values())
        for enemy in room["enemies"]:
            target = min(targets, key=lambda p: math.hypot(enemy["x"]-p["x"], enemy["y"]-p["y"]))
            dx,dy=target["x"]-enemy["x"],target["y"]-enemy["y"]
            dist=max(1e-5,math.hypot(dx,dy));speed=.075 if enemy["type"] == "tank" else .10;enemy["x"]+=dx/dist*speed*dt;enemy["y"]+=dy/dist*speed*dt
            if dist < .065 and now >= target.get("invulnerable_until", 0):
                target["hp"] = max(0, target["hp"]-1); target["invulnerable_until"] = now+.85
        if targets and all(p["hp"] <= 0 for p in targets):
            room["ended"] = True; room["reason"] = "The whole team was defeated"

    def snapshot(self, room_id):
        with self.lock:
            room=self.rooms.get(room_id)
            if not room:return {"ok":False,"error":"room_closed"}
            self._tick(room)
            elapsed=max(0.0,time.monotonic()-room["started_at"]) if room["started"] else 0.0
            return {"ok":True,"started":room["started"],"ended":room["ended"],"victory":room["victory"],"reason":room["reason"],"elapsed":int(elapsed),"wave":min(6,int(elapsed//30)+1),"players":room["players"],"enemies":room["enemies"]}


def _read_exact(conn, size):
    data=b""
    while len(data)<size:
        chunk=conn.recv(size-len(data))
        if not chunk: return None
        data+=chunk
    return data


def read_frame(conn):
    head=_read_exact(conn,2)
    if not head:return None
    first,second=head; opcode=first&15; masked=bool(second&128); length=second&127
    if length==126:length=struct.unpack("!H",_read_exact(conn,2))[0]
    elif length==127:length=struct.unpack("!Q",_read_exact(conn,8))[0]
    if length>65536:return None
    mask=_read_exact(conn,4) if masked else b""
    payload=_read_exact(conn,length) or b""
    if masked:payload=bytes(b ^ mask[i%4] for i,b in enumerate(payload))
    if opcode==8:return None
    if opcode==9:write_frame(conn,payload,10)
    return payload.decode("utf-8", "ignore") if opcode==1 else ""


def write_frame(conn, payload, opcode=1):
    if isinstance(payload,str):payload=payload.encode("utf-8")
    length=len(payload)
    head=bytes([0x80|opcode])
    if length<126:head+=bytes([length])
    elif length<65536:head+=bytes([126])+struct.pack("!H",length)
    else:head+=bytes([127])+struct.pack("!Q",length)
    conn.sendall(head+payload)


def websocket_loop(handler, hub, social):
    """Upgrade one HTTP request and run a match connection."""
    from urllib.parse import urlparse, parse_qs
    query=parse_qs(urlparse(handler.path).query)
    token=query.get("token",[""])[0];room_id=query.get("room",[""])[0]
    uid=social.authenticate(token)
    social_room_id, social_room = social.room_for(uid)
    if not room_id or social_room_id != room_id:
        raise ValueError("room_required")
    key=handler.headers.get("Sec-WebSocket-Key")
    if not key:raise ValueError("websocket_key_required")
    accept=base64.b64encode(hashlib.sha1((key+GUID).encode()).digest()).decode()
    handler.send_response(101,"Switching Protocols")
    handler.send_header("Upgrade","websocket");handler.send_header("Connection","Upgrade");handler.send_header("Sec-WebSocket-Accept",accept);handler.end_headers()
    conn=handler.connection;conn.settimeout(.10)
    hub.join(room_id, uid, host_uid=social_room.get('host'), started=bool(social_room.get('started')))
    try:
        while True:
            try:
                message=read_frame(conn)
            except socket.timeout:
                message=""
            if message is None:break
            if message:
                try:
                    hub.action(room_id,uid,json.loads(message))
                except (ValueError,TypeError):
                    pass
            write_frame(conn,json.dumps(hub.snapshot(room_id),separators=(",",":")))
    finally:
        hub.leave(room_id,uid)
