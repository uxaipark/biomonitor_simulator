"""현장 테스트: 웨어러블 패치 IoT 망을 설치한 뒤, 게이트웨이를 골라 그 게이트웨이의 환자에게 정해진 이벤트를 정해진
시간 동안 걸었다가 원래대로 돌린다.  그 게이트웨이를 보도록 설정한 파형 뷰어에 이벤트가 뜨는지, 알람 등급·경보음
볼륨이 맞는지를 현장에서 확인하는 용도다.

시간은 벽시계(time.time())로 잰다: 뷰어는 실제 초로 보므로 sim_speed 와 무관하게 '1분'은 1분이다.
환자·게이트웨이에 남기는 표시(ft_* · 게이트웨이 사유)는 테스트가 풀 때까지 유지되고, 시뮬 시각으로 풀리는 타이머
(리듬 에피소드·리드 오프)는 매 스텝 남은 시간만큼 다시 늘려 테스트보다 먼저 풀리지 않게 한다.
"""
from __future__ import annotations

import time

from .state import CTL

# prio: high(위급) · medium(주의) · low(권고·기술).  expect 는 뷰어에서 확인할 것.
EVENTS = {
    "vfib":     {"label": "심실세동 (VF)", "prio": "high", "rhythm": "vfib", "expect": "위급(적색) 알람 · VF 표시 · 최고 경보음"},
    "vt":       {"label": "심실빈맥 (VT)", "prio": "high", "rhythm": "vt", "expect": "위급(적색) 알람 · VT 표시 · HR 상승"},
    "asystole": {"label": "무수축 (Asystole)", "prio": "high", "expect": "위급(적색) 알람 · 평탄 파형 · HR 0"},
    "afib":     {"label": "심방세동 (AF)", "prio": "medium", "rhythm": "afib", "expect": "주의(황색) 알람 · 불규칙 RR"},
    "svt":      {"label": "빈맥 (SVT)", "prio": "medium", "rhythm": "svt", "expect": "주의(황색) 알람 · HR 상한 초과"},
    "brady":    {"label": "극서맥 (3도 방실차단)", "prio": "medium", "rhythm": "avb3", "expect": "주의(황색) 알람 · HR 하한 미만"},
    "stemi":    {"label": "ST 상승", "prio": "medium", "rhythm": "stemi", "expect": "주의(황색) 알람 · ST 변화 표시"},
    "spo2_low": {"label": "SpO2 저하 (약 85 %)", "prio": "medium", "expect": "주의(황색) 알람 · SpO2 하한 미만 (SpO2 기기 환자만)"},
    "pvc":      {"label": "PVC 이단맥", "prio": "low", "rhythm": "pvc_bigeminy", "expect": "권고(청색) 알람 · PVC 표시"},
    "lead_off": {"label": "리드 오프", "prio": "low", "expect": "기술 알람 · 리드 오프 표시 · 파형 레일"},
    "low_batt": {"label": "패치 배터리 부족", "prio": "low", "expect": "기술 알람 · 배터리 부족 표시 (8 %)"},
    "no_link":  {"label": "패치 신호 끊김 (BLE)", "prio": "low", "expect": "기술 알람 · 해당 환자 데이터 없음"},
    "gw_down":  {"label": "게이트웨이 무응답", "prio": "low", "expect": "기술 알람 · 게이트웨이 오프라인 (환자는 인접 게이트웨이로 이동)"},
}
PRIO_KO = {"high": "위급", "medium": "주의", "low": "권고·기술"}
RHYTHM_EVS = {k for k, v in EVENTS.items() if v.get("rhythm")} | {"asystole"}     # 리듬을 바꾸는 이벤트
GW_REASON = "수동 · 현장 테스트"          # "수동" 접두어: 장애 시나리오를 꺼도 유지
SIM_PAD_S = 3.0                            # 시뮬 시각 타이머 여유 (벽시계 초): 테스트가 먼저 끝나 스스로 풀게


