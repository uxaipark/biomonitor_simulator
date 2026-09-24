"""FHIR/HL7 v2 가 아닌 연동 형식 4종.

athena   : athenaOne 계열 REST — /v1/{practiceid}/..., MM/DD/YYYY 날짜, 문자열 숫자, 단건 조회도 배열, form-encoded 쓰기, 변경분 구독(changed)
kr-json  : 국내 대학병원 EMR 계열 — OCS 컬럼형 대문자 키(PT_NO, ADM_DTM...), 업무 오류도 HTTP 200 + RESULT_CD, 세로형(VS_CD/VS_VAL) 바이탈
kr-xml   : 중소병원 OCS 레거시 — 단일 URL 에 IF_ID 전문, EUC-KR XML, 가로형(BT/PR/RR/BP_H/BP_L/SPO2) 바이탈
cda      : 진료정보교류 표준 — HL7 CDA R2 문서(진료의뢰서·퇴원요약·수신 문서), 문서 목록/조회/등록
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xesc

from . import ids as idmod
from .sim import h64
from .clinical import CONDITIONS, ALLERGY_BY_KEY, VITALS, BY_ATHENA, BY_KR, BY_LOINC, c_to_f, f_to_c, in_range, normalize_unit


class VendorError(Exception):
    def __init__(self, status: int, body, ctype: str = "application/json"):
        super().__init__(str(body))
        self.status, self.body, self.ctype = status, body, ctype


def _mdY(t: float, sim) -> str:
    return sim.local(t).strftime("%m/%d/%Y")


def _mdYHMS(t: float, sim) -> str:
    return sim.local(t).strftime("%m/%d/%Y %H:%M:%S")


def _ymdhms(t: float, sim) -> str:
    return sim.local(t).strftime("%Y%m%d%H%M%S")


def _parse_local(sim, s: str, fmts: tuple[str, ...]) -> float | None:
    for f in fmts:
        try:
            return dt.datetime.strptime(s, f).replace(tzinfo=sim.tz).timestamp()
        except (ValueError, TypeError):
            continue
    return None


# ====================================================================== athena
def athena_patient(sim, p: dict) -> dict:
    a = p["address"]
    enc = sim.enc_of_person(p["idx"])
    return {"patientid": p["ids"]["patientid"], "enterpriseid": p["ids"]["enterpriseid"], "firstname": p["given"][0], "middlename": p["given"][1] if len(p["given"]) > 1 else "",
            "lastname": p["family"], "dob": dt.date.fromisoformat(p["birth"]).strftime("%m/%d/%Y"), "sex": p["sex"], "address1": a["line"][0],
            **({"address2": a["line"][1]} if len(a["line"]) > 1 else {}), "city": a["city"], "state": a["state"], "zip": a["postal"], "countrycode": "USA", "countrycode3166": "US",
            "mobilephone": re.sub(r"\D", "", p["phone"]), "contactpreference": "MOBILEPHONE", "status": "active", "primarydepartmentid": "1", "departmentid": "1",
            "race": [p["race"][0]], "racename": p["race"][1], "ethnicitycode": p["ethnicity"][0], "language6392code": "spa" if p.get("language") == "es" else "eng",
            "registrationdate": _mdY(p["updated"], sim), "lastupdated": _mdY(p["updated"], sim), "patientphoto": "false", "privacyinformationverified": "true",
            "consenttotext": "true", "hasmobileyn": "Y", **({"lastappointment": _mdY(enc[0]["admit"], sim)} if enc else {})}


def athena_encounter(sim, e: dict) -> dict:
    c = CONDITIONS[e["cond"]]
    doc = sim.staff[e["attending"]]
    return {"encounterid": e["visit"], "patientid": sim.people[e["person"]]["ids"]["patientid"], "departmentid": "1", "encounterdate": _mdY(e["admit"], sim),
            "encountertype": "VISIT", "encountervisitname": "Remote Cardiac Monitoring", "status": {"in-progress": "OPEN", "finished": "CLOSED", "cancelled": "DELETED"}[e["status"]],
            "stage": "INTAKE" if e["status"] == "in-progress" else "CHECKOUT", "providerid": str(1000 + doc["idx"]), "providerfirstname": doc["given"][0], "providerlastname": doc["family"],
            "lastupdated": _mdYHMS(e["updated"], sim), "appointmentid": str(1200000 + e["no"]),
            "diagnoses": [{"diagnosisid": str(700000 + e["no"]), "icdcodes": [{"code": c["icd10cm"], "codeset": "ICD10", "description": c["names"]["en"]}],
                           "snomedcode": c["snomed"], "description": c["names"]["en"]}]}


def athena_vitals(sim, e: dict) -> list[dict]:
    """athena 형식: key 별 묶음, readings = [[element...], ...]."""
    groups = {"BLOODPRESSURE": [], "HEARTRATE": [], "RESPIRATIONRATE": [], "TEMPERATURE": [], "O2SATURATION": []}
    rows = []
    for rec in sim.vitals_for(e):
        rows.append((rec["t"], "ENCOUNTER", {"sbp": rec["values"]["sbp"], "dbp": rec["values"]["dbp"], "hr": rec["values"]["hr"], "rr": rec["values"]["rr"],
                                              "temp": rec["values"]["temp"], "spo2": rec["values"]["spo2"]}, rec["id_base"]))
    for x in sim.inbound_for(enc_no=e["no"]):
        rows.append((x["t"], "DEVICE", {x["kind"]: x["value"]}, x["id"]))
    for t, src, vals, rid in sorted(rows, key=lambda r: r[0]):
        def el(kind, key):
            v = vals[kind]
            val = c_to_f(v) if kind == "temp" else v
            return {"clinicalelementid": VITALS[kind]["athena"], "value": str(int(val) if kind != "temp" else val), "readingid": "0", "vitalid": str(h64(rid, kind) % 10**8),
                    "source": src, "sourceid": e["visit"], "readingtaken": _mdYHMS(t, sim), "createddate": _mdY(t, sim), "codeset": "LOINC", "code": VITALS[kind]["loinc"],
                    "unit": {"temp": "F", "spo2": "%", "hr": "bpm", "rr": "breaths/min", "sbp": "mmHg", "dbp": "mmHg"}[kind]}
        if "sbp" in vals:
            groups["BLOODPRESSURE"].append([el("sbp", "SYSTOLIC"), el("dbp", "DIASTOLIC")])
        for kind, key in (("hr", "HEARTRATE"), ("rr", "RESPIRATIONRATE"), ("temp", "TEMPERATURE"), ("spo2", "O2SATURATION")):
            if kind in vals:
                groups[key].append([el(kind, key)])
    abbr = {"BLOODPRESSURE": "BP", "HEARTRATE": "Pulse", "RESPIRATIONRATE": "Resp", "TEMPERATURE": "Temp", "O2SATURATION": "SpO2"}
    return [{"ordering": i, "abbreviation": abbr[k], "key": k, "readings": v} for i, (k, v) in enumerate(groups.items()) if v]


def athena_handle(sim, method: str, parts: list[str], q: dict, form: dict, client: str) -> tuple[int, object]:
    """parts: 'v1', practiceid, ... (토큰 엔드포인트는 api 에서 처리)."""
    if len(parts) < 3 or parts[0] != "v1":
        raise VendorError(404, {"error": "Invalid endpoint."})
    if parts[1] != sim.site["practiceid"]:
        raise VendorError(403, {"error": "You do not have access to this practice.", "detailedmessage": f"practiceid {parts[1]} is not enabled for this client"})
    rest = parts[2:]
    limit = min(int(q.get("limit", 10) or 10), 5000)
    offset = int(q.get("offset", 0) or 0)

    def paged(key: str, rows: list, path: str) -> dict:
        page = rows[offset: offset + limit]
        out = {key: page, "totalcount": len(rows)}
        if offset + limit < len(rows):
            out["next"] = f"/v1/{sim.site['practiceid']}/{path}?offset={offset + limit}&limit={limit}"
        if offset > 0:
            out["previous"] = f"/v1/{sim.site['practiceid']}/{path}?offset={max(0, offset - limit)}&limit={limit}"
        return out

    if rest == ["departments"] and method == "GET":
        return 200, {"departments": [{"departmentid": "1", "name": "BHVC REMOTE MONITORING", "state": "CA", "city": "San Diego", "zip": "92103",
                                      "timezonename": sim.site["tz"], "timezone": -8, "clinicals": "ON", "patientdepartmentname": "Bayside Heart & Vascular",
                                      "providergroupid": "1", "servicedepartment": "true", "placeofservicefacility": "false", "ecommercecreditcardtypes": ["AX", "DI", "MC", "VI"]}],
                     "totalcount": 1}
    if rest == ["providers"] and method == "GET":
        return 200, paged("providers", [{"providerid": str(1000 + s["idx"]), "firstname": s["given"][0], "lastname": s["family"], "displayname": f"{s['family']}, {s['given'][0]}",
                                         "npi": s["ids"]["npi"], "specialty": sim.dept(s["dept"])[1], "providertype": "MD", "entitytype": "Person", "billable": "true"}
                                        for s in sim.staff], "providers")
    if rest == ["patients"] and method == "GET":
        crit = {k: q.get(k) for k in ("lastname", "firstname", "dob", "departmentid", "mobilephone", "enterpriseid") if q.get(k)}
        if not crit:
            raise VendorError(400, {"error": "Additional fields are required.", "missingfields": ["lastname or dob or departmentid"],
                                    "detailedmessage": "At least one of lastname, firstname, dob, departmentid, enterpriseid is required."})
        rows = []
        for p in sim.people:
            if crit.get("lastname") and not p["family"].lower().startswith(crit["lastname"].lower()):
                continue
            if crit.get("firstname") and not p["given"][0].lower().startswith(crit["firstname"].lower()):
                continue
            if crit.get("dob") and dt.date.fromisoformat(p["birth"]).strftime("%m/%d/%Y") != crit["dob"]:
                continue
            if crit.get("enterpriseid") and p["ids"]["enterpriseid"] != crit["enterpriseid"]:
                continue
            if crit.get("departmentid") and crit["departmentid"] != "1":
                continue
            if crit.get("departmentid") and not crit.keys() - {"departmentid"} and p["idx"] not in sim.active_by_person:
                continue                                # departmentid 만 주면 현재 모니터링 중인 환자
            rows.append(athena_patient(sim, p))
        return 200, paged("patients", rows, "patients")
    if len(rest) == 2 and rest[0] == "patients" and rest[1] not in ("changed",) and method == "GET":
        idx = sim.find_person(rest[1])
        if idx is None or sim.people[idx]["ids"]["patientid"] != rest[1]:
            raise VendorError(404, {"error": "The patient is not found.", "detailedmessage": f"patientid {rest[1]} does not exist"})
        return 200, [athena_patient(sim, sim.people[idx])]                  # athena 는 단건도 배열
    if rest[:2] == ["patients", "changed"]:
        if rest[2:] == ["subscription"]:
            if method == "POST":
                sim.athena_changed_cursor.setdefault(client, len(sim.events))
                return 200, {"success": "true"}
            if method == "GET":
                return 200, {"status": "ACTIVE" if client in sim.athena_changed_cursor else "INACTIVE", "subscriptions": [{"eventname": "UpdatePatient"}, {"eventname": "AddPatient"}]}
            if method == "DELETE":
                sim.athena_changed_cursor.pop(client, None)
                return 200, {"success": "true"}
        if method == "GET":
            if client not in sim.athena_changed_cursor:
                raise VendorError(400, {"error": "The practice is not subscribed to patient changed events.", "detailedmessage": "POST /patients/changed/subscription first"})
            start = sim.athena_changed_cursor[client]
            evs = sim.events_since(start, 1000)
            if str(q.get("leaveunprocessed", "false")).lower() != "true":
                sim.athena_changed_cursor[client] = start + len(evs)
            seen, rows = set(), []
            for ev in evs:
                if ev["person"] in seen:
                    continue
                seen.add(ev["person"])
                pr = athena_patient(sim, sim.people[ev["person"]])
                pr["changetype"] = {"A01": "ADD", "A08": "UPDATE", "A03": "UPDATE", "A11": "UPDATE", "A02": "UPDATE"}[ev["code"]]
                pr["changedatetime"] = _mdYHMS(ev["t"], sim)
                rows.append(pr)
            return 200, {"patients": rows, "totalcount": len(rows)}
    if len(rest) >= 3 and rest[0] == "chart" and rest[1] != "encounter":
        idx = sim.find_person(rest[1])
        if idx is None or sim.people[idx]["ids"]["patientid"] != rest[1]:
            raise VendorError(404, {"error": "The patient is not found."})
        if not q.get("departmentid"):
            raise VendorError(400, {"error": "Additional fields are required.", "missingfields": ["departmentid"]})
        if rest[2] == "encounters" and method == "GET":
            return 200, paged("encounters", [athena_encounter(sim, e) for e in sim.enc_of_person(idx)], f"chart/{rest[1]}/encounters")
        if rest[2] == "vitals" and method == "GET":
            groups = []
            for e in sim.enc_of_person(idx):
                groups += athena_vitals(sim, e)
            return 200, {"vitals": groups, "totalcount": sum(len(g["readings"]) for g in groups)}
        if rest[2] == "problems" and method == "GET":
            probs = [{"problemid": str(900000 + e["no"]), "code": CONDITIONS[e["cond"]]["snomed"], "codeset": "SNOMED", "name": CONDITIONS[e["cond"]]["names"]["en"],
                      "events": [{"eventtype": "START", "startdate": _mdY(e["admit"], sim), "status": "CHRONIC" if e["cond"] in ("afib", "hf") else "ACUTE"}]}
                     for e in sim.enc_of_person(idx)]
            return 200, {"problems": probs, "totalcount": len(probs)}
        if rest[2] == "allergies" and method == "GET":
            p = sim.people[idx]
            key, sct, rx, nm = ALLERGY_BY_KEY[p["allergy"]]
            return 200, {"nkda": "true" if key == "nka" else "false", "allergies": [] if key == "nka" else [{"allergenid": rx or sct, "allergenname": nm["en"], "reactions": [{"reactionname": "hives", "snomedcode": "247472004"}]}],
                         "lastmodifieddate": _mdY(p["updated"], sim)}
    if len(rest) == 4 and rest[:2] == ["chart", "encounter"] and rest[3] == "vitals":
        e = next((x for x in list(sim.encounters.values()) if x["visit"] == rest[2]), None)
        if e is None:
            raise VendorError(404, {"error": "The encounter is not found."})
        if method == "GET":
            return 200, {"vitals": athena_vitals(sim, e), "totalcount": len(sim.vitals_for(e))}
        if method == "POST":
            missing = [k for k in ("departmentid", "vitals") if not form.get(k)]
            if missing:
                raise VendorError(400, {"error": "Additional fields are required.", "missingfields": missing})
            if form["departmentid"] != "1":
                raise VendorError(400, {"error": "Invalid departmentid."})
            if e["status"] != "in-progress":
                raise VendorError(400, {"error": "The encounter is not open.", "detailedmessage": f"encounter {e['visit']} status is {athena_encounter(sim, e)['status']}"})
            try:
                groups = json.loads(form["vitals"])
            except (ValueError, TypeError):
                raise VendorError(400, {"error": "Invalid JSON in vitals field."})
            if not isinstance(groups, list) or not all(isinstance(g, list) for g in groups):
                raise VendorError(400, {"error": "vitals must be a JSON array of arrays of reading objects.", "detailedmessage": 'e.g. [[{"clinicalelementid":"VITALS.HEARTRATE","value":"72"}]]'})
            stored, bad = [], []
            for gi, g in enumerate(groups):
                for el in g:
                    ce = str(el.get("clinicalelementid", ""))
                    kind = BY_ATHENA.get(ce)
                    if kind is None:
                        bad.append(f"[{gi}] unknown clinicalelementid {ce}")
                        continue
                    try:
                        v = float(el.get("value"))
                    except (TypeError, ValueError):
                        bad.append(f"[{gi}] {ce} value must be numeric")
                        continue
                    std = f_to_c(v) if kind == "temp" and str(el.get("unit", "F")).upper() != "C" else v
                    if not in_range(kind, std):
                        bad.append(f"[{gi}] {ce} value {v} out of range")
                        continue
                    t = _parse_local(sim, el.get("readingtaken", ""), ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M")) or time.time()
                    stored.append((kind, std, t, v, el.get("unit")))
            if bad:
                raise VendorError(400, {"error": "One or more vitals are invalid.", "detailedmessage": "; ".join(bad[:5])})
            vids = []
            for kind, std, t, v, u in stored:
                rec = sim.store_inbound(e["person"], e["no"], t, kind, std, "athena/" + (form.get("source") or "DEVICE"), form.get("deviceid"), u or ("F" if kind == "temp" else None), v)
                vids.append(rec["id"])
            return 200, {"success": "true", "vitalids": vids}
    raise VendorError(404, {"error": "Invalid endpoint.", "detailedmessage": "/".join(parts)})


# ====================================================================== kr-json
def _kr_ok(data, total: int | None = None) -> dict:
    out = {"RESULT_CD": "0000", "RESULT_MSG": "정상 처리되었습니다.", "TRX_DTM": dt.datetime.now().strftime("%Y%m%d%H%M%S")}
    if total is not None:
        out["TOTAL_CNT"] = total
    out["DATA"] = data
    return out


def _kr_err(code: str, msg: str) -> dict:
    return {"RESULT_CD": code, "RESULT_MSG": msg, "TRX_DTM": dt.datetime.now().strftime("%Y%m%d%H%M%S"), "DATA": None}


def kr_inpatient_row(sim, e: dict) -> dict:
    p = sim.people[e["person"]]
    w, b = sim.bed_path(e["bed"])
    c = CONDITIONS[e["cond"]]
    dept = sim.dept(c["dept"])
    doc = sim.staff[e["attending"]]
    return {"HOSP_CD": sim.site["hosp_code"], "PT_NO": p["ids"]["mrn"], "PT_NM": p["text"], "SEX_TP_CD": p["sex"], "BRTH_DT": p["birth"].replace("-", ""), "AGE": str(p["age"]),
            "ADM_NO": e["visit"], "ADM_DTM": _ymdhms(e["admit"], sim), "WARD_CD": w["code"], "WARD_NM": w["name"], "ROOM_NO": b["room"], "BED_NO": b["bed"],
            "MEDDEPT_CD": dept[0], "MEDDEPT_NM": dept[1], "ATTND_DR_ID": doc["ids"]["local"], "ATTND_DR_NM": doc["text"],
            "MAIN_DIAG_CD": c["icd10"], "MAIN_DIAG_NM": c["names"]["ko"], "ALLG_YN": "N" if p["allergy"] == "nka" else "Y",
            "ADM_PATH_CD": {"emergency": "ER", "referral": "OPD", "elective": "OPD"}[e["source"]], "DSCH_DTM": _ymdhms(e["end"], sim) if e["end"] else "",
            "ADM_STAT_CD": {"in-progress": "A", "finished": "D", "cancelled": "C"}[e["status"]]}


def kr_json_handle(sim, method: str, parts: list[str], q: dict, body: bytes) -> dict:
    path = "/".join(parts)
    if path == "api/v1/adm/inpatients" and method == "GET":
        rows = [kr_inpatient_row(sim, e) for e in sim.census()]
        if q.get("WARD_CD"):
            rows = [r for r in rows if r["WARD_CD"] == q["WARD_CD"]]
        if q.get("MEDDEPT_CD"):
            rows = [r for r in rows if r["MEDDEPT_CD"] == q["MEDDEPT_CD"]]
        return _kr_ok(rows, len(rows))
    if path == "api/v1/adm/events" and method == "GET":
        try:
            frm = int(q.get("FROM_SEQ", "0") or 0)
        except ValueError:
            return _kr_err("E100", "FROM_SEQ 는 숫자여야 합니다.")
        rows = []
        for ev in sim.events_since(frm, 500):
            p = sim.people[ev["person"]]
            e = sim.encounters[ev["enc"]]
            w, b = sim.bed_path(ev["bed"])
            row = {"EVT_SEQ": ev["seq"], "EVT_TP_CD": {"A01": "ADM", "A02": "TRF", "A03": "DSC", "A08": "UPD", "A11": "CNL"}[ev["code"]],
                   "EVT_TP_NM": {"A01": "입원", "A02": "전동", "A03": "퇴원", "A08": "환자정보변경", "A11": "입원취소"}[ev["code"]],
                   "EVT_DTM": _ymdhms(ev["t"], sim), "PT_NO": p["ids"]["mrn"], "PT_NM": p["text"], "ADM_NO": e["visit"], "WARD_CD": w["code"], "ROOM_NO": b["room"], "BED_NO": b["bed"]}
            if ev.get("prior_bed") is not None:
                pw, pb = sim.bed_path(ev["prior_bed"])
                row.update(BF_WARD_CD=pw["code"], BF_ROOM_NO=pb["room"], BF_BED_NO=pb["bed"])
            rows.append(row)
        return _kr_ok(rows, len(rows))
    if len(parts) == 4 and parts[:3] == ["api", "v1", "pat"] and method == "GET":
        idx = sim.find_person(parts[3])
        if idx is None or sim.people[idx]["ids"]["mrn"] != parts[3]:
            return _kr_err("E200", f"등록번호 {parts[3]} 에 해당하는 환자가 없습니다.")
        p = sim.people[idx]
        a = p["address"]
        key, sct, rx, nm = ALLERGY_BY_KEY[p["allergy"]]
        return _kr_ok({"PT_NO": p["ids"]["mrn"], "PT_NM": p["text"], "SEX_TP_CD": p["sex"], "BRTH_DT": p["birth"].replace("-", ""), "AGE": str(p["age"]),
                       "RRN_MASK": idmod.rrn_masked(p["ids"]["rrn"]), "MOBILE_NO": p["phone"], "TEL_NO": "", "ZIP_CD": a["postal"],
                       "ADDR": f"{a['state']} {a['city']} {a.get('district') or ''}".strip(), "ADDR_DTL": a["line"][0],
                       "ALLG_LIST": [] if key == "nka" else [{"ALLG_CD": sct, "ALLG_NM": nm["ko"], "ALLG_TP_CD": "D" if key in ("penicillin", "aspirin", "contrast") else "F"}],
                       "CUR_ADM_NO": sim.encounters[sim.active_by_person[idx]]["visit"] if idx in sim.active_by_person else "",
                       "LAST_UPD_DTM": _ymdhms(p["updated"], sim)})
    if path == "api/v1/code/wards" and method == "GET":
        rows = [{"WARD_CD": w["code"], "WARD_NM": w["name"], "FLOOR": str(w["floor"]), "BED_CNT": sum(1 for b in sim.beds if b["ward"] == w["idx"]),
                 "MEDDEPT_CD": ",".join(sim.dept(d)[0] for d in w["depts"])} for w in sim.wards]
        return _kr_ok(rows, len(rows))
    if path == "api/v1/code/vs" and method == "GET":
        rows = [{"VS_CD": v["kr"][0], "VS_NM": v["kr"][1], "VS_UNIT": v["kr"][2], "LOINC_CD": v["loinc"], "MIN_VAL": str(v["range"][0]), "MAX_VAL": str(v["range"][1])} for v in VITALS.values()]
        return _kr_ok(rows, len(rows))
    if path == "api/v1/vs" and method == "GET":
        if not q.get("PT_NO"):
            return _kr_err("E100", "필수 항목(PT_NO)이 누락되었습니다.")
        idx = sim.find_person(q["PT_NO"])
        if idx is None:
            return _kr_err("E200", f"등록번호 {q['PT_NO']} 에 해당하는 환자가 없습니다.")
        frm = _parse_local(sim, q.get("FROM_DTM", ""), ("%Y%m%d%H%M%S", "%Y%m%d")) or 0
        to = _parse_local(sim, q.get("TO_DTM", ""), ("%Y%m%d%H%M%S", "%Y%m%d")) or time.time() + 1
        rows = []
        for e in sim.enc_of_person(idx):
            for rec in sim.vitals_for(e):
                if frm <= rec["t"] <= to:
                    nurse = sim.staff[rec["performer"]]
                    for k in ("temp", "hr", "rr", "sbp", "dbp", "spo2"):
                        rows.append({"PT_NO": q["PT_NO"], "ADM_NO": e["visit"], "MSR_DTM": _ymdhms(rec["t"], sim), "VS_CD": VITALS[k]["kr"][0], "VS_NM": VITALS[k]["kr"][1],
                                     "VS_VAL": str(rec["values"][k]), "VS_UNIT": VITALS[k]["kr"][2], "MSR_TP_CD": "N", "RGST_ID": nurse["ids"]["local"], "DEVICE_ID": ""})
            for x in sim.inbound_for(enc_no=e["no"]):
                if frm <= x["t"] <= to:
                    rows.append({"PT_NO": q["PT_NO"], "ADM_NO": e["visit"], "MSR_DTM": _ymdhms(x["t"], sim), "VS_CD": VITALS[x["kind"]]["kr"][0], "VS_NM": VITALS[x["kind"]]["kr"][1],
                                 "VS_VAL": str(x["value"]), "VS_UNIT": VITALS[x["kind"]]["kr"][2], "MSR_TP_CD": "D", "RGST_ID": "IF_BIOMON", "DEVICE_ID": x["device"] or ""})
        rows.sort(key=lambda r: r["MSR_DTM"])
        return _kr_ok(rows, len(rows))
    if path == "api/v1/vs" and method == "POST":
        try:
            req = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return _kr_err("E101", "요청 본문이 올바른 JSON(UTF-8)이 아닙니다.")
        miss = [k for k in ("PT_NO", "MSR_DTM", "VS_LIST") if not req.get(k)]
        if miss:
            return _kr_err("E100", f"필수 항목({', '.join(miss)})이 누락되었습니다.")
        idx = sim.find_person(req["PT_NO"])
        if idx is None or sim.people[idx]["ids"]["mrn"] != str(req["PT_NO"]):
            return _kr_err("E200", f"등록번호 {req['PT_NO']} 에 해당하는 환자가 없습니다.")
        enc_no = sim.active_by_person.get(idx)
        if req.get("ADM_NO"):
            e = next((x for x in list(sim.encounters.values()) if x["visit"] == str(req["ADM_NO"]) and x["person"] == idx), None)
            if e is None:
                return _kr_err("E201", f"입원번호 {req['ADM_NO']} 가 환자 {req['PT_NO']} 의 입원이 아닙니다.")
            enc_no = e["no"]
        if enc_no is None:
            return _kr_err("E201", "재원 중인 입원 정보가 없습니다. (퇴원 환자는 ADM_NO 필수)")
        t = _parse_local(sim, str(req["MSR_DTM"]), ("%Y%m%d%H%M%S",))
        if t is None:
            return _kr_err("E102", "MSR_DTM 형식 오류 (YYYYMMDDHH24MISS, 14자리)")
        good, bad = [], []
        for it in req["VS_LIST"]:
            kind = BY_KR.get(str(it.get("VS_CD", "")).upper())
            if kind is None:
                bad.append(f"VS_CD {it.get('VS_CD')} 미등록")
                continue
            try:
                v = float(it.get("VS_VAL"))
            except (TypeError, ValueError):
                bad.append(f"{it.get('VS_CD')} 값 숫자 아님")
                continue
            if not in_range(kind, v):
                bad.append(f"{it.get('VS_CD')} 값 {v} 범위 초과 {VITALS[kind]['range']}")
                continue
            good.append((kind, v))
        if bad:
            return _kr_err("E300", "; ".join(bad))
        seqs = [sim.store_inbound(idx, enc_no, t, k, v, "kr-json", req.get("DEVICE_ID"), VITALS[k]["kr"][2], v)["id"] for k, v in good]
        return _kr_ok({"RGST_CNT": len(seqs), "VS_SEQ": seqs})
    return _kr_err("E404", f"정의되지 않은 서비스입니다: {method} /{path}")


# ====================================================================== kr-xml (EUC-KR)
def _x(tag: str, v) -> str:
    return f"<{tag}>{xesc('' if v is None else str(v))}</{tag}>"


def kr_xml_wrap(sim, if_id: str, trx_id: str, rslt: str, msg: str, body_xml: str, cnt: int | None = None) -> bytes:
    now = dt.datetime.now(sim.tz).strftime("%Y%m%d%H%M%S")
    hdr = (f"<HEADER>{_x('IF_ID', if_id)}{_x('SND_SYS_CD', 'OCS')}{_x('RCV_SYS_CD', 'BIOMON')}{_x('TRX_ID', trx_id)}{_x('TRX_DTM', now)}"
           f"{_x('RSLT_CD', rslt)}{_x('RSLT_MSG', msg)}{_x('DATA_CNT', cnt if cnt is not None else '')}</HEADER>")
    xml = f'<?xml version="1.0" encoding="EUC-KR"?>\n<IF_MSG>{hdr}<BODY>{body_xml}</BODY></IF_MSG>'
    return xml.encode("cp949", errors="replace")


def kr_xml_handle(sim, body: bytes, client: str) -> tuple[int, bytes, str, str]:
    """→ (http status, 응답 바이트, RSLT_CD, 요약)."""
    try:
        text = body.decode("cp949")
    except UnicodeDecodeError:
        return 200, kr_xml_wrap(sim, "", "", "E", "전문 인코딩 오류: EUC-KR(CP949) 로 보내야 합니다.", ""), "E", "인코딩 오류"
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return 200, kr_xml_wrap(sim, "", "", "E", f"XML 파싱 오류: {e}", ""), "E", "XML 파싱 오류"
    hdr = root.find("HEADER")
    if root.tag != "IF_MSG" or hdr is None:
        return 200, kr_xml_wrap(sim, "", "", "E", "루트는 IF_MSG, HEADER 필수", ""), "E", "전문 구조 오류"
    g = lambda el, tag: (el.findtext(tag) or "").strip() if el is not None else ""
    if_id, trx, snd = g(hdr, "IF_ID"), g(hdr, "TRX_ID"), g(hdr, "SND_SYS_CD")
    if not snd:
        return 200, kr_xml_wrap(sim, if_id, trx, "E", "SND_SYS_CD 누락 (송신 시스템 코드 필수)", ""), "E", "SND_SYS_CD 누락"
    req = root.find("BODY/REQ")
    if if_id == "EMR_ADT_0001":                                   # 재원환자 조회
        wd = g(req, "WD_CD")
        rows = []
        for e in sim.census():
            w, b = sim.bed_path(e["bed"])
            if wd and w["code"] != wd:
                continue
            p = sim.people[e["person"]]
            c = CONDITIONS[e["cond"]]
            rows.append("<DATA>" + "".join(_x(k, v) for k, v in (("PTNT_NO", p["ids"]["mrn"]), ("PTNT_NM", p["text"]), ("SEX", p["sex"]), ("BIRTH_YMD", p["birth"].replace("-", "")),
                                                                  ("IO_DT", sim.local(e["admit"]).strftime("%Y%m%d")), ("IO_TM", sim.local(e["admit"]).strftime("%H%M")),
                                                                  ("INPT_NO", e["visit"]), ("WD_CD", w["code"]), ("RM_NO", b["room"]), ("BD_NO", b["bed"]),
                                                                  ("DEPT_CD", sim.dept(c["dept"])[0]), ("DR_NM", sim.staff[e["attending"]]["text"]),
                                                                  ("DX_CD", c["icd10"].replace(".", "")), ("DX_NM", c["names"]["ko"]))) + "</DATA>")
        return 200, kr_xml_wrap(sim, if_id, trx, "S", "정상", "<DATA_LIST>" + "".join(rows) + "</DATA_LIST>", len(rows)), "S", f"재원 {len(rows)}명"
    if if_id == "EMR_PAT_0001":
        no = g(req, "PTNT_NO")
        idx = sim.find_person(no)
        if not no or idx is None or sim.people[idx]["ids"]["mrn"] != no:
            return 200, kr_xml_wrap(sim, if_id, trx, "E", f"환자번호 없음: {no}", ""), "E", f"환자 없음 {no}"
        p = sim.people[idx]
        a = p["address"]
        row = "<DATA>" + "".join(_x(k, v) for k, v in (("PTNT_NO", no), ("PTNT_NM", p["text"]), ("SEX", p["sex"]), ("BIRTH_YMD", p["birth"].replace("-", "")),
                                                        ("JUMIN_NO", idmod.rrn_masked(p["ids"]["rrn"]).replace("-", "")), ("HP_NO", p["phone"].replace("-", "")),
                                                        ("ZIP_NO", a["postal"]), ("ADDR1", f"{a['state']} {a['city']}"), ("ADDR2", a["line"][0]),
                                                        ("ALRG_NM", ALLERGY_BY_KEY[p["allergy"]][3]["ko"] if p["allergy"] != "nka" else ""))) + "</DATA>"
        return 200, kr_xml_wrap(sim, if_id, trx, "S", "정상", "<DATA_LIST>" + row + "</DATA_LIST>", 1), "S", f"환자 {no}"
    if if_id == "EMR_ADT_0002":
        try:
            frm = int(g(req, "FROM_SEQ") or 0)
        except ValueError:
            frm = 0
        rows = []
        for ev in sim.events_since(frm, 300):
            p = sim.people[ev["person"]]
            w, b = sim.bed_path(ev["bed"])
            rows.append("<DATA>" + "".join(_x(k, v) for k, v in (("SEQ", ev["seq"]), ("EVT_GB", {"A01": "I", "A02": "T", "A03": "O", "A08": "U", "A11": "C"}[ev["code"]]),
                                                                  ("EVT_DT", sim.local(ev["t"]).strftime("%Y%m%d")), ("EVT_TM", sim.local(ev["t"]).strftime("%H%M%S")),
                                                                  ("PTNT_NO", p["ids"]["mrn"]), ("PTNT_NM", p["text"]), ("INPT_NO", sim.encounters[ev["enc"]]["visit"]),
                                                                  ("WD_CD", w["code"]), ("RM_NO", b["room"]), ("BD_NO", b["bed"]))) + "</DATA>")
        return 200, kr_xml_wrap(sim, if_id, trx, "S", "정상", "<DATA_LIST>" + "".join(rows) + "</DATA_LIST>", len(rows)), "S", f"ADT {len(rows)}건"
    if if_id == "EMR_VS_0001":
        no = g(req, "PTNT_NO")
        idx = sim.find_person(no)
        if not no or idx is None:
            return 200, kr_xml_wrap(sim, if_id, trx, "E", f"환자번호 없음: {no}", ""), "E", f"환자 없음 {no}"
        rows = []
        for e in sim.enc_of_person(idx):
            for rec in sim.vitals_for(e):
                v = rec["values"]
                rows.append((rec["t"], "N", {"BT": v["temp"], "PR": v["hr"], "RR": v["rr"], "BP_H": v["sbp"], "BP_L": v["dbp"], "SPO2": v["spo2"]}, e["visit"]))
            byt = {}
            for x in sim.inbound_for(enc_no=e["no"]):
                col = {"temp": "BT", "hr": "PR", "rr": "RR", "sbp": "BP_H", "dbp": "BP_L", "spo2": "SPO2"}[x["kind"]]
                byt.setdefault(x["t"], {})[col] = x["value"]
            rows += [(t, "M", cols, e["visit"]) for t, cols in byt.items()]
        rows.sort(key=lambda r: r[0])
        xml_rows = "".join("<DATA>" + _x("PTNT_NO", no) + _x("INPT_NO", visit) + _x("VS_DT", sim.local(t).strftime("%Y%m%d")) + _x("VS_TM", sim.local(t).strftime("%H%M")) +
                           _x("INPUT_GB", gb) + "".join(_x(c, cols.get(c, "")) for c in ("BT", "PR", "RR", "BP_H", "BP_L", "SPO2")) + "</DATA>" for t, gb, cols, visit in rows)
        return 200, kr_xml_wrap(sim, if_id, trx, "S", "정상", "<DATA_LIST>" + xml_rows + "</DATA_LIST>", len(rows)), "S", f"바이탈 {len(rows)}행"
    if if_id == "EMR_VS_0002":                                    # 바이탈 등록 (가로형 1행 = 1회 측정)
        datas = root.findall("BODY/DATA_LIST/DATA")
        if not datas:
            return 200, kr_xml_wrap(sim, if_id, trx, "E", "DATA_LIST/DATA 가 없습니다.", ""), "E", "DATA 없음"
        results, n_ok, errs = [], 0, 0
        for i, d in enumerate(datas, 1):
            no, ymd, hm = g(d, "PTNT_NO"), g(d, "VS_DT"), g(d, "VS_TM")
            idx = sim.find_person(no)
            err = None
            t = _parse_local(sim, ymd + hm, ("%Y%m%d%H%M", "%Y%m%d%H%M%S"))
            if idx is None or sim.people[idx]["ids"]["mrn"] != no:
                err = f"환자번호 없음: {no}"
            elif t is None:
                err = "VS_DT(YYYYMMDD)/VS_TM(HH24MI) 형식 오류"
            elif idx not in sim.active_by_person:
                err = "재원 환자가 아님"
            vals = []
            if not err:
                for col, kind in (("BT", "temp"), ("PR", "hr"), ("RR", "rr"), ("BP_H", "sbp"), ("BP_L", "dbp"), ("SPO2", "spo2")):
                    raw = g(d, col)
                    if raw == "":
                        continue
                    try:
                        v = float(raw)
                    except ValueError:
                        err = f"{col} 숫자 아님: {raw}"
                        break
                    if not in_range(kind, v):
                        err = f"{col} 범위 초과: {raw}"
                        break
                    vals.append((kind, v))
                if not err and not vals:
                    err = "측정값 컬럼(BT/PR/RR/BP_H/BP_L/SPO2)이 모두 비었습니다."
            if err:
                errs += 1
                results.append(f"<DATA>{_x('ROW_NO', i)}{_x('PROC_CD', 'E')}{_x('PROC_MSG', err)}</DATA>")
                continue
            enc_no = sim.active_by_person[idx]
            for kind, v in vals:
                sim.store_inbound(idx, enc_no, t, kind, v, "kr-xml", g(d, "EQUIP_ID") or None, None, v, trx)
            n_ok += 1
            results.append(f"<DATA>{_x('ROW_NO', i)}{_x('PROC_CD', 'S')}{_x('PROC_MSG', '등록')}</DATA>")
        rs = "S" if not errs else ("P" if n_ok else "E")                # P = 부분 성공
        return 200, kr_xml_wrap(sim, if_id, trx, rs, {"S": "정상", "P": "일부 오류", "E": "전건 오류"}[rs], "<DATA_LIST>" + "".join(results) + "</DATA_LIST>", len(datas)), rs, \
            f"바이탈 등록 {n_ok}/{len(datas)}행"
    return 200, kr_xml_wrap(sim, if_id, trx, "E", f"정의되지 않은 IF_ID: {if_id}", ""), "E", f"미정의 IF_ID {if_id}"


# ====================================================================== CDA (진료정보교류)
CDA_NS = "urn:hl7-org:v3"
KR_TEMPLATES = {"REFERRAL": ("1.2.410.100110.40.2.1.1", "57133-1", "Referral note", "진료의뢰서"),
                "RETURN": ("1.2.410.100110.40.2.1.2", "34133-9", "Summary of episode note", "진료회송서"),
                "DISCHARGE": ("1.2.410.100110.40.2.1.3", "18842-5", "Discharge summary", "퇴원요약지"),
                "VITALS": ("1.2.410.100110.40.2.1.9", "8716-3", "Vital signs", "활력징후 기록")}


def _hosp_oid(sim) -> str:
    return f"1.2.410.100110.10.{sim.site['hosp_code']}"


def cda_doc_list(sim) -> list[dict]:
    """생성 문서(입원 시 진료의뢰서, 퇴원 시 퇴원요약) + 수신 문서."""
    sim.advance()
    out = []
    for e in list(sim.encounters.values()):
        if e["status"] == "cancelled":
            continue
        if e["source"] == "referral":
            out.append({"doc_id": f"R{e['visit']}", "type": "REFERRAL", "enc": e["no"], "person": e["person"], "t": e["admit"] - 3600 * 3, "origin": "generated"})
        if e["status"] == "finished":
            out.append({"doc_id": f"D{e['visit']}", "type": "DISCHARGE", "enc": e["no"], "person": e["person"], "t": e["end"], "origin": "generated"})
    for d in sim.documents:
        out.append({"doc_id": d["doc_id"], "type": d["type"], "enc": d["enc"], "person": d["person"], "t": d["t"], "origin": "received"})
    return sorted(out, key=lambda d: d["t"])


def _cda_ts(sim, t: float) -> str:
    return sim.local(t).strftime("%Y%m%d%H%M%S%z")


def cda_document(sim, d: dict) -> str:
    if d.get("origin") == "received":
        doc = next(x for x in sim.documents if x["doc_id"] == d["doc_id"])
        return doc["xml"]
    e = sim.encounters[d["enc"]]
    p = sim.people[e["person"]]
    doc = sim.staff[e["attending"]]
    tmpl, loinc, ldisp, title = KR_TEMPLATES[d["type"]]
    oid = _hosp_oid(sim)
    a = p["address"]
    c = CONDITIONS[e["cond"]]
    vit = sim.vitals_for(e, until=d["t"])[-3:]
    key, sct, rx, nm = ALLERGY_BY_KEY[p["allergy"]]
    rows = "".join(f"<tr><td>{sim.local(r['t']).strftime('%Y-%m-%d %H:%M')}</td><td>{r['values']['sbp']}/{r['values']['dbp']}</td><td>{r['values']['hr']}</td>"
                   f"<td>{r['values']['rr']}</td><td>{r['values']['temp']}</td><td>{r['values']['spo2']}</td></tr>" for r in vit)
    entries = ""
    for r in vit:
        comps = "".join(f'<component><observation classCode="OBS" moodCode="EVN"><code code="{VITALS[k]["loinc"]}" codeSystem="2.16.840.1.113883.6.1" codeSystemName="LOINC" displayName="{VITALS[k]["display"]}"/>'
                        f'<statusCode code="completed"/><effectiveTime value="{_cda_ts(sim, r["t"])}"/><value xsi:type="PQ" value="{r["values"][k]}" unit="{VITALS[k]["ucum"]}"/></observation></component>'
                        for k in ("sbp", "dbp", "hr", "rr", "temp", "spo2"))
        entries += (f'<entry typeCode="DRIV"><organizer classCode="CLUSTER" moodCode="EVN"><templateId root="1.2.410.100110.40.2.3.9"/><statusCode code="completed"/>'
                    f'<effectiveTime value="{_cda_ts(sim, r["t"])}"/>{comps}</organizer></entry>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <realmCode code="KR"/>
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/>
  <templateId root="{tmpl}"/>
  <id root="{oid}.1" extension="{d['doc_id']}"/>
  <code code="{loinc}" codeSystem="2.16.840.1.113883.6.1" codeSystemName="LOINC" displayName="{ldisp}"/>
  <title>{title}</title>
  <effectiveTime value="{_cda_ts(sim, d['t'])}"/>
  <confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>
  <languageCode code="ko-KR"/>
  <setId root="{oid}.1" extension="{d['doc_id']}"/>
  <versionNumber value="1"/>
  <recordTarget>
    <patientRole>
      <id root="{oid}.100" extension="{p['ids']['mrn']}"/>
      <addr use="H"><postalCode>{a['postal']}</postalCode><state>{xesc(a['state'])}</state><city>{xesc(a['city'])}</city><streetAddressLine>{xesc(a['line'][0])}</streetAddressLine></addr>
      <telecom use="MC" value="tel:{p['phone']}"/>
      <patient>
        <name>{xesc(p['text'])}</name>
        <administrativeGenderCode code="{p['sex']}" codeSystem="2.16.840.1.113883.5.1"/>
        <birthTime value="{p['birth'].replace('-', '')}"/>
      </patient>
    </patientRole>
  </recordTarget>
  <author>
    <time value="{_cda_ts(sim, d['t'])}"/>
    <assignedAuthor>
      <id root="1.2.410.100110.20.1" extension="{doc['ids']['license']}"/>
      <assignedPerson><name>{xesc(doc['text'])}</name></assignedPerson>
      <representedOrganization><id root="1.2.410.100110.10" extension="{sim.site['hosp_code']}"/><name>{xesc(sim.site['name_local'])}</name></representedOrganization>
    </assignedAuthor>
  </author>
  <custodian><assignedCustodian><representedCustodianOrganization><id root="1.2.410.100110.10" extension="{sim.site['hosp_code']}"/><name>{xesc(sim.site['name_local'])}</name></representedCustodianOrganization></assignedCustodian></custodian>
  <componentOf><encompassingEncounter><id root="{oid}.200" extension="{e['visit']}"/><effectiveTime><low value="{_cda_ts(sim, e['admit'])}"/>{f'<high value="{_cda_ts(sim, e["end"])}"/>' if e['end'] else ''}</effectiveTime></encompassingEncounter></componentOf>
  <component>
    <structuredBody>
      <component><section><templateId root="1.2.410.100110.40.2.2.1"/><code code="29548-5" codeSystem="2.16.840.1.113883.6.1" displayName="Diagnosis"/><title>진단</title>
        <text><list><item>{c['icd10']} {xesc(c['names']['ko'])}</item></list></text>
        <entry><observation classCode="OBS" moodCode="EVN"><code code="29308-4" codeSystem="2.16.840.1.113883.6.1" displayName="Diagnosis"/><statusCode code="completed"/>
          <value xsi:type="CD" code="{c['icd10']}" codeSystem="2.16.840.1.113883.6.3.2" codeSystemName="KCD-8" displayName="{xesc(c['names']['ko'])}"/></observation></entry>
      </section></component>
      <component><section><templateId root="1.2.410.100110.40.2.2.2"/><code code="48765-2" codeSystem="2.16.840.1.113883.6.1" displayName="Allergies"/><title>알레르기 및 부작용</title>
        <text>{xesc(nm['ko'])}</text></section></component>
      <component><section><templateId root="1.2.410.100110.40.2.2.9"/><code code="8716-3" codeSystem="2.16.840.1.113883.6.1" displayName="Vital signs"/><title>활력징후</title>
        <text><table><thead><tr><th>측정일시</th><th>혈압(mmHg)</th><th>맥박(회/분)</th><th>호흡(회/분)</th><th>체온(℃)</th><th>SpO2(%)</th></tr></thead><tbody>{rows}</tbody></table></text>
        {entries}
      </section></component>
    </structuredBody>
  </component>
</ClinicalDocument>
"""


