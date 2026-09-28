"""10,000-patient profile pool with disease-driven physiology assignment."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .names import korean_name, foreign_name, overseas_name, NATION_LABEL

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


# ------------------------------------------------------------------ 거주지 (동네 수준)
# 환자 주소는 시/도 · 구/군(시) · 동/읍/면까지만 — 번지·건물은 두지 않는다.  가중치는 대형 병원 진료권을 흉내낸다:
# 수도권(서울·경기·인천)이 약 80 %, 나머지는 광역시·도 순.  각 항목: (시도, 구/군 또는 시 구, [동...], 가중치)
ADDRESS_TABLE = [
    ("서울", "강남구", ["역삼동", "삼성동", "대치동", "논현동", "개포동", "청담동"], 4), ("서울", "서초구", ["서초동", "반포동", "방배동", "잠원동"], 3),
    ("서울", "송파구", ["잠실동", "가락동", "문정동", "방이동", "석촌동"], 4), ("서울", "강동구", ["천호동", "길동", "명일동", "암사동"], 2),
    ("서울", "마포구", ["공덕동", "합정동", "망원동", "상암동", "아현동"], 3), ("서울", "용산구", ["이촌동", "한남동", "후암동", "청파동"], 2),
    ("서울", "성동구", ["행당동", "옥수동", "성수동", "금호동"], 2), ("서울", "광진구", ["구의동", "자양동", "중곡동", "화양동"], 2),
    ("서울", "동대문구", ["장안동", "답십리동", "전농동", "휘경동"], 2), ("서울", "중랑구", ["면목동", "상봉동", "묵동", "신내동"], 2),
    ("서울", "성북구", ["길음동", "돈암동", "장위동", "정릉동"], 2), ("서울", "강북구", ["미아동", "수유동", "번동"], 2),
    ("서울", "도봉구", ["창동", "쌍문동", "방학동"], 2), ("서울", "노원구", ["상계동", "중계동", "하계동", "월계동"], 3),
    ("서울", "은평구", ["불광동", "응암동", "진관동", "녹번동"], 2), ("서울", "서대문구", ["홍제동", "북가좌동", "연희동", "남가좌동"], 2),
    ("서울", "종로구", ["혜화동", "평창동", "창신동", "사직동"], 1), ("서울", "중구", ["신당동", "황학동", "회현동"], 1),
    ("서울", "양천구", ["목동", "신정동", "신월동"], 3), ("서울", "강서구", ["화곡동", "마곡동", "등촌동", "방화동", "염창동"], 3),
    ("서울", "구로구", ["구로동", "신도림동", "개봉동", "오류동"], 2), ("서울", "금천구", ["독산동", "시흥동", "가산동"], 2),
    ("서울", "영등포구", ["당산동", "여의도동", "신길동", "문래동"], 2), ("서울", "동작구", ["사당동", "상도동", "노량진동", "흑석동"], 2),
    ("서울", "관악구", ["봉천동", "신림동", "남현동"], 3),
    ("경기", "성남시 분당구", ["정자동", "서현동", "야탑동", "판교동", "수내동"], 3), ("경기", "성남시 수정구", ["신흥동", "태평동", "위례동"], 1),
    ("경기", "수원시 영통구", ["매탄동", "영통동", "광교동"], 2), ("경기", "수원시 장안구", ["정자동", "율전동", "천천동"], 1), ("경기", "수원시 권선구", ["권선동", "호매실동", "금곡동"], 1),
    ("경기", "고양시 일산동구", ["백석동", "마두동", "장항동", "식사동"], 2), ("경기", "고양시 일산서구", ["주엽동", "대화동", "탄현동"], 1), ("경기", "고양시 덕양구", ["화정동", "행신동", "삼송동"], 2),
    ("경기", "용인시 수지구", ["죽전동", "풍덕천동", "동천동", "상현동"], 2), ("경기", "용인시 기흥구", ["구갈동", "동백동", "보정동"], 1),
    ("경기", "부천시", ["중동", "상동", "송내동", "역곡동"], 2), ("경기", "안양시 동안구", ["평촌동", "관양동", "호계동"], 2), ("경기", "안양시 만안구", ["안양동", "석수동"], 1),
    ("경기", "화성시", ["동탄동", "병점동", "향남읍", "봉담읍"], 2), ("경기", "남양주시", ["다산동", "별내동", "화도읍", "진접읍"], 2),
    ("경기", "파주시", ["운정동", "금촌동", "교하동"], 1), ("경기", "김포시", ["장기동", "구래동", "풍무동"], 1), ("경기", "하남시", ["미사동", "덕풍동", "신장동"], 1),
    ("경기", "광명시", ["철산동", "하안동", "소하동"], 1), ("경기", "의정부시", ["민락동", "호원동", "신곡동"], 1), ("경기", "시흥시", ["배곧동", "정왕동", "은행동"], 1),
    ("경기", "평택시", ["비전동", "동삭동", "안중읍"], 1), ("경기", "군포시", ["산본동", "금정동"], 1), ("경기", "구리시", ["교문동", "인창동"], 1),
    ("경기", "안산시 단원구", ["고잔동", "원곡동"], 1), ("경기", "안산시 상록구", ["본오동", "사동"], 1), ("경기", "광주시", ["경안동", "오포읍"], 1), ("경기", "양주시", ["옥정동", "덕정동"], 1),
    ("인천", "남동구", ["구월동", "논현동", "간석동", "만수동"], 2), ("인천", "연수구", ["송도동", "연수동", "청학동"], 2), ("인천", "부평구", ["부평동", "삼산동", "산곡동"], 2),
    ("인천", "서구", ["청라동", "검단동", "가정동"], 2), ("인천", "계양구", ["계산동", "작전동", "박촌동"], 1), ("인천", "미추홀구", ["주안동", "용현동", "학익동"], 1),
    ("부산", "해운대구", ["우동", "좌동", "재송동"], 1), ("부산", "부산진구", ["부전동", "개금동", "양정동"], 1), ("부산", "남구", ["대연동", "용호동"], 1), ("부산", "동래구", ["명륜동", "사직동"], 1),
    ("부산", "사하구", ["하단동", "괘법동"], 1), ("부산", "수영구", ["광안동", "남천동"], 1),
    ("대구", "수성구", ["범어동", "만촌동", "황금동"], 1), ("대구", "달서구", ["상인동", "월성동", "용산동"], 1), ("대구", "북구", ["칠곡동", "침산동"], 1), ("대구", "중구", ["삼덕동", "대신동"], 1),
    ("대전", "유성구", ["봉명동", "노은동", "관평동"], 1), ("대전", "서구", ["둔산동", "관저동", "월평동"], 1), ("대전", "중구", ["대흥동", "태평동"], 1),
    ("광주", "서구", ["치평동", "화정동"], 1), ("광주", "북구", ["용봉동", "일곡동"], 1), ("광주", "광산구", ["수완동", "첨단동"], 1),
    ("울산", "남구", ["삼산동", "무거동"], 1), ("울산", "북구", ["화봉동", "천곡동"], 1), ("세종", "세종시", ["나성동", "새롬동", "아름동"], 1),
    ("강원", "춘천시", ["석사동", "퇴계동"], 1), ("강원", "원주시", ["무실동", "단계동"], 1), ("강원", "강릉시", ["교동", "포남동"], 1),
    ("충북", "청주시 흥덕구", ["복대동", "가경동"], 1), ("충북", "충주시", ["연수동", "호암동"], 1),
    ("충남", "천안시 서북구", ["불당동", "쌍용동"], 1), ("충남", "아산시", ["배방읍", "탕정면"], 1),
    ("전북", "전주시 덕진구", ["송천동", "인후동"], 1), ("전북", "익산시", ["영등동", "모현동"], 1),
    ("전남", "여수시", ["학동", "여서동"], 1), ("전남", "순천시", ["조례동", "연향동"], 1), ("전남", "목포시", ["상동", "옥암동"], 1),
    ("경북", "포항시 남구", ["대잠동", "효자동"], 1), ("경북", "구미시", ["인동동", "옥계동"], 1), ("경북", "경주시", ["황성동", "동천동"], 1),
    ("경남", "창원시 성산구", ["상남동", "반림동"], 1), ("경남", "김해시", ["장유동", "내외동"], 1), ("경남", "진주시", ["충무공동", "평거동"], 1), ("경남", "양산시", ["물금읍", "중부동"], 1),
    ("제주", "제주시", ["노형동", "연동", "아라동"], 1), ("제주", "서귀포시", ["동홍동", "서홍동"], 1),
]
_ADDR_W = np.array([a[3] for a in ADDRESS_TABLE], dtype=float); _ADDR_W /= _ADDR_W.sum()


def make_address(rng: np.random.Generator) -> dict:
    """Home address down to the neighbourhood (동/읍/면) only -- what the router needs to show 'where the
    patient lives'; nothing finer is generated on purpose."""
    sido, sigungu, dongs, _ = ADDRESS_TABLE[int(rng.choice(len(ADDRESS_TABLE), p=_ADDR_W))]
    dong = dongs[int(rng.integers(len(dongs)))]
    return {"sido": sido, "sigungu": sigungu, "dong": dong, "label": f"{sido} {sigungu} {dong}"}


