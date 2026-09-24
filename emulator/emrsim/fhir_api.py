"""FHIR REST 처리: read / search-type / create(Observation) / transaction·batch, 계열별 검색 제약과 페이지 링크."""
from __future__ import annotations

import copy
import datetime as dt
import json
import time
from urllib.parse import urlencode

from . import fhir as F
from .clinical import BY_LOINC, VITALS

# 계열별: 페이지 파라미터 이름, 기본/최대 _count, 필수 검색 조건
PAGING = {"oracle": ("-pageContext", 50, 100), "epic": ("sessionID", 100, 1000)}
REQUIRED = {
    "epic": {"Patient": [("_id",), ("identifier",), ("family",), ("given",), ("name",), ("birthdate",)],
             "Encounter": [("_id",), ("patient",), ("subject",), ("identifier",)],
             "Observation": [("patient", "category"), ("patient", "code"), ("subject", "category"), ("subject", "code"), ("_id",)],
             "Condition": [("patient",), ("subject",), ("_id",)], "AllergyIntolerance": [("patient",), ("_id",)]},
    "oracle": {"Patient": [("_id",), ("identifier",), ("family",), ("given",), ("name",), ("birthdate",)],
               "Encounter": [("_id",), ("patient",), ("subject",), ("identifier",), ("location",)],
               "Observation": [("patient",), ("subject",), ("_id",), ("encounter",)],
               "Condition": [("patient",), ("subject",), ("_id",)], "AllergyIntolerance": [("patient",), ("_id",)]},
}
DEFAULT_REQUIRED = {"Observation": [("patient",), ("subject",), ("_id",), ("encounter",)], "Condition": [("patient",), ("subject",), ("encounter",), ("_id",)],
                    "AllergyIntolerance": [("patient",), ("_id",)]}
SUPPORTED = ("Patient", "Encounter", "Observation", "Condition", "AllergyIntolerance", "Practitioner", "Location", "Organization", "Group")


def _ref_id(v: str) -> str:
    return v.split("/")[-1] if v else v


def _all_locations(sim) -> list[dict]:
    out = [F.location(sim, "ward", w["idx"]) for w in sim.wards]
    seen = set()
    for b in sim.beds:
        k = (b["ward"], b["room"])
        if k not in seen:
            seen.add(k)
            out.append(F.location(sim, "room", k))
    out += [F.location(sim, "bed", b["idx"]) for b in sim.beds]
    return out


def _groups(sim) -> list[dict]:
    census = sim.census()
    gs = [("inpatient-census", "재원 환자 전체" if sim.site["lang"] == "ko" else "Current inpatient census", census)]
    for w in sim.wards:
        gs.append((f"census-{w['code'].lower()}", w["name"], [e for e in census if sim.beds[e["bed"]]["ward"] == w["idx"]]))
    return [{"resourceType": "Group", "id": gid, "meta": {"lastUpdated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")},
             "type": "person", "actual": True, "name": name, "quantity": len(encs),
             "member": [{"entity": {"reference": "Patient/" + sim.people[e["person"]]["fhir_id"], "display": sim.people[e["person"]]["text"]},
                         "period": {"start": sim.iso(e["admit"])}} for e in encs]} for gid, name, encs in gs]


def _obs_all(sim, pidx: int, enc_no: int | None = None) -> list[dict]:
    out = []
    for e in sim.enc_of_person(pidx):
        if enc_no is not None and e["no"] != enc_no:
            continue
        for rec in sim.vitals_for(e):
            for r in F.vital_resources(sim, rec):
                sim.fhir_obs_cache[r["id"]] = r
                out.append(r)
    out += [copy.deepcopy(r) for r in sim.fhir_store.values() if r["_person"] == pidx and (enc_no is None or r["_enc"] == enc_no)]
    return out


def _obs_kinds(r: dict) -> set[str]:
    codes = {c.get("code") for c in r["code"].get("coding", [])}
    return codes


def _check_required(sim, rtype: str, keys: set[str]):
    req = REQUIRED.get(sim.site["flavor"], {}).get(rtype) or DEFAULT_REQUIRED.get(rtype)
    if not req:
        return
    if not any(all(k in keys for k in combo) for combo in req):
        combos = " | ".join("+".join(c) for c in req)
        raise F.FhirError(400, "required" if sim.site["flavor"] != "epic" else "processing",
                          f"{rtype} search requires one of: {combos}" if sim.site["flavor"] != "epic" else
                          f"The search for {rtype} requires at least one of the following parameter combinations: {combos}.")


