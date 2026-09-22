"""현장 현실감 모델 (디지털 트윈).

월드(World)는 이 객체를 하나 들고 훅으로 부른다.  기능별 구획:

  1. 네트워크 토폴로지   건물 코어 → 층 스위치 → 무선 AP → 게이트웨이.  장비 하나가 죽으면 그 아래가 같이 끊긴다.
  2. 정전과 UPS          한전 정전 → 발전기 기동·ATS 전환(8~15 s) → 계통별로 다른 복구 (UPS 유지 / 비상 재부팅 / 일반 전원 차단)
  3. 병원 일과            활력징후 측정·회진·식사·면회·저녁 샤워·소등이 시간표대로 환자 움직임을 만든다
  4. 임상 악화            패혈증·심부전 악화·호흡부전·저혈당·출혈성 쇼크가 수 시간에 걸쳐 진행, 일부는 코드블루로
  5. 정답 라벨            리듬 에피소드·리드오프·패치 분리·음영·악화·코드블루·게이트웨이/망/전원 장애를 구간으로 기록
  6. MCOT 단말            앱 강제 종료·절전 모드(일괄 업로드)·야간 OS 업데이트·비행기 모드·지역별 커버리지
  7. 재원 수 변동         요일·시간대별 재원 곡선, 대량 환자 유입(surge)

게이트웨이 상태값: 0 정상 · 1 저하 · 2 장애(전원 꺼짐 등, 패치가 다른 GW 로 옮겨감) ·
3 업링크 단절(게이트웨이는 살아 있어 패치가 붙어 있고, 프레임은 게이트웨이에 쌓였다가 복구 때 올라간다).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time

import numpy as np

from .state import CTL

GW_DOWN, GW_UPLINK = 2, 3
TAG_NET, TAG_PWR, TAG_PHONE = "[망] ", "[전원] ", "[단말] "
WIFI_TYPES = {"corridor", "elevator", "stairs", "lobby", "toilet"}      # 배선이 어려운 곳은 무선 AP 경유
UPS_TYPES = {"er", "nurse_station"}                                      # 응급실·간호사실은 UPS 계통
RURAL = {"강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}

# ---- 3. 병원 일과 (시각 → 구간)
ROUTINE = [
    (0, 6, "night", "소등·수면"), (6, 7, "vitals", "기상·채혈·활력징후"), (7, 8, "meal", "아침 식사"), (8, 10, "rounds", "회진"),
    (10, 12, "care", "투약·처치·검사"), (12, 13, "meal", "점심 식사"), (13, 17, "care", "검사·재활·면회"), (17, 18, "visit", "면회"),
    (18, 19, "meal", "저녁 식사"), (19, 21, "evening", "샤워·세면"), (21, 22, "vitals", "활력징후·취침 투약"), (22, 24, "night", "소등·수면"),
]
VITALS_HOURS = (2, 6, 10, 14, 18, 22)                                    # 4시간마다 활력징후 측정

# ---- 4. 임상 악화: 최고조일 때의 변화량 (HR 배수, RR, SpO2, 체온, 혈당)
DETER = {
    "sepsis":      {"label": "패혈증",        "hr": 0.35, "rr": 8,  "spo2": -5,  "temp": 2.0,  "gl": 25},
    "hf":          {"label": "심부전 악화",   "hr": 0.20, "rr": 10, "spo2": -7,  "temp": 0.0,  "gl": 0},
    "resp":        {"label": "호흡부전",      "hr": 0.25, "rr": 12, "spo2": -12, "temp": 0.3,  "gl": 0},
    "hypoglycemia": {"label": "저혈당",       "hr": 0.20, "rr": 2,  "spo2": 0,   "temp": -0.3, "gl": -60},
    "bleeding":    {"label": "출혈성 쇼크",   "hr": 0.45, "rr": 6,  "spo2": -3,  "temp": -0.5, "gl": 0},
}

# ---- 7. 재원 수: 요일(월=0) · 시각 배수
CENSUS_DOW = (0.97, 1.00, 1.00, 0.99, 0.96, 0.90, 0.88)
CENSUS_HOUR = [1.0] * 10 + [0.99, 0.98, 0.975, 0.975, 0.98, 0.99, 1.0, 1.005, 1.01, 1.01, 1.005, 1.0, 1.0, 1.0]


def _dur(sec: float) -> str:
    return f"{sec:.0f}초" if sec < 120 else f"{sec / 60:.0f}분"


def _hour_frac(t: float) -> tuple[int, float, int]:
    d = dt.datetime.fromtimestamp(t)
    return d.hour, d.hour + d.minute / 60.0, d.weekday()


class Realism:
    def __init__(self, world):
        self.w = world
        seed = int(world.cfg.get("general", "seed")) + 313
        self.rng = np.random.default_rng(seed)                           # 독립 난수열: 기존 월드 난수 순서를 흔들지 않는다
        self.dev: dict[str, dict] = {}
        self.gw_up: dict[int, dict] = {}
        self.win: dict[str, list] = {}                                    # entity -> [(start, off_end, boot_end, cause)]
        self.tagged: set[int] = set()                                     # 이 모델이 상태를 바꿔 둔 게이트웨이
        self.labels_open: dict[tuple, dict] = {}
        self.label_seen: dict[int, dict] = {}
        self.gw_seen: dict[int, tuple] = {}
        self.surge: dict | None = None
        self.recording: dict | None = None
        self._last10 = 0.0
        self._v2r: dict[int, str] = {}
        self._build_topology()

    # ======================================================================= 1. 토폴로지
    def _build_topology(self) -> None:
        h = self.w.hospital
        rng = np.random.default_rng(int(self.w.cfg.get("general", "seed")) + 311)   # 병원이 같으면 배선도 같다
        for g in h.gateways:
            if g["type"] == "mobile":
                continue
            b, f = int(g["building_idx"]), int(g["floor"])
            core = f"CORE-{b}"
            self.dev.setdefault(core, {"id": core, "kind": "core", "building_idx": b, "building": g["building"], "floor": None, "ups": True, "gws": []})
            sw = f"SW-{b}-{f:02d}"
            if sw not in self.dev:
                self.dev[sw] = {"id": sw, "kind": "switch", "building_idx": b, "building": g["building"], "floor": f, "parent": core,
                                "ups": bool(rng.random() < 0.6), "gws": []}
            wifi = g["type"] in WIFI_TYPES or rng.random() < 0.12
            ap = None
            if wifi:
                ap = f"AP-{b}-{f:02d}-{int(max(0.0, g['x']) // 30)}"
                self.dev.setdefault(ap, {"id": ap, "kind": "ap", "building_idx": b, "building": g["building"], "floor": f, "parent": sw, "ups": None, "gws": []})
                self.dev[ap]["gws"].append(g["idx"])
            self.dev[sw]["gws"].append(g["idx"])
            self.dev[core]["gws"].append(g["idx"])
            circuit = "ups" if g["type"] in UPS_TYPES else ("normal" if rng.random() < 0.2 else "emergency")
            self.gw_up[g["idx"]] = {"uplink": "wifi" if wifi else "wired", "core": core, "switch": sw, "ap": ap, "power": circuit}

    def meta_net(self, gw: int) -> dict | None:
        u = self.gw_up.get(gw)
        return {"uplink": u["uplink"], "switch": u["switch"], "ap": u["ap"], "power": u["power"]} if u else None

    def _state(self, ent: str, now: float) -> tuple[str, str | None]:
        for s, off_end, boot_end, cause in self.win.get(ent, ()):
            if s <= now < off_end:
                return "off", cause
            if off_end <= now < boot_end:
                return "boot", cause
        return "on", None

    def _add_win(self, ent: str, start: float, off_s: float, boot_s: float, cause: str) -> None:
        self.win.setdefault(ent, []).append((start, start + off_s, start + off_s + boot_s, cause))

    def _gw_effective(self, gw: int, now: float) -> tuple[str, str]:
        p, cause = self._state(f"gw:{gw}", now)
        if p == "off":
            return "down", TAG_PWR + f"전원 차단 ({cause})"
        if p == "boot":
            return "boot", TAG_PWR + "재부팅 중"
        u = self.gw_up.get(gw)
        if not u:
            return "ok", ""
        chain = [u["core"], u["switch"]] + ([u["ap"]] if u["ap"] else [])
        for ent in chain:
            st, cause = self._state(ent, now)
            if ent == u["ap"] and st == "on":                             # AP 는 스위치 PoE 로 전원을 받는다
                st2, _ = self._state(u["switch"], now)
                st = "off" if st2 != "on" else st
            if st != "on":
                what = {"core": "건물 코어", "switch": "층 스위치", "ap": "무선 AP"}[self.dev[ent]["kind"]]
                return "uplink", TAG_NET + f"{what} {ent} {'재부팅' if st == 'boot' else (cause or '장애')}"
        return "ok", ""

    def _apply_net_power(self, now: float, dt_s: float) -> None:
        if not self.win and not self.tagged:
            return
        G = self.w.st.gw.arr
        affected = set(self.tagged)
        for ent in list(self.win):
            self.win[ent] = [x for x in self.win[ent] if x[2] > now]     # 끝난 구간 정리
            if not self.win[ent]:
                del self.win[ent]
                continue
            if ent.startswith("gw:"):
                affected.add(int(ent[3:]))
            else:
                affected.update(self.dev[ent]["gws"])
        for gw in affected:
            gs = self.w.gw_state[gw]
            want, reason = self._gw_effective(gw, now)
            ours = gs["reason"].startswith((TAG_NET, TAG_PWR))
            st = int(G["status"][gw])
            if want == "ok":
                if ours:
                    if gs.get("_booting"):                               # 부팅 끝: 방금 켜진 장비
                        gs["_booting"] = False
                    G["status"][gw] = 0
                    gs["reason"] = ""
                self.tagged.discard(gw)
                continue
            if st == GW_DOWN and not ours:
                continue                                                  # 다른 원인의 장애(하드웨어 고장 등)가 우선
            if want in ("down", "boot"):
                if want == "boot" and not gs.get("_booting"):             # 전원이 돌아와 부팅 시작: 가동 시간 0, CPU 부팅 부하
                    gs["_booting"] = True
                    G["uptime_s"][gw] = 0
                    gs["boot_at"] = now
                G["status"][gw] = GW_DOWN
            else:
                G["status"][gw] = GW_UPLINK
            gs["reason"] = reason
            gs["fault_until"] = 0.0                                       # 기존 복구 로직이 건드리지 않게
            self.tagged.add(gw)

    def device_fault(self, kind: str, target: str | None = None, cause: str = "장애", off_s: float | None = None) -> str:
        """kind: switch | ap | core.  target: 장비 id (없으면 무작위).  off_s: 장애 지속 (없으면 장비별 무작위)."""
        now = self.w.sim_time
        pool = [d for d in self.dev.values() if d["kind"] == kind]
        if not pool:
            return "no device"
        d = self.dev.get(target) if target in self.dev else pool[int(self.rng.integers(len(pool)))]
        off = float(off_s) if off_s else {"switch": self.rng.uniform(120, 1800), "ap": self.rng.uniform(60, 600), "core": self.rng.uniform(60, 300)}[kind]
        boot = {"switch": self.rng.uniform(60, 120), "ap": self.rng.uniform(30, 60), "core": self.rng.uniform(120, 240)}[kind]
        self._add_win(d["id"], now, off, boot, cause)
        name = {"switch": "층 스위치", "ap": "무선 AP", "core": "건물 코어 스위치"}[kind]
        where = f"{d['building']}" + (f" {d['floor']}층" if d.get("floor") is not None else "")
        self.w.log.add("network", f"{name} {d['id']} {cause} ({where}) — 게이트웨이 {len(d['gws'])}대 업링크 단절, {_dur(off)} 후 복구·재부팅 {boot:.0f}초")
        self.w.counters["net_device_faults"] = self.w.counters.get("net_device_faults", 0) + 1
        self._label_add("net_device", f"{d['id']} {cause}", gw=None, t0=time.time(), t1=time.time() + (off + boot) / self._speed(),
                        meta={"device": d["id"], "kind": kind, "gateways": len(d["gws"])})
        return f"{d['id']} {cause}: {len(d['gws'])} gateways lose uplink for {off:.0f}s"

    # ======================================================================= 2. 정전과 UPS
    def power_event(self, b: int | None = None, mains_s: float | None = None, cause: str = "한전 정전") -> str:
        h, now = self.w.hospital, self.w.sim_time
        cores = [d for d in self.dev.values() if d["kind"] == "core"]
        if not cores:
            return "no building"
        core = next((d for d in cores if d["building_idx"] == b), None) if b is not None else cores[int(self.rng.integers(len(cores)))]
        b = core["building_idx"]
        mains = float(mains_s) if mains_s else float(self.rng.uniform(180, 1200))
        ats = float(self.rng.uniform(8, 15))
        n = {"ups": 0, "emergency": 0, "normal": 0, "reblip": 0}
        for gw in core["gws"]:
            c = self.gw_up[gw]["power"]
            n[c] += 1
            boot = float(self.rng.uniform(30, 60))
            if c == "emergency":
                self._add_win(f"gw:{gw}", now, ats, boot, "발전기 전환")
                if self.rng.random() < 0.15:                              # 한전 복귀 시 재절체 순간 끊김
                    self._add_win(f"gw:{gw}", now + mains, 1.0, boot, "재절체")
                    n["reblip"] += 1
            elif c == "normal":
                self._add_win(f"gw:{gw}", now, mains, boot, "일반 전원 차단")
        sws = [d for d in self.dev.values() if d["kind"] == "switch" and d["building_idx"] == b and not d["ups"]]
        for d in sws:
            self._add_win(d["id"], now, ats, float(self.rng.uniform(60, 120)), "정전")
        bname = core["building"]
        self.w.log.add("network", f"{bname} {cause}: 발전기 전환 {ats:.0f}초 · 비상 계통 {n['emergency']}대 재부팅 · 일반 전원 {n['normal']}대 {_dur(mains)} 차단 · "
                                  f"UPS {n['ups']}대 유지 · UPS 없는 층 스위치 {len(sws)}대 재부팅 · 복전 재절체 {n['reblip']}대")
        self.w.counters["power_events"] = self.w.counters.get("power_events", 0) + 1
        sp = self._speed()
        self._label_add("power", f"{bname} {cause}", t0=time.time(), t1=time.time() + (mains + 60) / sp,
                        meta={"building": bname, "mains_s": round(mains), "ats_s": round(ats, 1), **n, "switches_rebooting": len(sws)})
        return f"power event {bname}: ats {ats:.0f}s, normal-circuit {n['normal']} gw down {mains:.0f}s"

    def _step_topology_faults(self, dt_s: float) -> None:
        net = self.w.cfg.get("scenario", "network")
        if not net.get("enabled") or not net.get("topology", True):
            return
        ni = net["intensity"] / 100.0
        for d in list(self.dev.values()):
            if d["id"] in self.win:
                continue
            p = {"core": 2e-7, "switch": 2e-6, "ap": 4e-6}[d["kind"]] * ni * dt_s
            if self.rng.random() < p:
                self.device_fault(d["kind"], d["id"], "장애")

    # ======================================================================= 3. 병원 일과
    def routine_block(self, t: float | None = None) -> tuple[str, str]:
        hr, _, _ = _hour_frac(self.w.sim_time if t is None else t)
        for a, b, key, label in ROUTINE:
            if a <= hr < b:
                return key, label
        return "night", "소등·수면"

    def routine_choose(self, rec: dict, prof: dict, intensity: float) -> bool:
        """활동을 새로 고를 때 일과가 먼저 끼어든다.  무언가를 배정했으면 True."""
        if not self.w.cfg.get("scenario", "routine", default={}).get("enabled", True):
            return False
        w, now, rng = self.w, self.w.sim_time, self.rng
        hr, hf, _ = _hour_frac(now)
        artk = 0.45 + 0.55 * intensity                                   # 사람이 움직이면 신호가 흔들린다 (시나리오와 무관), 강도는 세기만
        key, _ = self.routine_block(now)
        # 활력징후 측정: 해당 시각부터 40분 동안 병동을 돌며 환자마다 한 번
        vh = next((v for v in VITALS_HOURS if 0 <= hf - v < 40 / 60), None)
        if vh is not None and rec.get("_vitals") != (int(now // 86400), vh):
            if rng.random() < 0.5:
                rec["_vitals"] = (int(now // 86400), vh)
                w._set_activity(rec, "restless", rng.uniform(20, 60), rng.uniform(0.25, 0.45) * artk, "sitting")
                return True
        if key == "rounds":                                              # 회진: 병동마다 시작 시각이 다르고 한 병동 15분 남짓
            ward = int(rec.get("ward_idx", 0) or 0)
            start = 8 + (ward * 7 % 100) / 60.0
            if start <= hf < start + 0.25 and rec.get("_rounds") != int(now // 86400):
                rec["_rounds"] = int(now // 86400)
                w._set_activity(rec, "restless", rng.uniform(30, 120), rng.uniform(0.2, 0.4) * artk, "sitting")
                if rng.random() < 0.08:                                  # 회진 중 패치 확인 → 떼었다 다시 붙임
                    patch = w.patches.get(rec["row"])
                    if patch:
                        patch.lead_off_until = now + float(rng.uniform(10, 30))
                return True
        if key == "meal" and rec.get("_meal") != (int(now // 86400), hr) and rng.random() < 0.6:
            rec["_meal"] = (int(now // 86400), hr)
            w._set_activity(rec, "still", rng.uniform(900, 1800), rng.uniform(0.04, 0.1) * artk, "sitting")
            return True
        if key == "evening" and prof.get("mobility") != "bedridden" and rng.random() < 0.08:
            w._trip_shower(rec)
            return True
        if key in ("visit", "care") and 14 <= hf < 18 and rng.random() < 0.05:   # 면회: 앉아서 대화
            w._set_activity(rec, "restless", rng.uniform(300, 1200), rng.uniform(0.08, 0.18) * artk, "sitting")
            return True
        return False

    # ======================================================================= 4. 임상 악화
    def _deter_kind(self, prof: dict) -> str:
        dis, com = prof.get("disease", ""), prof.get("comorbidities") or []
        if prof.get("resp_kind") in ("copd", "apnea") or "폐" in dis or "호흡" in dis:
            return "resp"
        if "심부전" in dis or "심근" in dis:
            return "hf"
        if prof.get("glucose_profile") in ("diabetic", "hypo_risk") and self.rng.random() < 0.5:
            return "hypoglycemia"
        if any(k in dis for k in ("수술", "출혈", "위장관", "궤양")) and self.rng.random() < 0.4:
            return "bleeding"
        return "sepsis"

    def start_deterioration(self, pid: int, kind: str | None = None, fast: bool = False, ramp_min: float | None = None) -> str:
        w = self.w
        rec, prof = w.admitted.get(pid), w.by_id.get(pid)
        if not rec or rec.get("deter") or rec.get("code_blue"):
            return "not eligible"
        kind = kind if kind in DETER else self._deter_kind(prof)
        k = 0.25 if fast else 1.0                                        # 수동 주입은 빨리 진행해 관찰하기 쉽게
        rec["deter"] = {"kind": kind, "t0": w.sim_time, "ramp": self.rng.uniform(2, 6) * 3600 * k, "hold": self.rng.uniform(1, 3) * 3600 * k,
                        "recover": self.rng.uniform(1, 2) * 3600 * k, "mag": float(self.rng.uniform(0.7, 1.1)),
                        "escalate": bool(self.rng.random() < (0.35 if fast else 0.06)), "label_t0": time.time()}
        if ramp_min:                                                     # 관찰용: 진행 시간을 직접 지정 (최고조 유지·회복도 같은 길이)
            d = rec["deter"]; d["ramp"] = d["hold"] = d["recover"] = float(ramp_min) * 60
        w.log.add("clinical", f"{prof['name']} {DETER[kind]['label']} 진행 시작 (최고조까지 약 {rec['deter']['ramp'] / 3600:.1f}시간)", patient_id=pid)
        w.counters["deteriorations"] = w.counters.get("deteriorations", 0) + 1
        return f"{DETER[kind]['label']} on {prof['name']}"

    def deter_offsets(self, rec: dict, now: float) -> tuple[float, float, float, float, float]:
        d = rec.get("deter")
        cb = rec.get("code_blue")
        if cb:                                                           # 심정지: 호흡·산소포화도 소실
            return 0.0, -30.0, -60.0, 0.0, 0.0
        if not d:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        e = now - d["t0"]
        if e < d["ramp"]:
            f = e / d["ramp"]
        elif e < d["ramp"] + d["hold"]:
            f = 1.0
        else:
            f = max(0.0, 1.0 - (e - d["ramp"] - d["hold"]) / d["recover"])
        f = (f * f * (3 - 2 * f)) * d["mag"]                               # 부드러운 시작·끝
        p = DETER[d["kind"]]
        return p["hr"] * f, p["rr"] * f, p["spo2"] * f, p["temp"] * f, p["gl"] * f

    def _step_clinical(self, dt_s: float) -> None:
        w, now = self.w, self.w.sim_time
        cc = w.cfg.get("scenario", "clinical", default={})
        rate = float(cc.get("per_1000_patient_days", 20)) if cc.get("enabled", True) else 0.0
        p_new = rate / 1000.0 / 86400.0 * dt_s
        for pid, rec in list(w.admitted.items()):
            prof = w.by_id[pid]
            d = rec.get("deter")
            cb = rec.get("code_blue")
            if cb:
                if now >= cb["until"]:
                    self._end_code_blue(pid, rec, prof)
                continue
            if d:
                e = now - d["t0"]
                if d["escalate"] and e >= d["ramp"] and not d.get("escalated"):
                    d["escalated"] = True
                    self.code_blue(pid, cause=DETER[d["kind"]]["label"])
                elif e >= d["ramp"] + d["hold"] + d["recover"]:
                    self._label_add("deterioration", DETER[d["kind"]]["label"], patient_id=pid, patch_id=self._patch_id(rec),
                                    t0=d["label_t0"], t1=time.time(), meta={"kind": d["kind"]})
                    rec["deter"] = None
                    w.log.add("clinical", f"{prof['name']} {DETER[d['kind']]['label']} 호전 — 활력징후 기준치 복귀", patient_id=pid)
                continue
            if p_new and not rec["outpatient"] and not self._busy(pid, rec) and self.rng.random() < p_new:
                self.start_deterioration(pid)

    def _busy(self, pid: int, rec: dict) -> bool:
        """우선순위: 현장 테스트 > 실제 시그널 > 임상 악화·코드블루 > 부정맥 에피소드 > 루프 변형 순환."""
        ft = getattr(self.w, "ft", None)
        return "rs_paced" in rec or bool(ft and any(a["pid"] == pid for a in ft.active))

    def code_blue(self, pid: int, cause: str = "급성 심정지") -> str:
        w = self.w
        rec, prof = w.admitted.get(pid), w.by_id.get(pid)
        if not rec or rec.get("code_blue"):
            return "not eligible"
        if self._busy(pid, rec):
            return "not eligible (현장 테스트·실제 시그널 환자)"
        now = w.sim_time
        dur = float(self.rng.uniform(300, 1200))
        rec["code_blue"] = {"t0": now, "until": now + dur, "rosc": bool(self.rng.random() < 0.6), "cause": cause, "label_t0": time.time()}
        rec["episode_until"] = now + dur + 5                              # 일반 에피소드 로직이 리듬을 되돌리지 못하게
        w._switch_variant(rec["row"], w._variant_for("vfib", prof["age"]), w._tick_now())
        w._set_activity(rec, "restless", dur, 0.9, "supine")               # 흉부압박 아티팩트
        w.log.add("clinical", f"코드블루: {prof['name']} ({cause}) — 심실세동, 소생술 시작", patient_id=pid)
        w.counters["code_blue"] = w.counters.get("code_blue", 0) + 1
        return f"code blue on {prof['name']}"

    def _end_code_blue(self, pid: int, rec: dict, prof: dict) -> None:
        w, cb = self.w, rec["code_blue"]
        rec["code_blue"] = None
        self._label_add("code_blue", cb["cause"], patient_id=pid, patch_id=self._patch_id(rec), t0=cb["label_t0"], t1=time.time(),
                        meta={"outcome": "rosc" if cb["rosc"] else "death"})
        if rec.get("deter"):
            d = rec["deter"]
            self._label_add("deterioration", DETER[d["kind"]]["label"], patient_id=pid, patch_id=self._patch_id(rec), t0=d["label_t0"], t1=time.time(), meta={"kind": d["kind"]})
            rec["deter"] = None
        if self._busy(pid, rec):                                          # 코드블루 중 현장 테스트가 걸렸으면 리듬은 테스트가 정한다
            pass
        elif cb["rosc"]:
            rec["episode_until"] = w.sim_time + 1800                      # 자발순환 회복: 30분 동빈맥 뒤 기저 리듬
            w._switch_variant(rec["row"], w._variant_for("sinus_tachy", prof["age"]), w._tick_now())
            w._set_activity(rec, "still", 1800, 0.02, "supine")
            w.log.add("clinical", f"{prof['name']} 자발순환 회복(ROSC) — 동빈맥, 집중 관찰", patient_id=pid)
        else:
            w.log.add("clinical", f"{prof['name']} 소생술 중단 — 사망", patient_id=pid)
            w.discharge(pid, "사망 (코드블루)")

    # ======================================================================= 6. MCOT 단말
    def region_factor(self, prof: dict) -> float:
        a = prof.get("address") or {}
        return 2.0 if a.get("sido") in RURAL else 1.0

    def phone_step(self, rec: dict, gw: int, dt_s: float) -> None:
        w = self.w
        if not w.cfg.get("scenario", "mcot_device", default={}).get("enabled", True) or gw < 0:
            return
        G, gs, now = w.st.gw.arr, w.gw_state[gw], w.sim_time
        ph = rec.setdefault("phone", {"state": "ok", "until": 0.0, "t0": 0.0})
        if G["status"][gw] == GW_DOWN and not gs["reason"].startswith(TAG_PHONE):
            return                                                        # 배터리 방전 등 다른 원인이 우선
        if ph["state"] != "ok":
            if now >= ph["until"]:
                prev = ph["state"]
                self._label_add("mcot_device", {"killed": "앱 강제 종료", "saver": "절전 모드", "update": "OS 업데이트", "airplane": "비행기 모드"}[prev],
                                patient_id=rec["id"], patch_id=self._patch_id(rec), gw=gw, t0=ph["label_t0"], t1=time.time())
                ph.update(state="ok", until=0.0)
                if gs["reason"].startswith(TAG_PHONE):
                    G["status"][gw] = 0
                    gs["reason"] = ""
                w._mcot_uplink(gw, outside=rec["home_state"] == "outside")
            return
        hr, _, _ = _hour_frac(now)
        r = self.rng.random()
        per_h = dt_s / 3600.0
        new = None
        if gs.get("mobile_batt", 100) < 20 and r < 0.2 * per_h * 60:     # 배터리 20 % 미만이면 곧 절전 모드
            new = ("saver", self.rng.uniform(1800, 5400))
        elif r < 0.02 * per_h * (2.0 if hr >= 23 or hr < 7 else 1.0):   # OS 가 백그라운드 앱 정리 (밤에 더 잦다)
            new = ("killed", self.rng.uniform(300, 5400))
        elif 2 <= hr < 4 and r < 0.004 * per_h:
            new = ("update", self.rng.uniform(120, 420))
        elif r < 0.0005 * per_h:
            new = ("airplane", self.rng.uniform(1800, 10800))
        if not new:
            return
        state, dur = new
        ph.update(state=state, until=now + dur, t0=now, label_t0=time.time())
        prof = w.by_id[rec["id"]]
        if state == "saver":                                              # 일괄 업로드: 30~60초치를 모아서 보낸다
            G["latency_ms"][gw] = float(self.rng.uniform(30000, 60000))
            G["jitter_ms"][gw] = float(self.rng.uniform(1000, 5000))
        else:                                                             # 끊김: 패치 메모리에 쌓였다가 복구 때 올라간다
            G["status"][gw] = GW_UPLINK
            gs["reason"] = TAG_PHONE + {"killed": "앱 강제 종료 (OS 백그라운드 정리)", "update": "OS 업데이트 재부팅", "airplane": "비행기 모드"}[state]
        what = {"saver": "절전 모드 — 30~60초치를 모아 일괄 업로드", "killed": "앱 강제 종료", "update": "OS 업데이트 재부팅", "airplane": "비행기 모드"}[state]
        w.log.add("gateway", f"MCOT {prof['name']} 단말: {what} ({dur / 60:.0f}분)", gw=gw, patient_id=rec["id"])

    def force_phone(self, pid: int, state: str, minutes: float) -> str:
        """수동 주입: MCOT 환자 단말을 지정 상태로 (killed | saver | update | airplane)."""
        w = self.w
        rec = w.admitted.get(pid)
        if not rec or not rec["outpatient"] or state not in ("killed", "saver", "update", "airplane"):
            return "not an MCOT patient / unknown state"
        gw = rec["mobile_gw"]
        G, gs, now = w.st.gw.arr, w.gw_state[gw], w.sim_time
        rec["phone"] = {"state": state, "until": now + minutes * 60, "t0": now, "label_t0": time.time()}
        if state == "saver":
            G["latency_ms"][gw] = float(self.rng.uniform(30000, 60000)); G["jitter_ms"][gw] = float(self.rng.uniform(1000, 5000))
        else:
            G["status"][gw] = GW_UPLINK
            gs["reason"] = TAG_PHONE + {"killed": "앱 강제 종료 (OS 백그라운드 정리)", "update": "OS 업데이트 재부팅", "airplane": "비행기 모드"}[state]
        w.log.add("gateway", f"[수동] MCOT {w.by_id[pid]['name']} 단말 {state} {minutes:.0f}분", gw=gw, patient_id=pid)
        return f"phone {state} {minutes:.0f} min"

    # ======================================================================= 7. 재원 수 변동
    def census_target(self, base: int, now: float, beds: int) -> int:
        g = self.w.cfg.get("general")
        t = float(base)
        if g.get("census_mode", "fixed") == "weekly":
            hr, hf, dow = _hour_frac(now)
            h0, h1 = int(hf) % 24, (int(hf) + 1) % 24
            hm = CENSUS_HOUR[h0] + (CENSUS_HOUR[h1] - CENSUS_HOUR[h0]) * (hf - int(hf))
            t *= CENSUS_DOW[dow] * hm
        s = self.surge
        if s:
            if now < s["end"]:
                t += s["count"] * min(1.0, (now - s["start"]) / max(1.0, s["end"] - s["start"]))
            else:
                extra = s["count"] * math.exp(-(now - s["end"]) / 172800.0)   # 이틀 시상수로 퇴원
                if extra < 0.5:
                    self.surge = None
                t += extra
        return int(max(0, min(beds, round(t))))

    def start_surge(self, count: int | None = None, over_min: float = 60.0) -> str:
        w = self.w
        beds = w.hospital.bed_capacity
        n_in = sum(1 for r in w.admitted.values() if not r["outpatient"])
        room = max(0, beds - n_in)
        count = int(count if count else max(5, round(0.05 * beds)))
        count = min(count, room)
        if count <= 0:
            return "no free beds"
        now = w.sim_time
        self.surge = {"start": now, "end": now + over_min * 60, "count": count}
        w.log.add("adt", f"대량 환자 유입: {over_min:.0f}분 동안 {count}명 응급 입원 예정 (가용 병상 {room})")
        self._label_add("surge", f"{count}명 유입", t0=time.time(), t1=time.time() + over_min * 60 / self._speed(), meta={"count": count, "over_min": over_min})
        return f"surge: {count} admissions over {over_min:.0f} min"

    # ======================================================================= 5. 정답 라벨
    def _speed(self) -> float:
        return max(0.01, float(self.w.cfg.get("general", "sim_speed") or 1.0))

    def _patch_id(self, rec: dict):
        p = self.w.patches.get(rec["row"])
        return int(p.patch_id) if p else None

    def _label_add(self, kind: str, value: str, patient_id=None, patch_id=None, gw=None, t0: float = 0.0, t1: float | None = None, meta: dict | None = None) -> None:
        self.w.db.add_label(kind, value, patient_id, patch_id, gw, int(t0 * 1000), int(t1 * 1000) if t1 else None, meta)

    def _open(self, key: tuple, kind: str, value: str, t_ms: int, **kw) -> None:
        self.labels_open[key] = {"kind": kind, "value": value, "t0": t_ms, **kw}

    def _close(self, key: tuple, t_ms: int) -> None:
        o = self.labels_open.pop(key, None)
        if o:
            self.w.db.add_label(o["kind"], o["value"], o.get("patient_id"), o.get("patch_id"), o.get("gw"), o["t0"], t_ms, o.get("meta"))

    def _rhythm_of_variant(self, v: int) -> str:
        if not self._v2r:
            for r, vs in (self.w.rhythm_variants or {}).items():
                for x in vs:
                    self._v2r[int(x)] = r
        return self._v2r.get(int(v), "")

    def _step_labels(self) -> None:
        w = self.w
        P, G = w.st.patch.arr, w.st.gw.arr
        now_ms = int(time.time() * 1000)
        epoch_ms = w.st.ctl[CTL["epoch_ns"]] / 1e6
        bms = float(w.st.ctl[CTL["bundle_ms"]]) or 200.0
        alive = set()
        for pid, rec in w.admitted.items():
            row = rec["row"]
            alive.add(pid)
            prof = w.by_id[pid]
            pat = self._patch_id(rec)
            cur = {
                "rhythm": self._rhythm_of_variant(int(P["variant"][row])),
                "lead_off": bool(P["lead_off"][row]),
                "patch_off": rec.get("patch_removed_until", 0) > w.sim_time,
                "no_link": rec["gw"] < 0,
                "trip": bool(rec["trip"]) or rec["trip_step_until"] > w.sim_time,
            }
            prev = self.label_seen.get(pid, {})
            base = prof["rhythm"]
            # 리듬: 에피소드 전환 시각은 프레임 단위로 정확 (switch_tick)
            if cur["rhythm"] != prev.get("rhythm"):
                t_sw = int(epoch_ms + int(P["switch_tick"][row]) * bms) if epoch_ms else now_ms
                self._close(("rhythm", pid), t_sw)
                if cur["rhythm"] and cur["rhythm"] != base:
                    self._open(("rhythm", pid), "rhythm_episode", cur["rhythm"], t_sw, patient_id=pid, patch_id=pat, meta={"base": base})
            for k, kind, val in (("lead_off", "lead_off", "리드 오프"), ("patch_off", "patch_off", "패치 분리"), ("no_link", "no_link", "게이트웨이 미연결"),
                                 ("trip", "trip", rec.get("note") or "이동")):
                if cur[k] and not prev.get(k):
                    self._open((k, pid), kind, val, now_ms, patient_id=pid, patch_id=pat)
                elif not cur[k] and prev.get(k):
                    self._close((k, pid), now_ms)
            self.label_seen[pid] = cur
        for pid in [p for p in self.label_seen if p not in alive]:        # 퇴원: 열린 구간 모두 닫기
            for k in ("rhythm", "lead_off", "patch_off", "no_link", "trip"):
                self._close((k, pid), now_ms)
            del self.label_seen[pid]
        # 게이트웨이 장애 구간 (정상 이외 상태)
        for gw in range(len(w.hospital.gateways)):
            st = int(G["status"][gw])
            prev = self.gw_seen.get(gw, (0, ""))
            reason = w.gw_state[gw]["reason"]
            if st != prev[0]:
                self._close(("gw", gw), now_ms)
                if st:
                    self._open(("gw", gw), "gateway", {1: "성능 저하", 2: "장애", 3: "업링크 단절"}.get(st, str(st)) + (f" · {reason}" if reason else ""),
                               now_ms, gw=gw, meta={"status": st, "gw_id": w.hospital.gateways[gw]["id"]})
                self.gw_seen[gw] = (st, reason)

    def labels_open_view(self) -> list[dict]:
        out = [{"kind": o["kind"], "value": o["value"], "patient_id": o.get("patient_id"), "patch_id": o.get("patch_id"), "gw": o.get("gw"),
                "t_start_ms": o["t0"], "t_end_ms": None, "meta": o.get("meta")} for o in self.labels_open.values()]
        for pid, rec in self.w.admitted.items():                          # 진행 중인 악화·코드블루·단말 상태
            d, cb, ph = rec.get("deter"), rec.get("code_blue"), rec.get("phone") or {}
            base = {"patient_id": pid, "patch_id": self._patch_id(rec), "gw": None, "t_end_ms": None}
            if d:
                out.append({**base, "kind": "deterioration", "value": DETER[d["kind"]]["label"], "t_start_ms": int(d["label_t0"] * 1000), "meta": {"kind": d["kind"]}})
            if cb:
                out.append({**base, "kind": "code_blue", "value": cb["cause"], "t_start_ms": int(cb["label_t0"] * 1000), "meta": None})
            if ph.get("state", "ok") != "ok" and ph.get("label_t0"):
                out.append({**base, "kind": "mcot_device", "gw": rec.get("mobile_gw"), "value": ph["state"], "t_start_ms": int(ph["label_t0"] * 1000), "meta": None})
        return out

    def clear_events(self, clinical: bool = False, network: bool = False) -> dict:
        """프리셋이 해당 시나리오를 끌 때 진행 중인 사건도 정리한다 (깨끗한 기준선).  코드블루는 소생 성공으로 끝낸다."""
        w, n = self.w, {"clinical": 0, "network": 0}
        if clinical:
            for pid, rec in list(w.admitted.items()):
                if rec.get("code_blue"):
                    rec["code_blue"]["rosc"] = True
                    self._end_code_blue(pid, rec, w.by_id[pid]); n["clinical"] += 1
                d = rec.get("deter")
                if d:
                    self._label_add("deterioration", DETER[d["kind"]]["label"], patient_id=pid, patch_id=self._patch_id(rec), t0=d["label_t0"], t1=time.time(),
                                    meta={"kind": d["kind"], "ended": "preset"})
                    rec["deter"] = None; n["clinical"] += 1
        if network and self.win:
            n["network"] = len(self.win)
            self.win.clear()                                              # 다음 스텝에서 표시해 둔 게이트웨이가 정상으로 돌아온다
        if n["clinical"] or n["network"]:
            w.log.add("script", f"프리셋 전환: 진행 중이던 임상 사건 {n['clinical']}건 · 망/전원 장애 {n['network']}건 정리")
        return n

    # ======================================================================= 재현: 녹화
    def record_start(self, name: str) -> dict:
        self.recording = {"name": name, "t0": self.w.sim_time, "items": [], "started": time.time()}
        self.w.log.add("script", f"녹화 시작: {name} — 이후 수동 조작과 설정 변경을 시뮬 시각 기준으로 기록")
        return {"recording": True, "name": name}

    def record(self, what: str, target=None, params: dict | None = None) -> None:
        r = self.recording
        if r is not None and what not in ("record",):
            r["items"].append({"at": round(self.w.sim_time - r["t0"], 1), "what": what, "target": target, "params": params or {}})

    def record_stop(self) -> dict | None:
        r, self.recording = self.recording, None
        if r:
            self.w.log.add("script", f"녹화 종료: {r['name']} ({len(r['items'])}단계, {self.w.sim_time - r['t0']:.0f}초)")
        return r

    # ======================================================================= 8. 전파 방해 (2.4 GHz 혼잡)
    def _step_rf(self, dt_s: float) -> None:
        w, now = self.w, self.w.sim_time
        rf = w.cfg.get("scenario", "rf_noise", default={}) or {}
        net_on = bool((w.cfg.get("scenario", "network", default={}) or {}).get("enabled"))
        lvl = float(rf.get("level", 0)) / 100.0 if rf.get("enabled") and net_on else 0.0    # 네트워크 장애 카드의 '시나리오 사용'에 딸림
        G = w.st.gw.arr
        if not lvl:
            if getattr(self, "_rf_gws", None):                            # 끌 때 무선 GW 손실·지연 원복
                for gw in self._rf_gws:
                    if G["status"][gw] == 0:
                        G["loss"][gw] = 0.0; G["latency_ms"][gw] = w.gw_state[gw]["base_lat"]; G["jitter_ms"][gw] = w.gw_state[gw]["base_jit"]
                self._rf_gws = set()
            return
        p = 12.0 * lvl / 3600.0 * dt_s                                   # 환자당 시간당 최대 12번의 짧은 BLE 끊김
        for rec in w.admitted.values():
            if rec["outpatient"] or rec.get("rf_drop_until", 0) > now or rec["gw"] < 0:
                continue
            if self.rng.random() < p:
                rec["rf_drop_until"] = now + float(self.rng.uniform(3, 25))
                w._relink(rec)
        if now - getattr(self, "_rf_t", 0.0) >= 30.0:                    # 30초마다 무선 AP 경유 GW 의 손실·지연을 흔든다
            self._rf_t = now
            self._rf_gws = getattr(self, "_rf_gws", set())
            for gw, u in self.gw_up.items():
                if u["uplink"] == "wifi" and G["status"][gw] == 0:
                    G["loss"][gw] = float(self.rng.uniform(0.0, 0.06) * lvl)
                    G["latency_ms"][gw] = float(self.rng.uniform(5, 60) * lvl)
                    G["jitter_ms"][gw] = float(self.rng.uniform(5, 40) * lvl)
                    self._rf_gws.add(gw)
        if now - getattr(self, "_rf_log_t", now) >= 60.0 or not hasattr(self, "_rf_log_t"):
            n = w.counters.get("rf_drops", 0) - getattr(self, "_rf_log_n", 0)
            if hasattr(self, "_rf_log_t") and n:
                w.log.add("link", f"전파 방해: 최근 1분 BLE 끊김 {n}건 (2.4 GHz 혼잡, 강도 {int(lvl * 100)} %)")
            self._rf_log_t, self._rf_log_n = now, w.counters.get("rf_drops", 0)

    # ======================================================================= 매 스텝
    def step(self, dt_s: float) -> None:
        now = self.w.sim_time
        self._step_topology_faults(dt_s)
        self._apply_net_power(now, dt_s)
        self._step_rf(dt_s)
        if now - self._last10 >= 10.0:                                    # 임상 진행은 10초 단위로 충분
            self._step_clinical(now - self._last10 if self._last10 else 10.0)
            self._last10 = now
        self._step_labels()

    def status(self) -> dict:
        w, now = self.w, self.w.sim_time
        key, label = self.routine_block(now)
        det = []
        for pid, rec in w.admitted.items():
            d, cb = rec.get("deter"), rec.get("code_blue")
            if d or cb:
                prof = w.by_id[pid]
                e = now - (d["t0"] if d else cb["t0"])
                stage = "코드블루" if cb else ("악화 중" if d and e < d["ramp"] else "최고조" if d and e < d["ramp"] + d["hold"] else "회복 중")
                det.append({"patient_id": pid, "name": prof["name"], "kind": (DETER[d["kind"]]["label"] if d else cb["cause"]), "stage": stage,
                            "elapsed_min": round(e / 60), "row": rec["row"]})
        down = [{"id": ent, "state": self._state(ent, now)[0], "cause": self._state(ent, now)[1], "gws": len(self.dev[ent]["gws"]) if ent in self.dev else 1}
                for ent in self.win if not ent.startswith("gw:") and self._state(ent, now)[0] != "on"]
        phones = {}
        for rec in w.admitted.values():
            if rec["outpatient"]:
                s = (rec.get("phone") or {}).get("state", "ok")
                phones[s] = phones.get(s, 0) + 1
        s = self.surge
        return {"routine": {"key": key, "label": label}, "deteriorating": det,
                "network": {"devices": {k: sum(1 for d in self.dev.values() if d["kind"] == k) for k in ("core", "switch", "ap")},
                            "down": down, "power_windows": sum(1 for e in self.win if e.startswith("gw:")),
                            "switch_no_ups": sum(1 for d in self.dev.values() if d["kind"] == "switch" and not d["ups"])},
                "phones": phones, "surge": ({"count": s["count"], "until": s["end"]} if s else None),
                "recording": ({"name": self.recording["name"], "items": len(self.recording["items"])} if self.recording else None)}
