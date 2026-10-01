"""정답 라벨 표기 통일 (2026-10-01 라우터 요청): value 는 언어와 무관한 코드, 표시 이름은 display(한국어)·display_en,
meta.mechanism 으로 '무엇을 흉내 낸 것인지'(cardiac 실제 심장 현상 · electrode 전극 · signal_path 신호 경로 · device 단말 · network · power ·
clinical · workflow)를 구분한다.  예전에 한국어 문장으로 저장된 라벨도 읽을 때 같은 형식으로 바꿔 돌려준다."""
from __future__ import annotations

from ..signals.rhythms import RHYTHMS

MECH = {"rhythm_episode": "cardiac", "base_rhythm": "cardiac", "lead_off": "electrode", "patch_off": "electrode", "no_link": "signal_path",
        "trip": "workflow", "deterioration": "clinical", "code_blue": "clinical", "gateway": "network", "net_device": "network", "power": "power",
        "mcot_device": "device", "surge": "workflow", "battery_dead": "device"}
DISPLAY = {"lead_off": ("리드 오프 (전극 분리)", "Lead off"), "patch_off": ("패치 분리", "Patch removed"), "no_link": ("게이트웨이 미연결", "No gateway link"),
           "trip": ("이동", "Patient transfer"), "code_blue": ("코드블루", "Code blue"), "surge": ("대량 유입", "Patient surge"),
           "degraded": ("게이트웨이 성능 저하", "Gateway degraded"), "down": ("게이트웨이 장애", "Gateway down"), "uplink_lost": ("업링크 단절", "Uplink lost"),
           "net_device_down": ("망 장비 장애", "Network device outage"), "power_outage": ("정전", "Power outage"),
           "killed": ("앱 강제 종료", "App killed"), "saver": ("절전 모드", "Power saver"), "update": ("OS 업데이트", "OS update"), "airplane": ("비행기 모드", "Airplane mode"),
           "asystole": ("무수축", "Asystole"), "battery_dead": ("패치 배터리 소진", "Patch battery depleted")}
_KO_TO_CODE = {"리드 오프": "lead_off", "패치 분리": "patch_off", "게이트웨이 미연결": "no_link", "앱 강제 종료": "killed", "절전 모드": "saver",
               "OS 업데이트": "update", "비행기 모드": "airplane"}
# 리듬별 '무엇인가' — 휴지·무수축은 실제 심장 휴지(전극·신호 문제가 아님)
RHYTHM_NATURE = {"sinus_pause": "동결절 휴지 — 실제 심장 박동이 없는 구간(1.8~3.5 RR), 접합부·심실 이탈 박동이 뒤따를 수 있음",
                 "asystole": "심정지 무수축 — 실제 심장 전기 활동 없음(평탄선), 전극·신호 문제 흉내가 아님",
                 "vfib": "심실세동 — 조직화된 QRS 없음", "avb2_m1": "QRS 가 실제로 탈락(비전도 P)", "avb2_m2": "QRS 가 실제로 탈락(비전도 P)",
                 "avb3": "방실 해리 — 심실 이탈 리듬"}


def rhythm_display(code: str) -> tuple[str, str]:
    if code == "asystole":
        return DISPLAY["asystole"]
    r = RHYTHMS.get(code)
    if not r:
        return code, code
    lab = r["label"]
    en = lab[lab.find("(") + 1: lab.rfind(")")] if "(" in lab else code
    return lab, en


def normalize(row: dict) -> dict:
    """DB·열린 구간 라벨 한 줄을 통일 형식으로: value=코드, display/display_en, meta.mechanism (+ 리듬이면 meta.nature)."""
    kind, val = row.get("kind"), row.get("value")
    meta = dict(row.get("meta") or {})
    code, disp = val, None
    if kind in ("rhythm_episode", "base_rhythm"):
        disp = rhythm_display(val)
        if val in RHYTHM_NATURE:
            meta.setdefault("nature", RHYTHM_NATURE[val])
    elif kind in ("lead_off", "patch_off", "no_link"):
        code = kind
    elif kind == "trip":
        code = "trip"
        if val and val not in ("trip", "이동"):
            meta.setdefault("note", val)
    elif kind == "deterioration":
        code = meta.get("kind") or val
        disp = (val, code) if val != code else None
    elif kind == "code_blue":
        code = "code_blue"
        if val and val != "code_blue":
            meta.setdefault("cause", val)
    elif kind == "gateway":
        code = {1: "degraded", 2: "down", 3: "uplink_lost"}.get(meta.get("status"), val)
        if val and " · " in str(val):
            meta.setdefault("reason", str(val).split(" · ", 1)[1])
    elif kind == "net_device":
        code = "net_device_down"
        meta.setdefault("detail", val)
    elif kind == "power":
        code = "power_outage"
        meta.setdefault("detail", val)
    elif kind == "mcot_device":
        code = _KO_TO_CODE.get(val, val)
    elif kind == "surge":
        code = "surge"
        meta.setdefault("detail", val)
    if disp is None:
        disp = DISPLAY.get(code, (str(val), str(code)))
    meta.setdefault("mechanism", MECH.get(kind, "other"))
    return {**row, "value": code, "display": disp[0], "display_en": disp[1], "meta": meta}
