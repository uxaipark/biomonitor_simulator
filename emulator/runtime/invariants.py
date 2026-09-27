"""월드 불변식 검사 — 시나리오 로직이 조용히 어긋나는 것을 잡는다.

2026-09-27 에 이동 마지막 단계 뒤 병실 복귀가 한 번도 불리지 않아 입원 환자 77 % 가 복도에 방치된 채 며칠을 돈 적이 있다.
그런 오류는 개별 단위 테스트로는 안 잡히고, 아래처럼 '지금 상태가 말이 되는가'를 주기적으로 확인해야 드러난다.
같은 검사를 (1) 월드가 60 시뮬초마다 스스로 돌려 위반을 로그·카운터로 남기고, (2) tools/world_sim.py 와
tests/test_world_scenarios.py 가 가속 시뮬레이션 뒤에 돌린다.
"""
from __future__ import annotations

import collections
import time


def check(w) -> dict:
    """위반 목록을 돌려준다. 각 항목: kind, count, sample(최대 5), detail."""
    h, now = w.hospital, w.sim_time
    G = w.st.gw.arr
    viol: list[dict] = []

    def add(kind: str, items: list, detail: str = ""):
        if items:
            viol.append({"kind": kind, "count": len(items), "sample": items[:5], "detail": detail})

    admitted = list(w.admitted.items())
    # 1. 이동이 끝난 입원 환자는 자기 병실에 있고, 이동 메모·단계가 비어 있다
    stuck, stale = [], []
    for pid, rec in admitted:
        if rec["outpatient"]:
            continue
        moving = bool(rec["trip"]) or rec["trip_step_until"] > now
        if not moving:
            if rec["room_idx"] >= 0 and rec["location"] != rec["room_idx"]:
                stuck.append((pid, h.rooms[rec["location"]]["id"] if rec["location"] >= 0 else None, rec.get("note")))
            if rec.get("stage_label") or rec.get("trip_plan"):
                stale.append((pid, rec.get("stage_label")))
    add("idle_patient_away_from_bed", stuck, "이동 중이 아닌 입원 환자가 병실 밖에 있음")
    add("trip_state_not_cleared", stale, "이동이 끝났는데 단계 표시가 남아 있음")
    # 2. 게이트웨이 연결 수 = 실제 연결 환자 수, 용량 이하, 다운 게이트웨이에 연결 없음
    linked = collections.Counter(rec["gw"] for _, rec in admitted if rec["gw"] >= 0)
    mismatch, over, on_down = [], [], []
    for gw, g in enumerate(h.gateways):
        n_state = w.gw_state[gw]["n_conn"]
        if n_state != linked.get(gw, 0) or int(G["n_conn"][gw]) != min(255, n_state):
            mismatch.append((g["id"], n_state, linked.get(gw, 0), int(G["n_conn"][gw])))
        if n_state > g["capacity"]:
            over.append((g["id"], n_state, g["capacity"]))
        if int(G["status"][gw]) == 2 and linked.get(gw, 0):
            on_down.append((g["id"], linked.get(gw, 0)))
    add("gateway_conn_count_mismatch", mismatch, "gw_state.n_conn · 공유 배열 · 실제 연결 환자 수가 다름")
    add("gateway_over_capacity", over, "연결 수가 용량을 넘음")
    add("patients_on_down_gateway", on_down, "다운(2) 게이트웨이에 환자가 연결돼 있음 (주기 재연결이 처리해야 함)")
    # 3. 병상: 입원 환자마다 병상 하나, 병상은 한 명만, 병상 표기가 환자와 일치
    bed_owner: dict[int, list] = collections.defaultdict(list)
    bad_bed = []
    for pid, rec in admitted:
        if rec["outpatient"]:
            continue
        b = rec["bed_idx"]
        if b < 0:
            bad_bed.append((pid, "병상 없음"))
            continue
        bed_owner[b].append(pid)
        if h.beds[b]["patient_id"] != pid:
            bad_bed.append((pid, h.beds[b]["id"], h.beds[b]["patient_id"]))
    add("bed_assignment_mismatch", bad_bed, "병상의 patient_id 가 입원 기록과 다름")
    add("bed_shared", [(h.beds[b]["id"], ps) for b, ps in bed_owner.items() if len(ps) > 1], "한 병상에 두 명")
    # 4. 패치: 입원 행마다 활성 패치, 시리얼 중복 없음, 레지스트리 상태 일치
    serials = collections.Counter(p.serial for p in w.patches.values())
    add("patch_serial_duplicate", [s for s, n in serials.items() if n > 1])
    no_patch = [(pid, rec["row"]) for pid, rec in admitted if rec["row"] not in w.patches]
    add("admitted_without_patch", no_patch)
    orphan = [row for row in w.patches if row not in {rec["row"] for _, rec in admitted}]
    add("patch_without_patient", orphan, "입원 기록이 없는 행에 활성 패치")
    reg_bad = [p.serial for p in w.patches.values() if w.patch_registry.get(p.serial, {}).get("status") != "active"]
    add("patch_registry_status_mismatch", reg_bad)
    # 5. 처방·공유 배열
    add("admitted_without_prescription", [pid for pid, rec in admitted if not rec.get("rx")])
    P = w.st.patch.arr
    inactive = [(pid, rec["row"]) for pid, rec in admitted if int(P["active"][rec["row"]]) != 1]
    add("admitted_row_inactive", inactive, "입원 환자의 공유 배열 active 가 0")
    ghost = [int(r) for r in range(len(P)) if int(P["active"][r]) == 1 and r not in {rec["row"] for _, rec in admitted}]
    add("active_row_without_patient", ghost)
    # 6. 정답 라벨: 열린 구간은 재원 환자·존재하는 게이트웨이만
    real = getattr(w, "real", None)
    if real is not None:
        alive = {pid for pid, _ in admitted}
        open_bad = [k for k, o in real.labels_open.items() if o.get("patient_id") is not None and o["patient_id"] not in alive]
        add("open_label_for_discharged", open_bad)
    moving = sum(1 for _, rec in admitted if not rec["outpatient"] and (rec["trip"] or rec["trip_step_until"] > now))
    n_in = sum(1 for _, rec in admitted if not rec["outpatient"])
    return {"ok": not viol, "violations": viol, "checked_sim_time": now, "checked_at": time.time(),
            "stats": {"inpatients": n_in, "outpatients": len(admitted) - n_in, "moving": moving, "linked": sum(linked.values()),
                      "moving_pct": round(100.0 * moving / n_in, 1) if n_in else 0.0}}
