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
        assert -56 < a["lat"] < 72 and -125 < a["lon"] < 145 and a["postal"] and a["district"] and a["city"] and a["region"]     # 지도에 찍을 좌표 + 실제 주소 체계
        assert a["text"].endswith(RESIDENCE[a["country"]]["en"]) and a["label"] == f"{NATION_LABEL[a['country']]} · {a['text']}"
    dom = [p for p in ps if not p.get("overseas")]
    assert all(not p["address"].get("overseas") and p["phone"].startswith("010") for p in dom[:300])
    assert [p["name"] for p in make_profiles(200, 7)] == [p["name"] for p in make_profiles(200, 7)]      # 시드 결정적


def test_arrhythmia_beat_generation_quality():
    """부정맥 박동열: PVC 결합 간격 일정, 다양한 박동 종류(N/S/V/F/Q 모두 도달), AFib 불응기, VT 의 capture/fusion, Wenckebach PR 패턴."""
    import numpy as np
    from emulator.signals.rhythms import generate_beats, pick_hr, BEAT_KINDS, AAMI, AAMI_CLASSES, RHYTHMS
    seen = set()
    for rhythm in RHYTHMS:
        for seed in (1, 2, 3):
            rng = np.random.default_rng(seed)
            hr = pick_hr(rhythm, rng)
            beats, info = generate_beats(rhythm, rng, 600, hr or 70, lambda t: 0.0)
            for b in beats:
                assert b["kind"] in BEAT_KINDS or b["kind"] in ("P", "Sp"), b["kind"]
                if b["kind"] in BEAT_KINDS:
                    seen.add(AAMI[b["kind"]])
            ts = [b["t"] for b in beats if b["kind"] in BEAT_KINDS]
            assert all(t2 - t1 >= 0.25 for t1, t2 in zip(ts, ts[1:])), rhythm      # 어떤 리듬도 250 ms 보다 짧은 RR 은 없다
    assert seen == set(AAMI_CLASSES)                                                 # N, S, V, F, Q 모두 생성된다
    # PVC: 선행 동조율 박동 대비 결합 간격의 산포가 작다 (단초점)
    rng = np.random.default_rng(11)
    beats, _ = generate_beats("pvc", rng, 1800, 72, lambda t: 0.0)
    coup = [beats[i]["t"] - beats[i - 1]["t"] for i in range(1, len(beats)) if beats[i]["kind"] == "V" and beats[i - 1]["kind"] == "N" and beats[i]["extra"].get("focus", 0) == 0]
    assert len(coup) >= 10 and np.std(coup) < 0.06, (len(coup), np.std(coup))
    # AFib: 최소 RR 이 AV 결절 불응기(≥0.30 s) 이상, 가끔 변행전도(aberr) 표시
    rng = np.random.default_rng(5)
    beats, _ = generate_beats("afib", rng, 1800, 100, lambda t: 0.0)
    rr = np.diff([b["t"] for b in beats])
    assert rr.min() >= 0.30 and any(b["extra"].get("aberr") for b in beats)
    # VT: 대부분 V, capture(N)·fusion(FV) 이 섞인다
    rng = np.random.default_rng(7)
    beats, _ = generate_beats("vt", rng, 1800, 160, lambda t: 0.0)
    kinds = [b["kind"] for b in beats]
    assert kinds.count("V") > 0.85 * len(kinds) and "FV" in kinds and "N" in kinds
    # Wenckebach: 탈락 전까지 PR 이 점점 늘되 증가폭은 줄어든다
    rng = np.random.default_rng(3)
    beats, _ = generate_beats("avb2_m1", rng, 600, 70, lambda t: 0.0)
    prs = []
    for b in beats:
        if b["kind"] == "N":
            prs.append(b["extra"]["pr"])
        elif b["kind"] == "P":
            break
    incs = np.diff(prs)
    assert len(prs) >= 3 and all(x > 0 for x in incs) and all(incs[i + 1] <= incs[i] + 1e-9 for i in range(len(incs) - 1))


