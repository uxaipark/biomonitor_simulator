"""Deterministic clinical models + layout regulation checks + scenario script files."""
import glob, json, os
from emulator.signals import trend
from emulator.hospital import layout as L

TRIGGERS = {"gateway_fault", "gateway_replace", "network_event", "lead_off", "episode", "exam", "replace_patch", "patch_wear_expire", "patch_low_battery", "rx_expire", "discharge", "admit", "vfib",
            "storm", "half_open", "dup_id", "capture", "lead_off_off", "episode_off", "exam_off",
            "config"}                                                 # 녹화 재생 항목: 설정 변경 (world.trigger 가 처리)


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


def test_overseas_profiles_for_mcot():
    """해외 체류 외국인(주요 고객국) 프로필: 풀의 일정 비율, 거주국 주소·시간대·통신사·국제 번호가 일관되고 국내 프로필은 그대로."""
    import zoneinfo
    from emulator.hospital.profiles import make_profiles, RESIDENCE
    from emulator.hospital.names import NATION_LABEL, OVERSEAS
    ps = make_profiles(3000, 20240905)
    ovs = [p for p in ps if p.get("overseas")]
    assert 0.05 < len(ovs) / len(ps) < 0.12
    codes = {p["nationality"] for p in ovs}
    assert {"US", "JP", "GB", "DE", "BR"} <= codes <= set(RESIDENCE) == set(OVERSEAS)
    for p in ovs[:300]:
        a = p["address"]
        assert a["overseas"] and a["country"] == p["nationality"] and a["carrier"] and a["label"].startswith(NATION_LABEL[p["nationality"]])
        assert zoneinfo.ZoneInfo(a["tz"]) is not None and p["phone"].startswith("+") and p["nationality_label"] == NATION_LABEL[p["nationality"]]
    dom = [p for p in ps if not p.get("overseas")]
    assert all(not p["address"].get("overseas") and p["phone"].startswith("010") for p in dom[:300])
    assert [p["name"] for p in make_profiles(200, 7)] == [p["name"] for p in make_profiles(200, 7)]      # 시드 결정적