def search(sim, rtype: str, qm: list[tuple[str, str]], base: str, path_url: str) -> dict:
    q: dict[str, list[str]] = {}
    for k, v in qm:
        q.setdefault(k, []).append(v)
    one = lambda k: q.get(k, [None])[0]
    pkey, dflt, mx = PAGING.get(sim.site["flavor"], ("_getpagesoffset", 50, 500))
    try:
        count = min(int(one("_count") or dflt), mx)
    except ValueError:
        raise F.FhirError(400, "invalid", f"Invalid _count value '{one('_count')}'")
    offset = 0
    if one(pkey):
        offset = F.page_offset(one(pkey)) if pkey != "_getpagesoffset" else int(one(pkey))
    ctrl = {"_count", pkey, "-pageDirection", "_include", "_revinclude", "_sort", "_format", "_elements", "_total"}
    keys = {k.split(":")[0] for k in q if k not in ctrl}
    unknown = keys - {"_id", "identifier", "family", "given", "name", "birthdate", "gender", "patient", "subject", "status", "date", "location", "_lastUpdated",
                      "category", "code", "encounter", "clinical-status", "type", "partof", "active"}
    if unknown and sim.site["flavor"] in ("oracle", "uk-core", "isik"):
        raise F.FhirError(400, "not-supported", f"Unknown search parameter(s) {sorted(unknown)} for {rtype}")
    _check_required(sim, rtype, keys)
    tz = sim.tz

    def pat_filter(ref_vals: list[str]) -> int | None:
        rid = _ref_id(ref_vals[0])
        idx = sim.find_person(rid)
        if idx is None or sim.people[idx]["fhir_id"] != rid:
            return -1
        return idx

    rows: list[dict] = []
    includes: list[dict] = []
    if rtype == "Patient":
        people = sim.people
        for p in people:
            if one("_id") and p["fhir_id"] not in one("_id").split(","):
                continue
            if one("identifier"):
                sys, val = F.parse_token(one("identifier"))
                if sim.find_person(val) != p["idx"]:
                    continue
                if sys and sys not in {i.get("system") for i in F.patient(sim, p)["identifier"]}:
                    continue
            nm = " ".join([p["family"], *p["given"], p.get("text", ""), p.get("kana_text", "")]).lower()
            if one("family") and not any(x.lower().startswith(one("family").lower()) for x in (p["family"], p.get("kana_family", ""), p.get("family_own", ""))):
                continue
            if one("given") and not any(g.lower().startswith(one("given").lower()) for g in p["given"] + ([p["kana_given"]] if p.get("kana_given") else [])):
                continue
            if one("name") and one("name").lower() not in nm:
                continue
            if one("gender") and one("gender") != ("male" if p["sex"] == "M" else "female"):
                continue
            if q.get("birthdate"):
                bt = dt.datetime.fromisoformat(p["birth"]).replace(tzinfo=tz).timestamp() + 43200
                if not F.date_match(bt, [F.parse_date(v, tz) for v in q["birthdate"]]):
                    continue
            rows.append(F.patient(sim, p))
    elif rtype == "Encounter":
        sim.advance()
        pidx = pat_filter(q.get("patient") or q.get("subject")) if (q.get("patient") or q.get("subject")) else None
        statuses = set(",".join(q.get("status", [])).split(",")) - {""}
        loc_ids = {_ref_id(v) for v in ",".join(q.get("location", [])).split(",") if v}
        encs = sorted(list(sim.encounters.values()), key=lambda e: e["admit"])
        for e in encs:
            if pidx is not None and e["person"] != pidx:
                continue
            if one("_id") and e["fhir_id"] not in one("_id").split(","):
                continue
            if one("identifier") and F.parse_token(one("identifier"))[1] != e["visit"]:
                continue
            if statuses and e["status"] not in statuses:
                continue
            if loc_ids:
                w, b = sim.bed_path(e["bed"])
                mine = {F.loc_id(sim, "bed", e["bed"]), F.loc_id(sim, "room", (b["ward"], b["room"])), F.loc_id(sim, "ward", b["ward"])}
                if not (mine & loc_ids) or e["status"] != "in-progress" and not statuses:
                    continue
            if q.get("date"):
                conds = [F.parse_date(v, tz) for v in q["date"]]
                end = e["end"] or time.time()
                if not all({"ge": end >= a, "gt": end >= b, "le": e["admit"] < b, "lt": e["admit"] < a, "eq": e["admit"] < b and end >= a}.get(pre, True) for pre, a, b in conds):
                    continue
            if q.get("_lastUpdated") and not F.date_match(e["updated"], [F.parse_date(v, tz) for v in q["_lastUpdated"]]):
                continue
            rows.append(F.encounter(sim, e))
        inc = set(q.get("_include", []))
        if "Encounter:patient" in inc or "Encounter:subject" in inc:
            seen = set()
            for r in rows:
                pid = _ref_id(r["subject"]["reference"])
                if pid not in seen:
                    seen.add(pid)
                    includes.append(F.patient(sim, sim.people[sim.find_person(pid)]))
        if "Encounter:location" in inc:
            allloc = {x["id"]: x for x in _all_locations(sim)}
            seen = set()
            for r in rows:
                for l in r.get("location", []):
                    lid = _ref_id(l["location"]["reference"])
                    if lid not in seen and lid in allloc:
                        seen.add(lid)
                        includes.append(allloc[lid])
    elif rtype == "Observation":
        if one("_id"):
            r = read(sim, "Observation", one("_id"))
            rows = [r]
        else:
            pidx = pat_filter(q.get("patient") or q.get("subject")) if (q.get("patient") or q.get("subject")) else None
            enc_no = None
            if one("encounter"):
                eid = _ref_id(one("encounter"))
                e = next((x for x in list(sim.encounters.values()) if x["fhir_id"] == eid), None)
                enc_no = e["no"] if e else -1
                if pidx is None and e:
                    pidx = e["person"]
            if pidx is None or pidx == -1 or enc_no == -1:
                rows = []
            else:
                cats = set(",".join(q.get("category", [])).split(",")) - {""}
                cats = {F.parse_token(c)[1] for c in cats}
                codes = {F.parse_token(c)[1] for c in ",".join(q.get("code", [])).split(",") if c}
                rows = _obs_all(sim, pidx, enc_no)
                if cats and "vital-signs" not in cats:
                    rows = []
                if codes:
                    rows = [r for r in rows if _obs_kinds(r) & codes or any(c in codes for comp in r.get("component", []) for c in _obs_kinds(comp))]
                if q.get("date"):
                    conds = [F.parse_date(v, tz) for v in q["date"]]
                    rows = [r for r in rows if F.date_match(dt.datetime.fromisoformat(r["effectiveDateTime"].replace("Z", "+00:00")).timestamp(), conds)]
                if q.get("_lastUpdated"):
                    conds = [F.parse_date(v, tz) for v in q["_lastUpdated"]]
                    rows = [r for r in rows if F.date_match(dt.datetime.fromisoformat(r["meta"]["lastUpdated"].replace("Z", "+00:00")).timestamp(), conds)]
                desc = (one("_sort") or ("-date" if sim.site["flavor"] in ("oracle", "epic") else "date")).startswith("-")
                rows.sort(key=lambda r: r["effectiveDateTime"], reverse=desc)
    elif rtype == "Condition":
        pidx = pat_filter(q.get("patient") or q.get("subject")) if (q.get("patient") or q.get("subject")) else None
        eid = _ref_id(one("encounter")) if one("encounter") else None
        for e in sorted(list(sim.encounters.values()), key=lambda e: e["admit"]):
            if pidx is not None and e["person"] != pidx or eid and e["fhir_id"] != eid:
                continue
            for c in F.encounter_conditions(sim, e):
                if one("_id") and c["id"] != one("_id"):
                    continue
                cs = c["clinicalStatus"] if isinstance(c["clinicalStatus"], str) else c["clinicalStatus"]["coding"][0]["code"]
                if one("clinical-status") and F.parse_token(one("clinical-status"))[1] != cs:
                    continue
                rows.append(c)
    elif rtype == "AllergyIntolerance":
        pidx = pat_filter(q.get("patient")) if q.get("patient") else None
        rows = [F.allergy(sim, p) for p in sim.people if (pidx is None or p["idx"] == pidx) and (not one("_id") or F.allergy(sim, p)["id"] == one("_id"))]
    elif rtype == "Practitioner":
        for s in sim.staff:
            r = F.practitioner(sim, s)
            if one("_id") and r["id"] != one("_id"):
                continue
            if one("identifier") and F.parse_token(one("identifier"))[1] not in {i["value"] for i in r["identifier"]}:
                continue
            if one("name") and one("name").lower() not in (s["family"] + " " + " ".join(s["given"])).lower():
                continue
            rows.append(r)
    elif rtype == "Location":
        for r in _all_locations(sim):
            if one("_id") and r["id"] != one("_id"):
                continue
            if one("name") and one("name").lower() not in r["name"].lower():
                continue
            if one("type") and F.parse_token(one("type"))[1] not in {c["code"] for c in r["physicalType"]["coding"]}:
                continue
            if one("partof") and _ref_id(one("partof")) != _ref_id((r.get("partOf") or {}).get("reference", "")):
                continue
            rows.append(r)
    elif rtype == "Organization":
        rows = [F.organization(sim)]
    elif rtype == "Group":
        rows = [g for g in _groups(sim) if not one("_id") or g["id"] == one("_id")]
    else:
        raise F.FhirError(404, "not-supported", f"Resource type {rtype} is not supported by this server")

    total = len(rows)
    page = rows[offset: offset + count]
    base_q = [(k, v) for k, v in qm if k not in (pkey, "-pageDirection")]
    self_url = path_url + ("?" + urlencode(qm) if qm else "")
    nxt = None
    if offset + count < total:
        tok = F.page_token(offset + count) if pkey != "_getpagesoffset" else str(offset + count)
        extra = [(pkey, tok)] + ([("-pageDirection", "NEXT")] if pkey == "-pageContext" else [])
        nxt = path_url + "?" + urlencode(base_q + extra)
    inc_ids = {(r["resourceType"], r["id"]) for r in includes}
    show_total = sim.site["flavor"] not in ("epic",) or one("_total") == "accurate"          # Epic 는 total 을 생략하는 경우가 많다
    return F.bundle(sim, page + includes, base, total if show_total else None, self_url, nxt, include_ids=inc_ids)


