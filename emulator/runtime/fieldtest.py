"""현장 테스트: 웨어러블 패치 IoT 망을 설치한 뒤, 게이트웨이를 골라 그 게이트웨이의 환자에게 정해진 이벤트를 정해진
시간 동안 걸었다가 원래대로 돌린다.  그 게이트웨이를 보도록 설정한 파형 뷰어에 이벤트가 뜨는지, 알람 등급·경보음
볼륨이 맞는지를 현장에서 확인하는 용도다.
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
        now = w.sim_time
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
            for a in [x for x in self.active if x["pid"] == pid]:            # 같은 환자의 이전 테스트는 먼저 풀기
                self._end(a, note="새 테스트로 대체")
            r = self._apply(pid, ev, now + dur)
            (done if r is True else skipped).append((pid, r))
        if ev == "gw_down":
            for a in [x for x in self.active if x["ev"] == "gw_down" and x["gw"] == gw]:
                self._end(a, note="새 테스트로 대체")
            G = w.st.gw.arr
            G["status"][gw] = 2
            w.gw_state[gw]["fault_until"] = now + dur
            w.gw_state[gw]["reason"] = "수동 · 현장 테스트"          # "수동" 접두어: 장애 시나리오를 꺼도 유지
            done = [(None, True)]
        t_wall = time.time()
        for pid, _ in done:
            self.seq += 1
            self.active.append({"id": self.seq, "gw": gw, "gw_id": gid, "pid": pid, "ev": ev, "t0": now, "until": now + dur, "t_wall": t_wall})
        who = "게이트웨이" if ev == "gw_down" else (w.by_id[pids[0]]["name"] if len(pids) == 1 else f"환자 {len(done)}명")
        msg = f"[현장 테스트] {gid} · {who} · {e['label']} {dur:.0f}초 ({PRIO_KO[e['prio']]})"
        if skipped:
            msg += f" — 제외 {len(skipped)}명: {skipped[0][1]}"
        w.log.add("test", msg, gw=gw, patient_id=pids[0] if len(pids) == 1 else None)
        self.history.insert(0, {"t_wall": t_wall, "gw_id": gid, "who": who, "ev": ev, "label": e["label"], "prio": e["prio"], "dur": dur, "n": len(done), "skipped": len(skipped)})
        del self.history[50:]
        return {"ok": bool(done), "sent_at": t_wall, "message": msg, "applied": len(done), "skipped": [{"patient_id": p, "reason": r} for p, r in skipped]}

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
            rec["episode_until"] = until                                    # 변형 순환·자연 에피소드를 막고, 끝나면 기저 리듬
            w._switch_variant(row, w._variant_for(e["rhythm"], prof["age"]), w._tick_now())
        elif ev == "asystole":
            rec["episode_until"] = until
            rec["ft_flat_until"] = until
            P["gain"][row] = 0.0
            P["hr_override"][row] = 255                                     # 255 = HR 0 으로 보냄
        elif ev == "spo2_low":
            m = int(P["chan_mask"][row]); gm = int(w.st.ctl[CTL["chan_mask"]])
            from ..config import CH_SPO2
            if not ((gm if m == 0 else gm & m) & (1 << CH_SPO2)):
                return "SpO2 기기 없음"
            rec["ft_spo2_until"] = until
            P["spo2_add"][row] = float(P["spo2_add"][row]) - 12.0
        elif ev == "lead_off":
            patch = w.patches.get(row)
            if patch is None:
                return "패치 없음"
            patch.lead_off_until = until
        elif ev == "low_batt":
            rec["ft_lowbatt_until"] = until
        elif ev == "no_link":
            rec["ft_nolink_until"] = until
            rec["shadow"] = True
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
            if gs.get("reason") == "수동 · 현장 테스트":
                gs["fault_until"] = min(gs.get("fault_until", 0.0), w.sim_time)   # 게이트웨이 단계가 다음 스텝에 복구
            if note != "새 테스트로 대체":
                w.log.add("test", f"[현장 테스트] {a['gw_id']} · 게이트웨이 무응답 종료{(' — ' + note) if note else ''} → 정상 복귀", gw=a["gw"])
            return
        rec = w.admitted.get(a["pid"])
        if rec is None:
            return
        row = rec["row"]
        P = w.st.patch.arr
        if EVENTS[ev].get("rhythm") or ev == "asystole":
            rec["episode_until"] = 0.0
            w._switch_variant(row, rec["base_variant"], w._tick_now())
        if ev == "asystole":
            rec.pop("ft_flat_until", None)
            P["gain"][row] = rec.get("gain0", 1.0)
            P["hr_override"][row] = 0
        elif ev == "spo2_low":
            if rec.pop("ft_spo2_until", None):
                P["spo2_add"][row] = float(P["spo2_add"][row]) + 12.0
        elif ev == "lead_off":
            patch = w.patches.get(row)
            if patch:
                patch.lead_off_until = 0.0
        elif ev == "low_batt":
            rec.pop("ft_lowbatt_until", None)
        elif ev == "no_link":
            rec.pop("ft_nolink_until", None)
            rec["shadow"] = False
            w._relink(rec, force=True)
        if note != "새 테스트로 대체":
            w.log.add("test", f"[현장 테스트] {a['gw_id']} · {w.by_id[a['pid']]['name']} · {EVENTS[ev]['label']} 종료{(' — ' + note) if note else ''} → 정상 복귀",
                      gw=a["gw"], patient_id=a["pid"])

    def clear(self, test_id: int | None = None, gw: int | None = None) -> int:
        n = 0
        for a in list(self.active):
            if (test_id is None or a["id"] == test_id) and (gw is None or a["gw"] == gw):
                self._end(a, note="수동 해제"); n += 1
        return n

    def step(self) -> None:
        now = self.w.sim_time
        for a in list(self.active):
            if now >= a["until"] or (a["pid"] is not None and a["pid"] not in self.w.admitted):
                self._end(a)

    # ---------------------------------------------------------------- 모듈레이션에서 부르는 유지 값
    def hold(self, rec: dict, row: int, now: float) -> None:
        """10초마다 재계산되는 값(gain·SpO2 보정)이 테스트 중에는 테스트 값을 유지하도록."""
        P = self.w.st.patch.arr
        if rec.get("ft_flat_until", 0) > now:
            P["gain"][row] = 0.0
        if rec.get("ft_spo2_until", 0) > now:
            P["spo2_add"][row] = float(P["spo2_add"][row]) - 12.0

    def status(self) -> dict:
        w = self.w
        now = w.sim_time
        act = [{"id": a["id"], "gw": a["gw"], "gw_id": a["gw_id"], "patient_id": a["pid"],
                "patient": w.by_id[a["pid"]]["name"] if a["pid"] is not None and a["pid"] in w.by_id else None,
                "ev": a["ev"], "label": EVENTS[a["ev"]]["label"], "prio": EVENTS[a["ev"]]["prio"], "expect": EVENTS[a["ev"]]["expect"],
                "remaining_s": max(0, round(a["until"] - now)), "sent_at": a["t_wall"]} for a in self.active]
        return {"events": [{"id": k, **v} for k, v in EVENTS.items()], "active": act, "history": self.history[:30]}
