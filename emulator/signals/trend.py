"""Multi-day modulation model shared by the live generator and the trend API.

state(pid, prof, t) -> slow, deterministic per-patient factors:
  hr_scale  : multiplicative heart-rate factor (also time-stretches ECG/PPG)
  rr_add    : respiration rate offset [brpm]
  spo2_add  : SpO2 offset [%]
  temp_add  : temperature offset [°C]
  gl_add    : glucose offset [mg/dL] (meals + drift)
Circadian terms use local clock hours; drift terms are sums of sinusoids with
6 h ... 71 h periods and seeded phases, so the value is a pure function of time.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

MEALS = (7.5, 12.5, 18.5)
MEAL_AMP = {"normal": 35.0, "prediabetic": 55.0, "diabetic": 90.0, "hypo_risk": 30.0, "hyper": 110.0}
_PERIODS_H = (6.3, 13.1, 29.7, 71.0)


def _drift(seed: int, t: float, scale: float, k: int = 4) -> float:
    rng = np.random.default_rng(seed)
    out = 0.0
    for j in range(k):
        amp = rng.uniform(0.3, 1.0)
        ph = rng.uniform(0, 2 * math.pi)
        out += amp * math.sin(2 * math.pi * t / (_PERIODS_H[j] * 3600.0) + ph)
    return scale * out / k


def state(pid: int, prof: dict, t: float) -> dict:
    d = dt.datetime.fromtimestamp(t)
    hour = d.hour + d.minute / 60.0 + d.second / 3600.0
    circ = lambda peak_h, amp: amp * math.cos(2 * math.pi * (hour - peak_h) / 24.0)
    base = pid * 7919
    hr_scale = 1.0 + 0.07 * math.cos(2 * math.pi * (hour - 15) / 24.0) + _drift(base + 1, t, 0.05)
    rr_add = circ(15, 1.0) + _drift(base + 2, t, 1.0)
    night_dip = max(0.0, -circ(15, 1.2))
    spo2_add = -night_dip * (2.5 if prof.get("resp_kind") == "apnea" else 1.0) + _drift(base + 3, t, 0.5)
    temp_add = circ(17, 0.3) + _drift(base + 4, t, 0.15)
    gl_add = _drift(base + 5, t, 8.0, 3)
    amp = MEAL_AMP.get(prof.get("glucose_profile", "normal"), 40.0)
    mrng = np.random.default_rng(base + 6 + int(t // 86400))
    for i, mh in enumerate(MEALS):
        dd = ((hour - mh + 12) % 24) - 12
        if dd > -1.2:
            gl_add += amp * float(mrng.uniform(0.6, 1.2)) * math.exp(-0.5 * (dd / 0.9) ** 2)
    return {"hr_scale": max(0.75, min(1.3, hr_scale)), "rr_add": rr_add, "spo2_add": spo2_add, "temp_add": temp_add, "gl_add": gl_add}


def series(pid: int, prof: dict, ts: np.ndarray) -> dict[str, np.ndarray]:
    keys = ("hr_scale", "rr_add", "spo2_add", "temp_add", "gl_add")
    out = {k: np.empty(ts.size) for k in keys}
    for i, t in enumerate(ts):
        st = state(pid, prof, float(t))
        for k in keys:
            out[k][i] = st[k]
    return out


def nibp(pid: int, prof: dict, t: float, interval_s: int = 3600) -> dict:
    """Nursing NIBP spot check (cuff, q1h): the latest measurement at or before t.  Not a patch channel — the ward
    charts it — so it is EMR data: baseline from age / hypertension / renal disease, nocturnal dip, slow drift and a
    per-measurement cuff noise.  Each patient has their own check minute within the hour."""
    base = pid * 7919
    rng = np.random.default_rng(base + 11)
    age = float(prof.get("age", 60))
    sys0 = 112.0 + 0.35 * max(0.0, age - 40.0) + rng.normal(0.0, 8.0)
    dia0 = 70.0 + 0.10 * max(0.0, age - 40.0) + rng.normal(0.0, 5.0)
    comorb = prof.get("comorbidities") or []
    disease = prof.get("disease", "") or ""
    if "고혈압" in comorb or disease.startswith("고혈압"):
        sys0 += 18.0
        dia0 += 8.0
    if "만성 신질환" in comorb:
        sys0 += 6.0
    if disease.startswith("심부전") or "쇼크" in disease:
        sys0 -= 10.0
        dia0 -= 4.0
    offset = int(rng.integers(0, interval_s))
    t_meas = math.floor((t - offset) / interval_s) * interval_s + offset
    d = dt.datetime.fromtimestamp(t_meas)
    hour = d.hour + d.minute / 60.0
    circ = 6.0 * math.cos(2 * math.pi * (hour - 15.0) / 24.0)          # afternoon peak, nocturnal dip (~10 %)
    drift = _drift(base + 12, t_meas, 6.0)
    noise = float(np.random.default_rng(base + 13 + int(t_meas // interval_s)).normal(0.0, 3.0))
    sys_ = sys0 + circ + drift + noise
    dia_ = dia0 + 0.5 * (circ + drift) + 0.6 * noise
    sys_ = max(80.0, min(210.0, sys_))
    dia_ = max(45.0, min(min(120.0, sys_ - 25.0), dia_))
    return {"sys": int(round(sys_)), "dia": int(round(dia_)), "map": int(round(dia_ + (sys_ - dia_) / 3.0)), "t": int(t_meas), "interval_s": interval_s}
