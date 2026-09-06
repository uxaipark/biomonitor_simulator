"""Realistic Korean (90 %) / foreign (10 %) name generation.

Korean given names follow birth-decade naming trends so an 82-year-old is
'영자/영수' style while a 30-year-old is '지훈/서연' style.
"""
from __future__ import annotations

import numpy as np

KO_SURNAMES = [("김", 21.5), ("이", 14.7), ("박", 8.4), ("최", 4.7), ("정", 4.3), ("강", 2.4), ("조", 2.1), ("윤", 2.1),
               ("장", 2.0), ("임", 1.7), ("한", 1.5), ("오", 1.4), ("서", 1.4), ("신", 1.4), ("권", 1.3), ("황", 1.3),
               ("안", 1.1), ("송", 1.0), ("류", 0.9), ("전", 0.9), ("홍", 0.9), ("고", 0.9), ("문", 0.9), ("양", 0.9),
               ("손", 0.8), ("배", 0.8), ("백", 0.8), ("허", 0.6), ("유", 0.6), ("남", 0.5), ("심", 0.5), ("노", 0.5),
               ("하", 0.4), ("곽", 0.4), ("성", 0.4), ("차", 0.4), ("주", 0.4), ("우", 0.4), ("구", 0.4), ("민", 0.3),
               ("나", 0.3), ("진", 0.3), ("지", 0.3), ("엄", 0.3), ("채", 0.2), ("원", 0.2), ("천", 0.2), ("방", 0.2),
               ("공", 0.2), ("현", 0.2), ("함", 0.2), ("변", 0.2), ("염", 0.1), ("여", 0.1), ("추", 0.1), ("도", 0.1),
               ("소", 0.1), ("석", 0.1), ("선", 0.1), ("설", 0.1), ("마", 0.1), ("길", 0.1), ("연", 0.1), ("위", 0.1),
               ("표", 0.1), ("명", 0.1), ("기", 0.1), ("반", 0.1), ("왕", 0.1), ("금", 0.1), ("옥", 0.1), ("육", 0.05),
               ("인", 0.05), ("맹", 0.05), ("제", 0.05), ("모", 0.05), ("탁", 0.05), ("국", 0.05), ("어", 0.05), ("은", 0.05)]

# given names by birth decade (approximate popularity trends)
KO_GIVEN = {
    "M": {
        1930: ["영수", "영호", "영식", "정웅", "종수", "병철", "용식", "재수", "성수", "기영", "태수", "영남", "정수", "만수", "덕수", "상철", "종식", "봉수", "광수", "명수"],
        1950: ["영수", "성호", "영철", "정호", "상철", "재호", "종호", "병호", "영길", "광수", "동수", "명수", "기수", "성수", "종철", "진수", "정남", "봉수", "용호", "승호"],
        1960: ["성민", "정훈", "상호", "성호", "영진", "재호", "경수", "영식", "동현", "진우", "종민", "태호", "성진", "상현", "지훈", "형수", "정민", "승호", "용진", "기태"],
        1970: ["지훈", "정훈", "성민", "동현", "진우", "상우", "준호", "재영", "성진", "현우", "민수", "승현", "태현", "경민", "우진", "재원", "형준", "동훈", "성훈", "영민"],
        1980: ["지훈", "동현", "현우", "민수", "준호", "성민", "재현", "승현", "정우", "진호", "상현", "우진", "재원", "민재", "태양", "종현", "승우", "형준", "준영", "영훈"],
        1990: ["민준", "지훈", "현우", "준서", "동현", "준영", "성민", "우진", "지원", "승민", "정우", "민재", "현준", "재원", "예준", "도현", "지호", "승현", "준혁", "시우"],
        2000: ["민준", "서준", "예준", "도윤", "시우", "주원", "하준", "지호", "지후", "준서", "준우", "현우", "도현", "지훈", "건우", "우진", "선우", "서진", "민재", "현준"],
    },
    "F": {
        1930: ["영자", "순자", "정자", "옥순", "말자", "춘자", "옥자", "정순", "영숙", "순옥", "복순", "명자", "금자", "귀남", "정희", "영희", "옥희", "순희", "말순", "분이"],
        1950: ["영숙", "정숙", "미자", "영자", "순자", "정희", "경자", "명숙", "영희", "옥순", "정순", "순희", "경희", "미숙", "정자", "혜숙", "은자", "말자", "춘자", "귀순"],
        1960: ["미경", "미숙", "은주", "경희", "은희", "영미", "정희", "미영", "현숙", "지영", "명희", "은영", "경숙", "순희", "미선", "미정", "정미", "혜경", "영주", "선희"],
        1970: ["지영", "은주", "미영", "은정", "지혜", "현정", "미경", "수진", "은희", "정은", "혜진", "지연", "선영", "민정", "성희", "은경", "미선", "미정", "정미", "경아"],
        1980: ["지영", "지혜", "수진", "민지", "은정", "현주", "지은", "혜진", "지연", "미영", "은지", "소영", "수현", "아름", "보람", "유진", "예진", "다혜", "지현", "선영"],
        1990: ["지은", "민지", "수빈", "지원", "예진", "서연", "수진", "지현", "은지", "유진", "혜원", "다은", "예린", "하영", "채원", "지민", "소연", "현지", "가은", "나영"],
        2000: ["서연", "서윤", "지우", "하은", "민서", "지민", "수아", "지아", "예은", "하윤", "채원", "지윤", "은서", "다은", "예린", "시은", "수빈", "윤서", "유나", "소율"],
    },
}
KO_SYL_M = ["준", "민", "현", "우", "진", "성", "호", "석", "훈", "영", "재", "태", "수", "동", "승", "혁", "환", "규", "철", "빈"]
KO_SYL_F = ["지", "수", "은", "민", "서", "연", "아", "예", "희", "정", "영", "미", "혜", "주", "윤", "채", "하", "다", "나", "린"]