def read(sim, rtype: str, rid: str) -> dict:
    def nf():
        return F.FhirError(404, "not-found", f"Resource {rtype}/{rid} is not known")
    if rtype == "Patient":
        idx = sim.find_person(rid)
        if idx is None or sim.people[idx]["fhir_id"] != rid:
            raise nf()
        return F.patient(sim, sim.people[idx])
    if rtype == "Encounter":
        sim.advance()
        e = next((x for x in list(sim.encounters.values()) if x["fhir_id"] == rid), None)
        if not e:
            raise nf()
        return F.encounter(sim, e)
    if rtype == "Observation":
        if rid in sim.fhir_store:
            r = copy.deepcopy(sim.fhir_store[rid])
            return r
        if rid in sim.fhir_obs_cache:
            return sim.fhir_obs_cache[rid]
        for e in sim.census():
            for r in _obs_all(sim, e["person"], e["no"]):
                if r["id"] == rid:
                    return r
        raise nf()
    if rtype == "Condition":
        for e in list(sim.encounters.values()):
            for c in F.encounter_conditions(sim, e):
                if c["id"] == rid:
                    return c
        raise nf()
    if rtype == "AllergyIntolerance":
        for p in sim.people:
            if sim.fid("AllergyIntolerance", p["idx"]) == rid:
                return F.allergy(sim, p)
        raise nf()
    if rtype == "Practitioner":
        s = next((x for x in sim.staff if x["fhir_id"] == rid), None)
        if not s:
            raise nf()
        return F.practitioner(sim, s)
    if rtype == "Location":
        r = next((x for x in _all_locations(sim) if x["id"] == rid), None)
        if not r:
            raise nf()
        return r
    if rtype == "Organization":
        if rid != F.org_id(sim):
            raise nf()
        return F.organization(sim)
    if rtype == "Group":
        g = next((x for x in _groups(sim) if x["id"] == rid), None)
        if not g:
            raise nf()
        return g
    raise F.FhirError(404, "not-supported", f"Resource type {rtype} is not supported by this server")