def cda_list_xml(sim, docs: list[dict], base: str) -> str:
    items = []
    for d in docs:
        p = sim.people[d["person"]]
        items.append(f"<Document>{_x('DocId', d['doc_id'])}{_x('DocType', d['type'])}{_x('DocTitle', KR_TEMPLATES[d['type']][3])}{_x('PtNo', p['ids']['mrn'])}"
                     f"{_x('PtNm', p['text'])}{_x('CreatedAt', _cda_ts(sim, d['t']))}{_x('Origin', d['origin'])}{_x('Url', base + '/cda/documents/' + d['doc_id'])}</Document>")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<DocumentList count="{len(docs)}">{"".join(items)}</DocumentList>'


def cda_receive(sim, body: bytes) -> tuple[int, str, str]:
    """수신 CDA 검증·저장 → (status, 응답 XML, 요약)."""
    def res(code, msg, doc_id="", status=400):
        return status, f'<?xml version="1.0" encoding="UTF-8"?>\n<Result>{_x("Code", code)}{_x("Message", msg)}{_x("DocId", doc_id)}</Result>', msg
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return res("E100", f"XML 파싱 오류: {e}")
    ns = {"h": CDA_NS}
    if root.tag != f"{{{CDA_NS}}}ClinicalDocument":
        return res("E101", "루트는 urn:hl7-org:v3 네임스페이스의 ClinicalDocument 여야 합니다.")
    tid = root.find("h:typeId", ns)
    if tid is None or tid.get("root") != "2.16.840.1.113883.1.3" or tid.get("extension") != "POCD_HD000040":
        return res("E102", "typeId root=2.16.840.1.113883.1.3 extension=POCD_HD000040 필수")
    tmpls = {t.get("root") for t in root.findall("h:templateId", ns)}
    dtype = next((k for k, v in KR_TEMPLATES.items() if v[0] in tmpls), None)
    if dtype is None:
        return res("E103", f"지원 templateId 아님: {sorted(tmpls)} (허용: {', '.join(v[0] for v in KR_TEMPLATES.values())})")
    did = root.find("h:id", ns)
    if did is None or not did.get("extension"):
        return res("E104", "문서 id(extension) 필수")
    pid = root.find("h:recordTarget/h:patientRole/h:id", ns)
    if pid is None or not pid.get("extension"):
        return res("E105", "recordTarget/patientRole/id 필수")
    if pid.get("root") != _hosp_oid(sim) + ".100":
        return res("E106", f"환자 id root 는 본원 등록번호 OID({_hosp_oid(sim)}.100)여야 합니다: {pid.get('root')}")
    idx = sim.find_person(pid.get("extension"))
    if idx is None or sim.people[idx]["ids"]["mrn"] != pid.get("extension"):
        return res("E200", f"등록번호 없음: {pid.get('extension')}", status=404)
    if any(x["doc_id"] == did.get("extension") for x in sim.documents):
        return res("E107", f"이미 등록된 문서 id: {did.get('extension')}", status=409)
    eff = root.find("h:effectiveTime", ns)
    t = None
    if eff is not None:
        v = eff.get("value", "")
        t = _parse_local(sim, v[:14], ("%Y%m%d%H%M%S",)) or _parse_local(sim, v[:12], ("%Y%m%d%H%M",))
    if t is None:
        return res("E108", "effectiveTime value(YYYYMMDDHHMMSS[+ZZZZ]) 필수")
    enc_no = sim.active_by_person.get(idx)
    stored = 0
    for obs in root.iter(f"{{{CDA_NS}}}observation"):
        code = obs.find("h:code", ns)
        val = obs.find("h:value", ns)
        if code is None or val is None or code.get("codeSystem") != "2.16.840.1.113883.6.1":
            continue
        kind = BY_LOINC.get(code.get("code"))
        if kind is None:
            continue
        try:
            v = float(val.get("value"))
        except (TypeError, ValueError):
            return res("E300", f"관찰값이 숫자가 아님: {code.get('code')} = {val.get('value')}")
        std, err = normalize_unit(kind, v, val.get("unit"))
        if err or not in_range(kind, std):
            return res("E301", f"{code.get('code')} 값/단위 오류: {v} {val.get('unit')} ({err or '범위 초과'})")
        et = obs.find("h:effectiveTime", ns)
        tt = _parse_local(sim, (et.get("value", "") if et is not None else "")[:14], ("%Y%m%d%H%M%S",)) or t
        sim.store_inbound(idx, enc_no, tt, kind, std, "cda", None, val.get("unit"), v, did.get("extension"))
        stored += 1
    sim.documents.append({"doc_id": did.get("extension"), "type": dtype, "enc": enc_no, "person": idx, "t": t, "xml": body.decode("utf-8", errors="replace")})
    st, xml, _ = res("0000", f"정상 등록 ({KR_TEMPLATES[dtype][3]}, 관찰값 {stored}건)", did.get("extension"), status=201)
    return st, xml, f"CDA {dtype} {did.get('extension')} 수신, 관찰값 {stored}건"