class FieldTest:
    def __init__(self, world):
        self.w = world
        self.active: list[dict] = []
        self.history: list[dict] = []
        self.seq = 0

    # ---------------------------------------------------------------- 걸기
    def inject(self, gw: int, ev: str, patient_id: int | None, duration_s: float) -> dict:
        w, h = self.w, self.w.hospital
        if ev not in EVENTS:
            return {"ok": False, "error": "알 수 없는 이벤트"}
        if not (0 <= gw < len(h.gateways)):
            return {"ok": False, "error": "게이트웨이 없음"}
        e = EVENTS[ev]
        dur = max(5.0, min(3600.0, float(duration_s)))
        t_wall = time.time()
        until = t_wall + dur                                                # 벽시계 기준 (sim_speed 와 무관)
        gid = h.gateways[gw]["id"]
        if ev == "gw_down":
            pids = []
        else:
            pids = [pid for pid, rec in w.admitted.items() if rec["gw"] == gw]
            pids.sort(key=lambda p: (w.admitted[p].get("bed_idx", 10 ** 9), p))
            if patient_id is not None:
                if patient_id not in pids:
                    return {"ok": False, "error": "그 환자는 이 게이트웨이에 연결돼 있지 않습니다"}
                pids = [patient_id]
            if not pids:
                return {"ok": False, "error": f"{gid} 에 연결된 환자가 없습니다"}
        done, skipped = [], []
        for pid in pids:
            if ev in RHYTHM_EVS and w.admitted[pid].get("code_blue"):        # 심실세동·소생술 중: 리듬은 코드블루가 정한다 (진행 중인 다른 테스트는 그대로 둔다)
                skipped.append((pid, "코드블루 진행 중"))
                continue
            for a in [x for x in self.active if x["pid"] == pid]:            # 같은 환자의 이전 테스트는 먼저 풀기
                self._end(a, note="새 테스트로 대체")
            r = self._apply(pid, ev, until)
            (done if r is True else skipped).append((pid, r))
        prev = None
        if ev == "gw_down":
            for a in [x for x in self.active if x["ev"] == "gw_down" and x["gw"] == gw]:
                self._end(a, note="새 테스트로 대체")
            G, gs = w.st.gw.arr, w.gw_state[gw]
            if int(G["status"][gw]) == 2 and gs.get("reason") and gs["reason"] != GW_REASON and gs.get("fault_until", 0.0) > 0:
                prev = {"reason": gs["reason"], "fault_until": gs["fault_until"]}     # 이미 장애(하드웨어 고장 등): 끝나면 그 장애로 되돌린다
            G["status"][gw] = 2
            gs["fault_until"] = 0.0                                           # 복구 단계가 먼저 풀지 않게: 끝은 테스트가 정한다
            gs["reason"] = GW_REASON
            done = [(None, True)]
        for pid, _ in done:
            self.seq += 1
            a = {"id": self.seq, "gw": gw, "gw_id": gid, "pid": pid, "ev": ev, "t0": t_wall, "until": until, "t_wall": t_wall}
            if prev:
                a["prev"] = prev
            self.active.append(a)
        who = "게이트웨이" if ev == "gw_down" else (w.by_id[pids[0]]["name"] if len(pids) == 1 else f"환자 {len(done)}명")
        msg = f"[현장 테스트] {gid} · {who} · {e['label']} {dur:.0f}초 ({PRIO_KO[e['prio']]})"
        if skipped:
            msg += f" — 제외 {len(skipped)}명: {skipped[0][1]}"
        w.log.add("test", msg, gw=gw, patient_id=pids[0] if len(pids) == 1 else None)
        self.history.insert(0, {"t_wall": t_wall, "gw_id": gid, "who": who, "ev": ev, "label": e["label"], "prio": e["prio"], "dur": dur, "n": len(done), "skipped": len(skipped)})
        del self.history[50:]
        out = {"ok": bool(done), "sent_at": t_wall, "message": msg, "applied": len(done), "skipped": [{"patient_id": p, "reason": r} for p, r in skipped]}
        if not done:
            out["error"] = f"적용된 환자 없음 — {skipped[0][1]}"
        return out

    def _sim_until(self, until: float, t: float) -> float:
        """벽시계로 남은 시간을 시뮬 시각으로 옮긴 값 (시뮬 시각으로 풀리는 에피소드·리드 오프 타이머용)."""
        speed = float(self.w.cfg.get("general", "sim_speed") or 1.0)
        return self.w.sim_time + (max(0.0, until - t) + SIM_PAD_S) * speed

    def _apply(self, pid: int, ev: str, until: float):
        w = self.w
        rec = w.admitted[pid]
        row = rec["row"]
        P = w.st.patch.arr
        e = EVENTS[ev]
        prof = w.by_id[pid]
        if e.get("rhythm"):
            if e["rhythm"] not in w.rhythm_variants:
                return "리듬 없음(루프 은행)"
            rec["episode_until"] = self._sim_until(until, time.time())       # 변형 순환·자연 에피소드를 막고, 끝나면 기저 리듬
            w._switch_variant(row, w._variant_for(e["rhythm"], prof["age"]), w._tick_now())
        elif ev == "asystole":
            rec["episode_until"] = self._sim_until(until, time.time())
            rec["ft_flat"] = True
            P["gain"][row] = 0.0
            P["hr_override"][row] = 255                                     # 255 = HR 0 으로 보냄
        elif ev == "spo2_low":
            m = int(P["chan_mask"][row]); gm = int(w.st.ctl[CTL["chan_mask"]])
            from ..config import CH_SPO2
            if not ((gm if m == 0 else gm & m) & (1 << CH_SPO2)):
                return "SpO2 기기 없음"
            rec["ft_spo2"] = True                                           # 표시가 있는 동안만 -12 가 걸려 있다 (hold 가 유지)
            P["spo2_add"][row] = float(P["spo2_add"][row]) - 12.0
        elif ev == "lead_off":
            patch = w.patches.get(row)
            if patch is None:
                return "패치 없음"
            patch.lead_off_until = self._sim_until(until, time.time())
        elif ev == "low_batt":
            rec["ft_lowbatt"] = True
        elif ev == "no_link":
            rec["ft_nolink"] = True                                         # _relink 가 도달 불가로 본다 (음영 표시는 건드리지 않음)
            w._relink(rec, force=True)
        return True

    # ---------------------------------------------------------------- 풀기
    def _end(self, a: dict, note: str = "") -> None:
        w = self.w
        if a in self.active:
            self.active.remove(a)
        ev = a["ev"]
        if ev == "gw_down":
            gs = w.gw_state[a["gw"]]
            if gs.get("reason") == GW_REASON:                               # 다른 장애가 덮어썼으면 그쪽이 풀어 준다
                w.st.gw.arr["status"][a["gw"]] = 2
                prev = a.get("prev")
                if prev:                                                    # 테스트 전부터 있던 장애로 (하드웨어 고장은 교체로 끝나야 한다)
                    gs["reason"], gs["fault_until"] = prev["reason"], prev["fault_until"]
                else:
                    gs["fault_until"] = w.sim_time                          # 게이트웨이 단계가 다음 스텝에 복구
            if note != "새 테스트로 대체":
                w.log.add("test", f"[현장 테스트] {a['gw_id']} · 게이트웨이 무응답 종료{(' — ' + note) if note else ''} → 정상 복귀", gw=a["gw"])
            return
        rec = w.admitted.get(a["pid"])
        if rec is None:                                                     # 퇴원(사망 포함): 되돌릴 환자가 없다
            w.log.add("test", f"[현장 테스트] {a['gw_id']} · {w.by_id[a['pid']]['name']} · {EVENTS[ev]['label']} 종료 — 환자 퇴원",
                      gw=a["gw"], patient_id=a["pid"])
            return
        row = rec["row"]
        P = w.st.patch.arr
        if ev in RHYTHM_EVS:
            cb = rec.get("code_blue")
            if cb:                                                          # 코드블루가 아직 진행 중: 심실세동으로 되돌린다
                rec["episode_until"] = cb["until"] + 5
                w._switch_variant(row, w._variant_for("vfib", w.by_id[a["pid"]]["age"]), w._tick_now())
            else:
                rec["episode_until"] = 0.0
                w._switch_variant(row, rec["base_variant"], w._tick_now())
        if ev == "asystole":
            rec.pop("ft_flat", None)
            P["gain"][row] = rec.get("gain0", 1.0)
            P["hr_override"][row] = 0
        elif ev == "spo2_low":
            if rec.pop("ft_spo2", None):                                    # 표시가 있으면 -12 가 걸려 있다 (모듈레이션이 다시 계산해도 hold 가 유지)
                P["spo2_add"][row] = float(P["spo2_add"][row]) + 12.0
        elif ev == "lead_off":
            patch = w.patches.get(row)
            if patch:
                patch.lead_off_until = 0.0
        elif ev == "low_batt":
            rec.pop("ft_lowbatt", None)
        elif ev == "no_link":
            if rec.pop("ft_nolink", None):
                w._relink(rec, force=True)                                  # 지금 음영(엘리베이터·MRI)이면 계속 끊긴 채로
        if note != "새 테스트로 대체":
            w.log.add("test", f"[현장 테스트] {a['gw_id']} · {w.by_id[a['pid']]['name']} · {EVENTS[ev]['label']} 종료{(' — ' + note) if note else ''} → 정상 복귀",
                      gw=a["gw"], patient_id=a["pid"])

    def matching(self, test_id: int | None = None, gw: int | None = None, pid: int | None = None, ev: str | None = None) -> list[dict]:
        return [a for a in self.active if (test_id is None or a["id"] == test_id) and (gw is None or a["gw"] == gw)
                and (pid is None or a["pid"] == pid) and (ev is None or a["ev"] == ev)]

    def clear(self, test_id: int | None = None, gw: int | None = None, pid: int | None = None, ev: str | None = None) -> int:
        """test_id 하나 · gw 그 게이트웨이 · (gw, pid, ev) 녹화 재생용 · 비우면 전부."""
        found = self.matching(test_id, gw, pid, ev)
        for a in found:
            self._end(a, note="수동 해제")
        return len(found)

    def step(self) -> None:
        w = self.w
        t = time.time()
        for a in list(self.active):
            if t >= a["until"] or (a["pid"] is not None and a["pid"] not in w.admitted):
                self._end(a)
        G = w.st.gw.arr
        for a in self.active:                                               # 진행 중인 테스트 값 유지
            if a["ev"] == "gw_down":
                if int(G["status"][a["gw"]]) != 2:                          # 망 이벤트·복구 단계가 살려 놓아도 테스트 동안은 무응답
                    gs = w.gw_state[a["gw"]]
                    G["status"][a["gw"]] = 2
                    gs["fault_until"] = 0.0
                    gs["reason"] = GW_REASON
                continue
            rec = w.admitted.get(a["pid"])
            if rec is None:
                continue
            if a["ev"] in RHYTHM_EVS:                                       # sim_speed 를 바꿔도 에피소드가 먼저 풀리지 않게
                rh = EVENTS[a["ev"]].get("rhythm")
                if not rec["episode_until"] and rh in w.rhythm_variants:    # 그래도 먼저 풀려 기저 리듬으로 갔으면 테스트 리듬을 다시
                    w._switch_variant(rec["row"], w._variant_for(rh, w.by_id[a["pid"]]["age"]), w._tick_now())
                rec["episode_until"] = self._sim_until(a["until"], t)
            elif a["ev"] == "lead_off":
                patch = w.patches.get(rec["row"])
                if patch is not None:                                       # 테스트 중 패치를 갈아도 리드 오프 유지
                    patch.lead_off_until = self._sim_until(a["until"], t)

    # ---------------------------------------------------------------- 모듈레이션에서 부르는 유지 값
    def hold(self, rec: dict, row: int, now: float) -> None:
        """10초마다 재계산되는 값(gain·SpO2 보정)이 테스트 중에는 테스트 값을 유지하도록.  표시(ft_*)는 테스트가 풀 때 지운다."""
        P = self.w.st.patch.arr
        if rec.get("ft_flat"):
            P["gain"][row] = 0.0
        if rec.get("ft_spo2"):
            P["spo2_add"][row] = float(P["spo2_add"][row]) - 12.0

    def holds_rhythm(self, pid: int) -> bool:
        """이 환자의 리듬을 현장 테스트가 정하고 있는가 (리듬·무수축 이벤트)."""
        return any(a["pid"] == pid and a["ev"] in RHYTHM_EVS for a in self.active)

    def status(self) -> dict:
        w = self.w
        t = time.time()
        act = [{"id": a["id"], "gw": a["gw"], "gw_id": a["gw_id"], "patient_id": a["pid"],
                "patient": w.by_id[a["pid"]]["name"] if a["pid"] is not None and a["pid"] in w.by_id else None,
                "ev": a["ev"], "label": EVENTS[a["ev"]]["label"], "prio": EVENTS[a["ev"]]["prio"], "expect": EVENTS[a["ev"]]["expect"],
                "remaining_s": max(0, round(a["until"] - t)), "sent_at": a["t_wall"]} for a in self.active]
        return {"events": [{"id": k, **v} for k, v in EVENTS.items()], "active": act, "history": self.history[:30]}
