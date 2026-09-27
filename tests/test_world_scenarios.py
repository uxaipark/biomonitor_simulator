"""월드 수준 시나리오 회귀: 작은 병원을 고정 시계로 6 시뮬시간 돌리며 모든 트리거를 쏘고 불변식이 한 번도 깨지지 않는지 본다.

tools/world_sim.py 를 서브프로세스로 돌린다 — 데이터 폴더를 임시 폴더로 바꿔야 해서(BIOSIM_DATA_DIR 는 import 시점에 읽힘)
같은 프로세스에서는 할 수 없다.  2026-09-27 의 '이동 뒤 병실 복귀 누락'(입원 77 % 복도 방치) 같은 오류는 이 검사에서 바로 드러난다.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture(scope="module")
def sim_result(tmp_path_factory):
    d = tmp_path_factory.mktemp("worldsim")
    out = d / "result.json"
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "world_sim.py"), "--hours", "6", "--patients", "60", "--speed", "30",
                        "--data-dir", str(d / "data"), "--json", str(out), "--quiet"], capture_output=True, text=True, timeout=600)
    assert out.exists(), f"world_sim 실패 (rc {r.returncode}):\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}"
    return json.loads(out.read_text("utf-8"))


def test_world_runs_without_errors(sim_result):
    assert sim_result["errors"] == []
    assert sim_result["steps"] == 720 and sim_result["invariant_checks"] >= 30


def test_world_invariants_hold_throughout(sim_result):
    assert sim_result["invariant_failures"] == [], json.dumps(sim_result["invariant_failures"][:3], ensure_ascii=False)[:1500]


def test_scenarios_actually_happen(sim_result):
    c = sim_result["counters"]
    for k in ("admissions", "discharges", "exam_trips", "patch_replaced", "gw_faults", "gw_replaced", "net_events", "power_events", "lead_off",
              "deteriorations", "code_blue", "rx_completed", "gw_handover", "transfers"):
        assert c.get(k, 0) > 0, f"{k} 가 한 번도 일어나지 않음: {c}"
    f = sim_result["final"]
    assert 0 < f["moving_pct"] < 50, f                                # 이동 중 비율이 말이 되는 범위 (하한 8 % 설정)
    assert f["inpatients"] >= 55                                      # 목표 60명 ± 5 유지
    fired = dict(sim_result["fired"])
    assert "unknown" not in fired.values(), fired                    # 모든 트리거가 처리됨
