"""라우터 환자 관리 쓰기 연동: 등록(재고·재고 밖 패치, 기존 EMR·신규 환자) · 교체 · 반납(일시 제거·종료), request_id 멱등, 거절 사유."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def test_register_replace_return_flow():
    d = tempfile.mkdtemp()
    code = f"""
import os, sys, json
os.environ['BIOSIM_DATA_DIR'] = {d!r}
sys.path.insert(0, {ROOT!r})
from emulator.config import Config, BASE_DIR
from emulator.runtime.world import World, EventLog
from emulator.runtime.admin import AdminError
from emulator.signals.loops import LoopBank
cfg = Config(BASE_DIR / 'config.json')
cfg.update({{'general': {{'seed': 5, 'profile_count': 500, 'bed_capacity': 120, 'active_patients': 60, 'outpatient_count': 4, 'fixed_step': True,
            'fixed_start': '2026-09-21T09:00'}}, 'transport': {{'target_ip': '', 'workers': 1}}}})
s = cfg.snapshot()['signals']
w = World(cfg, LoopBank(s['ecg_fs'], s['ppg_fs'], s['resp_fs'], s['accel_fs'], s['loop_seconds'], s['variants_per_rhythm'], 7), EventLog(4000))
w.running = True
for i in range(5): w.step(1.0)
A = w.admin
out = {{}}
def call(action, body, fn):
    try:
        return A.run(action, body, fn)
    except AdminError as e:
        return e.status, {{'detail': e.detail}}
ward = w.hospital.wards[0]
free_bed = next(w.hospital.beds[b]['id'] for b in ward['bed_idxs'] if not w.hospital.beds[b]['patient_id'])
busy_bed = next((w.hospital.beds[b]['id'] for b in ward['bed_idxs'] if w.hospital.beds[b]['patient_id']), None)
pool = next(p for p in w.profiles if p['status'] == 'pool' and not p.get('overseas'))
stock1 = w.stock[ward['id']][0] if w.stock.get(ward['id']) else next(sr for v in w.stock.values() for sr in v)
reg = {{'request_id': 'R1', 'source': 'router', 'profile_id': pool['id'], 'placement': {{'mode': 'inpatient', 'ward': ward['id'], 'bed': free_bed, 'monitoring_days': 5}},
        'patch': {{'serial': stock1, 'source': 'stock'}}}}
c1, r1 = call('register', reg, A.register)
c1b, r1b = call('register', reg, A.register)                      # 같은 request_id
out['reg'] = [c1, r1.get('serial') == stock1, r1b == r1, c1b]
pno = r1['patient_id']
rec = next(r for r in w.admitted.values() if r.get('patient_no') == pno)
out['reg_state'] = [w.hospital.beds[rec['bed_idx']]['id'] == free_bed, int(w.st.patch.arr['active'][rec['row']]), w.patch_registry[stock1]['status'], round(rec['rx']['days'])]
errs = {{}}
errs['in_use'] = call('register', {{'request_id': 'E1', 'profile_id': next(p['id'] for p in w.profiles if p['status'] == 'pool' and not p.get('overseas')), 'placement': {{'ward': ward['id']}}, 'patch': {{'serial': stock1, 'source': 'stock'}}}}, A.register)[0]
errs['already'] = call('register', {{'request_id': 'E2', 'profile_id': pool['id'], 'placement': {{'ward': ward['id']}}, 'patch': {{'patch_id': 55555555, 'source': 'ble'}}}}, A.register)[0]
errs['bed'] = call('register', {{'request_id': 'E3', 'patient': {{'name': '홍길동', 'sex': 'M', 'birth_date': '1950-01-02'}}, 'placement': {{'ward': ward['id'], 'bed': busy_bed}}, 'patch': {{'patch_id': 55555556, 'source': 'manual'}}}}, A.register)[0] if busy_bed else 409
errs['noward'] = call('register', {{'request_id': 'E4', 'patient': {{'name': '홍길동', 'sex': 'M', 'birth_date': '1950-01-02'}}, 'placement': {{'mode': 'inpatient'}}, 'patch': {{'patch_id': 55555557, 'source': 'manual'}}}}, A.register)[0]
errs['missing'] = call('register', {{'request_id': 'E5', 'patient': {{'name': '홍길동'}}, 'placement': {{'ward': ward['id']}}, 'patch': {{'patch_id': 55555558, 'source': 'manual'}}}}, A.register)[0]
errs['notstock'] = call('register', {{'request_id': 'E6', 'patient': {{'name': '홍길동', 'sex': 'M', 'birth_date': '1950-01-02'}}, 'placement': {{'ward': ward['id']}}, 'patch': {{'patch_id': 55555559, 'source': 'stock'}}}}, A.register)[0]
out['errs'] = errs
# 신규 환자 + 재고 밖 패치 (BLE)
c2, r2 = call('register', {{'request_id': 'R2', 'patient': {{'name': '에이티', 'sex': 'F', 'birth_date': '1960-05-06', 'phone': '010-1111-2222'}},
                         'placement': {{'mode': 'inpatient', 'ward': ward['id']}}, 'patch': {{'patch_id': 65178647, 'source': 'ble'}}}}, A.register)
