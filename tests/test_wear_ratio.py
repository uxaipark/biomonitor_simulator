"""패치 착용 비율 (scenario.patch.wear_ratio): 미착용 입원 환자는 송신·게이트웨이 연결·이동이 없고 병상에 고정, 비율을 바꾸면 따라간다."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def test_wear_ratio_unworn_patients_stay_in_bed():
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.runtime import invariants
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 11, 'profile_count': 600, 'bed_capacity': 120, 'active_patients': 100, 'outpatient_count': 6, 'sim_speed': 30.0, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}},
            'scenario': {{'patch': {{'wear_ratio': 50}}, 'exam_trip_ratio': 15}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w.running = True
moved_unworn = 0
for i in range(600):
    w.step(1.0)
    for r in w.admitted.values():
        if not r.get('worn', True) and (r['location'] != r['room_idx'] or r['gw'] >= 0 or w.st.patch.arr['active'][r['row']]):
            moved_unworn += 1
st50 = w.wear_stats()
inv50 = w.invariants(fresh=True)
mcot_all_worn = all(r.get('worn', True) for r in w.admitted.values() if r['outpatient'])
pid = next(p for p, r in w.admitted.items() if not r['outpatient'] and r.get('worn', True))
w.detach_patch(pid, 'test'); det = w.admitted[pid]
det_ok = (not det['worn']) and det['gw'] < 0 and det['row'] not in w.patches and det['location'] == det['room_idx']
w.attach_patch(pid, 'test'); att_ok = w.admitted[pid]['worn'] and w.admitted[pid]['row'] in w.patches and int(w.st.patch.arr['active'][w.admitted[pid]['row']]) == 1
cfg.update({{'scenario': {{'patch': {{'wear_ratio': 100}}}}}}); w.apply_config()
for i in range(5):
    w.step(1.0)
st100 = w.wear_stats()
cfg.update({{'scenario': {{'patch': {{'wear_ratio': 0}}}}}}); w.apply_config()
for i in range(60):
    w.step(1.0)
st0 = w.wear_stats(); inv0 = w.invariants(fresh=True)
print(json.dumps({{'st50': st50, 'st100': st100, 'st0': st0, 'moved_unworn': moved_unworn, 'mcot': mcot_all_worn, 'det': det_ok, 'att': att_ok,
                  'inv50': inv50.get('violations', inv50), 'inv0': inv0.get('violations', inv0)}}, default=str))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    st50 = out["st50"]
    assert st50["inpatients"] >= 80 and 30 <= st50["worn_pct"] <= 70, st50
    assert out["moved_unworn"] == 0 and out["mcot"] and out["det"] and out["att"], out
    assert out["st100"]["unworn"] == 0 and out["st0"]["worn"] == 0, out
    for k in ("inv50", "inv0"):
        assert out[k] == [], (k, out[k])


def test_hospital_stock_patches():
    """병원 재고: 입고(로트·유효기간), 발급은 그 병동 보관함에서, 지정 serial 착용, 착용 환자 수보다 적으면 100 단위 자동 입고, 재고 목록 API 형식."""
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 80, 'active_patients': 60, 'outpatient_count': 4, 'sim_speed': 30.0, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}},
            'scenario': {{'patch': {{'wear_ratio': 50, 'stock_unit': 100}}}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w.running = True
for i in range(30):
    w.step(1.0)
v = w.patch_registry_view('stock', limit=1000)
from_stock = all(w.patch_registry[p.serial].get('lot') for p in w.patches.values())
ward_ok = all(w.patch_registry[p.serial]['ward'] in w.stock for p in w.patches.values())     # 보관함이 비면 다른 병동 보관함에서 융통
pid = next(p for p, r in w.admitted.items() if not r['outpatient'] and not r.get('worn', True))
key = w._stock_key(w.admitted[pid]); pick = w.stock[key][-1]
r = w.attach_patch(pid, 'test', serial=pick)
picked_ok = r['patch'] == pick and w.patch_registry[pick]['status'] == 'active' and pick not in w.stock[key]
try:
    w.attach_patch(next(p for p, r in w.admitted.items() if not r['outpatient'] and not r.get('worn', True)), 'test', serial=pick); dup = 'accepted'
except ValueError:
    dup = 'rejected'
w.step(61.0)
min_left = sum(len(x) for x in w.stock.values()) - sum(w.stock_need().values())
print(json.dumps({{'stock': v['stock'], 'keys': len(w.stock), 'sample': v['patches'][0], 'from_stock': from_stock, 'ward_ok': ward_ok, 'picked': picked_ok, 'dup': dup,
                  'min_left': min_left, 'inv': w.invariants(fresh=True)['violations']}}, default=str))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    smp = out["sample"]
    assert out["stock"] > 0 and set(("serial", "patch_id", "status", "lot", "expiry", "location", "ward", "received_at")) <= set(smp) and smp["status"] == "stock", out
    assert out["from_stock"] and out["ward_ok"] and out["picked"] and out["dup"] == "rejected", out
    assert out["min_left"] >= 0 and out["inv"] == [], out          # 재고 >= 패치 착용 환자 수


def test_patch_seq_restart_is_flagged_as_error():
    """패치는 소진 전에 꺼지지 않는다: 같은 patch_id 의 번호가 0 근처로 돌아가면 재정렬이 아니라 patch_seq_restart(오류)."""
    import struct
    sys.path.insert(0, ROOT)
    from emulator.config import CH_HR
    from emulator.runtime import protocol as P
    from emulator.runtime.verify import StreamChecker
    def rec(pseq):
        return struct.pack("<IIIBBbB", 0x10005, 77, pseq, 0, 100, -60, 1) + struct.pack("<BBH", CH_HR, P.DTYPE_CODE["uint8"], 1) + b"\x48"
    c = StreamChecker()
    seqs = list(range(1000, 1010)) + [1008] + [0, 1, 2]            # 1008: 늦게 온 재전송(재정렬), 0: 재시작
    for i, q in enumerate(seqs):
        c.feed(P.frame(9, i + 1, 1_790_000_000_000 + i * 200, 1, rec(q), 0))
    sm = c.summary()
    assert sm["patch_seq_restart"] == 1 and sm["patch_seq_reorder"] == 1 and sm["patch_seq_gap"] == 0, sm


def test_truth_patch_seq_never_restarts():
    sys.path.insert(0, ROOT)
    from emulator.runtime.truthbench import truth_seq_base
    a, b = truth_seq_base(1_791_000_000.0), truth_seq_base(1_791_000_060.0)
    assert b - a == 1200 and a > 10_000_000                          # 1 분 뒤 재시작해도 그 사이 최대 프레임 수(50 ms 묶음)보다 크다


def test_manual_stock_receive_api():
    """수동 입고: POST /api/v1/emr/patches/stock — 부족한 보관함부터 배분, 보관함 지정, request_id 멱등, 잘못된 수량·보관함 거절."""
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 80, 'active_patients': 60, 'outpatient_count': 4, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
before = sum(len(v) for v in w.stock.values())
r1 = w.receive_stock(100)
k = w._stock_keys()[0]; n0 = len(w.stock[k])
r2 = w.receive_stock(100, k)
errs = []
for args in ((0,), (20000,), (100, 'NOPE')):
    try:
        w.receive_stock(*args); errs.append('accepted')
    except (ValueError, KeyError):
        errs.append('rejected')
print(json.dumps({{'before': before, 'r1': r1, 'after': sum(len(v) for v in w.stock.values()), 'ward_add': len(w.stock[k]) - n0, 'errs': errs, 'lot_differs': r1['lot'] != r2['lot']}}, default=str))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["after"] == out["before"] + 200 and out["r1"]["received"] == 100 and sum(out["r1"]["by_ward"].values()) == 100, out
    assert out["ward_add"] == 100 and out["lot_differs"] and out["errs"] == ["rejected"] * 3, out


def test_patch_numbers_are_8_digits_and_old_stock_renumbered():
    """패치 번호 = 숫자 8 자리(시리얼 = patch_id).  옛 번호(BP-xxxxxx) 미사용 재고는 재시작 때 같은 수량을 새 번호로 다시 입고."""
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 80, 'active_patients': 60, 'outpatient_count': 4, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}}}})
s = cfg.snapshot()['signals']
mk = lambda: World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w = mk()
ok1 = all(e['serial'].isdigit() and len(e['serial']) == 8 and int(e['serial']) == e['patch_id'] for e in w.patch_registry.values())
truth = [int(x) for x in w.st.patch.arr['patch_id'][w.truth.rows]]
n_stock = sum(len(v) for v in w.stock.values())
# 옛 번호 재고를 흉내: 하나를 BP- 로 바꿔 DB 에 넣는다
k = w._stock_keys()[0]
w.db.upsert_patch({{'serial': 'BP-000777', 'patch_id': 0x10000 + 777, 'patient_id': 0, 'patient_name': None, 'status': 'stock', 'issued_at': None, 'issued_reason': None,
                   'retired_at': None, 'retired_reason': None, 'battery_at_retire': None}})
w.db.upsert_stock({{'serial': 'BP-000777', 'lot': 'L0', 'expiry': '2028-01-01', 'location': 'x', 'ward': k, 'received_at': 0}})
w.db.close(); w.st.close(); w.st.unlink()
w2 = mk()
stock2 = [sr for v in w2.stock.values() for sr in v]
ok2 = 'BP-000777' not in w2.patch_registry and all(sr.isdigit() and len(sr) == 8 for sr in stock2)
print(json.dumps({{'ok1': ok1, 'truth': [min(truth), max(truth)], 'ok2': ok2, 'n1': n_stock, 'n2': len(stock2)}}))
w2.db.close(); w2.st.close(); w2.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ok1"] and out["ok2"] and 99000001 <= out["truth"][0] and out["truth"][1] <= 99999999, out
    assert out["n2"] >= out["n1"], out


def test_router_stock_requests_scheduled_delivery():
    """라우터 재고 요청: 납품일 입고 시각에 입고(보관함 지정·착용 환자 수 비례), request_id 멱등, 주말·지난 날짜·형식 거절, 취소, 자동 입고 끄기."""
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 80, 'active_patients': 60, 'outpatient_count': 4, 'sim_speed': 60.0, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}}, 'scenario': {{'patch': {{'auto_restock': False}}}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w.running = True
tot = lambda: sum(len(v) for v in w.stock.values())
k = w._stock_keys()[0]
a, new_a = w.create_stock_request({{'request_id': 'r1', 'ward': '', 'qty': 500, 'deliver_on': '2026-09-21', 'source': 'router'}})
b, _ = w.create_stock_request({{'request_id': 'r2', 'ward': k, 'qty': 100, 'deliver_on': '2026-09-21'}})
c, _ = w.create_stock_request({{'request_id': 'r3', 'ward': 'MCOT', 'qty': 100, 'deliver_on': '2026-09-22'}})
a2, new_a2 = w.create_stock_request({{'request_id': 'r1', 'ward': '', 'qty': 999, 'deliver_on': '2026-09-23'}})
errs = []
for bad in ({{'request_id': 'x1', 'qty': 100, 'deliver_on': '2026-09-26'}}, {{'request_id': 'x2', 'qty': 100, 'deliver_on': '2026-09-18'}},
            {{'request_id': 'x3', 'qty': 0, 'deliver_on': '2026-09-22'}}, {{'request_id': 'x4', 'qty': 100, 'deliver_on': '22-09-2026'}}, {{'request_id': 'x5', 'qty': 100, 'deliver_on': '2026-09-22', 'ward': 'NOPE'}}):
    try:
        w.create_stock_request(bad); errs.append('accepted')
    except (ValueError, KeyError):
        errs.append('rejected')
w.cancel_stock_request('r3')
t0 = tot(); auto0 = w.counters.get('stock_received', 0)
for i in range(30):          # 09:00 → 09:30: 아직 입고 전
    w.step(1.0)
before_due = w.stock_requests['r1']['status']
for i in range(40):          # → 10:10
    w.step(1.0)
v = w.stock_requests_view()
r1 = w.stock_requests['r1']
print(json.dumps({{'new_a': new_a, 'new_a2': new_a2, 'a2_qty': a2['qty'], 'errs': errs, 'before_due': before_due, 'r1': r1['status'], 'r2': w.stock_requests['r2']['status'],
                  'r3': w.stock_requests['r3']['status'], 'r1_sum': sum(r1['by_ward'].values()), 'r1_n': len(r1['by_ward']), 'n_req': v['total'], 'auto': v['auto_restock']}}, default=str))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["new_a"] and not out["new_a2"] and out["a2_qty"] == 500, out                   # 같은 request_id: 처음 기록 그대로
    assert out["errs"] == ["rejected"] * 5, out                                              # 주말·지난 날짜·수량 0·형식·없는 보관함
    assert out["before_due"] == "scheduled" and out["r1"] == "delivered" and out["r2"] == "delivered" and out["r3"] == "cancelled", out
    assert out["r1_sum"] == 500 and out["r1_n"] > 1 and out["n_req"] == 3 and out["auto"] is False, out


def test_ward_transfer_keeps_the_same_patch():
    """병실 이동: 병상·병동이 바뀌어도 같은 patch_id·시리얼, 새 패치 발급 없음 (실제 병원은 이동 때 패치를 바꾸지 않는다)."""
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 120, 'active_patients': 60, 'outpatient_count': 2, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}}, 'scenario': {{'patch': {{'wear_ratio': 100}}}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
moved = []
for pid, rec in list(w.admitted.items()):
    if rec['outpatient']:
        continue
    before = (w.patches[rec['row']].serial, rec['ward_idx'], rec['bed_idx'])
    rep0 = w.counters['patch_replaced']
    w._transfer(rec, allow_discharge=False)
    if rec['bed_idx'] != before[2]:
        moved.append((before[0] == w.patches[rec['row']].serial, w.counters['patch_replaced'] == rep0))
    if len(moved) >= 5:
        break
print(json.dumps({{'moved': moved}}))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(out["moved"]) >= 3 and all(a and b for a, b in out["moved"]), out
