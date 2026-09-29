"""Rhythm catalogue and beat-sequence generators.

Each generator returns a list of beats: dict(t=R-peak time [s], kind=str,
extra=dict).  Beat times are later scaled so the total equals the loop length
exactly, and rendering wraps around, so the loop is seamless.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

# name, korean label, class, weight among heart patients, weight among others
RHYTHMS: dict[str, dict] = {
    "nsr":          {"label": "정상 동율동 (NSR)", "cls": "normal", "w_heart": 22, "w_other": 70, "hr": (58, 95)},
    "sinus_brady":  {"label": "동서맥 (Sinus Bradycardia)", "cls": "brady", "w_heart": 5, "w_other": 4, "hr": (38, 57)},
    "sinus_tachy":  {"label": "동빈맥 (Sinus Tachycardia)", "cls": "tachy", "w_heart": 5, "w_other": 10, "hr": (101, 145)},
    "afib":         {"label": "심방세동 (AFib)", "cls": "afib", "w_heart": 14, "w_other": 3, "hr": (60, 115)},
    "afib_rvr":     {"label": "심방세동 빠른 심실반응 (AFib RVR)", "cls": "afib", "w_heart": 5, "w_other": 1, "hr": (115, 160)},
    "aflutter":     {"label": "심방조동 (Atrial Flutter)", "cls": "afib", "w_heart": 4, "w_other": 0.5, "hr": (70, 150)},
    "pvc":          {"label": "심실조기수축 (PVC)", "cls": "ectopy", "w_heart": 8, "w_other": 4, "hr": (58, 95)},
    "pvc_bigeminy": {"label": "심실 이단맥 (Bigeminy)", "cls": "ectopy", "w_heart": 3, "w_other": 0.5, "hr": (55, 85)},
    "pac":          {"label": "심방조기수축 (PAC)", "cls": "ectopy", "w_heart": 5, "w_other": 3, "hr": (58, 95)},
    "nsvt":         {"label": "비지속성 심실빈맥 (NSVT)", "cls": "vt", "w_heart": 3, "w_other": 0.3, "hr": (60, 95)},
    "vt":           {"label": "지속성 심실빈맥 (VT)", "cls": "vt", "w_heart": 1, "w_other": 0.1, "hr": (140, 190)},
    "svt":          {"label": "상심실성 빈맥 (SVT episodes)", "cls": "tachy", "w_heart": 3, "w_other": 1, "hr": (60, 95)},
    "avb1":         {"label": "1도 방실차단", "cls": "block", "w_heart": 4, "w_other": 1, "hr": (55, 85)},
    "avb2_m1":      {"label": "2도 방실차단 Mobitz I (Wenckebach)", "cls": "block", "w_heart": 2, "w_other": 0.3, "hr": (60, 85)},
    "avb2_m2":      {"label": "2도 방실차단 Mobitz II", "cls": "block", "w_heart": 1.5, "w_other": 0.2, "hr": (60, 85)},
    "avb3":         {"label": "3도 (완전) 방실차단", "cls": "block", "w_heart": 1.5, "w_other": 0.1, "hr": (30, 45)},
    "lbbb":         {"label": "좌각차단 (LBBB)", "cls": "bbb", "w_heart": 3, "w_other": 0.5, "hr": (58, 95)},
    "rbbb":         {"label": "우각차단 (RBBB)", "cls": "bbb", "w_heart": 3, "w_other": 1, "hr": (58, 95)},
    "stemi":        {"label": "ST 상승 (STEMI)", "cls": "ischemia", "w_heart": 3, "w_other": 0.2, "hr": (70, 110)},
    "ischemia":     {"label": "ST 하강 / 허혈", "cls": "ischemia", "w_heart": 4, "w_other": 0.5, "hr": (65, 105)},
    "paced_vvi":    {"label": "단일심실 페이싱 (VVI/VVIR)", "cls": "paced", "w_heart": 0, "w_other": 0, "hr": (60, 70)},
    "paced_aai":    {"label": "단일심방 페이싱 (AAI/AAIR)", "cls": "paced", "w_heart": 0, "w_other": 0, "hr": (60, 70)},
    "paced_ddd":    {"label": "이중방 페이싱 (DDD/DDDR)", "cls": "paced", "w_heart": 0, "w_other": 0, "hr": (60, 70)},
    "paced_crt":    {"label": "양심실 페이싱 (CRT)", "cls": "paced", "w_heart": 0, "w_other": 0, "hr": (60, 70)},
    "paced_malfunction": {"label": "페이스메이커 오작동 (캡처실패/언더센싱/오버센싱)", "cls": "paced", "w_heart": 0, "w_other": 0, "hr": (60, 70)},
    "sinus_pause":  {"label": "동정지 / 동휴지 (Sinus Pause)", "cls": "brady", "w_heart": 2, "w_other": 0.3, "hr": (55, 85)},
    "vfib":         {"label": "심실세동 (VFib)", "cls": "lethal", "w_heart": 0.3, "w_other": 0.05, "hr": (0, 0)},
}
PACED_RHYTHMS = ("paced_vvi", "paced_aai", "paced_ddd", "paced_crt", "paced_malfunction")
CONDUCTED = {"N", "A", "V", "Vp", "AVp", "J", "E", "S", "Ap", "AsVp", "CRT", "F", "a", "FV"}
# 정답(TRUTH) 레코드의 박동 종류 코드 (append-only): N 정상, A 심방조기, V 심실조기, Vp 심실페이싱, AVp 방실페이싱, J 접합부, E 이탈, S 상심실, Ap 심방페이싱, AsVp 심방감지-심실페이싱, CRT 양심실, F 융합
# 12 a 변행전도 심방조기(aberrant PAC, RBBB 형), 13 FV 심실 융합(심실 박동 + 정상 전도의 융합; VT 중 capture/fusion)
BEAT_KINDS = ["N", "A", "V", "Vp", "AVp", "J", "E", "S", "Ap", "AsVp", "CRT", "F", "a", "FV"]
BEAT_KIND_ID = {k: i for i, k in enumerate(BEAT_KINDS)}
# ANSI/AAMI EC57 5 등급: N 정상(정상·각차단·접합부 이탈), S 상심실 이소성, V 심실 이소성(심실 이탈 포함), F 융합(심실+정상), Q 분류불가/페이싱
AAMI = {"N": "N", "J": "N", "A": "S", "a": "S", "S": "S", "V": "V", "E": "V", "FV": "F",
        "Vp": "Q", "AVp": "Q", "Ap": "Q", "AsVp": "Q", "CRT": "Q", "F": "Q"}
AAMI_CLASSES = ["N", "S", "V", "F", "Q"]
SPIKE_A, SPIKE_V, SPIKE_LV = 0, 1, 2


class HRModel:
    """Instantaneous heart-rate function of time (periodic over the loop)."""

    def __init__(self, rng: np.random.Generator, hr_mean: float, seconds: float,
                 resp_phase: Callable[[float], float], hrv: float = 1.0):
        self.hr_mean = hr_mean
        self.T = seconds
        self.resp_phase = resp_phase
        self.lf_amp = 0.035 * hrv * rng.uniform(0.5, 1.5)
        self.hf_amp = 0.045 * hrv * rng.uniform(0.4, 1.4)   # RSA
        self.lf_cycles = max(1, int(round(0.1 * seconds)))    # ~0.1 Hz Mayer wave
        self.lf_phase = rng.uniform(0, 2 * math.pi)
        # very-low-frequency trends: periods T/k
        self.vlf = [(k, rng.uniform(0.02, 0.06) * hrv, rng.uniform(0, 2 * math.pi)) for k in range(1, 5)]
        self.noise = 0.012 * hrv
        self.rng = rng

    def rr(self, t: float) -> float:
        mod = self.lf_amp * math.sin(2 * math.pi * self.lf_cycles * t / self.T + self.lf_phase)
        mod += self.hf_amp * math.sin(self.resp_phase(t))
        for k, a, ph in self.vlf:
            mod += a * math.sin(2 * math.pi * k * t / self.T + ph)
        mod += self.noise * self.rng.standard_normal()
        return 60.0 / self.hr_mean * (1.0 + mod)


def _beat(t: float, kind: str = "N", **extra) -> dict:
    return {"t": t, "kind": kind, "extra": extra}


def generate_beats(rhythm: str, rng: np.random.Generator, seconds: float, hr_mean: float,
                   resp_phase: Callable[[float], float], hrv: float = 1.0) -> tuple[list[dict], dict]:
    """Return (beats, info).  info describes global overlays (fwaves, ptrain, vfib)."""
    hrm = HRModel(rng, hr_mean, seconds, resp_phase, hrv)
    beats: list[dict] = []
    info: dict = {"overlay": None}
    t = rng.uniform(0.2, 0.8)

    if rhythm == "vfib":
        info["overlay"] = {"type": "vfib", "freq": float(rng.uniform(4.0, 6.5)), "amp": float(rng.uniform(0.35, 0.9))}
        return beats, info

    if rhythm in ("afib", "afib_rvr"):
        info["overlay"] = {"type": "fwave", "freq": float(rng.uniform(5.5, 8.5)), "amp": float(rng.uniform(0.02, 0.09))}
        sigma = rng.uniform(0.18, 0.30)
        refr = rng.uniform(0.30, 0.38)                   # AV 결절 불응기: 그보다 짧은 RR 은 나오지 않는다
        pvc_af = rng.uniform(0.0, 0.03)
        rr_prev = hrm.rr(t)
        rr_med = 60.0 / hr_mean
        while t < seconds:
            rr = hrm.rr(t) * float(np.exp(rng.normal(0, sigma)))
            rr = float(np.clip(rr, refr, 2.2))
            # Ashman 현상: 긴 RR 뒤의 짧은 RR 은 우각 불응으로 변행전도(RBBB 형) — 라벨은 N(AAMI N), 파형만 넓다
            aberr = rr_prev > 1.25 * rr_med and rr < 0.72 * rr_med and rng.random() < 0.7
            if pvc_af and rng.random() < pvc_af:
                beats.append(_beat(t, "V", nop=True, focus=0))
            else:
                beats.append(_beat(t, "N", nop=True, aberr=aberr))
            t += rr
            rr_prev = rr
        return beats, info

    if rhythm == "aflutter":
        f_rate = rng.uniform(250, 340) / 60.0          # F waves /s
        info["overlay"] = {"type": "flutter", "freq": float(f_rate), "amp": float(rng.uniform(0.10, 0.25))}
        ratio = int(rng.choice([2, 3, 4], p=[0.55, 0.15, 0.30]))
        f_int = 1.0 / f_rate
        while t < seconds:
            beats.append(_beat(t, "N", nop=True))
            if rng.random() < 0.06:
                ratio = int(rng.choice([2, 3, 4]))
            t += ratio * f_int + rng.normal(0, 0.006)
        return beats, info

    if rhythm == "avb3":
        p_rate = rng.uniform(65, 95)
        info["overlay"] = {"type": "ptrain", "rate": float(p_rate)}
        rr_v = 60.0 / hr_mean
        while t < seconds:
            beats.append(_beat(t, "E", nop=True))
            t += rr_v * (1 + rng.normal(0, 0.03))
        return beats, info

    if rhythm == "vt":
        rr_v = 60.0 / hr_mean
        while t < seconds:
            u = rng.random()
            if u < 0.02:
                beats.append(_beat(t, "N", nop=True))                       # capture beat: 동방 자극이 잠깐 심실을 잡음 (좁은 QRS)
            elif u < 0.05:
                beats.append(_beat(t, "FV", nop=True))                      # fusion beat: 심실 박동 + 정상 전도의 융합
            else:
                beats.append(_beat(t, "V", nop=True, focus=0))              # 단형(monomorphic) VT: 한 초점
            t += rr_v * (1 + rng.normal(0, 0.025))
        return beats, info

    # ------------ sinus-based rhythms with state machines
    pvc_p = 0.0
    pac_p = 0.0
    # 심실조기수축: 환자마다 결합 간격(coupling)이 거의 일정(단초점), 30 % 는 다초점(두 형태·두 결합 간격), 커플릿·삽입성 PVC·삼단맥/사단맥 패턴
    pvc_coup = float(rng.uniform(0.45, 0.65)); pvc_coup2 = float(rng.uniform(0.40, 0.70))
    multifocal = rng.random() < 0.30
    couplet_p = float(rng.uniform(0.03, 0.12))
    interp_p = 0.20 if hr_mean < 70 else 0.04                     # 서맥이면 보상휴지 없는 삽입성 PVC 가 흔하다
    pvc_pattern = "random"
    if rhythm == "pvc":
        pvc_p = rng.uniform(0.03, 0.15)
        pvc_pattern = str(rng.choice(["random", "trigeminy", "quadrigeminy"], p=[0.7, 0.2, 0.1]))
    elif rhythm == "pac":
        pac_p = rng.uniform(0.04, 0.15)
    # 심방조기수축: 결합 간격 일정, 15 % 변행전도(a), 10 % 차단된 PAC(QRS 없는 P + 휴지), 가끔 심방 커플릿
    pac_coup = float(rng.uniform(0.55, 0.75)); pac_aberr_p = 0.15; pac_block_p = 0.10; pac_couplet_p = 0.05
    n_since_pvc = 0
    pause_p = rng.uniform(0.004, 0.012) if rhythm == "sinus_pause" else 0.0
    wenck_len = int(rng.integers(3, 7)) if rhythm == "avb2_m1" else 0
    m2_drop_p = rng.uniform(0.10, 0.30) if rhythm == "avb2_m2" else 0.0
    m2_fixed = rhythm == "avb2_m2" and rng.random() < 0.4      # 2:1 block variant
    episode_types = {"nsvt": ("V", rng.uniform(150, 200), (4, 20)),
                     "svt": ("S", rng.uniform(160, 220), (30, 400))}
    ep = episode_types.get(rhythm)
    next_ep_t = rng.uniform(60, 400) if ep else float("inf")
    if rhythm in PACED_RHYTHMS:
        return _generate_paced(rhythm, rng, seconds, hr_mean, resp_phase, hrv, t)
    big_toggle = False
    wenck_i = 0
    while t < seconds:
        rr = hrm.rr(t)
        # ---- episodes (NSVT/SVT)
        if ep and t >= next_ep_t:
            kind, ep_hr, (lo, hi) = ep
            n = int(rng.integers(lo, hi))
            rr_e = 60.0 / ep_hr
            beats.append(_beat(t, "N"))                      # last sinus beat, then premature onset
            te = t + rr * rng.uniform(0.5, 0.7)
            for _ in range(n):
                beats.append(_beat(te, kind, nop=True))
                te += rr_e * (1 + rng.normal(0, 0.02))
            t = te + rr * 1.3                                # post-episode pause
            next_ep_t = t + rng.uniform(90, 600)
            continue
        # ---- Wenckebach
        if wenck_len:
            incs = [0.0, 0.08, 0.05, 0.03, 0.02, 0.015, 0.01]  # PR 증가폭이 점점 줄어(전형적 Wenckebach) RR 은 점점 짧아진다
            pr_ext = 0.16 + sum(incs[: min(wenck_i, len(incs) - 1) + 1])
            if wenck_i >= wenck_len:
                beats.append(_beat(t, "P"))                   # dropped QRS
                wenck_i = 0
                t += rr
                continue
            beats.append(_beat(t, "N", pr=pr_ext))
            wenck_i += 1
            step = incs[min(wenck_i, len(incs) - 1)] - incs[min(wenck_i - 1, len(incs) - 1)] if wenck_i >= 2 else 0.0
            t += rr + step                                    # PR 이 늘어난 만큼 RR 이 늘고, 증가폭이 줄면 RR 이 짧아진다
            continue
        # ---- Mobitz II
        if m2_drop_p:
            drop = (big_toggle if m2_fixed else rng.random() < m2_drop_p)
            big_toggle = not big_toggle
            beats.append(_beat(t, "P" if drop else "N", pr=0.20))
            t += rr
            continue
        # ---- bigeminy: 결합 간격 일정(±20 ms), 완전 보상휴지
        if rhythm == "pvc_bigeminy":
            beats.append(_beat(t, "N"))
            coup = rr * pvc_coup + rng.normal(0, 0.02)
            beats.append(_beat(t + coup, "V", focus=0))
            t += 2 * rr * 1.02
            continue
        # ---- PVC: 패턴(삼단맥/사단맥) 또는 확률, 단초점/다초점, 커플릿, 삽입성
        n_since_pvc += 1
        fire = (pvc_pattern == "trigeminy" and n_since_pvc >= 2) or (pvc_pattern == "quadrigeminy" and n_since_pvc >= 3) or \
               (pvc_pattern == "random" and pvc_p and rng.random() < pvc_p)
        if pvc_p and fire:
            beats.append(_beat(t, "N"))                       # 선행 동조율 박동
            focus = 1 if (multifocal and rng.random() < 0.4) else 0
            coup = rr * (pvc_coup2 if focus else pvc_coup) + rng.normal(0, 0.02)
            beats.append(_beat(t + coup, "V", focus=focus))
            n_since_pvc = 0
            if rng.random() < couplet_p:                      # 커플릿: 두 번째 심실 박동이 짧은 간격으로
                beats.append(_beat(t + coup + rr * rng.uniform(0.40, 0.55), "V", focus=focus))
                t += 2 * rr + rr * 0.45
            elif rng.random() < interp_p and coup < 0.55 * rr: # 삽입성: 동조율은 흔들리지 않고 다음 박동이 제때 온다
                t += rr
            else:
                t += 2 * rr                                   # 완전 보상휴지
            continue
        if pac_p and rng.random() < pac_p:
            beats.append(_beat(t, "N"))
            coup = rr * pac_coup + rng.normal(0, 0.02)
            u = rng.random()
            if u < pac_block_p:                               # 차단된 PAC: 조기 P 만 있고 QRS 없음 → 휴지
                beats.append(_beat(t + coup, "P"))
                t += coup + rr * 1.15
            else:
                kind = "a" if u < pac_block_p + pac_aberr_p else "A"   # 변행전도(RBBB 형) 또는 정상 전도
                beats.append(_beat(t + coup, kind))
                if rng.random() < pac_couplet_p:
                    beats.append(_beat(t + coup + rr * rng.uniform(0.55, 0.7), "A"))
                    t += coup + rr * 0.62 + rr * 1.08
                else:
                    t += coup + rr * 1.08                     # 비보상휴지
            continue
        if pause_p and rng.random() < pause_p:
            t += rr * rng.uniform(1.8, 3.5)                    # sinus pause
            u = rng.random()
            if u < 0.45:
                beats.append(_beat(t, "J"))                    # junctional escape
                t += rr
            elif u < 0.6:
                beats.append(_beat(t, "E", focus=0))           # ventricular escape (넓은 QRS)
                t += rr
            continue
        pr_override = rng.uniform(0.22, 0.32) if rhythm == "avb1" else None
        beats.append(_beat(t, "N", pr=pr_override) if pr_override else _beat(t, "N"))
        t += rr
    return beats, info


def pick_hr(rhythm: str, rng: np.random.Generator) -> float:
    lo, hi = RHYTHMS[rhythm]["hr"]
    if lo == hi == 0:
        return 0.0
    return float(rng.uniform(lo, hi))


# ----------------------------------------------------------------------------- pacemakers
def _sensor_rate(rng: np.random.Generator, seconds: float, lower: float, upper_sensor: float):
    """Rate-responsive (xxxR) sensor-indicated rate profile: a few activity bouts per hour, periodic."""
    n_b = int(rng.integers(1, 4))
    bouts = [(rng.uniform(0, seconds), rng.uniform(60, 300), rng.uniform(0.3, 0.8)) for _ in range(n_b)]

    def rate(t: float) -> float:
        a = 0.0
        for c, w, lvl in bouts:
            d = ((t - c + seconds / 2) % seconds) - seconds / 2
            a = max(a, lvl * math.exp(-0.5 * (d / (w / 2.5)) ** 4))
        return lower + (upper_sensor - lower) * a
    return rate


def _generate_paced(rhythm: str, rng: np.random.Generator, seconds: float, lower: float,
                    resp_phase: Callable[[float], float], hrv: float, t0: float) -> tuple[list[dict], dict]:
    """Event-driven pacemaker simulation.  Beat extras carry `spikes`: list of (offset_s rel. R, type)."""
    beats: list[dict] = []
    info: dict = {"overlay": None, "pm": {}}
    rate_resp = rng.random() < 0.7
    upper_sensor = rng.uniform(85, 120)
    rate_fn = _sensor_rate(rng, seconds, lower, upper_sensor) if rate_resp else (lambda t: lower)
    avd = rng.uniform(0.14, 0.21)                       # AV delay
    fusion_win = 0.04
    # ---- underlying intrinsic rhythm
    sub = ""
    if rhythm == "paced_vvi":
        sub = "af" if rng.random() < 0.6 else "snd"     # AF w/ slow VR, or sinus node dysfunction
    elif rhythm == "paced_aai":
        sub = "snd"
    elif rhythm == "paced_ddd":
        sub = str(rng.choice(["avb", "snd", "both"], p=[0.45, 0.35, 0.20]))
    elif rhythm == "paced_crt":
        sub = "crt_avb" if rng.random() < 0.5 else "crt_sinus"
    elif rhythm == "paced_malfunction":
        sub = str(rng.choice(["loss_of_capture", "undersensing", "oversensing"]))
    info["pm"] = {"sub": sub, "rate_responsive": rate_resp, "lower": lower, "av_delay": avd}
    intrinsic_hr = {"af": rng.uniform(40, 75), "snd": rng.uniform(40, 66), "avb": rng.uniform(65, 95), "both": rng.uniform(35, 55),
                    "crt_avb": rng.uniform(65, 90), "crt_sinus": rng.uniform(60, 85)}.get(sub, rng.uniform(45, 65))
    if sub in ("loss_of_capture", "oversensing"):
        intrinsic_hr = rng.uniform(35, 55)
    elif sub == "undersensing":
        intrinsic_hr = rng.uniform(55, 80)
    hrm = HRModel(rng, intrinsic_hr, seconds, resp_phase, hrv)
    af_sigma = rng.uniform(0.2, 0.3)
    if sub == "af":
        info["overlay"] = {"type": "fwave", "freq": float(rng.uniform(5.5, 8.5)), "amp": float(rng.uniform(0.02, 0.07))}
    pause_p = 0.02 if sub in ("snd", "both") else 0.0

    def next_intrinsic(t_last: float) -> float:
        rr = hrm.rr(t_last)
        if sub == "af":
            rr *= float(np.exp(rng.normal(0, af_sigma)))
        if pause_p and rng.random() < pause_p:
            rr *= rng.uniform(2.0, 4.0)
        return t_last + max(0.3, rr)

    t_last_v = t0                      # last ventricular event (sensed or paced)
    t_last_a = t0
    t_int = next_intrinsic(t0)         # next intrinsic atrial (sinus) or ventricular (AF) event
    n_events = 0
    vent_paced_kind = "CRT" if rhythm == "paced_crt" else "Vp"
    lv_offset = rng.uniform(0.0, 0.03) if rhythm == "paced_crt" else 0.0
    V_SP = -0.080                                        # spike ~15 ms before the paced QRS onset (R peak = 0)
    v_spikes = [(V_SP, SPIKE_V)] + ([(V_SP + lv_offset, SPIKE_LV)] if rhythm == "paced_crt" else [])
    fixed_int = 60.0 / lower
    inhibit_until = 0.0
    while t_last_v < seconds and n_events < 20000:
        n_events += 1
        pi = 60.0 / rate_fn(t_last_v)
        t_pace = t_last_v + pi
        # ------------------------------------------------- single chamber ventricular (VVI) family
        if rhythm in ("paced_vvi", "paced_malfunction"):
            if sub == "undersensing":                   # fixed-rate firing, ignores intrinsic beats
                t_pace = t_last_a + fixed_int           # t_last_a used as 'last spike time' here
                if t_int < t_pace:
                    beats.append(_beat(t_int, "N" if rng.random() < 0.9 else "V"))
                    t_last_v = t_int
                    t_int = next_intrinsic(t_int)
                    continue
                t_last_a = t_pace
                if t_pace - t_last_v > 0.35:            # outside refractory -> captures
                    beats.append(_beat(t_pace, "Vp", spikes=v_spikes))
                    t_last_v = t_pace
                    t_int = next_intrinsic(t_pace)
                else:                                   # spike lands in T wave / refractory: no capture
                    beats.append(_beat(t_pace, "Sp", spikes=[(0.0, SPIKE_V)]))
                continue
            if sub == "oversensing" and t_pace > inhibit_until and rng.random() < 0.08:
                inhibit_until = t_pace + rng.uniform(1.5, 4.0)      # pacing inhibited (myopotential oversensing)
            if sub == "oversensing" and t_pace < inhibit_until:
                t_pace = inhibit_until
            if t_int < t_pace - fusion_win:             # intrinsic beat inhibits the pacemaker
                beats.append(_beat(t_int, "N" if sub != "af" else "N", nop=(sub == "af")))
                t_last_v = t_int
                t_int = next_intrinsic(t_int)
                continue
            if abs(t_int - t_pace) <= fusion_win:       # fusion / pseudo-fusion
                beats.append(_beat(t_pace, "F", spikes=[(-0.055, SPIKE_V)]))
                t_last_v = t_pace
                t_int = next_intrinsic(t_pace)
                continue
            if sub == "loss_of_capture" and rng.random() < 0.45:
                beats.append(_beat(t_pace, "Sp", spikes=[(0.0, SPIKE_V)]))       # spike, no QRS
                t_last_v = t_pace                        # timer restarts (pacemaker thinks it paced)
                if t_int < t_pace:
                    t_int = next_intrinsic(t_pace)
                continue
            beats.append(_beat(t_pace, "Vp", spikes=[(V_SP, SPIKE_V)]))
            t_last_v = t_pace
            t_int = next_intrinsic(t_pace)              # paced beat resets the intrinsic timer (retrograde)
            continue
        # ------------------------------------------------- single chamber atrial (AAI)
        if rhythm == "paced_aai":
            pr = rng.uniform(0.16, 0.22)
            if t_int < t_pace - fusion_win:             # sinus faster than lower rate -> sensed, inhibited
                beats.append(_beat(t_int + pr, "N"))
                t_last_v = t_int
                t_int = next_intrinsic(t_int)
            else:                                       # atrial paced, intrinsic conduction
                beats.append(_beat(t_pace + pr, "Ap", spikes=[(-pr - 0.07, SPIKE_A)]))
                t_last_v = t_pace
                t_int = next_intrinsic(t_pace)
            continue
        # ------------------------------------------------- dual chamber (DDD / CRT)
        # atrial event: sensed (sinus) or paced at lower rate; then ventricle: conducted (if AV ok) or paced after AVD
        a_paced = t_int >= t_pace - fusion_win
        t_a = t_pace if a_paced else t_int
        av_ok = sub in ("snd", "crt_sinus") or (sub == "both" and rng.random() < 0.15)
        if av_ok:                                        # intrinsic conduction -> narrow QRS, ventricle inhibited
            pr = rng.uniform(0.16, 0.24)
            if rhythm == "paced_crt":                    # CRT: biventricular pacing is forced even with intact AV (short AVD)
                beats.append(_beat(t_a + avd, "CRT", spikes=([(-avd - 0.07, SPIKE_A)] if a_paced else []) + v_spikes, has_p=True, pr=avd))
            else:
                beats.append(_beat(t_a + pr, "Ap" if a_paced else "N", spikes=[(-pr - 0.07, SPIKE_A)] if a_paced else [], pr=pr))
        else:                                            # AV block -> ventricular pacing after AVD
            kind = ("AVp" if a_paced else "AsVp") if rhythm != "paced_crt" else "CRT"
            sp = ([(-avd - 0.07, SPIKE_A)] if a_paced else []) + v_spikes
            beats.append(_beat(t_a + avd, kind, spikes=sp, has_p=True, pr=avd))    # P wave sits AVD before the paced QRS
        t_last_v = t_a
        t_int = next_intrinsic(t_a)
        # HF substrate for CRT: occasional PVC
        if rhythm == "paced_crt" and rng.random() < 0.04:
            tv = t_a + avd + hrm.rr(t_a) * rng.uniform(0.5, 0.65)
            beats.append(_beat(tv, "V"))
            t_last_v = tv
            t_int = next_intrinsic(tv)
    beats = [b for b in beats if b["t"] < seconds - 0.05]
    beats.sort(key=lambda b: b["t"])
    return beats, info
