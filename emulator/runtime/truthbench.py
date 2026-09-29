"""정답 전용 벤치마크 패치 — 라우터 파형 알고리즘의 정확도를 실시간으로 채점할 수 있게, 실제 환자가 아닌 가상 패치 64개가
파형과 정답(TRUTH 레코드, 채널 11)을 같은 프레임에 실어 보낸다.

  * 다양한 부정맥을 골고루 배치한다(PLAN: 리듬 종류마다 최소 1개, 임상적으로 중요한 부정맥에 더 많이).
  * hop_s 간격마다 같은 리듬의 다른 변형으로, 또는(switch_rhythm_pct) 다른 리듬으로 바꾼다 — 전환은 프레임 단위로 정확하고
    크로스페이드 동안 정답에 이전 리듬(rhythm_prev)과 switching 플래그가 붙는다.
  * 가끔 동작 아티팩트·잡음·리드 오프가 들어가고, 그 동안에도 정답(플래그·수준·R-peak)은 계속 나간다.
  * 페이싱 리듬 패치는 스파이크 위치(모든 스파이크, 챔버)를 정답에 싣는다 — 채널 10(하드웨어 검출)은 그대로 손실이 있다.

정답 패치는 환자 목록·병상·패치 레지스트리에 나타나지 않고 정답 게이트웨이(TGW-xx, 도면 밖)에만 묶인다.  워커는 공유 배열의
truth=1 행에 대해 프레임마다 TRUTH 레코드를 만든다(fastpath.FrameBuilder).  R-peak 주석은 루프 은행에 있어야 한다(2026-09-29
이후 생성분); 없으면 정답에 beats_unavailable 플래그가 서고 박동 목록이 빈다.
"""
from __future__ import annotations

import time

import numpy as np

from ..config import CH_ECG, CH_HR, CH_PACE, CH_TRUTH
from ..signals.rhythms import RHYTHMS
from .state import CTL, FLAG_PACED

TRUTH_MAX = 64
# 등급 세트: 간소 16 · 일반 32 · 정밀 64 채널(패치).  값 = 리듬별 패치 수.  정밀은 리듬 종류마다 최소 1개, 임상적으로 중요한 부정맥에 더 많이.
PLANS = {
    "basic": {"nsr": 2, "sinus_brady": 1, "sinus_tachy": 1, "afib": 2, "aflutter": 1, "pvc": 2, "pac": 1, "vt": 1, "svt": 1, "avb3": 1, "lbbb": 1, "paced_ddd": 1, "vfib": 1},
    "standard": {"nsr": 2, "sinus_brady": 1, "sinus_tachy": 1, "afib": 3, "afib_rvr": 1, "aflutter": 2, "pvc": 2, "pvc_bigeminy": 1, "pac": 2, "nsvt": 1, "vt": 1, "svt": 2,
                 "avb1": 1, "avb2_m1": 1, "avb2_m2": 1, "avb3": 1, "lbbb": 1, "rbbb": 1, "stemi": 1, "ischemia": 1, "paced_vvi": 1, "paced_ddd": 1, "paced_malfunction": 1,
                 "sinus_pause": 1, "vfib": 1},
    "precise": {"nsr": 4, "sinus_brady": 2, "sinus_tachy": 2, "afib": 5, "afib_rvr": 3, "aflutter": 3, "pvc": 4, "pvc_bigeminy": 2, "pac": 3, "nsvt": 3, "vt": 2, "svt": 3,
                "avb1": 2, "avb2_m1": 2, "avb2_m2": 2, "avb3": 2, "lbbb": 2, "rbbb": 2, "stemi": 2, "ischemia": 2, "paced_vvi": 2, "paced_aai": 1, "paced_ddd": 2,
                "paced_crt": 1, "paced_malfunction": 2, "sinus_pause": 2, "vfib": 2},
}
GRADES = {"basic": ("간소", 16), "standard": ("일반", 32), "precise": ("정밀", 64)}
assert all(sum(PLANS[g].values()) == n for g, (_, n) in GRADES.items()) and set(PLANS["precise"]) == set(RHYTHMS)
assert all(set(PLANS[g]) <= set(RHYTHMS) for g in PLANS)
PLAN = PLANS["precise"]                 # 리듬 전환 가중치용 (모든 등급이 정밀 세트의 비중으로 다른 리듬을 고른다)


