"""기대 경보 기준표 — 에뮬레이터가 보낸 신호를 받은 라우터·뷰어에서 '떠야 할' 경보 (2026-10-11).

에뮬레이터는 경보를 내보내지 않는다.  신호(파형·수치)와 정답 레코드만 보낸다.  이 표는 그 신호를 받은 쪽이 어떤 우선순위로
경보해야 하는지에 대한 시험 기준이고, 한 곳에서만 정의한다:
- 현장 테스트 이벤트의 기대 결과(합격 기준)          runtime/fieldtest.py EVENTS
- 에뮬레이터 내부 참고 화면(중앙 감시)의 색          GET /api/v1/alarms → app.js
- 시작 매뉴얼의 설명                                 index.html (tests/test_alarms.py 가 일치를 검사)

우선순위는 IEC 60601-1-8 관례: high = 적색(즉시 대응), medium = 황색(신속 대응), low = 청록(인지).
기술 경보(technical: 리드 오프·신호 없음·배터리·게이트웨이)는 생리 경보와 따로 표기하고 우선순위는 위험도로 정한다.
"""
from __future__ import annotations

PRIO_COLOR = {"high": "red", "medium": "yellow", "low": "cyan"}
PRIO_KO = {"high": "위급", "medium": "주의", "low": "권고"}
COLOR_KO = {"red": "적색", "yellow": "황색", "cyan": "청록"}

# 리듬 (정답 레코드의 rhythm 코드, 접두어 일치: avb2 → avb2_m1·avb2_m2, afib → afib·afib_rvr)
RHYTHMS: dict[str, tuple[str, str]] = {
    "vfib": ("high", "VFIB"),
    "vt": ("high", "VTACH"),
    "asystole": ("high", "ASYSTOLE"),
    "avb3": ("high", "AV BLOCK III"),
    "nsvt": ("medium", "NSVT"),
    "paced_malfunction": ("medium", "PACER FAIL"),
    "svt": ("medium", "SVT"),
    "afib": ("medium", "AFIB"),
    "aflutter": ("medium", "AFL"),
    "avb2": ("medium", "AV BLOCK II"),
    "stemi": ("medium", "ST ELEV"),
    "pvc_bigeminy": ("low", "PVC BIGEMINY"),
}

# 수치 한계: high 는 즉시 위험, medium 은 일반 한계 이탈 (lo, hi 중 None = 그쪽 없음)
LIMITS: dict[str, dict[str, tuple[float | None, float | None]]] = {
    "hr": {"high": (40, 150), "medium": (50, 120)},
    "spo2": {"high": (88, None), "medium": (90, None)},
    "rr": {"medium": (8, 30)},
    "nibp_sys": {"high": (80, 190), "medium": (90, 160)},
    "temp": {"medium": (None, 38.5)},
    "glucose": {"medium": (70, 250)},
}

# 기술 경보
TECHNICAL: dict[str, tuple[str, str]] = {
    "no_signal": ("medium", "NO SIGNAL"),
    "lead_off": ("medium", "ECG LEAD OFF"),
    "gw_down": ("medium", "GATEWAY DOWN"),
    "battery_low": ("low", "BATTERY LOW"),
}


def expected(kind: str, key: str) -> dict:
    """kind = rhythm | limit | technical.  limit 의 key 는 'spo2:85' 처럼 '항목:값'."""
    if kind == "rhythm":
        prio, code = next((v for k, v in RHYTHMS.items() if key == k or key.startswith(k)), (None, None))
        tech = False
    elif kind == "technical":
        prio, code = TECHNICAL[key]
        tech = True
    else:
        name, val = key.split(":")
        v = float(val)
        prio, code, tech = None, name.upper(), False
        for p in ("high", "medium"):
            lo, hi = LIMITS[name].get(p, (None, None))
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                prio = p
                code = f"{name.upper()} {'LOW' if lo is not None and v < lo else 'HIGH'}"
                break
    if prio is None:
        return {"prio": None, "color": None, "code": None, "technical": tech, "text": "경보 없음"}
    color = PRIO_COLOR[prio]
    return {"prio": prio, "color": color, "code": code, "technical": tech,
            "text": f"{PRIO_KO[prio]}({COLOR_KO[color]}) {'기술 ' if tech else ''}경보 · {code}"}


def table() -> dict:
    """GET /api/v1/alarms."""
    return {"priority_colors": PRIO_COLOR, "priority_ko": PRIO_KO,
            "rhythms": {k: {"prio": p, "color": PRIO_COLOR[p], "code": c} for k, (p, c) in RHYTHMS.items()},
            "limits": {k: {p: list(v) for p, v in d.items()} for k, d in LIMITS.items()},
            "technical": {k: {"prio": p, "color": PRIO_COLOR[p], "code": c} for k, (p, c) in TECHNICAL.items()},
            "note": "에뮬레이터는 경보를 내보내지 않는다. 이 표는 라우터·뷰어가 이 신호를 받았을 때 떠야 할 경보(시험 기준)다."}
