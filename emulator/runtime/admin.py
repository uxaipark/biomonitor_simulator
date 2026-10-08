"""라우터 환자 관리 쓰기 연동 (2026-10-08): 환자 등록 · 패치 교체 · 패치 반납.

계약: biomonitor_router docs/contract/patient-admin.md 4·5·6절.
- 모든 요청에 request_id: 처리에 성공한 요청은 결과를 DB(admin_requests)에 남기고, 같은 id 로 다시 오면 그 결과를 그대로 돌려준다.
- 거절은 AdminError(status, 한국어 사유) → HTTP 409/410/422 + {"detail": 사유}.  404 는 쓰지 않는다: 라우터는 404/405/501 을
  '아직 지원 안 함'으로 보고 30초마다 영원히 다시 보낸다 (2026-10-08).  모니터링 중이 아닌 환자번호 = 410, 없는 사람·병동·침대 = 422.
- 처리 결과는 /emr/admissions · /emr/patches · /emr/beds · 소켓 스트림(새 패치는 NEW_PATCH, META patches[])에 바로 반영되고,
  events kind "adt" 에 request_id·action 과 함께 남는다.

patient_id 는 에뮬레이터의 환자번호(입원마다 새 번호, 레코드의 patient_id)다.  패치는 병원 재고(stock)에서 꺼내거나,
재고에 없는 8자리 번호(BLE 스캔·수기 입력)는 그 번호 그대로 새 패치로 등록한다.
"""
from __future__ import annotations

import datetime as dt
import json

REPLACE_REASONS = {"battery": "배터리", "adhesion": "접착 불량", "skin": "피부 문제", "signal": "신호 불량", "wear_expire": "착용 기간 만료",
                   "malfunction": "기기 고장", "relocation": "부착 위치 변경", "other": "기타"}
RETURN_REASONS = {"monitoring_end": "모니터링 종료", "discharge": "퇴원", "transfer_out": "타병원 전원", "patient_refused": "환자 거부",
                  "expired": "기간 만료", "procedure": "시술·검사", "skin": "피부 문제", "other": "기타"}
CONDITIONS = {"normal": "정상", "damaged": "파손", "lost": "분실", "disposed": "폐기"}
EXT_MIN, EXT_MAX = 10_000_000, 99_999_999
TRUTH_RANGE = range(99_000_001, 99_000_065)


class AdminError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _iso(t: float | None) -> str | None:
    return dt.datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else None


