"""Wearable device catalogue and per-patient device-set assignment.

A patient's transmitted channels = (global allowed channels) ∩ (channels of the
devices the patient wears).  The ECG patch is the BLE device itself and is
always present; the others are optional sensors paired through the patch.
"""
from __future__ import annotations

import numpy as np

from ..config import CHANNEL_BY_KEY

DEVICES: dict[str, dict] = {
    "ecg_patch":      {"label": "ECG 패치", "short": "ECG", "channels": ["ecg", "hr", "resp", "resp_wave", "accel", "pace"], "fixed": True,
                       "desc": "BLE 웨어러블 패치: ECG, 심박수, 용량성/EDR 호흡, 가속도, 페이스 마커"},
    "temp_patch":     {"label": "체온 패치", "short": "TEMP", "channels": ["temp"], "desc": "피부 부착형 체온 센서"},
    "spo2_fingertip": {"label": "손가락 끝 SpO2", "short": "SpO2·F", "channels": ["spo2", "ppg"], "spo2_src": 0, "desc": "손끝 PPG 프로브(입원 중 사용, 정확도 높음)"},
    "spo2_ring":      {"label": "반지형 SpO2", "short": "SpO2·R", "channels": ["spo2", "ppg"], "spo2_src": 1, "desc": "반지형 PPG(장시간 착용, 움직임에 민감)"},
    "bp_wrist":       {"label": "손목 밴드 (PTT/PAT)", "short": "WRIST", "channels": ["spo2", "ppg"], "spo2_src": 2, "desc": "용량성 어레이 손목 밴드: PTT/PAT 기반 혈압·SpO2 추정(지연·노이즈 큼)"},
    "cgm":            {"label": "연속혈당 (CGM)", "short": "CGM", "channels": ["glucose"], "desc": "간질액 연속혈당 센서"},
}
SPO2_DEVICES = ("spo2_fingertip", "spo2_ring", "bp_wrist")
POLICIES = {
    "auto": "증상 맞춤 (질환·병동·재원 형태에 따라 자동 배정)",
    "all": "전체 장착 (ECG 패치 + 체온 + 손끝 SpO2 + CGM)",
    "minimal": "ECG 패치만",
}
RESP_WARDS = ("호흡기내과", "감염내과")
FEVER_WARDS = ("호흡기내과", "감염내과", "일반외과", "정형외과", "종양내과")


def devices_mask(devices: list[str]) -> int:
    m = 0
    for d in devices:
        for key in DEVICES.get(d, {}).get("channels", []):
            m |= 1 << CHANNEL_BY_KEY[key]
    return m


def spo2_source(devices: list[str]) -> int | None:
    for d in devices:
        if d in SPO2_DEVICES:
            return DEVICES[d]["spo2_src"]
    return None


DEFAULT_MIX = {"fingertip": 55.0, "ring": 35.0, "wrist_ptt": 10.0}
_MIX_DEV = {"fingertip": "spo2_fingertip", "ring": "spo2_ring", "wrist_ptt": "bp_wrist"}


def pick_spo2(rng: np.random.Generator, outpatient: bool, mix: dict | None) -> str:
    """Choose an SpO2 sensor type from the configured weights (fingertip is impractical at home)."""
    m = dict(DEFAULT_MIX)
    m.update({k: float(v) for k, v in (mix or {}).items() if k in m})
    if outpatient:
        m["ring"] += m["fingertip"]
        m["fingertip"] = 0.0
    keys = list(m.keys())
    w = np.array([m[k] for k in keys], dtype=np.float64)
    if w.sum() <= 0:
        w = np.array([0.0 if outpatient else 55.0, 35.0 + (55.0 if outpatient else 0.0), 10.0])
    return _MIX_DEV[keys[int(rng.choice(len(keys), p=w / w.sum()))]]


def assign_devices(rng: np.random.Generator, prof: dict, outpatient: bool, policy: str = "auto", spo2_mix: dict | None = None) -> list[str]:
    if policy == "all":
        return ["ecg_patch", "temp_patch", pick_spo2(rng, outpatient, spo2_mix), "cgm"]
    if policy == "minimal":
        return ["ecg_patch"]
    devs = ["ecg_patch"]
    dis, ward, group = prof["disease"], prof["ward_specialty"], prof["disease_group"]
    comorb = set(prof.get("comorbidities", []))
    # temperature: infection / post-op / oncology wards almost always, others about half
    p_temp = 0.95 if (ward in FEVER_WARDS or prof["temp_profile"] != "normal") else 0.5
    if rng.random() < p_temp:
        devs.append("temp_patch")
    # SpO2: respiratory, heart failure, post cardiac surgery, sepsis, sleep apnea -> high; others moderate
    resp_risk = ward in RESP_WARDS or prof["resp_kind"] != "normal" or any(k in dis for k in ("심부전", "심장수술", "패혈증", "수면무호흡", "폐렴", "폐질환", "심근경색", "심정지"))
    p_spo2 = 0.92 if resp_risk else (0.45 if group == "heart" else 0.35)
    if rng.random() < p_spo2:
        devs.append(pick_spo2(rng, outpatient, spo2_mix))
    elif ("고혈압" in comorb or group == "heart") and rng.random() < 0.15:
        devs.append("bp_wrist")                                  # BP trend band for hypertensive / cardiac patients
    # CGM: diabetes (diagnosis, comorbidity or glucose profile)
    diabetic = "당뇨" in dis or "제2형 당뇨병" in comorb or prof["glucose_profile"] in ("diabetic", "hyper", "hypo_risk")
    if rng.random() < (0.95 if diabetic else 0.04):
        devs.append("cgm")
    return devs


def describe() -> dict:
    return {"devices": {k: {kk: vv for kk, vv in v.items()} for k, v in DEVICES.items()}, "policies": POLICIES,
            "rule": "transmitted channels = global enabled channels ∩ union(channels of the patient's devices); ecg_patch is always present"}