def test_country_rosters_us_jp():
    """병원 국가 US/JP: 한국과 같은 수의 명단이 그 나라식 이름·주소·전화·MRN 으로, 해외 체류 풀에는 자기 나라가 없다."""
    import re
    from emulator.hospital.profiles import make_profiles
    kr = make_profiles(2000, 11)
    for c, phone_re, label_re in (("US", r"^\(\d{3}\) 555-01\d\d$", r", (IL|IN|WI|MI|IA) \d{5}$"), ("JP", r"^0[789]0-\d{4}-\d{4}$", r"^〒\d{3}-\d{4} .+丁目$")):
        ps = make_profiles(2000, 11, country=c)
        assert len(ps) == len(kr)
        home = [p for p in ps if not p["overseas"]]
        assert 0.85 < sum(p["nationality"] == c for p in home) / len(home) < 0.95          # 자국민 비율 0.9
        for p in home[:200]:
            a = p["address"]
            assert a["country"] == c and re.match(phone_re, p["phone"]) and re.search(label_re, a["label"]) and a["postal"] and a["lat"] and p["home_country"] == c
            assert not p["mrn"].startswith("MRN-")
        assert not any(p["nationality"] == c for p in ps if p["overseas"])
    jp = [p for p in make_profiles(500, 11, country="JP") if p["nationality"] == "JP"]
    assert all(" " in p["name"] and p["name_kana"] for p in jp[:50])


def test_identity_sets_keep_clinical_core():
    """국가별 신원 세트: 성별·출생연도는 코어 그대로, 이름·주소·전화·MRN 만 그 나라식, 결정적, 해외 체류 프로필은 제외."""
    from emulator.hospital.profiles import make_profiles
    from emulator.hospital.intl import make_identities
    ps = make_profiles(800, 5)
    for c in ("US", "JP"):
        a, b = make_identities(ps, c, 5), make_identities(ps, c, 5)
        assert a == b and len(a) >= sum(1 for p in ps if not p["overseas"])           # 해외 체류 풀도 대부분 그 나라 거주자로 바뀐다
        kept = [p for p in ps if p["overseas"] and p["id"] not in a]
        assert all(p["nationality"] != c for p in kept) and len(kept) < 0.5 * sum(1 for p in ps if p["overseas"])
        assert all(i["overseas"] is False for i in a.values())
        for p in ps[:100]:
            if p["id"] not in a:
                continue
            i = a[p["id"]]
            assert i["address"]["country"] == c and i["home_country"] == c and i["name"] != p["name"] and i["mrn"] != p["mrn"] and i["avatar"]


def test_korean_addresses_have_map_coordinates():
    """한국 거주지에도 지도 좌표(lat/lon)가 있어야 한다 — 없으면 라우터 MCOT 지도가 이전(미국) 위치를 그대로 둔다."""
    from emulator.hospital.profiles import make_profiles
    ps = [p for p in make_profiles(2000, 20240905) if not p["overseas"]]
    assert all(33.0 < p["address"]["lat"] < 38.7 and 124.5 < p["address"]["lon"] < 130.0 for p in ps)
    assert ps[0]["name"] == "안재민"                                   # 좌표를 붙여도 명단(난수열)은 그대로


def test_two_level_battery_gauge_runs_through_world():
    """연료계 2단계: 부족 표시 → (교체가 늦으면) 소진·전원 꺼짐 → 교체. 레코드 battery 는 100/10 만."""
    import os, subprocess, sys, json, tempfile
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {root!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 7, 'profile_count': 400, 'bed_capacity': 60, 'active_patients': 40, 'outpatient_count': 4, 'sim_speed': 60.0, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}},
            'scenario': {{'patch': {{'battery_days': 1.0, 'max_wear_days': 14, 'low_warn_hours': [4, 6], 'swap_delay_hours': [1, 8], 'battery_drain_enabled': True, 'replace_enabled': True}}}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w.running = True
vals = set()
for i in range(int(36 * 3600 / 60)):
    w.step(1.0)
    vals |= set(int(w.st.patch.arr['battery'][r['row']]) for r in w.admitted.values())
print(json.dumps({{'vals': sorted(vals), 'dead': int(w.counters['patch_battery_dead']), 'replaced': int(w.counters['patch_replaced'])}}))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(out["vals"]) <= {100, 10} and 10 in out["vals"], out
    assert out["replaced"] > 0 and out["dead"] > 0, out                 # 교체가 늦어 꺼지는 패치도 생긴다