def cda_sample(sim) -> str:
    """연동 테스트용 수신 문서 예시(활력징후 기록)."""
    e = sim.census()[0]
    p = sim.people[e["person"]]
    now = time.time()
    oid = _hosp_oid(sim)
    obs = "".join(f'<component><observation classCode="OBS" moodCode="EVN"><code code="{VITALS[k]["loinc"]}" codeSystem="2.16.840.1.113883.6.1" codeSystemName="LOINC"/>'
                  f'<effectiveTime value="{_cda_ts(sim, now)}"/><value xsi:type="PQ" value="{v}" unit="{VITALS[k]["ucum"]}"/></observation></component>'
                  for k, v in (("hr", 76), ("rr", 16), ("spo2", 97), ("temp", 36.7)))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <realmCode code="KR"/>
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/>
  <templateId root="{KR_TEMPLATES['VITALS'][0]}"/>
  <id root="1.2.410.999999.1" extension="BM{int(now)}"/>
  <code code="8716-3" codeSystem="2.16.840.1.113883.6.1" displayName="Vital signs"/>
  <title>활력징후 기록 (생체신호 모니터링)</title>
  <effectiveTime value="{_cda_ts(sim, now)}"/>
  <confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>
  <recordTarget><patientRole><id root="{oid}.100" extension="{p['ids']['mrn']}"/><patient><name>{xesc(p['text'])}</name></patient></patientRole></recordTarget>
  <author><time value="{_cda_ts(sim, now)}"/><assignedAuthor><id root="1.2.410.999999.2" extension="BIOMON-GW"/></assignedAuthor></author>
  <custodian><assignedCustodian><representedCustodianOrganization><id root="1.2.410.100110.10" extension="{sim.site['hosp_code']}"/></representedCustodianOrganization></assignedCustodian></custodian>
  <component><structuredBody><component><section><code code="8716-3" codeSystem="2.16.840.1.113883.6.1"/><title>활력징후</title><text>장비 측정값</text>
    <entry><organizer classCode="CLUSTER" moodCode="EVN"><statusCode code="completed"/>{obs}</organizer></entry>
  </section></component></structuredBody></component>
</ClinicalDocument>
"""