def create_observation(sim, res: dict) -> tuple[dict, list]:
    pidx, enc_no, t, values, warns = F.validate_observation(sim, res)
    with sim.lock:
        sim.fhir_seq += 1
        oid = sim.fid("Observation", f"in:{sim.fhir_seq}")
    now = time.time()
    stored = copy.deepcopy(res)
    stored["id"] = oid
    stored["meta"] = {**(res.get("meta") or {}), "versionId": "1", "lastUpdated": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    if "issued" not in stored:
        stored["issued"] = stored["meta"]["lastUpdated"]
    dev = None
    if isinstance(res.get("device"), dict):
        dev = res["device"].get("display") or (res["device"].get("identifier") or {}).get("value") or res["device"].get("reference")
    for kind, std, unit, raw in values:
        sim.store_inbound(pidx, enc_no, t, kind, std, "fhir", dev, unit, raw, oid)
    stored["_person"], stored["_enc"] = pidx, enc_no
    sim.fhir_store[oid] = stored
    out = F._clean(stored)
    return out, warns


def handle(sim, method: str, rp: list[str], qm: list[tuple[str, str]], body: bytes, base: str, headers: dict) -> tuple[int, dict | None, dict]:
    """rp: FHIR base 뒤 경로 조각. → (status, json|None, 추가 헤더)."""
    hdr = {}
    if not rp:
        if method != "POST":
            raise F.FhirError(400, "not-supported", "Only transaction/batch Bundle POST is supported at the base")
        return transaction(sim, body, base)
    rtype = rp[0]
    if rtype == "metadata" and method == "GET":
        return 200, F.capability(sim, base), hdr
    if rtype not in SUPPORTED:
        raise F.FhirError(404, "not-supported", f"Resource type '{rtype}' is not supported by this server")
    if method == "GET" and len(rp) == 1 or (method == "POST" and rp[1:] == ["_search"]):
        if method == "POST":
            from urllib.parse import parse_qsl
            qm = qm + parse_qsl(body.decode("utf-8", "replace"))
        return 200, search(sim, rtype, qm, base, f"{base}/{rtype}"), hdr
    if method == "GET" and len(rp) == 2:
        r = read(sim, rtype, rp[1])
        r = F._clean(r)
        hdr["ETag"] = f'W/"{r.get("meta", {}).get("versionId", "1")}"'
        return 200, r, hdr
    if method == "POST" and len(rp) == 1:
        if rtype != "Observation":
            raise F.FhirError(405, "not-supported", f"create is not supported for {rtype} (read-only)")
        try:
            res = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise F.FhirError(400, "structure", f"Unable to parse JSON body: {e}")
        out, warns = create_observation(sim, res)
        hdr.update({"Location": f"{base}/Observation/{out['id']}/_history/1", "ETag": 'W/"1"', "Last-Modified": out["meta"]["lastUpdated"]})
        prefer = headers.get("prefer", "")
        if "return=minimal" in prefer:
            return 201, None, hdr
        if "return=OperationOutcome" in prefer:
            return 201, F.outcome([("information", "informational", "Observation created", None)] + warns), hdr
        return 201, out, hdr
    if method in ("PUT", "DELETE", "PATCH"):
        raise F.FhirError(405, "not-supported", f"{method} is not allowed on {rtype}")
    raise F.FhirError(400, "not-supported", "Unsupported request")


def transaction(sim, body: bytes, base: str) -> tuple[int, dict, dict]:
    try:
        b = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise F.FhirError(400, "structure", f"Unable to parse JSON body: {e}")
    if b.get("resourceType") != "Bundle" or b.get("type") not in ("transaction", "batch"):
        raise F.FhirError(400, "invalid", "Body must be a Bundle of type transaction or batch")
    entries = b.get("entry") or []
    if b["type"] == "transaction":
        for i, en in enumerate(entries):
            req = en.get("request") or {}
            if req.get("method") != "POST" or req.get("url") != "Observation":
                raise F.FhirError(400, "not-supported", f"Bundle.entry[{i}].request must be POST Observation", f"Bundle.entry[{i}].request")
            try:
                F.validate_observation(sim, en.get("resource"))
            except F.FhirError as e:
                e.diagnostics = f"Bundle.entry[{i}]: {e.diagnostics}"
                raise e
    out = []
    for i, en in enumerate(entries):
        req = en.get("request") or {}
        try:
            if req.get("method") != "POST" or req.get("url") != "Observation":
                raise F.FhirError(400, "not-supported", "only POST Observation is supported in batch")
            res, _ = create_observation(sim, en.get("resource"))
            out.append({"fullUrl": f"{base}/Observation/{res['id']}", "response": {"status": "201 Created", "location": f"Observation/{res['id']}/_history/1", "etag": 'W/"1"',
                                                                                   "lastModified": res["meta"]["lastUpdated"]}})
        except F.FhirError as e:
            out.append({"response": {"status": f"{e.status} {'Bad Request' if e.status == 400 else 'Unprocessable Entity' if e.status == 422 else 'Error'}", "outcome": e.outcome()}})
    return 200, {"resourceType": "Bundle", "id": sim.fid("Bundle", time.time_ns()), "type": b["type"] + "-response", "entry": out}, {}
