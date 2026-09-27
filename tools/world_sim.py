"""헤드리스 월드 시뮬레이션: 작은 병원을 고정 시계로 몇 시뮬시간 돌리며 시나리오 트리거를 쏘고 불변식을 검사한다.

  python tools/world_sim.py --hours 6 --patients 60 --speed 30 [--json out.json] [--data-dir DIR]

전송(워커)은 띄우지 않는다(루프 은행 없이도 월드는 돈다).  데이터 폴더는 --data-dir(기본 임시 폴더)를 쓰므로 운영 DB 를 건드리지 않는다.
tests/test_world_scenarios.py 가 이 스크립트를 서브프로세스로 돌린다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0, help="시뮬 시간(시간)")
    ap.add_argument("--patients", type=int, default=60)
    ap.add_argument("--outpatients", type=int, default=6)
    ap.add_argument("--speed", type=float, default=30.0, help="스텝당 시뮬 초 (fixed_step)")
    ap.add_argument("--seed", type=int, default=20240905)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    data_dir = a.data_dir or tempfile.mkdtemp(prefix="biosim-worldsim-")
    os.environ["BIOSIM_DATA_DIR"] = data_dir                         # emulator.config 를 읽기 전에
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from emulator.config import Config, BASE_DIR
    from emulator.runtime.world import World, EventLog
    from emulator.runtime import invariants
    from emulator.signals.loops import LoopBank

    cfg = Config(BASE_DIR / "config.json")
    cfg.update({"general": {"seed": a.seed, "profile_count": max(400, a.patients * 4), "bed_capacity": max(40, int(a.patients / 0.8)), "active_patients": a.patients,
                            "outpatient_count": a.outpatients, "sim_speed": a.speed, "fixed_step": True, "fixed_start": "2026-09-21T09:00", "admissions_per_hour": 6, "discharges_per_hour": 6},
                "transport": {"target_ip": "", "workers": 1},
                "scenario": {"network": {"enabled": True, "intensity": 60, "wireless_noise": True, "wired_failure": True, "latency": True, "power_outage": True, "topology": True},
                             "gateway": {"fault_enabled": True, "fault_intensity": 80}, "artifacts": {"enabled": True, "intensity": 60},
                             "clinical": {"enabled": True, "per_1000_patient_days": 400}, "rhythm_episodes": True, "exam_trip_ratio": 8.0,
                             "patch": {"battery_days": 15.5, "max_wear_days": 0.15, "rx_enabled": True, "battery_drain_enabled": True, "replace_enabled": True},
                             "rf_noise": {"enabled": True, "level": 50}}})
    s = cfg.snapshot()["signals"]
    bank = LoopBank(s["ecg_fs"], s["ppg_fs"], s["resp_fs"], s["accel_fs"], s["loop_seconds"], s["variants_per_rhythm"], a.seed)   # 로드하지 않음
    log = EventLog(4000)
    t0 = time.time()
    w = World(cfg, bank, log)
    w.running = True
    steps = int(a.hours * 3600 / a.speed)
    # 시나리오 트리거: 시뮬 진행 비율로 쏜다
    plan = [(0.02, "network_event", {}), (0.05, "gateway_fault", {}), (0.08, "gateway_replace", {}), (0.10, "switch_fault", {"off_s": 300}),
            (0.15, "power_outage", {"mains_s": 240}), (0.20, "deteriorate", {"fast": True}), (0.22, "code_blue", {}), (0.25, "exam", {}),
            (0.30, "replace_patch", {}), (0.35, "lead_off", {}), (0.40, "discharge", {}), (0.41, "admit", {}), (0.45, "surge", {"count": 5, "over_min": 20}),
            (0.50, "patch_wear_expire", {"count": 5, "within_s": 600}), (0.55, "rx_expire", {"count": 6, "within_s": 600}), (0.60, "phone", {"state": "killed", "minutes": 5}),
            (0.65, "half_open", {"count": 2, "duration": 120}), (0.70, "dup_id", {"duration": 60}), (0.75, "storm", {"duration": 10}), (0.80, "episode", {}), (0.85, "vfib", {})]
    fired, checks, errors = [], [], []
    next_plan = 0
    for i in range(steps):
        frac = i / max(1, steps)
        while next_plan < len(plan) and plan[next_plan][0] <= frac:
            _, what, params = plan[next_plan]
            next_plan += 1
            try:
                fired.append((what, w.trigger(what, None, params)))
            except Exception as e:
                errors.append(f"trigger {what}: {type(e).__name__}: {e}")
        try:
            w.step(1.0)
        except Exception as e:
            errors.append(f"step {i}: {type(e).__name__}: {e}")
            break
        if i % max(1, int(600 / a.speed)) == 0:                              # 10 시뮬분마다 불변식
            r = invariants.check(w)
            checks.append({"step": i, "sim_h": round(i * a.speed / 3600, 2), "ok": r["ok"], "violations": r["violations"], "stats": r["stats"]})
    final = invariants.check(w)
    try:
        w.db.close(); w.st.close(); w.st.unlink()                          # 공유메모리·DB 정리 (resource_tracker 경고 방지)
    except Exception:
        pass
    bad = [c for c in checks if not c["ok"]] + ([] if final["ok"] else [{"step": steps, "ok": False, "violations": final["violations"]}])
    ev_kinds = {}
    for e in log.recent(0, 4000):
        ev_kinds[e["kind"]] = ev_kinds.get(e["kind"], 0) + 1
    out = {"steps": steps, "sim_hours": a.hours, "patients": a.patients, "wall_s": round(time.time() - t0, 1), "errors": errors,
           "counters": {k: int(v) for k, v in w.counters.items()}, "events_by_kind": ev_kinds, "fired": fired, "invariant_checks": len(checks),
           "invariant_failures": bad, "final": final["stats"], "ok": not errors and not bad}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    if not a.quiet:
        print(json.dumps({k: out[k] for k in ("steps", "sim_hours", "wall_s", "ok", "errors", "final")}, ensure_ascii=False))
        print("counters:", json.dumps(out["counters"], ensure_ascii=False))
        for b in bad[:5]:
            print("VIOLATION", json.dumps(b, ensure_ascii=False, default=str)[:600])
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
