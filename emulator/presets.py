"""대표 테스트 시나리오 프리셋 (기본값 + 9개).

적용은 '절대적'이다: 시나리오 계층(scenario.* · 재원 곡선 · 드릴 설정)을 기본값으로 되돌린 뒤 프리셋 값을 얹는다.
그래서 어떤 상태에서 눌러도 결과가 같다.  환자 수·장소 시나리오는 프리셋이 명시할 때만 바뀐다(규모는 사용자 몫).
actions 는 적용 직후 한 번 실행하는 수동 주입(트리거)이다.
"""
from __future__ import annotations

import copy

from .config import DEFAULT_CONFIG

# 시나리오 계층: 프리셋 적용 때 기본값으로 되돌리는 경로
LAYER = [("scenario",), ("general", "census_mode"), ("transport", "fuzz"), ("transport", "storm_smoothing"), ("transport", "store_forward")]
KEEP_IN_SCENARIO = ("site", "devices")           # 장소·기기 정책은 사용자 설정 유지 (프리셋이 명시하면 덮어씀)

_ALL_NET = {"wireless_noise": True, "wired_failure": True, "latency": True, "power_outage": True, "topology": True}
_NO_NET = {"enabled": False}
_QUIET = {"network": _NO_NET, "artifacts": {"enabled": False}, "gateway": {"fault_enabled": False},
          "clinical": {"enabled": False}, "routine": {"enabled": False}, "mcot_device": {"enabled": False}}

