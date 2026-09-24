"""ECG 패치 모니터링 처방(위중도 → 3~14일), 처방 종료·연장, 시작 시 경과 흩뿌리기."""
import collections
import random
import types

from emulator.hospital import monitoring as M
from emulator.hospital.profiles import make_profiles
from emulator.runtime.world import World, Patch


def test_prescription_days_follow_acuity():
    profs = make_profiles(3000, 7)
    r = random.Random(1)
    rx = [M.prescribe(p, r.random(), r.random()) for p in profs]
    assert all(3 <= x["days"] <= 14 for x in rx)
    assert {x["days"] for x in rx} == {3, 4, 5, 7, 14}
    tiers = collections.Counter(x["tier"] for x in rx)
    assert all(tiers[t] > 150 for t in ("short", "mid", "long")), tiers
    by_acuity = collections.defaultdict(list)
    for x in rx:
        by_acuity[x["acuity"]].append(x["days"])
    mean = {a: sum(v) / len(v) for a, v in by_acuity.items() if len(v) > 30}
    ks = sorted(mean)
    assert mean[ks[-1]] > mean[ks[0]] + 5                        # 위중할수록 길게
    heavy = next(p for p in profs if p["icd10"] == "I46.0")      # 심정지 후 → 장기(부정맥 포착)
    assert M.prescribe(heavy, 0.5, 0.5)["days"] == 14
    mild = dict(profs[0], icd10="E11.9", rhythm="nsr", age=50, comorbidities=[], pacemaker=False)
    assert M.prescribe(mild, 0.5, 0.5)["days"] == 3
    assert M.prescribe(mild, 0.5, 0.5, outpatient=True)["days"] in (4, 5)   # MCOT 는 한 단계 위


def _stub(now=1_000_000.0):
    w = World.__new__(World)
    w.sim_time = now
    w.rng = __import__("numpy").random.default_rng(3)
    w.cfg = types.SimpleNamespace(get=lambda *k, default=None: {("scenario", "patch"): {"rx_enabled": True, "battery_days": 15.5, "max_wear_days": 14},
                                                                 ("scenario", "patch", "battery_days"): 15.5}[k])
    w.admitted, w.by_id = {}, {}
    w.counters = collections.Counter()
    w.log = types.SimpleNamespace(add=lambda *a, **k: None)
    w.meta_dirty = False
    w.discharged = []
    w.discharge = lambda pid, reason="": (w.discharged.append((pid, reason)), w.admitted.pop(pid))
    return w


def test_prescription_end_discharges_or_extends():
    w = _stub()
    profs = make_profiles(400, 11)
    for p in profs:
        p["admission"] = {}
        rec = {"id": p["id"]}
        w._prescribe(rec, p, Patch(0, "BP-1", 1), False)
        assert rec["rx"]["end"] - rec["rx"]["start"] == rec["rx"]["days"] * 86400
        assert p["admission"]["monitoring"]["days"] == rec["rx"]["days"]
        w.admitted[p["id"]], w.by_id[p["id"]] = rec, p
    w.sim_time += 5 * 86400 + 1                                   # 5일 뒤: 단기(3~5일)는 모두 끝남
    w._step_prescriptions()
    ended = {pid for pid, _ in w.discharged}
    assert ended and all(w.by_id[pid] and "모니터링 처방 종료" in r for pid, r in w.discharged)
    assert all(rec["rx"]["days"] > 5 for rec in w.admitted.values())
    w.sim_time += 10 * 86400                                      # 15일 뒤: 연장된 장기만 남는다
    w._step_prescriptions()
    left = list(w.admitted.values())
    assert w.counters["rx_extended"] == len(left) >= 1
    assert all(r["rx"]["tier"] == "long" and r["rx"]["extended_days"] in (7, 14) for r in left)


def test_boot_fill_staggers_start_and_battery():
    w = _stub()
    w._boot_fill = True
    p = make_profiles(1, 5)[0]
    p["admission"] = {}
    patch = Patch(0, "BP-1", 1)
    patch.activated = w.sim_time
    rec = {"id": p["id"]}
    starts = []
    for _ in range(50):
        w._prescribe(rec, p, patch, False)
        starts.append(rec["rx"]["start"])
        assert rec["rx"]["start"] <= w.sim_time < rec["rx"]["end"]
        assert patch.activated == rec["rx"]["start"]
        assert abs(patch.battery - (100 - 100 * (w.sim_time - patch.activated) / (15.5 * 86400))) < 1.01
    assert len({round(s) for s in starts}) > 40


def test_patch_scenario_triggers():
    w = _stub()
    w.cfg = types.SimpleNamespace(get=lambda *k, default=None: {("scenario", "patch"): {"rx_enabled": True, "battery_days": 15.5, "max_wear_days": 14, "replace_below_pct": 5},
                                                                 ("scenario", "patch", "battery_days"): 15.5}[k])
    w.patches = {}
    for i, p in enumerate(make_profiles(20, 3)):
        p["admission"] = {}
        pt = Patch(i, f"BP-{i}", i)
        pt.activated = w.sim_time
        w.patches[i] = pt
        rec = {"id": p["id"], "row": i}
        w._prescribe(rec, p, pt, False)
        w.admitted[p["id"]], w.by_id[p["id"]] = rec, p
    assert "착용 만료 5건" in w._patch_scenario("patch_wear_expire", None, {"count": 5, "within_s": 60})
    due = [pt for pt in w.patches.values() if w.sim_time - pt.activated > 14 * 86400 - 61]
    assert len(due) == 5
    w._patch_scenario("patch_low_battery", None, {"count": 3})
    assert sum(1 for pt in w.patches.values() if pt.battery <= 5) == 3
    w._patch_scenario("rx_expire", None, {"count": 4, "within_s": 0})
    w._step_prescriptions()
    assert len(w.discharged) + w.counters["rx_extended"] == 4
