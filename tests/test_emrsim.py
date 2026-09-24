"""가상 EMR 20곳: 식별번호 체크 디지트, ADT 결정성, 형식별 수신 왕복(FHIR·HL7 v2·벤더 REST·EUC-KR XML·CDA)."""
import json
import re
import time
from collections import Counter

import pytest

from emulator.emrsim import ids, sim as S, hl7v2, vendor, fhir as F, fhir_api
from emulator.emrsim.api import Ctx, dispatch, samples, token_path
from emulator.emrsim.sites import SITES


@pytest.fixture(scope="module")
def sims():
    return {s["id"]: S.SiteSim(s) for s in SITES}


def test_twenty_sites_mix():
    c = Counter(s["country"] for s in SITES)
    assert len(SITES) == 20 and c["US"] == 4 and c["GB"] == 2 and c["JP"] == 2 and c["KR"] == 4
    assert len({s["id"] for s in SITES}) == 20
    assert {s["protocol"] for s in SITES} == {"fhir", "hl7v2", "athena", "kr-json", "kr-xml", "cda"}


def test_check_digits(sims):
    for sim in sims.values():
        for p in sim.people:
            i = p["ids"]
            if "nhs" in i:
                assert ids.nhs_ok(i["nhs"]) and i["nhs"].startswith("999")
            if "bsn" in i:
                assert ids.bsn_ok(i["bsn"])
            if "cpf" in i:
                assert ids.cpf_ok(i["cpf"])
            if "cns" in i:
                assert ids.cns_ok(i["cns"])
            if "ins" in i:
                assert ids.nir_ok(i["ins"])
            if "nric" in i:
                assert ids.nric_ok(i["nric"])
            if "eid" in i:
                assert ids.emirates_ok(i["eid"])
            if "ihi" in i:
                assert ids.luhn_ok(i["ihi"]) and i["ihi"].startswith("800360")
            if "ohip" in i:
                assert ids.luhn_ok(i["ohip"])
        for s in sim.staff:
            if "npi" in s["ids"]:
                assert ids.npi_ok(s["ids"]["npi"])


def test_identifiers_unique_per_site(sims):
    for sim in sims.values():
        mrns = [p["ids"].get("mrn") or p["ids"].get("patientid") for p in sim.people]
        assert len(set(mrns)) == len(mrns), sim.id
        assert all(re.fullmatch(r"[A-Za-z0-9\-.]{1,64}", p["fhir_id"]) for p in sim.people)


def test_adt_deterministic_and_consistent():
    s = SITES[0]
    now = time.time()
    a, b = S.SiteSim(s, now), S.SiteSim(s, now)
    a.advance(now), b.advance(now)
    assert [(e["code"], e["enc"], round(e["t"])) for e in a.events] == [(e["code"], e["enc"], round(e["t"])) for e in b.events]
    occ = [n for n in a.bed_occ if n is not None]
    assert len(occ) == len(set(occ))                                   # 한 입원이 두 병상을 차지하지 않는다
    assert all(a.encounters[n]["status"] == "in-progress" for n in occ)
    assert len(a.active_by_person) == len(occ)


def _ctx(sim, method, path, headers, body=b""):
    return Ctx(method, path, [], {k.lower(): v for k, v in headers.items()}, body, f"http://t/emrsim/{sim.id}", "pytest")


@pytest.mark.parametrize("site_id", [s["id"] for s in SITES])
def test_sample_roundtrip(sims, site_id):
    """카탈로그의 예시 요청(토큰 포함)이 그 기관의 검증을 통과해 저장된다."""
    sim = sims[site_id]
    token = None
    before = sim.counters["inbound_values"]
    for r in samples(sim, "http://t"):
        hdr = {k: (v.replace("<access_token>", token) if token else v) for k, v in r["headers"].items()}
        body = r["body"]
        raw = body.encode(r.get("encoding") or (hl7v2.charset(sim) if sim.site["protocol"] == "hl7v2" else "utf-8")) if body else b""
        path = r["url"].split(f"/emrsim/{site_id}/", 1)[1] if f"/emrsim/{site_id}/" in r["url"] else r["url"].split(f"/emrsim/{site_id}", 1)[1].strip("/")
        rep = dispatch(sim, _ctx(sim, r["method"], path, hdr, raw))
        assert rep.status < 300, (r["title"], rep.status, rep.body[:400])
        if "토큰" in r["title"]:
            token = json.loads(rep.body)["access_token"]
    assert sim.counters["inbound_values"] > before


