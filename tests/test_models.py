"""Deterministic clinical models + layout regulation checks + scenario script files."""
import glob, json, os
from emulator.signals import trend
from emulator.hospital import layout as L

TRIGGERS = {"gateway_fault", "gateway_replace", "network_event", "lead_off", "episode", "exam", "replace_patch", "discharge", "admit", "vfib",
            "storm", "half_open", "dup_id", "capture", "lead_off_off", "episode_off", "exam_off"}


def test_nibp_is_deterministic_and_plausible():
    prof = {"age": 70, "comorbidities": ["고혈압"], "disease": "협심증"}
    a = trend.nibp(81, prof, 1_788_626_000.0)
    b = trend.nibp(81, prof, 1_788_626_000.0)
    assert a == b
    assert 80 <= a["sys"] <= 210 and 45 <= a["dia"] < a["sys"] and a["dia"] <= a["map"] <= a["sys"]
    later = trend.nibp(81, prof, 1_788_626_000.0 + 3600)
    assert later["t"] == a["t"] + 3600                     # one cuff reading per hour, same minute


def test_layout_templates_meet_ward_rules():
    for name, t in L.TEMPLATES.items():
        lay = L.generate(min(500, t["max_beds"]), 1, name)
        assert L.validate(lay) == []
        for f in lay["floors"]:
            if f["kind"] != "ward":
                continue
            rooms = [r for r in f["rooms"] if not r.get("ensuite")]
            assert sum(r["kind"] == "toilet" for r in rooms) >= 2, name
            assert sum(r["kind"] == "nurse_station" for r in rooms) >= 2, name
            assert all(len(w.get("zones", [])) >= 1 for w in f["wards"]), name


def test_scenario_scripts_reference_known_triggers():
    files = glob.glob(os.path.join(os.path.dirname(__file__), "..", "scenarios", "*.json"))
    assert files
    for fp in files:
        doc = json.load(open(fp, encoding="utf-8"))
        items = doc["items"]
        assert items == sorted(items, key=lambda x: x.get("at", 0))
        for it in items:
            assert it["what"] in TRIGGERS, (fp, it["what"])
