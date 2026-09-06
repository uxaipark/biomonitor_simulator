"""10,000-patient profile pool with disease-driven physiology assignment."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .names import korean_name, foreign_name, NATION_LABEL

# name, icd10, ward specialty, rhythm weights, temp weights, glucose weights, resp kind weights, pacemaker prob
HEART_DISEASES = [
    {"name": "심방세동", "icd": "I48.0", "ward": "심장내과", "rhythms": {"afib": 6, "afib_rvr": 2, "aflutter": 1, "nsr": 1}, "pm": 0.05},
    {"name": "협심증 / 관상동맥질환", "icd": "I25.1", "ward": "심장내과", "rhythms": {"nsr": 5, "ischemia": 3, "pvc": 2, "pac": 1}, "pm": 0.02},
    {"name": "급성 심근경색", "icd": "I21.0", "ward": "심장내과", "rhythms": {"stemi": 4, "ischemia": 2, "pvc": 2, "nsvt": 1, "sinus_tachy": 1}, "pm": 0.02},
    {"name": "울혈성 심부전", "icd": "I50.0", "ward": "심장내과", "rhythms": {"nsr": 3, "afib": 3, "pvc": 2, "sinus_tachy": 2, "lbbb": 1, "nsvt": 1}, "pm": 0.10},
    {"name": "고혈압성 심장질환", "icd": "I11.0", "ward": "순환기내과", "rhythms": {"nsr": 6, "lbbb": 1, "pac": 1, "pvc": 1}, "pm": 0.02},
    {"name": "기타 부정맥", "icd": "I49.9", "ward": "순환기내과", "rhythms": {"pvc": 3, "pac": 3, "pvc_bigeminy": 1, "svt": 2, "sinus_pause": 1, "sinus_tachy": 1}, "pm": 0.05},
    {"name": "동기능부전증후군", "icd": "I49.5", "ward": "순환기내과", "rhythms": {"sinus_brady": 4, "sinus_pause": 3, "afib": 1}, "pm": 0.45},
    {"name": "방실차단", "icd": "I44.2", "ward": "순환기내과", "rhythms": {"avb1": 3, "avb2_m1": 2, "avb2_m2": 2, "avb3": 2}, "pm": 0.40},
    {"name": "심장판막질환", "icd": "I35.0", "ward": "흉부외과", "rhythms": {"afib": 3, "nsr": 4, "rbbb": 1, "pac": 1}, "pm": 0.06},
    {"name": "심근병증", "icd": "I42.0", "ward": "심장내과", "rhythms": {"nsvt": 3, "lbbb": 2, "afib": 2, "vt": 1, "pvc": 2}, "pm": 0.15},
    {"name": "심장수술 후 (CABG/판막)", "icd": "Z95.1", "ward": "흉부외과", "rhythms": {"afib": 3, "pvc": 2, "nsr": 3, "sinus_tachy": 2, "rbbb": 1}, "pm": 0.05},
    {"name": "심정지 후 회복", "icd": "I46.0", "ward": "심장내과", "rhythms": {"nsr": 2, "nsvt": 2, "vt": 1, "vfib": 1, "pvc": 2, "sinus_brady": 1}, "pm": 0.2},
]
OTHER_DISEASES = [
    {"name": "제2형 당뇨병", "icd": "E11.9", "ward": "내분비내과", "rhythms": {"nsr": 8, "pac": 1, "sinus_tachy": 1}, "glucose": {"diabetic": 6, "hyper": 2, "hypo_risk": 2}},
    {"name": "폐렴", "icd": "J18.9", "ward": "호흡기내과", "rhythms": {"nsr": 5, "sinus_tachy": 4, "pac": 1}, "temp": {"fever": 5, "low_grade": 3, "high_fever": 2}, "resp": {"tachypnea": 5, "normal": 3, "copd": 2}},
    {"name": "만성폐쇄성폐질환", "icd": "J44.1", "ward": "호흡기내과", "rhythms": {"nsr": 4, "sinus_tachy": 3, "pac": 2, "afib": 1}, "resp": {"copd": 8, "tachypnea": 2}},
    {"name": "만성 신부전 (투석)", "icd": "N18.5", "ward": "신장내과", "rhythms": {"nsr": 5, "pvc": 2, "sinus_brady": 1, "afib": 1, "avb1": 1}},
    {"name": "뇌경색", "icd": "I63.9", "ward": "신경과", "rhythms": {"nsr": 5, "afib": 3, "pac": 1, "sinus_tachy": 1}},
    {"name": "위장관 출혈", "icd": "K92.2", "ward": "소화기내과", "rhythms": {"sinus_tachy": 5, "nsr": 4, "pac": 1}},
    {"name": "암 (항암치료)", "icd": "C34.9", "ward": "종양내과", "rhythms": {"nsr": 6, "sinus_tachy": 3, "pac": 1}, "temp": {"normal": 6, "low_grade": 3, "fever": 1}},
    {"name": "고관절 골절 수술 후", "icd": "S72.0", "ward": "정형외과", "rhythms": {"nsr": 7, "sinus_tachy": 2, "pac": 1}, "temp": {"normal": 6, "low_grade": 4}},
    {"name": "패혈증", "icd": "A41.9", "ward": "감염내과", "rhythms": {"sinus_tachy": 6, "afib": 2, "nsr": 1, "pvc": 1}, "temp": {"high_fever": 4, "fever": 4, "hypothermia": 2}, "resp": {"tachypnea": 7, "normal": 3}},
    {"name": "폐쇄성 수면무호흡", "icd": "G47.3", "ward": "호흡기내과", "rhythms": {"nsr": 5, "sinus_brady": 2, "pac": 2, "afib": 1}, "resp": {"apnea": 8, "normal": 2}},
    {"name": "갑상선 기능항진증", "icd": "E05.9", "ward": "내분비내과", "rhythms": {"sinus_tachy": 6, "afib": 2, "pac": 2}},
    {"name": "급성 담낭염 수술 후", "icd": "K81.0", "ward": "일반외과", "rhythms": {"nsr": 7, "sinus_tachy": 3}, "temp": {"low_grade": 5, "normal": 4, "fever": 1}},
]
DEFAULT_TEMP = {"normal": 9, "low_grade": 1}
DEFAULT_GLUC = {"normal": 7, "prediabetic": 2, "diabetic": 1}
DEFAULT_RESP = {"normal": 8, "copd": 1, "brady": 0.5, "apnea": 0.5}
BLOOD = (["A+", "B+", "O+", "AB+", "A-", "B-", "O-", "AB-"], [0.34, 0.27, 0.27, 0.11, 0.003, 0.003, 0.003, 0.001])
ALLERGIES = ["없음", "없음", "없음", "없음", "페니실린", "조영제", "NSAIDs", "아스피린", "갑각류", "땅콩", "설파제"]
MOBILITY = ["bedridden", "limited", "ambulatory"]
EXAM_TYPES = [
    {"type": "심전도 검사", "room": "ECG실", "min": 10, "patch": "keep"},
    {"type": "심장초음파", "room": "심초음파실", "min": 30, "patch": "keep"},
    {"type": "흉부 X-ray", "room": "X-ray실", "min": 10, "patch": "keep"},
    {"type": "CT", "room": "CT실", "min": 20, "patch": "keep"},
    {"type": "MRI", "room": "MRI실", "min": 40, "patch": "remove"},
    {"type": "혈액검사", "room": "채혈실", "min": 10, "patch": "keep"},
    {"type": "내시경", "room": "내시경실", "min": 30, "patch": "keep"},
    {"type": "폐기능검사", "room": "폐기능검사실", "min": 20, "patch": "keep"},
    {"type": "재활치료", "room": "재활치료실", "min": 45, "patch": "keep"},
    {"type": "혈액투석", "room": "투석실", "min": 240, "patch": "keep"},
    {"type": "관상동맥조영술", "room": "심혈관조영실", "min": 60, "patch": "keep"},
]


PM_VENDORS = {"Medtronic": 0.40, "Abbott": 0.22, "Boston Scientific": 0.20, "BIOTRONIK": 0.15, "MicroPort CRM": 0.03}
PM_TYPES = {  # key: (label, default mode weights, paced rhythm)
    "dual_chamber": ("이중방 페이스메이커", {"DDDR": 0.7, "DDD": 0.3}, "paced_ddd"),
    "single_ventricular": ("단일심실 페이스메이커", {"VVIR": 0.65, "VVI": 0.35}, "paced_vvi"),
    "single_atrial": ("단일심방 페이스메이커", {"AAIR": 0.6, "AAI": 0.4}, "paced_aai"),
    "leadless": ("리드리스 페이스메이커 (Micra형)", {"VVIR": 0.75, "VVI": 0.25}, "paced_vvi"),
    "crt_p": ("양심실 재동기화 CRT-P", {"DDDR": 0.6, "DDD": 0.4}, "paced_crt"),
    "crt_d": ("양심실 재동기화 제세동기 CRT-D", {"DDDR": 0.6, "DDD": 0.4}, "paced_crt"),
    "icd": ("삽입형 제세동기 ICD (백업 페이싱)", {"VVI 40 백업": 0.6, "DDD 백업": 0.4}, None),
}
PM_INDICATION = {"dual_chamber": "완전/고도 방실차단 또는 동기능부전증후군", "single_ventricular": "서맥성 심방세동 (영구 AF)", "single_atrial": "방실전도 정상인 동기능부전",
                 "leadless": "서맥성 심방세동, 감염 고위험", "crt_p": "심부전 + LBBB (QRS ≥150ms)", "crt_d": "심부전 + LBBB, 급사 고위험", "icd": "급사 1차/2차 예방"}


def make_pacemaker(rng: np.random.Generator, disease: str, age: int, today: dt.date) -> dict:
    """Realistic implantable device profile: type/mode/lead polarity/programming."""
    w = {"dual_chamber": 0.52, "single_ventricular": 0.20, "single_atrial": 0.02, "leadless": 0.05, "crt_p": 0.05, "crt_d": 0.06, "icd": 0.10}
    if disease.startswith("방실차단"):
        w = {"dual_chamber": 0.85, "single_ventricular": 0.08, "leadless": 0.04, "crt_p": 0.03}
    elif disease.startswith("동기능부전"):
        w = {"dual_chamber": 0.80, "single_atrial": 0.08, "single_ventricular": 0.06, "leadless": 0.06}
    elif disease.startswith("심방세동"):
        w = {"single_ventricular": 0.55, "leadless": 0.25, "dual_chamber": 0.20}
    elif disease.startswith("울혈성 심부전") or disease.startswith("심근병증"):
        w = {"crt_d": 0.40, "crt_p": 0.15, "icd": 0.30, "dual_chamber": 0.15}
    elif disease.startswith("심정지"):
        w = {"icd": 0.75, "crt_d": 0.20, "dual_chamber": 0.05}
    typ = _pick(rng, w)
    label, modes, paced_rhythm = PM_TYPES[typ]
    mode = _pick(rng, modes)
    implant_year = today.year - int(rng.integers(0, 14 if typ != "leadless" else 7))
    if typ == "leadless":
        lead = "leadless"
    else:
        lead = "unipolar" if rng.random() < (0.30 if implant_year < 2012 else 0.08) else "bipolar"
    if paced_rhythm and rng.random() < 0.05:
        paced_rhythm = "paced_malfunction"
    if typ == "icd":
        lead = "bipolar"                                   # true-bipolar/integrated ICD leads
    # visible spike amplitude (mV) on a surface ECG and hardware pace-detection probability
    if lead == "unipolar":
        amp, detect = float(rng.uniform(2.0, 8.0)), 99
    elif lead == "leadless":
        amp, detect = float(rng.uniform(0.05, 0.25)), 86
    else:
        amp, detect = float(rng.uniform(0.08, 0.5)), 93
    if rng.random() < 0.25:
        amp = -amp                                         # spike polarity depends on lead vector
    return {"type": typ, "type_label": label, "mode": mode, "lead": lead, "rate_responsive": mode.endswith("R"),
            "lower_rate": int(rng.choice([50, 55, 60, 60, 60, 65, 70])), "upper_rate": int(rng.choice([120, 130, 130, 140, 150])),
            "av_delay_ms": int(rng.integers(140, 220)) if typ in ("dual_chamber", "crt_p", "crt_d") else None,
            "vendor": _pick(rng, PM_VENDORS), "implant_year": implant_year, "indication": PM_INDICATION[typ],
            "spike_amp_mv": round(amp, 2), "detect_pct": detect, "paced_rhythm": paced_rhythm,
            "battery_status": "정상" if rng.random() < 0.9 else "ERI 임박 (교체 권고)"}


def _pick(rng: np.random.Generator, weights: dict) -> str:
    keys = list(weights.keys())
    w = np.array([weights[k] for k in keys], dtype=np.float64)
    return keys[int(rng.choice(len(keys), p=w / w.sum()))]


def make_profiles(n: int, seed: int, heart_ratio: float = 0.7, korean_ratio: float = 0.9,
                  pacemaker_ratio: float = 0.06, today: dt.date | None = None) -> list[dict]:
    rng = np.random.default_rng(seed)
    today = today or dt.date.today()
    out = []
    for i in range(n):
        sex = "M" if rng.random() < 0.52 else "F"
        age = int(np.clip(rng.normal(64, 15), 19, 96))
        birth = today - dt.timedelta(days=int(age * 365.25 + rng.integers(0, 365)))
        korean = rng.random() < korean_ratio
        if korean:
            name, nat = korean_name(rng, sex, birth.year), "KR"
        else:
            name, nat = foreign_name(rng, sex)
            if nat == "CN" and rng.random() < 0.35:                   # Korean-Chinese, hangul name
                name = korean_name(rng, sex, birth.year)
        height = float(np.clip(rng.normal(171 if sex == "M" else 158, 6), 140, 195)) - (0.15 * max(0, age - 50))
        bmi = float(np.clip(rng.normal(23.8, 3.4), 15.5, 40))
        weight = round(bmi * (height / 100) ** 2, 1)
        heart = rng.random() < heart_ratio
        dis = (HEART_DISEASES if heart else OTHER_DISEASES)[int(rng.integers(len(HEART_DISEASES if heart else OTHER_DISEASES)))]
        comorb_pool = ["고혈압", "이상지질혈증", "제2형 당뇨병", "만성 신질환", "COPD", "비만", "갑상선질환", "뇌졸중 병력", "흡연"]
        n_com = int(rng.choice([0, 1, 2, 3], p=[0.25, 0.4, 0.25, 0.1]))
        comorb = list(rng.choice(comorb_pool, size=n_com, replace=False)) if n_com else []
        pacemaker = rng.random() < (dis.get("pm", 0.0) if heart else 0.005) * (pacemaker_ratio / 0.125)
        pm_info = make_pacemaker(rng, dis["name"], age, today) if pacemaker else None
        if pm_info and pm_info["paced_rhythm"]:
            rhythm = pm_info["paced_rhythm"]
        else:
            rhythm = _pick(rng, dis["rhythms"])
        gl_w = dict(dis.get("glucose", DEFAULT_GLUC))
        if "제2형 당뇨병" in comorb:
            gl_w = {"diabetic": 6, "prediabetic": 2, "hyper": 1, "hypo_risk": 1}
        temp_p = _pick(rng, dis.get("temp", DEFAULT_TEMP))
        gluc_p = _pick(rng, gl_w)
        resp_k = _pick(rng, dis.get("resp", DEFAULT_RESP))
        if "COPD" in comorb and resp_k == "normal" and rng.random() < 0.6:
            resp_k = "copd"
        mobility = _pick(rng, {"bedridden": 1 + (age > 80) * 2 + (dis["name"].startswith("고관절")) * 4,
                               "limited": 4 + (age > 70) * 2, "ambulatory": 5 - (age > 75) * 2})
        out.append({
            "id": i + 1,
            "mrn": f"MRN-{(seed % 97) * 100000 + 10000000 + i:08d}",
            "name": name, "sex": sex, "age": age, "birth_date": birth.isoformat(),
            "nationality": nat, "nationality_label": NATION_LABEL[nat],
            "height_cm": round(height, 1), "weight_kg": weight, "bmi": round(bmi, 1),
            "blood_type": BLOOD[0][int(rng.choice(8, p=BLOOD[1]))],
            "allergies": ALLERGIES[int(rng.integers(len(ALLERGIES)))],
            "disease": dis["name"], "icd10": dis["icd"], "disease_group": "heart" if heart else "other",
            "ward_specialty": dis["ward"], "comorbidities": comorb,
            "pacemaker": bool(pacemaker), "pacemaker_info": pm_info, "rhythm": rhythm,
            "temp_profile": temp_p, "glucose_profile": gluc_p, "resp_kind": resp_k, "mobility": mobility,
            "phone": f"010-{rng.integers(1000, 9999):04d}-{rng.integers(1000, 9999):04d}",
            "emergency_contact": ("배우자" if age > 40 else "부모") if rng.random() < 0.7 else "자녀",
            "avatar": None,          # filled by avatars.assign
        })
    return out


def make_exam_schedule(rng: np.random.Generator, profile: dict, admit_time: float, days: int = 3, available: set | None = None) -> list[dict]:
    """Random exams over `days`, times relative to admit_time (unix s).  `available` = exam rooms that exist in this hospital."""
    n = int(rng.choice([0, 1, 2, 3, 4, 5], p=[0.1, 0.25, 0.3, 0.2, 0.1, 0.05]))
    exams = []
    pool = [e for e in EXAM_TYPES if available is None or e["room"] in available]
    if profile["disease_group"] != "heart":
        pool = [e for e in pool if e["type"] not in ("관상동맥조영술",)]
    if not pool:
        return []
    if profile["disease"].startswith("만성 신부전") and (available is None or "투석실" in available):
        exams.append({**EXAM_TYPES[9], "t": admit_time + 3600 * rng.uniform(2, 10)})
    for _ in range(n):
        e = pool[int(rng.integers(len(pool)))]
        day = int(rng.integers(0, days))
        hour = float(rng.uniform(8, 17))
        midnight = dt.datetime.fromtimestamp(admit_time).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()   # local midnight, so exams land 08-17 local
        exams.append({**e, "t": midnight + day * 86400 + hour * 3600})
    exams.sort(key=lambda e: e["t"])
    return [{"type": e["type"], "room": e["room"], "duration_min": e["min"], "patch_policy": e["patch"],
             "time": dt.datetime.fromtimestamp(e["t"]).isoformat(timespec="minutes"), "t": e["t"], "done": False} for e in exams]