def test_fhir_rejects_bad_observation(sims):
    sim = sims["kr-hanbit"]
    e = sim.census()[0]
    p = sim.people[e["person"]]
    base = {"resourceType": "Observation", "status": "final", "category": [{"coding": [{"code": "vital-signs"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "59408-5"}]}, "subject": {"reference": f"Patient/{p['fhir_id']}"},
            "effectiveDateTime": sim.iso(time.time() - 60), "valueQuantity": {"value": 97, "system": "http://unitsofmeasure.org", "code": "%"}}
    F.validate_observation(sim, base)
    for mutate, code in ((lambda r: r.update(effectiveDateTime="2026-01-01T10:00:00"), 400),
                         (lambda r: r["valueQuantity"].update(value=140), 422),
                         (lambda r: r.update(subject={"reference": "Patient/nope"}), 422),
                         (lambda r: r.update(category=[{"coding": [{"code": "laboratory"}]}]), 422),
                         (lambda r: r["valueQuantity"].update(code="mm[Hg]"), 422)):
        bad = json.loads(json.dumps(base))
        mutate(bad)
        with pytest.raises(F.FhirError) as ei:
            F.validate_observation(sim, bad)
        assert ei.value.status == code


def test_fhir_unit_conversion_us(sims):
    sim = sims["us-lakeshore"]
    e = sim.census()[0]
    p = sim.people[e["person"]]
    res = {"resourceType": "Observation", "status": "final", "category": [{"coding": [{"code": "vital-signs"}]}],
           "code": {"coding": [{"system": "http://loinc.org", "code": "8310-5"}]}, "subject": {"reference": f"Patient/{p['fhir_id']}"},
           "effectiveDateTime": sim.iso(time.time() - 60), "valueQuantity": {"value": 100.4, "system": "http://unitsofmeasure.org", "code": "[degF]"}}
    _, _, _, vals, _ = F.validate_observation(sim, res)
    assert vals[0][0] == "temp" and vals[0][1] == 38.0


def test_epic_search_rules(sims):
    sim = sims["us-lakeshore"]
    with pytest.raises(F.FhirError):
        fhir_api.search(sim, "Patient", [], "b", "b/Patient")
    e = sim.census()[0]
    pid = sim.people[e["person"]]["fhir_id"]
    with pytest.raises(F.FhirError):
        fhir_api.search(sim, "Observation", [("patient", pid)], "b", "b/Observation")
    b = fhir_api.search(sim, "Group", [("_id", "inpatient-census")], "b", "b/Group")
    assert b["entry"][0]["resource"]["quantity"] == len(sim.census())


def test_hl7_ack_versions(sims):
    for sid in ("us-pineridge", "uk-wexcombe", "jp-toto", "fr-belveze", "ca-stlucien", "ae-alwaha"):
        sim = sims[sid]
        msg = hl7v2.sample_oru(sim)
        ack, code, _, n = hl7v2.process_inbound(sim, msg.encode(hl7v2.charset(sim)), "test")
        assert code == "AA" and n == 4, (sid, ack)
        assert ack.split("\r")[0].split("|")[11] == sim.site["hl7_version"]
        wrong = msg.replace(f"|P|{sim.site['hl7_version']}", "|P|2.1", 1)
        ack, code, _, _ = hl7v2.process_inbound(sim, wrong.encode(hl7v2.charset(sim)), "test")
        assert code == "AR" and "203" in ack


def test_hl7_jp_encoding_and_names(sims):
    sim = sims["jp-toto"]
    ev = sim.events[0]
    msg = hl7v2.adt(sim, ev)
    raw = msg.encode("iso-2022-jp")
    assert b"\x1b$B" in raw                                   # JIS X 0208 로 전환하는 이스케이프
    pid = next(l for l in msg.split("\r") if l.startswith("PID"))
    assert "^L^I~" in pid and pid.split("|")[5].endswith("^L^P")


def test_hl7_fr_pam(sims):
    sim = sims["fr-belveze"]
    msg = hl7v2.adt(sim, sim.events[0])
    segs = {l[:3]: l for l in msg.split("\r") if l}
    assert "ZBE" in segs and "1.2.250.1.213.1.4.8" in segs["PID"] and segs["PID"].split("|")[32] == "VALI"
    assert msg.encode("iso-8859-1")


def test_kr_xml_wide_rows_euc_kr(sims):
    sim = sims["kr-donghae"]
    e = sim.census()[0]
    p = sim.people[e["person"]]
    lt = sim.local(time.time() - 120)
    x = (f'<?xml version="1.0" encoding="EUC-KR"?><IF_MSG><HEADER><IF_ID>EMR_VS_0002</IF_ID><SND_SYS_CD>BIOMON</SND_SYS_CD><TRX_ID>t</TRX_ID></HEADER><BODY><DATA_LIST>'
         f'<DATA><PTNT_NO>{p["ids"]["mrn"]}</PTNT_NO><VS_DT>{lt:%Y%m%d}</VS_DT><VS_TM>{lt:%H%M}</VS_TM><PR>77</PR><SPO2>96</SPO2></DATA>'
         f'<DATA><PTNT_NO>0000000</PTNT_NO><VS_DT>{lt:%Y%m%d}</VS_DT><VS_TM>{lt:%H%M}</VS_TM><PR>77</PR></DATA></DATA_LIST></BODY></IF_MSG>')
    st, body, rs, _ = vendor.kr_xml_handle(sim, x.encode("cp949"), "t")
    txt = body.decode("cp949")
    assert rs == "P" and "<PROC_CD>S</PROC_CD>" in txt and "<PROC_CD>E</PROC_CD>" in txt


def test_cda_documents_parse(sims):
    import xml.etree.ElementTree as ET
    sim = sims["kr-cheongram"]
    docs = vendor.cda_doc_list(sim)
    assert docs
    root = ET.fromstring(vendor.cda_document(sim, docs[0]).encode())
    assert root.tag == "{urn:hl7-org:v3}ClinicalDocument"


def test_token_expiry(sims):
    sim = sims["au-brindabella"]
    sim.faults["token_ttl_s"] = 1
    tok, ttl = sim.issue_token()
    assert sim.token_ok(tok)
    sim.tokens[tok] = time.time() - 1
    assert not sim.token_ok(tok)
    sim.faults["token_ttl_s"] = 3600


# ---------------------------------------------------------------- 연동 병원 선택 (에뮬레이터 world → 병원 EMR)
class _FakeHospital:
    def __init__(self):
        self.wards = [{"idx": 0, "id": "W103A", "name": "심장내과 병동 3A", "specialty": "심장내과", "floor": 3, "bed_idxs": [0, 1]},
                      {"idx": 1, "id": "W204B", "name": "호흡기내과 병동 4B", "specialty": "호흡기내과", "floor": 4, "bed_idxs": [2]}]
        self.rooms = [{"idx": 0, "id": "103A01", "name": "103A01", "ward_idx": 0}, {"idx": 1, "id": "204B01", "name": "204B01", "ward_idx": 1},
                      {"idx": 2, "id": "ER01", "name": "응급실 1", "ward_idx": None}]
        self.beds = [{"idx": 0, "id": "103A01-A", "room_idx": 0}, {"idx": 1, "id": "103A01-B", "room_idx": 0}, {"idx": 2, "id": "204B01-A", "room_idx": 1},
                     {"idx": 3, "id": "ER01-1", "room_idx": 2}]


class _FakeWorld:
    def __init__(self):
        self.hospital = _FakeHospital()
        mk = lambda i, name, sex, icd: {"id": i, "name": name, "sex": sex, "age": 70, "birth_date": "1956-03-12", "mrn": f"MRN-{i:08d}", "icd10": icd,
                                        "comorbidities": ["고혈압", "제2형 당뇨병"], "allergies": "페니실린", "phone": "010-1234-5678",
                                        "address": {"sido": "서울", "sigungu": "마포구", "dong": "공덕동"}, "admission": {"time": "2026-09-20T10:00:00"}}
        self.by_id = {1: mk(1, "김영수", "M", "I48.0"), 2: mk(2, "남궁민", "F", "J18.9"), 3: mk(3, "이순자", "F", "N18.5")}
        self.admitted = {1: {"patient_no": 5001, "bed_idx": 0, "outpatient": False}, 2: {"patient_no": 5002, "bed_idx": 2, "outpatient": False},
                         3: {"patient_no": 5003, "bed_idx": -1, "outpatient": True}}


def test_link_follows_emulator_world():
    from emulator.emrsim.link import LinkedSim
    from emulator.emrsim.sites import SITE_BY_ID
    w = _FakeWorld()
    ls = LinkedSim(SITE_BY_ID["us-pineridge"], w)
    assert len(ls.census()) == 3 and not ls.events                   # 연동 시점의 재원 환자는 이벤트 없이 반영(census 로 동기화)
    p = ls.people[ls.person_of_profile[1]]
    assert p["sex"] == "M" and p["birth"] == "1956-03-12" and p["allergy"] == "penicillin" and p["ids"]["mrn"].startswith("M000")
    e = ls.encounters[5001]
    assert e["cond"] == "afib" and set(e["comorbid"]) == {"htn", "dm2"} and ls.beds[e["bed"]]["emu_bed"] == "103A01-A"
    assert ls.wards[ls.beds[ls.encounters[5003]["bed"]]["ward"]]["code"] == "RPM"   # 원외(MCOT) → 원격 모니터링 병동
    # 전동, 퇴원, 새 입원이 ADT 이벤트로
    w.admitted[1]["bed_idx"] = 1
    del w.admitted[2]
    w.by_id[4] = dict(w.by_id[1], id=4, name="박철수", icd10="I63.9")
    w.admitted[4] = {"patient_no": 5004, "bed_idx": 2, "outpatient": False}
    ls.advance()
    assert [x["code"] for x in ls.events] == ["A03", "A02", "A01"]
    a02 = hl7v2.adt(ls, ls.events[1])
    pv1 = next(l for l in a02.split("\r") if l.startswith("PV1")).split("|")
    assert pv1[3] == "W103A^103A01^B^PRCH" and pv1[6] == "W103A^103A01^A^PRCH"          # 새 병상 / 이전 병상
    # 같은 환자는 다시 만들어도 같은 번호
    ls2 = LinkedSim(SITE_BY_ID["us-pineridge"], _FakeWorld())
    assert ls2.people[ls2.person_of_profile[1]]["ids"] == p["ids"]


def test_link_korean_site_keeps_emulator_name():
    from emulator.emrsim.link import LinkedSim
    from emulator.emrsim.sites import SITE_BY_ID
    ls = LinkedSim(SITE_BY_ID["kr-saesol"], _FakeWorld())
    p = ls.people[ls.person_of_profile[2]]
    assert p["text"] == "남궁민" and p["family"] == "남궁" and p["given"] == ["민"]
    rows = vendor.kr_json_handle(ls, "GET", ["api", "v1", "adm", "inpatients"], {}, b"")["DATA"]
    assert {r["PT_NM"] for r in rows} == {"김영수", "남궁민", "이순자"}