# 해외 체류 MCOT 환자의 거주지 — 주요 고객국 13개국.  라우터 MCOT 지도가 거주지를 찍을 수 있게 실제 해외 주소 체계(동네·도시·주/지역·우편번호·국가)와
# 좌표(lat/lon, 동네 중심 ±0.9 km 흔들림)를 준다.  국내 주소처럼 동네(district) 수준까지만이고 번지는 만들지 않는다.
# places: (region, city, district, postal, lat, lon, tz)
RESIDENCE = {
    "US": {"dial": "+1", "carriers": ["Verizon", "AT&T", "T-Mobile"], "en": "USA",
           "places": [("New York", "Brooklyn", "Park Slope", "11215", 40.671, -73.977, "America/New_York"), ("New York", "Queens", "Flushing", "11354", 40.766, -73.826, "America/New_York"),
                      ("New York", "New York", "Upper West Side", "10024", 40.787, -73.975, "America/New_York"), ("New Jersey", "Fort Lee", "Palisade", "07024", 40.851, -73.970, "America/New_York"),
                      ("California", "Los Angeles", "Koreatown", "90005", 34.058, -118.301, "America/Los_Angeles"), ("California", "Irvine", "Woodbury", "92618", 33.669, -117.767, "America/Los_Angeles"),
                      ("California", "San Jose", "North San Jose", "95131", 37.386, -121.893, "America/Los_Angeles"), ("Texas", "Houston", "Westchase", "77042", 29.740, -95.560, "America/Chicago"),
                      ("Texas", "Carrollton", "Old Downtown", "75007", 32.975, -96.890, "America/Chicago"), ("Illinois", "Chicago", "Lincoln Park", "60614", 41.921, -87.648, "America/Chicago"),
                      ("Florida", "Miami", "Brickell", "33131", 25.766, -80.191, "America/New_York"), ("Washington", "Seattle", "South Lake Union", "98109", 47.627, -122.343, "America/Los_Angeles"),
                      ("Washington", "Bellevue", "Downtown", "98004", 47.616, -122.204, "America/Los_Angeles"), ("Georgia", "Duluth", "Sugarloaf", "30096", 33.995, -84.146, "America/New_York")]},
    "JP": {"dial": "+81", "carriers": ["NTT docomo", "au", "SoftBank"], "en": "Japan",
           "places": [("東京都", "新宿区", "大久保", "169-0072", 35.701, 139.700, "Asia/Tokyo"), ("東京都", "世田谷区", "三軒茶屋", "154-0024", 35.643, 139.669, "Asia/Tokyo"),
                      ("東京都", "港区", "麻布十番", "106-0045", 35.656, 139.735, "Asia/Tokyo"), ("大阪府", "大阪市", "生野区", "544-0034", 34.651, 135.540, "Asia/Tokyo"),
                      ("神奈川県", "横浜市", "中区", "231-0023", 35.444, 139.643, "Asia/Tokyo"), ("愛知県", "名古屋市", "中区", "460-0008", 35.166, 136.906, "Asia/Tokyo"),
                      ("福岡県", "福岡市", "博多区", "812-0011", 33.590, 130.420, "Asia/Tokyo")]},
    "GB": {"dial": "+44", "carriers": ["EE", "Vodafone", "O2", "Three"], "en": "United Kingdom",
           "places": [("Greater London", "London", "Camden", "NW1 8QL", 51.539, -0.143, "Europe/London"), ("Greater London", "New Malden", "Kingston upon Thames", "KT3 4DE", 51.401, -0.256, "Europe/London"),
                      ("Greater London", "Croydon", "Central Croydon", "CR0 1LB", 51.376, -0.098, "Europe/London"), ("Greater Manchester", "Manchester", "Northern Quarter", "M1 1JQ", 53.483, -2.236, "Europe/London"),
                      ("West Midlands", "Birmingham", "Jewellery Quarter", "B1 3HN", 52.486, -1.911, "Europe/London"), ("West Yorkshire", "Leeds", "City Centre", "LS1 4AP", 53.798, -1.549, "Europe/London"),
                      ("Scotland", "Edinburgh", "New Town", "EH1 3SB", 55.955, -3.196, "Europe/London")]},
    "DE": {"dial": "+49", "carriers": ["Telekom", "Vodafone", "O2"], "en": "Deutschland",
           "places": [("Berlin", "Berlin", "Mitte", "10115", 52.531, 13.385, "Europe/Berlin"), ("Bayern", "München", "Schwabing", "80801", 48.161, 11.581, "Europe/Berlin"),
                      ("Hamburg", "Hamburg", "Altona", "22767", 53.551, 9.935, "Europe/Berlin"), ("Hessen", "Frankfurt am Main", "Westend", "60323", 50.119, 8.660, "Europe/Berlin"),
                      ("Nordrhein-Westfalen", "Köln", "Ehrenfeld", "50823", 50.951, 6.916, "Europe/Berlin"), ("Nordrhein-Westfalen", "Düsseldorf", "Oberkassel", "40545", 51.232, 6.752, "Europe/Berlin")]},
    "FR": {"dial": "+33", "carriers": ["Orange", "SFR", "Bouygues", "Free"], "en": "France",
           "places": [("Île-de-France", "Paris", "15e arrondissement", "75015", 48.841, 2.300, "Europe/Paris"), ("Île-de-France", "Paris", "13e arrondissement", "75013", 48.829, 2.363, "Europe/Paris"),
                      ("Auvergne-Rhône-Alpes", "Lyon", "Part-Dieu", "69003", 45.760, 4.860, "Europe/Paris"), ("Provence-Alpes-Côte d'Azur", "Marseille", "Prado", "13008", 43.270, 5.383, "Europe/Paris"),
                      ("Occitanie", "Toulouse", "Capitole", "31000", 43.604, 1.444, "Europe/Paris")]},
    "NL": {"dial": "+31", "carriers": ["KPN", "Vodafone", "Odido"], "en": "Nederland",
           "places": [("Noord-Holland", "Amsterdam", "Zuid", "1077 XV", 52.347, 4.877, "Europe/Amsterdam"), ("Noord-Holland", "Amstelveen", "Stadshart", "1181 ZL", 52.303, 4.863, "Europe/Amsterdam"),
                      ("Zuid-Holland", "Rotterdam", "Centrum", "3011 AD", 51.922, 4.479, "Europe/Amsterdam"), ("Utrecht", "Utrecht", "Binnenstad", "3511 LX", 52.091, 5.121, "Europe/Amsterdam"),
                      ("Noord-Brabant", "Eindhoven", "Centrum", "5611 AZ", 51.441, 5.478, "Europe/Amsterdam")]},
    "ES": {"dial": "+34", "carriers": ["Movistar", "Vodafone", "Orange"], "en": "España",
           "places": [("Madrid", "Madrid", "Chamberí", "28010", 40.434, -3.700, "Europe/Madrid"), ("Cataluña", "Barcelona", "Eixample", "08009", 41.394, 2.164, "Europe/Madrid"),
                      ("Comunidad Valenciana", "Valencia", "Ruzafa", "46004", 39.462, -0.372, "Europe/Madrid"), ("Andalucía", "Sevilla", "Casco Antiguo", "41001", 37.389, -5.994, "Europe/Madrid")]},
    "IT": {"dial": "+39", "carriers": ["TIM", "Vodafone", "WindTre"], "en": "Italia",
           "places": [("Lazio", "Roma", "Prati", "00192", 41.909, 12.464, "Europe/Rome"), ("Lombardia", "Milano", "Porta Nuova", "20124", 45.484, 9.191, "Europe/Rome"),
                      ("Campania", "Napoli", "Vomero", "80129", 40.845, 14.228, "Europe/Rome"), ("Piemonte", "Torino", "Centro", "10121", 45.070, 7.682, "Europe/Rome")]},
    "BR": {"dial": "+55", "carriers": ["Vivo", "Claro", "TIM"], "en": "Brasil",
           "places": [("São Paulo", "São Paulo", "Liberdade", "01503-000", -23.558, -46.635, "America/Sao_Paulo"), ("São Paulo", "São Paulo", "Bom Retiro", "01123-000", -23.526, -46.640, "America/Sao_Paulo"),
                      ("Rio de Janeiro", "Rio de Janeiro", "Copacabana", "22070-000", -22.970, -43.184, "America/Sao_Paulo"), ("Minas Gerais", "Belo Horizonte", "Savassi", "30140-000", -19.936, -43.933, "America/Sao_Paulo"),
                      ("Paraná", "Curitiba", "Batel", "80420-000", -25.440, -49.288, "America/Sao_Paulo")]},
    "MX": {"dial": "+52", "carriers": ["Telcel", "AT&T", "Movistar"], "en": "México",
           "places": [("Ciudad de México", "Ciudad de México", "Polanco", "11560", 19.433, -99.199, "America/Mexico_City"), ("Ciudad de México", "Ciudad de México", "Roma Norte", "06700", 19.419, -99.163, "America/Mexico_City"),
                      ("Jalisco", "Guadalajara", "Providencia", "44630", 20.700, -103.383, "America/Mexico_City"), ("Nuevo León", "San Pedro Garza García", "Del Valle", "66220", 25.657, -100.402, "America/Monterrey")]},
    "CL": {"dial": "+56", "carriers": ["Entel", "Movistar", "WOM"], "en": "Chile",
           "places": [("Región Metropolitana", "Santiago", "Providencia", "7500000", -33.431, -70.610, "America/Santiago"), ("Región Metropolitana", "Santiago", "Las Condes", "7550000", -33.410, -70.567, "America/Santiago"),
                      ("Valparaíso", "Viña del Mar", "Reñaca", "2520000", -32.978, -71.545, "America/Santiago")]},
    "CO": {"dial": "+57", "carriers": ["Claro", "Movistar", "Tigo"], "en": "Colombia",
           "places": [("Bogotá D.C.", "Bogotá", "Chapinero", "110221", 4.649, -74.062, "America/Bogota"), ("Antioquia", "Medellín", "El Poblado", "050021", 6.209, -75.568, "America/Bogota"),
                      ("Valle del Cauca", "Cali", "Granada", "760020", 3.460, -76.531, "America/Bogota")]},
    "AR": {"dial": "+54", "carriers": ["Personal", "Claro", "Movistar"], "en": "Argentina",
           "places": [("Buenos Aires", "Buenos Aires", "Palermo", "C1425", -34.588, -58.430, "America/Argentina/Buenos_Aires"), ("Buenos Aires", "Buenos Aires", "Belgrano", "C1426", -34.563, -58.456, "America/Argentina/Buenos_Aires"),
                      ("Córdoba", "Córdoba", "Nueva Córdoba", "X5000", -31.425, -64.185, "America/Argentina/Cordoba"), ("Santa Fe", "Rosario", "Centro", "S2000", -32.947, -60.640, "America/Argentina/Cordoba")]},
}


