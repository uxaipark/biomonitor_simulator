"""기대 경보 기준표(emulator/alarms.py)가 현장 테스트 합격 기준, 화면 기본값(app.js), 매뉴얼(index.html)과 일치하는지.
에뮬레이터는 경보를 내보내지 않는다 — 이 표는 라우터·뷰어에서 떠야 할 경보의 시험 기준이고 한 곳에서만 정의한다."""
import os
import re

from emulator import alarms
from emulator.runtime.fieldtest import EVENTS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
JS = open(os.path.join(ROOT, "emulator/web/static/app.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "emulator/web/static/index.html"), encoding="utf-8").read()


def _js_obj(name):
    m = re.search(r"const " + name + r" = \{(.*?)\};", JS)
    assert m, name
    return {k: (c, code) for k, c, code in re.findall(r"(\w+): \['(\w+)', '([^']+)'\]", m.group(1))}


def test_field_test_expectations_follow_the_table():
    for k, ev in EVENTS.items():
        assert ev["prio"] in ("high", "medium", "low"), (k, ev)
        assert ev["expect"].startswith(alarms.PRIO_KO[ev["prio"]]), (k, ev["expect"])
    # 리듬 이벤트는 같은 리듬의 표 우선순위, 3도 차단·SpO2 85 % 는 위급 (매뉴얼·중앙 감시와 같게)
    for k in ("vfib", "vt", "asystole", "brady", "spo2_low"):
        assert EVENTS[k]["prio"] == "high", k
    for k, ev in EVENTS.items():
        if ev.get("rhythm"):
            assert ev["prio"] == alarms.expected("rhythm", ev["rhythm"])["prio"], k
    assert all(EVENTS[k]["technical"] for k in ("lead_off", "low_batt", "no_link", "gw_down"))


def test_screen_defaults_match_the_table():
    rh = _js_obj("RH_ALARM")
    assert rh == {k: (alarms.PRIO_COLOR[p], c) for k, (p, c) in alarms.RHYTHMS.items()}
    tech = _js_obj("TECH_ALARM")
    assert tech == {k: (alarms.PRIO_COLOR[p], c) for k, (p, c) in alarms.TECHNICAL.items()}
    m = re.search(r"const ALIM_HIGH = \{ hr: \[(\d+), (\d+)\], spo2: \[(\d+), null\], nibp_sys: \[(\d+), (\d+)\] \};", JS)
    assert m and tuple(map(int, m.groups())) == (*alarms.LIMITS["hr"]["high"], alarms.LIMITS["spo2"]["high"][0], *alarms.LIMITS["nibp_sys"]["high"])


def test_manual_matches_the_table():
    red = re.search(r'<b class="red">적색\(위급\)</b>:(.*?)</li>', HTML).group(1)
    assert "3도 차단" in red and "SpO₂&lt;88" in red and "HR&gt;150/&lt;40" in red
    yel = re.search(r'<b class="yel">황색\(주의\)</b>:(.*?)</li>', HTML).group(1)
    assert "리드오프" in yel and "체온 ≤38.5" in yel and "ST 상승" in yel
    assert "에뮬레이터는 경보를 내보내지 않습니다" in HTML


def test_expected_helper():
    assert alarms.expected("rhythm", "avb2_m1")["prio"] == "medium"          # 접두어 일치
    assert alarms.expected("rhythm", "afib_rvr")["code"] == "AFIB"
    assert alarms.expected("limit", "spo2:89")["prio"] == "medium" and alarms.expected("limit", "spo2:85")["prio"] == "high"
    assert alarms.expected("limit", "hr:70")["prio"] is None


def test_target_port_and_ip_are_checked():
    """송출 대상: 8080(키오스크)·5445(자신)·7300(라우터 상태 API) 포트와 잘못된 주소는 422."""
    import pytest
    from fastapi import HTTPException
    from emulator.app import _check_target
    for bad in ({"target_port": 8080}, {"target_port": 5445}, {"target_port": 7300}, {"target_port": 0},
                {"target_ip": "192.168.0.91:9100"}, {"target_ip": "bad host"}, {"target_port": "x"}):
        with pytest.raises(HTTPException) as ei:
            _check_target({"transport": bad})
        assert ei.value.status_code == 422, bad
    for ok in ({"target_ip": "192.168.0.91", "target_port": 9100}, {"target_ip": "router.local"}, {"target_ip": ""}, {"bundle_ms": 200}):
        _check_target({"transport": ok})