PRESETS = [
    {"id": "default", "name": "기본값", "purpose": "출고 기본 설정",
     "desc": "설정 파일 기본값입니다. 아티팩트 40 %, 게이트웨이 장애 20 %, 병원 일과·임상 악화(1,000 환자·일당 20건)·MCOT 단말 동작 켜짐, 네트워크 장애 꺼짐.",
     "patch": {}},
    {"id": "baseline", "name": "무장애 기준선", "purpose": "처리량·장기 안정성 측정",
     "desc": "장애·아티팩트·일과·임상 악화·단말 동작을 모두 끕니다. 라우터·뷰어의 처리 성능과 메모리 누수를 잴 때 변수를 없앤 기준선입니다.",
     "patch": {"scenario": {**_QUIET, "patch": {"battery_drain_enabled": False, "lead_off_enabled": False}, "exam_trip_ratio": 0.0}}},
    {"id": "ward_day", "name": "일상 병동 운영", "purpose": "현실적인 하루 (디지털 트윈 기본)",
     "desc": "병원 일과·자연 임상 악화·요일/시간대 재원 곡선·약한 무선 간섭과 장비 장애·게이트웨이 장애 10 %. 현장과 가장 비슷한 평상시입니다.",
     "patch": {"general": {"census_mode": "weekly"},
               "scenario": {"network": {"enabled": True, "intensity": 15, "wireless_noise": True, "wired_failure": False, "latency": True, "power_outage": False, "topology": True},
                            "artifacts": {"enabled": True, "intensity": 40}, "gateway": {"fault_enabled": True, "fault_intensity": 10}}}},
    {"id": "network", "name": "네트워크 장비 장애", "purpose": "업링크 단절·버퍼·재전송 처리",
     "desc": "코어·층 스위치·무선 AP 장애와 무선 간섭·지연을 강하게 겁니다. 적용 즉시 층 스위치 하나를 5분간 떨어뜨립니다.",
     "patch": {"scenario": {"network": {"enabled": True, "intensity": 70, **_ALL_NET, "power_outage": False}, "gateway": {"fault_enabled": True, "fault_intensity": 10}}},
     "actions": [{"what": "switch_fault", "params": {"off_s": 300}}]},
    {"id": "power", "name": "건물 정전 · UPS", "purpose": "대량 동시 단절·순차 복구",
     "desc": "정전만 켭니다. 적용 즉시 한 건물에 5분 정전을 일으켜 UPS 유지 · 발전기 전환 재부팅 · 일반 전원 차단 · 층 스위치 재부팅이 차례로 복구되는 파도를 만듭니다.",
     "patch": {"scenario": {"network": {"enabled": True, "intensity": 40, "wireless_noise": False, "wired_failure": False, "latency": False, "power_outage": True, "topology": False},
                            "gateway": {"fault_enabled": False}}},            # 정전 효과만 보이게 개별 GW 장애는 끔
     "actions": [{"what": "power_outage", "params": {"mains_s": 300}}]},
    {"id": "gateway", "name": "게이트웨이 장애 · 교체", "purpose": "무응답·성능 저하·교체(새 번호) 처리",
     "desc": "게이트웨이 무응답·성능 저하·하드웨어 고장을 강도 80 %로 발생시킵니다. 교체되면 새 게이트웨이 번호·MAC 로 재접속하고 META 를 다시 보냅니다.",
     "patch": {"scenario": {"gateway": {"fault_enabled": True, "fault_intensity": 80, "outage": True, "degrade": True, "replace": True}}},
     "actions": [{"what": "gateway_replace"}]},
    {"id": "artifacts", "name": "움직임 · 아티팩트 최대", "purpose": "신호 품질 판정·잡음 내성",
     "desc": "움직임·샤워·검사 이동·재부착·전동·패치 교체를 최대 빈도로, 병실 밖 이동 비율 15 %. 신호 품질 판정 로직의 최악 조건입니다.",
     "patch": {"scenario": {"artifacts": {"enabled": True, "intensity": 100, "motion": True, "shower": True, "exam_trips": True, "patch_reattach": True,
                                          "transfer": True, "patch_replace": True, "home_interference": True}, "exam_trip_ratio": 15.0}}},
    {"id": "clinical", "name": "임상 악화 · 코드블루 다발", "purpose": "조기경보·부정맥 분석 검증",
     "desc": "임상 악화를 1,000 환자·일당 300건으로 올리고 부정맥 에피소드를 켭니다. 적용 즉시 세 명을 빠르게 악화시키고 한 명에게 코드블루를 겁니다. 정답 라벨로 채점하세요.",
     "patch": {"scenario": {"clinical": {"enabled": True, "per_1000_patient_days": 300}, "rhythm_episodes": True, "artifacts": {"enabled": True, "intensity": 20}}},
     "actions": [{"what": "deteriorate", "params": {"fast": True}}, {"what": "deteriorate", "params": {"fast": True}},
                 {"what": "deteriorate", "params": {"fast": True}}, {"what": "code_blue"}]},
    {"id": "mcot", "name": "원외 MCOT 중심", "purpose": "모바일 회선·단말 끊김·일괄 업로드",
     "desc": "장소를 혼합으로, 원외 환자를 200명으로 늘리고 단말 동작(앱 강제 종료·절전 일괄 업로드·OS 업데이트)과 가정 전파 간섭을 켭니다. 적용 즉시 한 명의 앱을 10분 종료시킵니다.",
     "patch": {"general": {"outpatient_count": 200}, "scenario": {"site": "mixed", "mcot_device": {"enabled": True},
                                                                   "artifacts": {"enabled": True, "intensity": 60, "home_interference": True}}},
     "actions": [{"what": "phone", "params": {"state": "killed", "minutes": 10}}]},
    {"id": "router_stress", "name": "라우터 내구성", "purpose": "파서·재접속·폭주 처리",
     "desc": "오염 프레임(1,000개당 5개, 전 종류), 재접속 완만화 끔, 네트워크·게이트웨이 장애 50 %. 적용 즉시 20초 연결 폭주를 겁니다.",
     "patch": {"transport": {"fuzz": {"enabled": True, "rate_per_1000": 5}, "storm_smoothing": False},
               "scenario": {"network": {"enabled": True, "intensity": 50, **_ALL_NET}, "gateway": {"fault_enabled": True, "fault_intensity": 50}}},
     "actions": [{"what": "storm", "params": {"duration": 20}}]},
]
POINTS = {'default': (['아티팩트 40 % · 게이트웨이 장애 20 %', '병원 일과 · 임상 악화(1,000 환자·일당 20건) · MCOT 단말 동작 켬', '네트워크 장애 꺼짐 · 재원 수 고정'], ''), 'baseline': (['네트워크 · 게이트웨이 장애 꺼짐', '아티팩트 · 병원 일과 · 임상 악화 · 단말 동작 꺼짐', '패치 배터리 소모 · 리드 오프 꺼짐, 병실 밖 이동 0 %', '진행 중이던 장애·악화도 정리'], ''), 'ward_day': (['병원 일과 · 자연 임상 악화 켬', '요일·시간대 재원 곡선', '무선 간섭 · 지연 · 장비 장애 약하게 (15 %)', '게이트웨이 장애 10 % · 아티팩트 40 %'], ''), 'network': (['네트워크 장애 70 % (무선 · 유선 · 지연 · 장비)', '정전은 제외', '게이트웨이 장애 10 %'], '층 스위치 하나 5분 장애'), 'power': (['정전만 켬 (강도 40 %)', '개별 게이트웨이 장애 끔', 'UPS 유지 → 발전기 전환 재부팅 → 일반 전원 복전 → 층 스위치 재부팅'], '한 건물 5분 정전'), 'gateway': (['게이트웨이 장애 80 % (무응답 · 성능 저하 · 하드웨어 고장)', '교체되면 새 번호·MAC 으로 재접속, META 재전송'], '게이트웨이 1대 고장 → 교체'), 'artifacts': (['아티팩트 100 % (움직임 · 샤워 · 검사 이동 · 재부착 · 전동 · 패치 교체)', '가정 전파 간섭 켬 · 병실 밖 이동 15 %'], ''), 'clinical': (['임상 악화 1,000 환자·일당 300건', '부정맥 에피소드 켬 · 아티팩트 20 %', '정답 라벨(임상 CSV)로 채점'], '3명 빠른 악화 · 1명 코드블루'), 'mcot': (['장소 혼합 · 원외 환자 200명', 'MCOT 단말 동작 켬 (앱 종료 · 절전 일괄 업로드 · OS 업데이트)', '가정 전파 간섭 · 아티팩트 60 %'], '1명 앱 강제 종료 10분'), 'router_stress': (['오염 프레임 1,000개당 5개 (전 종류)', '재접속 완만화 끔 (폭주)', '네트워크 · 게이트웨이 장애 50 %'], '20초 연결 폭주')}
for _p in PRESETS:
    _p["points"], _p["actions_text"] = POINTS[_p["id"]]
BY_ID = {p["id"]: p for p in PRESETS}


def _get(d: dict, path: tuple):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _set(d: dict, path: tuple, v) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = v


def build_patch(preset_id: str, current: dict) -> dict:
    """시나리오 계층을 기본값으로 되돌리고 프리셋을 얹은 전체 patch (cfg.update 에 그대로 넣는다)."""
    p = BY_ID[preset_id]
    patch: dict = {}
    for path in LAYER:
        _set(patch, path, copy.deepcopy(_get(DEFAULT_CONFIG, path)))
    for k in KEEP_IN_SCENARIO:                                         # 사용자 쪽 설정 유지
        if k in (current.get("scenario") or {}):
            patch["scenario"][k] = copy.deepcopy(current["scenario"][k])

    def merge(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)
    merge(patch, p["patch"])
    patch["scenario"]["preset"] = preset_id
    patch["scenario"]["preset_modified"] = False
    return patch


def public_list() -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "purpose": p["purpose"], "desc": p["desc"], "points": p["points"], "actions_text": p["actions_text"],
             "actions": [a["what"] for a in p.get("actions", [])], "patch": p["patch"]} for p in PRESETS]