def make_overseas_residence(rng: np.random.Generator, code: str) -> tuple[dict, str]:
    """해외 체류 환자의 거주지: 실제 해외 주소 체계(동네·도시·주/지역·우편번호·국가) + 좌표 + 시간대 + 현지 통신사, 그리고 국제 전화번호.
    address 의 기존 키(sido/sigungu/dong/label)는 그대로 두어 거주지를 보는 화면·API 가 동작하고, 라우터 MCOT 지도는 lat/lon 을 쓴다."""
    r = RESIDENCE[code]
    region, city, district, postal, lat, lon, tz = r["places"][int(rng.integers(len(r["places"])))]
    carrier = r["carriers"][int(rng.integers(len(r["carriers"])))]
    lat, lon = round(lat + float(rng.uniform(-0.008, 0.008)), 4), round(lon + float(rng.uniform(-0.008, 0.008)), 4)
    label = NATION_LABEL[code]
    text = f"{district}, {city}, {region} {postal}, {r['en']}" if code != "JP" else f"〒{postal} {region}{city}{district}, {r['en']}"
    addr = {"sido": label, "sigungu": region, "dong": district, "label": f"{label} · {text}",
            "overseas": True, "country": code, "country_label": label, "country_en": r["en"], "region": region, "city": city, "district": district, "postal": postal,
            "lat": lat, "lon": lon, "text": text, "tz": tz, "carrier": carrier}
    phone = f"{r['dial']} {rng.integers(200, 999)}-{rng.integers(100, 999)}-{rng.integers(1000, 9999)}"
    return addr, phone


def make_profiles(n: int, seed: int, heart_ratio: float = 0.7, korean_ratio: float = 0.9,
                  pacemaker_ratio: float = 0.06, today: dt.date | None = None, overseas_pool: float = 0.08) -> list[dict]:
    rng = np.random.default_rng(seed)
    addr_rng = np.random.default_rng(seed ^ 0xADD2E55)       # addresses draw from their own stream: the roster for a seed stays byte-identical
    ovs_rng = np.random.default_rng(seed ^ 0x0BE55EA5)       # 해외 체류자(MCOT 전용 풀)도 별도 난수열: 나머지 프로필은 그대로
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
        phone = f"010-{rng.integers(1000, 9999):04d}-{rng.integers(1000, 9999):04d}"
        address = make_address(addr_rng)
        overseas = ovs_rng.random() < overseas_pool             # 해외 체류 외국인: 원외 MCOT 로만 부착된다 (world.admit)
        if overseas:
            name, nat = overseas_name(ovs_rng, sex)
            address, phone = make_overseas_residence(ovs_rng, nat)
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
            "phone": phone, "address": address, "overseas": overseas,
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