def grade_of(c: dict) -> str:
    g = str(c.get("grade") or "precise")
    return g if g in GRADES else "precise"


def grade_sets() -> dict:
    """등급별 구성 (GUI·API 표시용): 리듬 키·이름·패치 수."""
    return {g: {"label": lab, "patches": n, "rhythms": [{"key": r, "label": RHYTHMS[r]["label"], "cls": RHYTHMS[r]["cls"], "n": k} for r, k in PLANS[g].items()]}
            for g, (lab, n) in GRADES.items()}
TRUTH_PATIENT_BASE = 900001            # 레코드 patient_id (실제 환자번호와 겹치지 않음)
TRUTH_PATCH_BASE = 0xF0000             # 레코드 patch_id (BP 패치는 0x10000 + 시리얼)
EVENTS = {"artifact": (10.0, 60.0), "noise": (10.0, 40.0), "lead_off": (10.0, 40.0)}
ACT_MOVING = ["walking", "turning", "sitting_up", "exercise"]


class TruthBench:
    def __init__(self, w, rows: list[int], gws: list[int]):
        self.w = w
        self.rows = list(rows)[:TRUTH_MAX]
        self.gws = list(gws)
        self.rng = np.random.default_rng(int(w.cfg.get("general", "seed")) + 4242)
        self.state: dict[int, dict] = {}                 # row -> {rhythm, hop_at, event, until, k}
        self.enabled = False
        self.n = 0
        self.counters = {"hops": 0, "rhythm_switches": 0, "artifact": 0, "noise": 0, "lead_off": 0}

    # ------------------------------------------------------------------ setup
    def cfg(self) -> dict:
        return self.w.cfg.get("transport", "truth", default={}) or {}

    def setup(self) -> None:
        """재구축 직후: 행을 초기화하고 배치 계획대로 리듬을 준다.  enabled 이면 바로 송출 대상이 된다."""
        w = self.w
        P = w.st.patch.arr
        from .world import ACT_ID, POSTURE_ID
        self.state = {}
        self._plan_rows(self.cfg())
        for k, row in enumerate(self.rows):
            rhythm = self._plan[k % len(self._plan)]
            P[row] = 0                                                       # 모든 필드 0 으로
            P["truth"][row] = 1
            P["gw"][row] = -1
            P["patch_id"][row] = TRUTH_PATCH_BASE + k
            P["patient_id"][row] = TRUTH_PATIENT_BASE + k
            P["variant"][row] = w._variant_for(rhythm, 60)
            P["variant_prev"][row] = -1
            P["switch_tick"][row] = -10 ** 9
            P["offset_ms"][row] = int(self.rng.integers(0, w.bank.seconds * 1000)) if w.bank.loaded else 0
            P["gain"][row] = 1.0
            P["activity"][row] = ACT_ID["still"]
            P["posture"][row] = POSTURE_ID["supine"]
            P["battery"][row] = 100
            P["rssi"][row] = -55
            P["hr_scale"][row] = 1.0
            P["chan_mask"][row] = (1 << CH_ECG) | (1 << CH_HR) | (1 << CH_PACE) | (1 << CH_TRUTH)
            self._apply_rhythm_device(row, rhythm, k)
            self.state[row] = {"k": k, "rhythm": rhythm, "hop_at": w.sim_time + float(self.rng.uniform(*self._hop())), "event": None, "until": 0.0}
        self.apply_config()

    def _plan_rows(self, c: dict) -> None:
        """등급 세트를 행에 펼친다 (같은 시드면 같은 배치).  등급이 바뀌면 apply_config 가 다시 부른다."""
        self.grade = grade_of(c)
        plan = [r for r, n in PLANS[self.grade].items() for _ in range(n)]
        rng = np.random.default_rng(int(self.w.cfg.get("general", "seed")) + 4243)
        rng.shuffle(plan)
        self._plan = plan + [r for r, n in PLANS["precise"].items() for _ in range(n)][len(plan):TRUTH_MAX]   # 등급 밖 행은 정밀 세트로 채워 두되 비활성

    def _hop(self) -> tuple[float, float]:
        hs = self.cfg().get("hop_s") or [120, 480]
        return float(hs[0]), float(hs[-1])

    def _apply_rhythm_device(self, row: int, rhythm: str, k: int) -> None:
        P = self.w.st.patch.arr
        paced = rhythm.startswith("paced")
        P["paced"][row] = 1 if paced else 0
        P["pace_amp"][row] = (1500.0 if k % 2 == 0 else 250.0) if paced else 0.0        # 단극(mV급) / 양극(sub-mV) 번갈아
        P["pace_detect"][row] = 85 if paced else 0
        P["flags"][row] = (int(P["flags"][row]) & ~FLAG_PACED) | (FLAG_PACED if paced else 0)

    def apply_config(self) -> None:
        """enabled·patches 를 즉시 반영: 활성 행 수와 정답 게이트웨이 활성 상태."""
        w = self.w
        c = self.cfg()
        self.enabled = bool(c.get("enabled", True)) and bool(self.gws)
        P, G = w.st.patch.arr, w.st.gw.arr
        if self.state and grade_of(c) != getattr(self, "grade", None):        # 등급 변경: 배치 계획을 다시 펼치고 리듬을 다시 준다
            self._plan_rows(c)
            tick = w._tick_now()
            for k, row in enumerate(self.rows):
                rhythm = self._plan[k % len(self._plan)]
                st = self.state[row]
                if st["rhythm"] != rhythm:
                    st["rhythm"] = rhythm
                    self._apply_rhythm_device(row, rhythm, k)
                    w._switch_variant(row, w._variant_for(rhythm, 60), tick)
        self.n = GRADES[grade_of(c)][1] if self.enabled else 0
        self.n = min(self.n, len(self.rows))
        per_gw: dict[int, int] = {g: 0 for g in self.gws}
        for k, row in enumerate(self.rows):
            on = k < self.n
            gw = self.gws[k % len(self.gws)] if self.gws else -1
            P["gw"][row] = gw if on else -1
            P["active"][row] = 1 if on else 0
            if on:
                per_gw[gw] += 1
        for g, cnt in per_gw.items():
            G["active"][g] = 1 if cnt else 0
            G["status"][g] = 0
            G["n_conn"][g] = min(255, cnt)
            if g < len(w.gw_state):
                w.gw_state[g]["n_conn"] = cnt
        w.meta_dirty = True

    # ------------------------------------------------------------------ per-second step
    def step(self, dt_s: float) -> None:
        if not self.n:
            return
        w = self.w
        P = w.st.patch.arr
        now = w.sim_time
        tick = w._tick_now()
        c = self.cfg()
        p_hour = {k: float(c.get(f"{k}_pct", 0)) / 100.0 for k in EVENTS}
        for k, row in enumerate(self.rows[: self.n]):
            st = self.state[row]
            # 리듬/변형 교체
            if now >= st["hop_at"]:
                if self.rng.random() < float(c.get("switch_rhythm_pct", 35)) / 100.0:
                    others = [r for r in PLAN if r != st["rhythm"]]
                    wts = np.array([PLAN[r] for r in others], dtype=float); wts /= wts.sum()
                    rhythm = str(self.rng.choice(others, p=wts))
                    v = w._variant_for(rhythm, 60)
                    st["rhythm"] = rhythm
                    self._apply_rhythm_device(row, rhythm, k)
                    self.counters["rhythm_switches"] += 1
                else:
                    v = w._similar_variant(st["rhythm"], int(P["variant"][row]), tol=1e9)
                w._switch_variant(row, v, tick)
                self.counters["hops"] += 1
                st["hop_at"] = now + float(self.rng.uniform(*self._hop()))
            # 이벤트: 아티팩트 · 잡음 · 리드 오프 (겹치지 않게 하나씩)
            ev = st["event"]
            if ev and now >= st["until"]:
                self._end_event(row, st, tick)
            elif not ev:
                for kind, (lo, hi) in EVENTS.items():
                    if self.rng.random() < p_hour[kind] * dt_s / 3600.0:
                        self._start_event(row, st, kind, float(self.rng.uniform(lo, hi)), tick)
                        break

    def _start_event(self, row: int, st: dict, kind: str, dur: float, tick: int) -> None:
        from .world import ACT_ID
        P = self.w.st.patch.arr
        st["event"], st["until"] = kind, self.w.sim_time + dur
        self.counters[kind] += 1
        if kind == "artifact":
            P["activity"][row] = ACT_ID.get(str(self.rng.choice(ACT_MOVING)), ACT_ID["still"])
            P["act_offset_ms"][row] = int(self.rng.integers(0, self.w.bank.seconds * 1000)) if self.w.bank.loaded else 0
            P["art_gain"][row] = float(self.rng.uniform(0.3, 1.2))
        elif kind == "noise":
            P["noise"][row] = float(self.rng.uniform(0.06, 0.4))
        else:
            P["lead_off"][row] = 1
            P["detach_tick"][row] = tick

    def _end_event(self, row: int, st: dict, tick: int) -> None:
        from .world import ACT_ID
        P = self.w.st.patch.arr
        kind, st["event"], st["until"] = st["event"], None, 0.0
        if kind == "artifact":
            P["art_gain"][row] = 0.0
            P["activity"][row] = ACT_ID["still"]
        elif kind == "noise":
            P["noise"][row] = 0.0
        else:
            P["lead_off"][row] = 0
            P["attach_tick"][row] = tick                                  # 재부착 안정화 과도가 정답에 settling 으로 표시된다

    # ------------------------------------------------------------------ views
    def meta_entries(self) -> dict[int, list[dict]]:
        """정답 게이트웨이의 META patches[] 항목 (benchmark=true).  리듬은 프레임마다 정답 레코드에 있으니 META 에는 넣지 않는다."""
        if not self.n:
            return {}
        P = self.w.st.patch.arr
        out: dict[int, list[dict]] = {}
        chans = [{"id": c, "key": k} for c, k in ((CH_ECG, "ecg"), (CH_HR, "hr"), (CH_PACE, "pace"), (CH_TRUTH, "truth"))]
        for k, row in enumerate(self.rows[: self.n]):
            gw = int(P["gw"][row])
            if gw < 0:
                continue
            out.setdefault(gw, []).append({"patch_id": int(P["patch_id"][row]), "serial": f"TR-{k + 1:04d}", "patient_id": int(P["patient_id"][row]), "profile_id": None,
                                           "mrn": f"TRUTH-{k + 1:02d}", "benchmark": True, "home": None, "fw": "truth-patch 1.0", "resp_source": None, "spo2_source": None,
                                           "devices": [{"key": "ecg_patch", "label": "ECG 패치 (정답 벤치마크)"}], "channels": chans,
                                           "pacemaker": ({"type": "benchmark", "mode": self.state[row]["rhythm"], "lead": None, "detect_pct": 85} if P["paced"][row] else None)})
        return out

    def status(self) -> dict:
        P = self.w.st.patch.arr
        bank_ok = bool(getattr(self.w.bank, "rpeaks", None))
        items = []
        for k, row in enumerate(self.rows[: self.n]):
            st = self.state.get(row, {})
            items.append({"k": k + 1, "serial": f"TR-{k + 1:04d}", "patch_id": int(P["patch_id"][row]), "patient_id": int(P["patient_id"][row]), "gateway": int(P["gw"][row]),
                          "rhythm": st.get("rhythm"), "rhythm_label": RHYTHMS.get(st.get("rhythm", ""), {}).get("label"), "variant": int(P["variant"][row]),
                          "paced": bool(P["paced"][row]), "event": st.get("event"), "event_left_s": max(0.0, st.get("until", 0.0) - self.w.sim_time) if st.get("event") else 0.0,
                          "next_hop_s": max(0.0, st.get("hop_at", 0.0) - self.w.sim_time), "lead_off": bool(P["lead_off"][row]), "art_gain": round(float(P["art_gain"][row]), 2),
                          "noise": round(float(P["noise"][row]), 3)})
        by_rhythm: dict[str, int] = {}
        for it in items:
            by_rhythm[it["rhythm"]] = by_rhythm.get(it["rhythm"], 0) + 1
        return {"enabled": self.enabled, "grade": getattr(self, "grade", "precise"), "grade_label": GRADES[getattr(self, "grade", "precise")][0], "patches": self.n, "max": len(self.rows),
                "gateways": [self.w.hospital.gateways[g]["id"] for g in self.gws], "grade_sets": grade_sets(),
                "beats_available": bank_ok, "note": None if bank_ok else "루프 은행에 R-peak 주석이 없습니다 — [루프 은행 재생성] 뒤에 박동 정답이 나갑니다",
                "config": self.cfg(), "counters": dict(self.counters), "by_rhythm": by_rhythm, "patches_list": items, "t": time.time()}
