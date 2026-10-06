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