FOREIGN = {
    "CN": {"w": 0.34, "surn": ["Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Zhao", "Wu", "Zhou", "Xu", "Sun"],
           "M": ["Wei", "Jun", "Lei", "Qiang", "Hao", "Ming", "Jian", "Tao", "Bo", "Peng"], "F": ["Fang", "Li", "Xiu", "Mei", "Yan", "Jing", "Ying", "Hui", "Na", "Xia"]},
    "VN": {"w": 0.18, "surn": ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui"],
           "M": ["Van Anh", "Minh", "Duc", "Hung", "Quang", "Tuan", "Nam", "Hai"], "F": ["Thi Lan", "Thi Hoa", "Ngoc", "Thu", "Huong", "Linh", "Mai", "Anh"]},
    "US": {"w": 0.12, "surn": ["Smith", "Johnson", "Williams", "Brown", "Miller", "Davis", "Wilson", "Anderson", "Taylor", "Moore"],
           "M": ["James", "John", "Robert", "Michael", "David", "William", "Daniel", "Thomas"], "F": ["Mary", "Jennifer", "Linda", "Susan", "Karen", "Emily", "Sarah", "Jessica"]},
    "TH": {"w": 0.07, "surn": ["Saetang", "Srisai", "Chaiyasit", "Boonmee", "Wongsawat"], "M": ["Somchai", "Anan", "Prasert", "Wichai", "Somsak"], "F": ["Siriporn", "Malee", "Kanya", "Pornthip", "Nid"]},
    "UZ": {"w": 0.06, "surn": ["Karimov", "Rakhimov", "Yusupov", "Tashkentov", "Abdullaev"], "M": ["Bekzod", "Jasur", "Sardor", "Aziz", "Rustam"], "F": ["Dilnoza", "Nilufar", "Gulnora", "Madina", "Zarina"]},
    "PH": {"w": 0.06, "surn": ["Santos", "Reyes", "Cruz", "Bautista", "Garcia", "Mendoza"], "M": ["Jose", "Juan", "Mark", "Carlo", "Rey"], "F": ["Maria", "Ana", "Rosa", "Grace", "Joy"]},
    "JP": {"w": 0.06, "surn": ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito"], "M": ["Hiroshi", "Takeshi", "Kenji", "Yuki", "Daiki"], "F": ["Yuko", "Keiko", "Aiko", "Sakura", "Haruka"]},
    "RU": {"w": 0.04, "surn": ["Ivanov", "Petrov", "Smirnov", "Kuznetsov", "Popov"], "M": ["Ivan", "Dmitri", "Sergei", "Alexei", "Nikolai"], "F": ["Olga", "Elena", "Natalia", "Anna", "Irina"]},
    "NP": {"w": 0.04, "surn": ["Shrestha", "Tamang", "Gurung", "Rai", "Thapa"], "M": ["Ramesh", "Suresh", "Bikash", "Sanjay", "Prakash"], "F": ["Sita", "Gita", "Anita", "Sunita", "Maya"]},
    "ID": {"w": 0.03, "surn": ["Santoso", "Wijaya", "Putra", "Saputra", "Hidayat"], "M": ["Budi", "Agus", "Andi", "Dedi", "Rizky"], "F": ["Siti", "Dewi", "Sri", "Ayu", "Putri"]},
}
NATION_LABEL = {"KR": "대한민국", "CN": "중국", "VN": "베트남", "US": "미국", "TH": "태국", "UZ": "우즈베키스탄",
                "PH": "필리핀", "JP": "일본", "RU": "러시아", "NP": "네팔", "ID": "인도네시아"}

_sur_names = [s for s, _ in KO_SURNAMES]
_sur_w = np.array([w for _, w in KO_SURNAMES], dtype=np.float64)
_sur_w /= _sur_w.sum()
_nat_codes = list(FOREIGN.keys())
_nat_w = np.array([FOREIGN[c]["w"] for c in _nat_codes])
_nat_w /= _nat_w.sum()


def korean_name(rng: np.random.Generator, sex: str, birth_year: int) -> str:
    sur = _sur_names[int(rng.choice(len(_sur_names), p=_sur_w))]
    decades = sorted(KO_GIVEN[sex].keys())
    dec = max(d for d in decades if d <= max(birth_year, decades[0])) if birth_year >= decades[0] else decades[0]
    pool = KO_GIVEN[sex][dec]
    if rng.random() < 0.75:
        given = pool[int(rng.integers(len(pool)))]
    else:
        syl = KO_SYL_M if sex == "M" else KO_SYL_F
        given = syl[int(rng.integers(len(syl)))] + syl[int(rng.integers(len(syl)))]
        if rng.random() < 0.08:
            given = given[0]                       # single-syllable given name
    return sur + given


def foreign_name(rng: np.random.Generator, sex: str) -> tuple[str, str]:
    code = _nat_codes[int(rng.choice(len(_nat_codes), p=_nat_w))]
    d = FOREIGN[code]
    sur = d["surn"][int(rng.integers(len(d["surn"])))]
    given = d[sex][int(rng.integers(len(d[sex])))]
    if code in ("CN", "VN", "JP"):
        name = f"{sur} {given}"
    else:
        name = f"{given} {sur}"
    return name, code


def staff_name(rng: np.random.Generator, sex: str) -> str:
    return korean_name(rng, sex, int(rng.integers(1965, 2000)))
