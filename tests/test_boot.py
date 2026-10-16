"""재가동 알림: 기동마다 새 boot_id, 라우터 확인(ack)까지 송출 대기, 시간 초과·수동 시작, 이전 기동 확인 거절."""
import threading
import time

import pytest

from emulator import boot


class Cfg:
    def __init__(self, **rr):
        self.rr = {"enabled": True, "require_ack": True, "ack_timeout_s": 2, "notify_url": "", **rr}

    def get(self, *path, default=None):
        return self.rr if path == ("transport", "router_reset") else default


class Chat:
    def __init__(self):
        self.msgs = []

    def post(self, sender, text, kind="msg"):
        self.msgs.append((sender, text, kind))


def _reset():
    boot._ack_event.clear()
    boot.BOOT.update(state="starting", ack=None, notified=[], waited_s=None)


def test_notify_posts_boot_id_and_ack_url():
    _reset()
    ch = Chat()
    boot.notify(Cfg(), ch, "http://192.168.0.125:5445")
    assert ch.msgs and boot.BOOT["id"] in ch.msgs[0][1] and ch.msgs[0][2] == "system"
    assert "백업" in ch.msgs[0][1]
    assert boot.meta_field()["id"] == boot.BOOT["id"]


def test_wait_until_router_acks():
    _reset()
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("how", boot.wait_for_ack(Cfg(ack_timeout_s=5))))
    th.start()
    time.sleep(0.2)
    assert boot.BOOT["state"] == "waiting_ack"
    with pytest.raises(ValueError):
        boot.ack("someoldboot", {})                       # 이전 기동에 대한 확인은 거절
    boot.ack(boot.BOOT["id"], {"by": "router", "remote_backup_deleted": True, "local_backup_deleted": True})
    th.join(2)
    assert res["how"] == "acked" and boot.BOOT["ack"]["remote_backup_deleted"] is True


def test_timeout_and_override_and_not_required():
    _reset()
    assert boot.wait_for_ack(Cfg(ack_timeout_s=0)) == "timeout"
    _reset()
    assert boot.wait_for_ack(Cfg(require_ack=False)) == "not_required"
    _reset()
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("how", boot.wait_for_ack(Cfg(ack_timeout_s=5))))
    th.start(); time.sleep(0.2)
    boot.override("test")
    th.join(2)
    assert res["how"] == "override"


def test_router_contract_post_then_poll_reset_done():
    """라우터 계약 (채팅 6912): POST /api/emu/restarted → 202, GET 의 reset.running=false · done_ms ≥ POST 시각이면 송출 시작."""
    import json as _j
    from http.server import BaseHTTPRequestHandler, HTTPServer
    state = {"posts": 0, "done_ms": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            state["posts"] += 1
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            threading.Timer(0.5, lambda: state.update(done_ms=int(time.time() * 1000))).start()   # 초기화에 0.5 초
            self.send_response(202); self.end_headers()

        def do_GET(self):
            b = _j.dumps({"reset": {"running": state["done_ms"] == 0 and state["posts"] > 0, "done_ms": state["done_ms"], "error": None}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _reset()
        boot.BOOT.pop("reset_post_ms", None)
        cfg = Cfg(ack_timeout_s=10, notify_url=f"http://127.0.0.1:{srv.server_port}/api/emu/restarted")
        boot.notify(cfg, Chat(), "http://x")
        assert state["posts"] == 1 and any(n["via"] == "router" and n["ok"] for n in boot.BOOT["notified"])
        t0 = time.time()
        assert boot.wait_for_ack(cfg) == "acked" and time.time() - t0 < 6
        assert boot.BOOT["ack"]["by"].startswith("router")
    finally:
        srv.shutdown()


def test_router_url_defaults_to_target_7300():
    class C2(Cfg):
        def get(self, *path, default=None):
            if path == ("transport", "target_ip"):
                return "192.168.0.91"
            return super().get(*path, default=default)
    assert boot.router_url(C2()) == "http://192.168.0.91:7300/api/emu/restarted"
