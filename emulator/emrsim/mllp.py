"""MLLP (HL7 v2 over TCP) 수신 서버와 ADT 푸시.

수신: 0.0.0.0:2575 한 포트로 HL7 v2 기관 6곳을 받는다.  MSH-6(수신 기관) 으로 기관을 고르고, 그 기관의 문자셋으로 디코딩한다.
      프레임은 0x0B <메시지> 0x1C 0x0D.  한 연결에서 여러 메시지를 연달아 보낼 수 있다(각각 ACK).
푸시: POST /api/v1/emrsim/{site}/push {host, port} → 해당 기관의 ADT 이벤트를 MLLP 로 순서대로 보내고 ACK(AA/CA) 를 기다린다.
"""
from __future__ import annotations

import socket
import socketserver
import threading
import time

from . import hl7v2
from .sim import all_sims

_server = None


def _site_for(fac: str, app: str):
    for sim in all_sims():
        if sim.site["protocol"] == "hl7v2" and (sim.site["fac"] == fac or (not fac and sim.site["app"] == app)):
            return sim
    return None


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(300)
        buf = b""
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        while True:
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, OSError):
                return
            if not chunk:
                return
            buf += chunk
            while True:
                s = buf.find(hl7v2.SB)
                e = buf.find(hl7v2.EB + hl7v2.CR, s + 1)
                if s < 0 or e < 0:
                    if s < 0 and len(buf) > 0 and hl7v2.SB not in buf:
                        buf = b""                               # 프레임 밖 쓰레기는 버린다
                    break
                raw, buf = buf[s + 1:e], buf[e + 2:]
                app, fac = hl7v2.peek_facility(raw)
                sim = _site_for(fac, app)
                if sim is None:
                    nack = ("MSH|^~\\&|EMRSIM|EMRSIM|||" + time.strftime("%Y%m%d%H%M%S") + "||ACK|NACK" + str(int(time.time())) + "|P|2.5\r"
                            f"MSA|AR||Unknown receiving facility '{fac}'\rERR||MSH^1^6|204^Unknown key identifier^HL70357|E\r")
                    try:
                        sock.sendall(hl7v2.frame(nack, "ascii"))
                    except OSError:
                        return
                    continue
                f = sim.faults
                if f.get("down"):
                    sim.add_log("in", "mllp", "장애 주입(down): 연결 끊음", "DROP", "MLLP", "", peer)
                    return
                if f.get("latency_ms"):
                    time.sleep(int(f["latency_ms"]) / 1000)
                ackmsg, code, summ, n = hl7v2.process_inbound(sim, raw, "mllp")
                sim.add_log("in", "mllp", summ, code, "MLLP", f"tcp/{_server.server_address[1] if _server else ''}", peer, hl7v2.decode(sim, raw)[:1500])
                try:
                    sock.sendall(hl7v2.frame(ackmsg, hl7v2.charset(sim)))
                except OSError:
                    return


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port: int = 2575) -> str:
    global _server
    if _server is not None:
        return "already running"
    try:
        _server = _Server(("0.0.0.0", port), _Handler)
    except OSError as e:
        _server = None
        return f"MLLP 포트 {port} 열기 실패: {e}"
    threading.Thread(target=_server.serve_forever, daemon=True, name="emrsim-mllp").start()
    return f"MLLP listening on {port}"


def stop():
    global _server
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None


# ------------------------------------------------------------------ ADT 푸시
def _push_loop(sim, stop_ev: threading.Event):
    st = sim.push
    sock = None
    while not stop_ev.is_set():
        try:
            if sock is None:
                sock = socket.create_connection((st["host"], st["port"]), timeout=10)
                st["connected"] = True
                st["last_error"] = None
            evs = sim.events_since(st["cursor"], 50)
            if not evs:
                stop_ev.wait(2.0)
                continue
            for ev in evs:
                msg = hl7v2.adt(sim, ev)
                sock.sendall(hl7v2.frame(msg, hl7v2.charset(sim)))
                data = b""
                deadline = time.time() + 15
                while hl7v2.EB not in data and time.time() < deadline:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("peer closed before ACK")
                    data += chunk
                txt = data.strip(b"\x0b\x1c\r").decode(hl7v2.charset(sim), errors="replace")
                code = next((line.split("|")[1] for line in txt.replace("\n", "\r").split("\r") if line.startswith("MSA|")), "")
                sim.add_log("out", "mllp", f"ADT {ev['code']} seq {ev['seq']} → {st['host']}:{st['port']} ACK {code or '없음'}", code or "NOACK", "MLLP", f"{st['host']}:{st['port']}", "", msg[:1500])
                if code not in ("AA", "CA"):
                    st["last_error"] = f"seq {ev['seq']} ACK {code or '없음'}"
                    st["nack"] += 1
                    stop_ev.wait(10.0)                        # 거부되면 같은 이벤트를 10초 뒤 재전송(순서 보장)
                    break
                st["cursor"] = ev["seq"]
                st["sent"] += 1
        except (OSError, ConnectionError) as e:
            st["connected"] = False
            st["last_error"] = str(e)
            try:
                if sock:
                    sock.close()
            except OSError:
                pass
            sock = None
            stop_ev.wait(5.0)
    if sock:
        try:
            sock.close()
        except OSError:
            pass


def start_push(sim, host: str, port: int, since: int) -> dict:
    stop_push(sim)
    ev = threading.Event()
    sim.push = {"host": host, "port": port, "cursor": max(0, since), "sent": 0, "nack": 0, "connected": False, "last_error": None, "stop": ev}
    th = threading.Thread(target=_push_loop, args=(sim, ev), daemon=True, name=f"emrsim-push-{sim.id}")
    sim.push["thread"] = th
    th.start()
    sim.add_log("sys", "admin", f"ADT 푸시 시작 → {host}:{port} (seq {since} 이후)")
    return {k: v for k, v in sim.push.items() if k not in ("stop", "thread")}


def stop_push(sim) -> dict:
    if sim.push:
        sim.push["stop"].set()
        sim.add_log("sys", "admin", "ADT 푸시 중지")
        sim.push = None
    return {"push": None}
