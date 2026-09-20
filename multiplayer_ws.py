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

    def join(self, room_id, uid):
        with self.lock:
            room = self.rooms.setdefault(room_id, {
                "started": False, "updated": time.monotonic(),
                "players": {}, "enemies": [], "next_enemy": 0,
            })
            if uid not in room["players"] and len(room["players"]) >= 3:
                raise ValueError("room_full")
            room["players"].setdefault(uid, {"x": 0.5, "y": 0.5, "dir": "idle", "hp": 3, "kills": 0})
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
                room["started"] = True
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
            self._tick(room)
            return self.snapshot(room_id)

    def _tick(self, room):
        now = time.monotonic()
        dt = min(.2, max(0, now-room["updated"])); room["updated"] = now
        if not room["started"] or not room["players"]:
            return
        if now >= room["next_enemy"]:
            room["next_enemy"] = now + 1.4
            angle = random.random()*math.tau
            room["enemies"].append({"id": random.randrange(1_000_000_000), "x": .5+math.cos(angle)*.48, "y": .5+math.sin(angle)*.42, "hp": 1})
        targets = list(room["players"].values())
        for enemy in room["enemies"]:
            target = min(targets, key=lambda p: math.hypot(enemy["x"]-p["x"], enemy["y"]-p["y"]))
            dx,dy=target["x"]-enemy["x"],target["y"]-enemy["y"]
            dist=max(1e-5,math.hypot(dx,dy));enemy["x"]+=dx/dist*.10*dt;enemy["y"]+=dy/dist*.10*dt

    def snapshot(self, room_id):
        with self.lock:
            room=self.rooms.get(room_id)
            if not room:return {"ok":False,"error":"room_closed"}
            self._tick(room)
            return {"ok":True,"started":room["started"],"players":room["players"],"enemies":room["enemies"]}


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
    if not room_id or social.room_for(uid)[0] != room_id:
        raise ValueError("room_required")
    key=handler.headers.get("Sec-WebSocket-Key")
    if not key:raise ValueError("websocket_key_required")
    accept=base64.b64encode(hashlib.sha1((key+GUID).encode()).digest()).decode()
    handler.send_response(101,"Switching Protocols")
    handler.send_header("Upgrade","websocket");handler.send_header("Connection","Upgrade");handler.send_header("Sec-WebSocket-Accept",accept);handler.end_headers()
    conn=handler.connection;conn.settimeout(.10);hub.join(room_id,uid)
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

