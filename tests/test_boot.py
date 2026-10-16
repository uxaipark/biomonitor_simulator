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
    assert ch.msgs and boot.BOOT["id"] in ch.msgs[0][1] and "/api/v1/boot/ack" in ch.msgs[0][1] and ch.msgs[0][2] == "system"
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
