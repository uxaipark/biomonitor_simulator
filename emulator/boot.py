"""에뮬레이터 재가동 알림 → 라우터 초기화 (2026-10-16 운영자 요청).

에뮬레이터 프로세스가 새로 뜰 때마다(배포·서비스 재시작·장비 재부팅) 환자번호·패치 번호·패킷 번호가 모두 새로 시작한다.
라우터가 이전 기동의 데이터(원격 백업·로컬 백업 포함)를 그대로 들고 있으면 새 데이터와 섞이므로, 라우터는 정상 동작을 멈추고
가동 초기화(원격·로컬 백업 삭제 포함)를 한 뒤 다시 시작해야 한다.

에뮬레이터가 하는 일:
1. 기동마다 새 boot_id 를 만든다.
2. 라우터에 알린다 — 채팅 시스템 메시지, 웹훅(transport.router_reset.notify_url 이 있으면 POST), 모든 게이트웨이 META 의 emulator_boot.
   라우터는 GET /api/v1/boot 를 주기적으로 읽어도 된다.
3. 라우터가 초기화를 마치고 POST /api/v1/boot/ack {boot_id} 를 보낼 때까지 송출을 시작하지 않는다 (require_ack).
   ack_timeout_s 안에 확인이 없으면 경고를 남기고 송출을 시작한다 (라우터가 꺼져 있어도 에뮬레이터는 돌아야 한다).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import uuid

BOOT: dict = {"id": uuid.uuid4().hex[:16], "at": time.time(), "state": "starting", "ack": None, "notified": [], "waited_s": None}
_ack_event = threading.Event()


def info() -> dict:
    d = dict(BOOT)
    d["at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(BOOT["at"]))
    return d


def meta_field() -> dict:
    """게이트웨이 META 에 싣는 기동 표지: 라우터는 이 id 가 바뀌면 재가동으로 본다."""
    return {"id": BOOT["id"], "at": int(BOOT["at"] * 1000)}


def notify(cfg, chat, emulator_url: str) -> None:
    """재가동을 라우터에 알린다 (채팅 + 웹훅).  실패해도 에뮬레이터 기동은 막지 않는다."""
    rr = (cfg.get("transport", "router_reset", default={}) or {})
    need = bool(rr.get("require_ack", True))
    text = (f"[에뮬레이터 재가동] boot_id={BOOT['id']} · {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(BOOT['at']))}. "
            f"환자번호·패치 번호·패킷 번호가 모두 새로 시작합니다. 라우터는 정상 동작을 멈추고 가동 초기화(원격 백업·로컬 백업 삭제 포함)를 한 뒤 "
            f"POST {emulator_url}/api/v1/boot/ack {{\"boot_id\": \"{BOOT['id']}\"}} 를 보내 주세요."
            + (f" 확인을 받을 때까지 송출을 시작하지 않습니다 (최대 {int(rr.get('ack_timeout_s', 120))}초)." if need else " (확인 없이 바로 송출합니다)"))
    try:
        chat.post("rp5-1", text, kind="system")
        BOOT["notified"].append({"via": "chat", "t": time.time(), "ok": True})
    except Exception as e:
        BOOT["notified"].append({"via": "chat", "t": time.time(), "ok": False, "error": str(e)})
    url = str(rr.get("notify_url") or "").strip()
    if url:
        body = json.dumps({"event": "emulator_restart", "boot_id": BOOT["id"], "boot_at_ms": int(BOOT["at"] * 1000),
                           "ack_url": f"{emulator_url}/api/v1/boot/ack", "require_ack": need,
                           "action": "stop normal operation, reset (delete remote and local backups), then POST ack"}).encode("utf-8")
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=3) as r:
                    BOOT["notified"].append({"via": "webhook", "t": time.time(), "ok": True, "status": r.status})
                break
            except Exception as e:
                BOOT["notified"].append({"via": "webhook", "t": time.time(), "ok": False, "error": str(e)[:200]})
                time.sleep(2 * (attempt + 1))


def ack(boot_id: str, body: dict) -> dict:
    if boot_id != BOOT["id"]:
        raise ValueError(f"boot_id 가 다릅니다 (지금 기동은 {BOOT['id']}) — 이전 기동에 대한 확인입니다")
    BOOT["ack"] = {"t": time.time(), "by": body.get("by"), "remote_backup_deleted": body.get("remote_backup_deleted"),
                   "local_backup_deleted": body.get("local_backup_deleted"), "note": body.get("note")}
    BOOT["state"] = "acked"
    _ack_event.set()
    return info()


def wait_for_ack(cfg) -> str:
    """require_ack 이면 라우터 확인 또는 시간 초과까지 기다린다.  반환: acked | timeout | not_required | override."""
    rr = (cfg.get("transport", "router_reset", default={}) or {})
    if not rr.get("enabled", True) or not rr.get("require_ack", True):
        BOOT["state"] = "not_required"
        return "not_required"
    BOOT["state"] = "waiting_ack"
    t0 = time.time()
    tmo = rr.get("ack_timeout_s")
    got = _ack_event.wait(timeout=float(120 if tmo is None else tmo))     # 0 = 기다리지 않음 (예전 'or 120' 은 0 을 120 으로 바꿨다)
    BOOT["waited_s"] = round(time.time() - t0, 1)
    if BOOT["state"] == "override":
        return "override"
    if got:
        return "acked"
    BOOT["state"] = "timeout"
    return "timeout"


def override(by: str = "manual") -> None:
    """사람이 확인을 기다리지 않고 송출을 시작했다 (시작 버튼)."""
    if BOOT["state"] == "waiting_ack":
        BOOT["state"] = "override"
        BOOT["override_by"] = by
        _ack_event.set()