class PatientAdmin:
    def __init__(self, world):
        self.w = world
        self._ensure_table()

    # ---------------------------------------------------------------- 멱등 기록
    def _ensure_table(self) -> None:
        db = self.w.db
        with db.lock:
            db.conn.execute("CREATE TABLE IF NOT EXISTS admin_requests (request_id TEXT PRIMARY KEY, action TEXT, status INTEGER, t REAL, data TEXT)")

    def _done(self, rid: str) -> tuple[int, dict] | None:
        if not rid:
            return None
        db = self.w.db
        with db.lock:
            r = db.conn.execute("SELECT status, data FROM admin_requests WHERE request_id = ?", (rid,)).fetchone()
        return (int(r["status"]), json.loads(r["data"])) if r else None

    def _remember(self, rid: str, action: str, status: int, body: dict) -> None:
        if not rid:
            return
        db = self.w.db
        with db.lock:
            db.conn.execute("INSERT OR REPLACE INTO admin_requests VALUES (?,?,?,?,?)", (rid, action, status, self.w.sim_time, json.dumps(body, ensure_ascii=False)))

    def run(self, action: str, body: dict, fn) -> tuple[int, dict]:
        rid = str((body or {}).get("request_id") or "").strip()
        prev = self._done(rid)
        if prev:
            return prev
        status, out = fn(body or {})
        out = {**out, "request_id": rid or None}
        self._remember(rid, action, status, out)
        return status, out

    # ---------------------------------------------------------------- 공통
    def _rec_by_patient_no(self, patient_no) -> tuple[int, dict]:
        try:
            n = int(patient_no)
        except (TypeError, ValueError):
            raise AdminError(422, f"환자번호 {patient_no} 이(가) 숫자가 아닙니다")
        for pid, rec in list(self.w.admitted.items()):
            if rec.get("patient_no") == n:
                return pid, rec
        raise AdminError(410, f"환자번호 {n} 은(는) 지금 모니터링 중인 환자가 아닙니다 (퇴원·종료됐거나 에뮬레이터 재시작으로 환자번호가 바뀜)")

    def _resolve_patch(self, spec: dict | None) -> dict:
        """{patch_id?, serial?, source} → {"serial": 재고 시리얼} 또는 {"ext_id": 새 번호}.  사용 중·반납된 패치는 거절."""
        spec = spec or {}
        w = self.w
        pid_raw, serial = spec.get("patch_id"), (str(spec.get("serial")).strip() if spec.get("serial") not in (None, "") else None)
        src = str(spec.get("source") or "stock")
        if pid_raw in (None, "") and not serial:
            raise AdminError(422, "패치 번호(patch_id 또는 serial)가 없습니다")
        try:
            num = int(pid_raw) if pid_raw not in (None, "") else (int(serial) if serial and serial.isdigit() else None)
        except (TypeError, ValueError):
            raise AdminError(422, f"패치 번호 {pid_raw} 이(가) 숫자가 아닙니다")
        key = serial or (f"{num:08d}" if num is not None else None)
        e = w.patch_registry.get(key) if key else None
        if e is None and num is not None:
            e = next((x for x in w.patch_registry.values() if x.get("patch_id") == num), None)
        if e is not None:
            st = e.get("status")
            if st == "stock":
                return {"serial": e["serial"], "patch_id": e["patch_id"], "from": "stock"}
            if st == "active":
                who = e.get("patient_name") or e.get("patient_id")
                raise AdminError(409, f"패치 {e['serial']} 은(는) 이미 사용 중입니다 (환자 {who})")
            raise AdminError(409, f"패치 {e['serial']} 은(는) 이미 사용 완료(반납)된 패치입니다 — 다시 쓸 수 없습니다")
        if src == "stock":
            raise AdminError(409, f"패치 {key} 은(는) 병원 재고에 없습니다")
        if num is None or not (EXT_MIN <= num <= EXT_MAX) or num in TRUTH_RANGE:
            raise AdminError(422, f"패치 번호 {pid_raw or serial} 은(는) 8자리 숫자(10000000~99999999, 정답 패치 번호 제외)여야 합니다")
        with w.db.lock:
            used = w.db.conn.execute("SELECT status FROM patches WHERE patch_id = ? OR serial = ?", (num, f"{num:08d}")).fetchone()
        if used:
            raise AdminError(409, f"패치 {num:08d} 은(는) 이미 기록이 있는 패치입니다 ({used['status']}) — 다시 쓸 수 없습니다")
        return {"ext_id": num, "patch_id": num, "from": src}

    def _event(self, msg: str, pid: int, rid: str, action: str) -> None:
        self.w.log.add("adt", msg + (f" [{rid}]" if rid else ""), patient_id=pid, request_id=rid or None, action=action)

    # ---------------------------------------------------------------- 1) 환자 등록
    def register(self, b: dict) -> tuple[int, dict]:
        w, h = self.w, self.w.hospital
        rid = str(b.get("request_id") or "")
        pl = b.get("placement") or {}
        mode = str(pl.get("mode") or "inpatient")
        if mode not in ("inpatient", "mcot"):
            raise AdminError(422, f"placement.mode 는 inpatient 또는 mcot 입니다 ({mode})")
        outpatient = mode == "mcot"
        # --- 사람
        prof = None
        if b.get("profile_id") not in (None, ""):
            try:
                prof = w.by_id.get(int(b["profile_id"]))
            except (TypeError, ValueError):
                prof = None
            if prof is None:
                raise AdminError(422, f"EMR 에 profile_id {b['profile_id']} 인 사람이 없습니다")
        elif b.get("mrn") and not b.get("patient"):
            prof = next((p for p in w.profiles if p.get("mrn") == b["mrn"]), None)
            if prof is None:
                raise AdminError(422, f"EMR 에 등록번호 {b['mrn']} 인 사람이 없습니다")
        pt = b.get("patient") or None
        if prof is None:
            if not pt:
                raise AdminError(422, "profile_id 또는 patient{name, sex, birth_date} 가 필요합니다")
            miss = [k for k in ("name", "sex", "birth_date") if not pt.get(k)]
            if miss:
                raise AdminError(422, "신규 환자 필수값 누락: " + ", ".join(miss))
            try:
                bd = dt.date.fromisoformat(str(pt["birth_date"]))
            except ValueError:
                raise AdminError(422, "birth_date 는 YYYY-MM-DD 형식이어야 합니다")
            sex = str(pt["sex"]).upper()[:1]
            if sex not in ("M", "F"):
                raise AdminError(422, "sex 는 M 또는 F 입니다")
            if pt.get("mrn") and any(p.get("mrn") == pt["mrn"] for p in w.profiles):
                raise AdminError(409, f"등록번호 {pt['mrn']} 은(는) 이미 다른 사람에게 있습니다")
            prof = w._pick_pool_patient(overseas=False)              # 대기 풀의 임상 배경(질환·리듬)을 빌려 신원만 바꾼다
            if prof is None:
                raise AdminError(409, "새 환자를 받을 EMR 대기 풀이 비어 있습니다")
            today = dt.datetime.fromtimestamp(w.sim_time).date()
            prof.update(name=str(pt["name"]), sex=sex, birth_date=bd.isoformat(), age=max(0, today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))),
                        phone=pt.get("phone") or prof.get("phone"), manual_identity=True, note=pt.get("note"))
            if pt.get("mrn"):
                prof["mrn"] = str(pt["mrn"])
            w.pool_cursor = max(0, w.pool_cursor - 1)
        if prof["id"] in w.admitted or prof.get("status") in ("admitted", "outpatient"):
            raise AdminError(409, f"{prof['name']} ({prof['mrn']}) 은(는) 이미 모니터링 중입니다")
        if prof.get("overseas") and not outpatient:
            raise AdminError(422, f"{prof['name']} 은(는) 해외 체류자라 입원 등록할 수 없습니다 (MCOT 로 등록)")
        # --- 자리
        place: dict = {}
        if outpatient:
            used = {r["mobile_gw"] for r in list(w.admitted.values()) if r["outpatient"]}
            if not any(g["type"] == "mobile" and g["idx"] not in used for g in h.gateways):
                raise AdminError(409, "MCOT 휴대폰 게이트웨이가 모두 사용 중입니다")
        else:
            wid = str(pl.get("ward") or "")
            if not wid:
                raise AdminError(422, "입원 등록에는 placement.ward(병동)가 필요합니다")
            wd = next((x for x in h.wards if x["id"] == wid), None)
            if wd is None:
                raise AdminError(422, f"병동 {wid} 이(가) 없습니다")
            place["ward_idx"] = wd["idx"]
            if pl.get("bed"):
                bi = next((i for i in wd["bed_idxs"] if h.beds[i]["id"] == pl["bed"]), None)
                if bi is None:
                    raise AdminError(422, f"병동 {wid} 에 침대 {pl['bed']} 이(가) 없습니다")
                if h.beds[bi]["patient_id"]:
                    raise AdminError(409, f"침대 {pl['bed']} 은(는) 이미 다른 환자가 쓰고 있습니다")
                place["bed_idx"] = bi
            elif h.free_bed_in_ward(wd["idx"]) < 0:
                raise AdminError(409, f"병동 {wd['name']} 에 빈 침대가 없습니다")
            place["doctor"], place["nurse"] = pl.get("doctor") or None, pl.get("nurse") or None
        if pl.get("monitoring_days") not in (None, ""):
            try:
                days = int(pl["monitoring_days"])
            except (TypeError, ValueError):
                raise AdminError(422, "monitoring_days 가 숫자가 아닙니다")
            if not 1 <= days <= 90:
                raise AdminError(422, "monitoring_days 는 1~90 일입니다")
            place["rx_days"] = days
        if not w.free_rows:
            raise AdminError(409, "에뮬레이터 송출 슬롯이 모두 찼습니다 (병상·MCOT 용량)")
        pt_spec = self._resolve_patch(b.get("patch"))
        place.update({k: pt_spec[k] for k in ("serial", "ext_id") if k in pt_spec})
        rec = w.admit(prof, outpatient=outpatient, place=place)
        if rec is None:
            raise AdminError(409, "입원 처리에 실패했습니다 (자리 없음)")
        rec["registered"] = {"by": b.get("by"), "source": b.get("source") or "api", "request_id": rid or None}
        patch = w.patches.get(rec["row"])
        gw = rec["gw"]
        msg = (f"[라우터] 환자 등록: {prof['name']} ({prof['mrn']}) → "
               + (f"{prof['admission']['ward_name']} {prof['admission']['bed']}" if not outpatient else "원외 MCOT")
               + f" · 패치 {patch.serial if patch else '-'}" + (" (재고 밖 패치)" if "ext_id" in pt_spec else ""))
        self._event(msg, prof["id"], rid, "register")
        return 201, {"patient_id": rec.get("patient_no"), "profile_id": prof["id"], "mrn": prof["mrn"], "name": prof["name"],
                     "patch_id": patch.patch_id if patch else None, "serial": patch.serial if patch else None,
                     "ward": prof["admission"]["ward"], "ward_name": prof["admission"]["ward_name"], "bed": prof["admission"]["bed"],
                     "gw_id": h.gateways[gw]["gw_no"] if gw >= 0 else None, "gateway": h.gateways[gw]["id"] if gw >= 0 else None,
                     "admit_time": prof["admission"]["time"], "mode": mode, "message": msg.replace("[라우터] ", "")}

    # ---------------------------------------------------------------- 2) 패치 교체
    def replace(self, patient_no, b: dict) -> tuple[int, dict]:
        w = self.w
        rid = str(b.get("request_id") or "")
        pid, rec = self._rec_by_patient_no(patient_no)
        prof = w.by_id[pid]
        old = w.patches.get(rec["row"])
        if old is not None and b.get("old_patch_id") not in (None, "") and int(b["old_patch_id"]) != old.patch_id:
            raise AdminError(409, f"지금 붙어 있는 패치는 {old.serial} 입니다 (요청의 old_patch_id {b['old_patch_id']} 와 다름)")
        reason = REPLACE_REASONS.get(str(b.get("reason") or "other"), "기타")
        if b.get("reason_text"):
            reason += f" ({b['reason_text']})"
        spec = self._resolve_patch(b.get("new_patch"))
        if old is not None and isinstance(b.get("old_battery"), (int, float)):
            old.battery = float(b["old_battery"])                    # 간호사가 본 옛 패치 배터리를 반납 기록에
        old_view = {"patch_id": old.patch_id, "serial": old.serial, "battery_at_retire": round(old.battery, 1)} if old else None
        if old is None:                                              # 일시 제거됐던 환자: 새 패치를 붙인다
            r = w.attach_patch(pid, f"교체: {reason}", serial=spec.get("serial"), ext_id=spec.get("ext_id"))
            new = w.patches.get(rec["row"])
        else:
            w._replace_patch(rec, reason, serial=spec.get("serial"), ext_id=spec.get("ext_id"))
            new = w.patches.get(rec["row"])
            old_view["retired_at"] = _iso(w.sim_time)
        msg = f"[라우터] 패치 교체: {prof['name']} {old_view['serial'] if old_view else '(제거 상태)'} → {new.serial} ({reason})"
        self._event(msg, pid, rid, "replace")
        return 200, {"patient_id": rec.get("patient_no"), "old": old_view, "new": {"patch_id": new.patch_id, "serial": new.serial, "issued_at": _iso(w.sim_time)},
                     "message": msg.replace("[라우터] ", "")}

    # ---------------------------------------------------------------- 3) 패치 반납
    def ret(self, patient_no, b: dict) -> tuple[int, dict]:
        w = self.w
        rid = str(b.get("request_id") or "")
        pid, rec = self._rec_by_patient_no(patient_no)
        prof = w.by_id[pid]
        patch = w.patches.get(rec["row"])
        if patch is not None and b.get("patch_id") not in (None, "") and int(b["patch_id"]) != patch.patch_id:
            raise AdminError(409, f"지금 붙어 있는 패치는 {patch.serial} 입니다 (요청의 patch_id {b['patch_id']} 와 다름)")
        end = bool(b.get("end_monitoring"))
        reason = RETURN_REASONS.get(str(b.get("reason") or "other"), "기타")
        if b.get("reason_text"):
            reason += f" ({b['reason_text']})"
        cond = CONDITIONS.get(str(b.get("condition") or "normal"), "정상")
        why = f"{reason} · 상태 {cond}"
        if patch is not None and isinstance(b.get("old_battery"), (int, float)):
            patch.battery = float(b["old_battery"])
        view = {"patch_id": patch.patch_id if patch else None, "serial": patch.serial if patch else None}
        if end:
            w.discharge(pid, "퇴원" if b.get("reason") == "discharge" else why)
            msg = f"[라우터] 패치 반납·모니터링 종료: {prof['name']} 패치 {view['serial'] or '-'} ({why})"
        else:
            if rec["outpatient"]:
                raise AdminError(409, "원외 MCOT 환자는 일시 제거할 수 없습니다 — end_monitoring=true 로 종료하세요")
            if patch is None:
                raise AdminError(409, f"{prof['name']} 은(는) 지금 붙어 있는 패치가 없습니다")
            w.detach_patch(pid, f"일시 제거: {why}")
            rec["removed_at"] = w.sim_time
            msg = f"[라우터] 패치 일시 제거: {prof['name']} 패치 {view['serial']} ({why}) — 입원 유지"
        self._event(msg, pid, rid, "return")
        return 200, {**view, "patient_id": rec.get("patient_no"), "retired_at": _iso(w.sim_time), "monitoring_ended": end, "message": msg.replace("[라우터] ", "")}
