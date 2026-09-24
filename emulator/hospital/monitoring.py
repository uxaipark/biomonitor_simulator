"""ECG 패치 모니터링 처방: 환자 위중도에 따라 처방 일수(3~14일)를 정한다.

임상 관행
  단기 3~5일 : 급성기 비심장 입원(폐렴·당뇨·골절 등) 중 리듬 감시
  중기 7일   : 심방세동 조절, 심부전·관상동맥질환, 심장수술 후 회복
  장기 14일  : 부정맥 포착이 목적(실신, 원인 불명 뇌경색의 AF 탐지, 심근병증 NSVT, 전도장애, 심정지 후, 원외 MCOT)
패치는 최대 14일 착용한다. 배터리는 15.5일이다(scenario.patch).
장기 처방은 일부 연장되며, 이때 14일째 패치가 새 번호로 교체된다.
"""
from __future__ import annotations

# ICD-10 → 기본 위중도 (1 경증 … 5 중증)
BASE_ACUITY = {
    "I46.0": 5, "I21.0": 4, "I42.0": 4, "I49.5": 4, "I44.2": 4, "I49.9": 4,
    "I63.9": 3, "I48.0": 3, "I50.0": 3, "Z95.1": 3, "I35.0": 3, "A41.9": 3,
    "I25.1": 2, "I11.0": 2, "J18.9": 2, "J44.1": 2, "N18.5": 2, "K92.2": 2, "C34.9": 2, "G47.3": 2, "E05.9": 2,
    "E11.9": 1, "S72.0": 1, "K81.0": 1,
}
# 장기 감시가 필요한 적응증(부정맥 포착 목적): 위중도와 별개로 한 단계 올린다
ARRHYTHMIA_WORKUP = {"I46.0", "I42.0", "I49.5", "I44.2", "I49.9", "I63.9"}
HIGH_RISK_RHYTHMS = {"vt", "nsvt", "vfib", "avb3", "avb2_m2", "stemi", "sinus_pause", "afib_rvr", "svt"}
TIERS = {"short": "단기", "mid": "중기", "long": "장기"}


def acuity(prof: dict, outpatient: bool = False) -> tuple[int, list[str]]:
    """위중도 점수(1~7)와 근거."""
    why = []
    s = BASE_ACUITY.get(prof.get("icd10"), 2)
    why.append(f"{prof.get('disease', '')} {s}")
    if prof.get("icd10") in ARRHYTHMIA_WORKUP:
        s += 1
        why.append("부정맥 포착 적응증 +1")
    if prof.get("rhythm") in HIGH_RISK_RHYTHMS:
        s += 1
        why.append(f"고위험 리듬({prof['rhythm']}) +1")
    if prof.get("age", 0) >= 75:
        s += 1
        why.append("75세 이상 +1")
    if len(prof.get("comorbidities") or []) >= 2:
        s += 1
        why.append("동반질환 2개 이상 +1")
    if prof.get("pacemaker"):
        s += 1
        why.append("삽입형 기기 +1")
    if outpatient:
        s += 1
        why.append("원외 MCOT +1")
    return min(7, max(1, s)), why


def prescribe(prof: dict, u: float, v: float, outpatient: bool = False) -> dict:
    """u, v: [0,1) 난수 두 개 (의사별 편차, 단기 일수).  반환: tier, days, acuity, reason."""
    score, why = acuity(prof, outpatient)
    tier = "long" if score >= 5 else "mid" if score >= 3 else "short"
    if u < 0.12:                                     # 같은 점수라도 처방의에 따라 한 단계 다르게
        order = ["short", "mid", "long"]
        i = order.index(tier) + (1 if u < 0.06 else -1)
        tier = order[max(0, min(2, i))]
    days = {"long": 14, "mid": 7}.get(tier) or (3 if score <= 1 else 4 if v < 0.5 else 5)
    return {"tier": tier, "tier_ko": TIERS[tier], "days": int(max(3, min(14, days))), "acuity": score, "reason": " · ".join(why)}


def extension_prob(rx: dict) -> float:
    """처방 종료 시 연장 확률 — 장기 처방에서 원하는 이벤트를 아직 못 잡은 경우."""
    if rx["tier"] != "long" or rx.get("extended_days", 0) >= 14:
        return 0.0
    return 0.3 if rx["acuity"] >= 6 else 0.15