p2 = w.by_id[r2['profile_id']]
out['new'] = [c2, r2['serial'], r2['patch_id'], p2['name'], p2['birth_date'], bool(p2.get('manual_identity'))]
# 교체 (재고)
stock2 = next(sr for v in w.stock.values() for sr in v)
c3, r3 = call('replace', {{'request_id': 'X1', 'old_patch_id': w.patch_registry[stock1]['patch_id'], 'new_patch': {{'serial': stock2, 'source': 'stock'}}, 'reason': 'adhesion'}}, lambda b: A.replace(pno, b))
out['replace'] = [c3, r3['old']['serial'] == stock1, r3['new']['serial'] == stock2, w.patch_registry[stock1]['status'], w.patch_registry[stock1]['retired_reason'], w.patch_registry[stock2]['issued_reason'], rec.get('patient_no') == pno]
out['replace_bad'] = call('replace', {{'request_id': 'X2', 'old_patch_id': 12345678, 'new_patch': {{'patch_id': 55555560, 'source': 'ble'}}}}, lambda b: A.replace(pno, b))[0]
# 일시 제거 → 다시 교체(부착) → 종료
c4, r4 = call('return', {{'request_id': 'T1', 'patch_id': w.patch_registry[stock2]['patch_id'], 'reason': 'procedure', 'end_monitoring': False, 'condition': 'normal'}}, lambda b: A.ret(pno, b))
out['remove'] = [c4, r4['monitoring_ended'], rec['worn'], pno in [r.get('patient_no') for r in w.admitted.values()], int(w.st.patch.arr['active'][rec['row']])]
c5, r5 = call('replace', {{'request_id': 'X3', 'new_patch': {{'patch_id': 55555561, 'source': 'manual'}}, 'reason': 'other'}}, lambda b: A.replace(pno, b))
out['reattach'] = [c5, r5['old'], r5['new']['serial'], rec['worn'], int(w.st.patch.arr['active'][rec['row']])]
for i in range(3): w.step(1.0)
c6, r6 = call('return', {{'request_id': 'T2', 'reason': 'discharge', 'end_monitoring': True}}, lambda b: A.ret(pno, b))
out['end'] = [c6, r6['monitoring_ended'], pno in [r.get('patient_no') for r in w.admitted.values()], w.patch_registry['55555561']['status']]
out['nf'] = call('return', {{'request_id': 'T3', 'end_monitoring': True}}, lambda b: A.ret(999999, b))[0]
for i in range(5): w.step(1.0)
out['r2_still'] = any(r.get('patient_no') == r2['patient_id'] for r in w.admitted.values())
out['inv'] = w.invariants(fresh=True)['violations']
print(json.dumps(out, ensure_ascii=False, default=str))
w.db.close(); w.st.close(); w.st.unlink()
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["reg"] == [201, True, True, 201], o
    assert o["reg_state"] == [True, 1, "active", 5], o
    assert o["errs"] == {"in_use": 409, "already": 409, "bed": 409, "noward": 422, "missing": 422, "notstock": 409}, o
    assert o["new"] == [201, "65178647", 65178647, "에이티", "1960-05-06", True], o
    assert o["replace"][:4] == [200, True, True, "retired"] and "접착" in o["replace"][4] and o["replace"][5].startswith("교체:") and o["replace"][6], o
    assert o["replace_bad"] == 409, o
    assert o["remove"] == [200, False, False, True, 0], o
    assert o["reattach"][0] == 200 and o["reattach"][1] is None and o["reattach"][2] == "55555561" and o["reattach"][3] is True and o["reattach"][4] == 1, o
    assert o["end"] == [200, True, False, "retired"] and o["nf"] == 404, o
    assert o["r2_still"] and o["inv"] == [], o
