"""FHIR R4 / STU3 리소스 렌더링·검색·검증 — 계열(flavor)마다 프로파일·식별자 체계·이름 표기·단위·검색 제약이 다르다.

flavor: epic, oracle (US Core) · uk-core · jp-core · kr-core · isik · nl-zib (STU3) · au-core · sg · br-rnds
"""
from __future__ import annotations

import base64
import datetime as dt
import re
import time

from . import ids as idmod
from .clinical import (CONDITIONS, ALLERGY_BY_KEY, VITALS, VITAL_ORDER, BP_PANEL, BY_LOINC, c_to_f, normalize_unit, in_range)

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SCT = "http://snomed.info/sct"
V2_0203 = "http://terminology.hl7.org/CodeSystem/v2-0203"
USC = "http://hl7.org/fhir/us/core/StructureDefinition/"
UKC = "https://fhir.hl7.org.uk/StructureDefinition/"
JPC = "http://jpfhir.jp/fhir/core/StructureDefinition/"
KRC = "https://hl7korea.or.kr/fhir/krcore/StructureDefinition/"
ISIK = "https://gematik.de/fhir/isik/StructureDefinition/"
NLC = "http://nictiz.nl/fhir/StructureDefinition/"
AUC = "http://hl7.org.au/fhir/core/StructureDefinition/"
BRR = "http://www.saude.gov.br/fhir/r4/StructureDefinition/"
BASE_VS = "http://hl7.org/fhir/StructureDefinition/"


class FhirError(Exception):
    def __init__(self, status: int, code: str, diagnostics: str, expression: str | None = None, extra: list | None = None):
        super().__init__(diagnostics)
        self.status, self.code, self.diagnostics, self.expression, self.extra = status, code, diagnostics, expression, extra or []

    def outcome(self) -> dict:
        iss = [{"severity": "error", "code": self.code, "diagnostics": self.diagnostics, **({"expression": [self.expression]} if self.expression else {})}]
        return {"resourceType": "OperationOutcome", "issue": iss + self.extra}


def outcome(issues: list[tuple[str, str, str, str | None]]) -> dict:
    return {"resourceType": "OperationOutcome", "issue": [{"severity": s, "code": c, "diagnostics": d, **({"expression": [e]} if e else {})} for s, c, d, e in issues]}


def stu3(sim) -> bool:
    return sim.site.get("fhir_version", "4.0.1").startswith("3")


def profiles(sim) -> dict:
    fl = sim.site["flavor"]
    if fl in ("epic", "oracle"):
        return {"Patient": USC + "us-core-patient", "Encounter": USC + "us-core-encounter", "Condition": USC + "us-core-condition-encounter-diagnosis",
                "Practitioner": USC + "us-core-practitioner", "Location": USC + "us-core-location", "AllergyIntolerance": USC + "us-core-allergyintolerance",
                "Organization": USC + "us-core-organization",
                "hr": USC + "us-core-heart-rate", "rr": USC + "us-core-respiratory-rate", "spo2": USC + "us-core-pulse-oximetry", "temp": USC + "us-core-body-temperature", "bp": USC + "us-core-blood-pressure"}
    if fl == "uk-core":
        return {k: UKC + "UKCore-" + k for k in ("Patient", "Encounter", "Condition", "Practitioner", "Location", "AllergyIntolerance", "Organization")} | \
               {k: UKC + "UKCore-Observation" for k in ("hr", "rr", "spo2", "temp", "bp")}
    if fl == "jp-core":
        return {k: JPC + "JP_" + k for k in ("Patient", "Encounter", "Condition", "Practitioner", "Location", "AllergyIntolerance", "Organization")} | \
               {"hr": JPC + "JP_Observation_VitalSigns", "rr": JPC + "JP_Observation_VitalSigns", "spo2": JPC + "JP_Observation_VitalSigns", "temp": JPC + "JP_Observation_BodyTemperature",
                "bp": JPC + "JP_Observation_BloodPressure"}
    if fl == "kr-core":
        return {k: KRC + "krcore-" + k.lower() for k in ("Patient", "Encounter", "Condition", "Practitioner", "Location", "AllergyIntolerance", "Organization")} | \
               {k: KRC + "krcore-observation-vital-signs" for k in ("hr", "rr", "spo2", "temp", "bp")}
    if fl == "isik":
        return {"Patient": ISIK + "ISiKPatient", "Encounter": ISIK + "ISiKKontaktGesundheitseinrichtung", "Condition": ISIK + "ISiKDiagnose",
                "Practitioner": ISIK + "ISiKPersonImGesundheitsberuf", "Location": ISIK + "ISiKStandortBettenstellplatz", "AllergyIntolerance": ISIK + "ISiKAllergieUnvertraeglichkeit",
                "Organization": ISIK + "ISiKOrganisation",
                "hr": ISIK + "ISiKHerzfrequenz", "rr": ISIK + "ISiKAtemfrequenz", "spo2": ISIK + "ISiKSauerstoffsaettigungImArteriellenBlut", "temp": ISIK + "ISiKKoerpertemperatur",
                "bp": ISIK + "ISiKBlutdruckSystemischArteriell"}
    if fl == "nl-zib":
        return {"Patient": "http://fhir.nl/fhir/StructureDefinition/nl-core-patient", "Encounter": NLC + "zib-Encounter", "Condition": NLC + "zib-Problem",
                "Practitioner": "http://fhir.nl/fhir/StructureDefinition/nl-core-practitioner", "Location": NLC + "zib-HealthcareProvider", "AllergyIntolerance": NLC + "zib-AllergyIntolerance",
                "Organization": "http://fhir.nl/fhir/StructureDefinition/nl-core-organization",
                "hr": NLC + "zib-HeartRate", "rr": NLC + "zib-Respiration", "spo2": NLC + "zib-O2Saturation", "temp": NLC + "zib-BodyTemperature", "bp": NLC + "zib-BloodPressure"}
    if fl == "au-core":
        return {k: AUC + "au-core-" + k.lower() for k in ("Patient", "Encounter", "Condition", "Practitioner", "Location", "AllergyIntolerance", "Organization")} | \
               {"hr": AUC + "au-core-heartrate", "rr": AUC + "au-core-resprate", "spo2": BASE_VS + "oxygensat", "temp": AUC + "au-core-bodytemp", "bp": AUC + "au-core-bloodpressure"}
    if fl == "br-rnds":
        return {"Patient": BRR + "BRIndividuo-1.0", "Encounter": BRR + "BRContatoAssistencial-1.0", "Condition": BRR + "BRProblemaDiagnostico-1.0",
                "Practitioner": BRR + "BRProfissional-1.0", "Location": BRR + "BRLocalizacao-1.0", "AllergyIntolerance": BRR + "BRAlergiaReacaoAdversa-1.0",
                "Organization": BRR + "BREstabelecimentoSaude-1.0"} | {k: BRR + "BRSinaisVitais-1.0" for k in ("hr", "rr", "spo2", "temp", "bp")}
    sg = f"https://fhir.{sim.id.split('-', 1)[1]}.sg/StructureDefinition/"
    return {k: sg + "sg-" + k.lower() for k in ("Patient", "Encounter", "Condition", "Practitioner", "Location", "AllergyIntolerance", "Organization")} | \
           {k: BASE_VS + {"hr": "heartrate", "rr": "resprate", "spo2": "oxygensat", "temp": "bodytemp", "bp": "bp"}[k] for k in ("hr", "rr", "spo2", "temp", "bp")}


def _meta(sim, key: str, version: int, t: float) -> dict:
    return {"versionId": str(version), "lastUpdated": dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "profile": [profiles(sim)[key]]}


def _code(system: str, code: str, display: str | None = None) -> dict:
    return {"system": system, "code": code, **({"display": display} if display else {})}


def _cc(codings: list[dict], text: str | None = None) -> dict:
    return {"coding": codings, **({"text": text} if text else {})}


def _local_domain(sim) -> str:
    return f"{sim.id.split('-', 1)[1]}.example.{sim.site['country'].lower()}"


# ------------------------------------------------------------------ Patient
def patient(sim, p: dict) -> dict:
    fl, s = sim.site["flavor"], sim.site
    pid = p["ids"]
    res = {"resourceType": "Patient", "id": p["fhir_id"], "meta": _meta(sim, "Patient", p["version"], p["updated"])}
    ext, ident = [], []
    if fl == "epic":
        oid = s["oid"]
        ident = [{"use": "usual", "type": {"text": "EPI"}, "system": f"urn:oid:{oid}.5.737384.0", "value": pid["epi"]},
                 {"use": "usual", "type": {"text": "MRN"}, "system": f"urn:oid:{oid}.5.737384.14", "value": pid["mrn"]},
                 {"use": "usual", "system": "http://open.epic.com/FHIR/StructureDefinition/patient-fhir-id", "value": p["fhir_id"]}]
    elif fl == "oracle":
        ident = [{"use": "usual", "type": _cc([_code(V2_0203, "MR", "Medical record number")], "MRN"), "system": f"urn:oid:{s['oid']}", "value": pid["mrn"],
                  "_value": {"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/rendered-value", "valueString": pid["mrn"]}]}, "period": {"start": p["birth"] + "T00:00:00.000Z"}},
                 {"use": "usual", "type": _cc([_code("https://fhir.cerner.com/" + s["tenant"] + "/codeSet/4", "2553236771", "Community Medical Record Number")], "CMRN"),
                  "system": "urn:oid:2.16.840.1.113883.3.787.0.0", "value": pid["cmrn"]}]
    elif fl == "uk-core":
        ident = [{"system": "https://fhir.nhs.uk/Id/nhs-number", "value": pid["nhs"],
                  "extension": [{"url": UKC + "Extension-UKCore-NHSNumberVerificationStatus",
                                 "valueCodeableConcept": _cc([_code("https://fhir.hl7.org.uk/CodeSystem/UKCore-NHSNumberVerificationStatusEngland", "01", "Number present and verified")])}]},
                 {"type": _cc([_code(V2_0203, "MR", "Medical record number")]), "system": f"https://fhir.{s['ods'].lower()}.nhs.uk/Id/hospital-number", "value": pid["mrn"]}]
        ext.append({"url": UKC + "Extension-UKCore-EthnicCategory", "valueCodeableConcept": _cc([_code("https://fhir.hl7.org.uk/CodeSystem/UKCore-EthnicCategoryEngland", *p["ethnic"])])})
    elif fl == "jp-core":
        ident = [{"system": f"urn:oid:1.2.392.100495.20.3.51.1{s['medical_code']}", "value": pid["mrn"]}]
    elif fl == "kr-core":
        ident = [{"use": "usual", "type": _cc([_code(V2_0203, "MR", "Medical record number")], "등록번호"), "system": f"urn:oid:1.2.410.100110.10.{s['hosp_code']}", "value": pid["mrn"]},
                 {"use": "official", "type": _cc([_code(V2_0203, "NNKOR", "National Person Identifier (KOR)")], "주민등록번호"), "system": "http://www.hl7korea.or.kr/Identifier/rrn",
                  "value": idmod.rrn_masked(pid["rrn"])}]
    elif fl == "isik":
        if "kvnr" in pid:
            ident.append({"type": _cc([_code("http://fhir.de/CodeSystem/identifier-type-de-basis", "KVZ10", "Krankenversichertennummer")]), "system": "http://fhir.de/sid/gkv/kvid-10",
                          "value": pid["kvnr"], "assigner": {"identifier": {"system": "http://fhir.de/sid/arge-ik/iknr", "value": pid["ik_insurer"]}}})
        else:
            ident.append({"type": _cc([_code("http://fhir.de/CodeSystem/identifier-type-de-basis", "PKV", "Private Krankenversicherung")]), "value": pid["pkv"]})
        ident.append({"type": _cc([_code(V2_0203, "MR", "Medical record number")]), "system": f"https://fhir.{_local_domain(sim)}/sid/patienten", "value": pid["mrn"]})
    elif fl == "nl-zib":
        ident = [{"system": "http://fhir.nl/fhir/NamingSystem/bsn", "value": pid["bsn"]}, {"system": f"https://fhir.{_local_domain(sim)}/NamingSystem/patientnummer", "value": pid["mrn"]}]
    elif fl == "au-core":
        ident = [{"extension": [{"url": "http://hl7.org.au/fhir/StructureDefinition/ihi-status", "valueCoding": _code("https://healthterminologies.gov.au/fhir/CodeSystem/ihi-status-1", "active", "Active")},
                                {"url": "http://hl7.org.au/fhir/StructureDefinition/ihi-record-status", "valueCoding": _code("https://healthterminologies.gov.au/fhir/CodeSystem/ihi-record-status-1", "verified", "verified")}],
                  "type": _cc([_code(V2_0203, "NI", "National unique individual identifier")], "IHI"), "system": "http://ns.electronichealth.net.au/id/hi/ihi/1.0", "value": pid["ihi"]},
                 {"type": _cc([_code(V2_0203, "MC", "Patient's Medicare number")], "Medicare Number"), "system": "http://ns.electronichealth.net.au/id/medicare-number", "value": pid["medicare"],
                  "period": {"end": f"{sim.today.year + 3}-0{(p['idx'] % 9) + 1}"}},
                 {"type": _cc([_code(V2_0203, "MR", "Medical record number")], "MRN"), "system": f"http://ns.electronichealth.net.au/id/hpio-scoped/medicalrecord/1.0/{s['hpio']}", "value": pid["mrn"]}]
        ext.append({"url": "http://hl7.org.au/fhir/StructureDefinition/indigenous-status",
                    "valueCoding": _code("https://healthterminologies.gov.au/fhir/CodeSystem/australian-indigenous-status-1", *p["indigenous"])})
    elif fl == "br-rnds":
        ident = [{"use": "official", "system": "http://rnds.saude.gov.br/fhir/r4/NamingSystem/cns", "value": pid["cns"]},
                 {"use": "official", "system": "http://rnds.saude.gov.br/fhir/r4/NamingSystem/cpf", "value": re.sub(r"\D", "", pid["cpf"])},
                 {"use": "usual", "type": _cc([_code(V2_0203, "MR", "Medical record number")], "Prontuário"), "system": f"https://fhir.{_local_domain(sim)}/prontuario", "value": pid["mrn"]}]
        ext.append({"url": BRR + "BRRacaCorEtnia-1.0", "valueCodeableConcept": _cc([_code("http://www.saude.gov.br/fhir/r4/CodeSystem/BRRacaCor", *p["race_br"])])})
        ext.append({"url": "http://hl7.org/fhir/StructureDefinition/patient-mothersMaidenName", "valueString": p["mother"]})
    else:  # sg
        ident = [{"use": "official", "type": _cc([_code(V2_0203, "NNSGP", "National Person Identifier (SGP)")], "NRIC"), "system": "https://ns.health.example.sg/id/nric", "value": p["ids"]["nric"]},
                 {"use": "usual", "type": _cc([_code(V2_0203, "MR", "Medical record number")]), "system": f"https://fhir.{sim.id.split('-', 1)[1]}.sg/id/mrn", "value": pid["mrn"]}]
        ext.append({"url": f"https://fhir.{sim.id.split('-', 1)[1]}.sg/StructureDefinition/race", "valueString": p["ethnic_sg"].capitalize()})
    if fl in ("epic", "oracle"):
        race_code, race_text = p["race"]
        ext += [{"url": USC + "us-core-race", "extension": [{"url": "ombCategory", "valueCoding": _code("urn:oid:2.16.840.1.113883.6.238", race_code, race_text)}, {"url": "text", "valueString": race_text}]},
                {"url": USC + "us-core-ethnicity", "extension": [{"url": "ombCategory", "valueCoding": _code("urn:oid:2.16.840.1.113883.6.238", *p["ethnicity"])}, {"url": "text", "valueString": p["ethnicity"][1]}]},
                {"url": USC + "us-core-birthsex", "valueCode": p["sex"]}]
    if ext:
        res["extension"] = ext
    res["identifier"] = ident
    res["active"] = True
    res["name"] = names(sim, p)
    res["telecom"] = [{"system": "phone", "value": p["phone"], "use": "mobile"}]
    res["gender"] = "male" if p["sex"] == "M" else "female"
    res["birthDate"] = p["birth"]
    a = p["address"]
    addr = {"use": "home", "line": a["line"], "city": a["city"], **({"district": a["district"]} if a.get("district") else {}), **({"state": a["state"]} if a.get("state") else {}),
            **({"postalCode": a["postal"]} if a.get("postal") else {}), "country": a["country"]}
    if fl in ("jp-core", "kr-core"):
        addr["text"] = " ".join(x for x in ([("〒" + a["postal"]) if fl == "jp-core" else None, a["state"], a["city"], a.get("district")] + a["line"]) if x)
    res["address"] = [addr]
    lang_code = p.get("language", "en")
    res["communication"] = [{"language": _cc([_code("urn:ietf:bcp:47", lang_code)]), "preferred": True}]
    if fl == "uk-core":
        res["generalPractitioner"] = [{"type": "Organization", "identifier": {"system": "https://fhir.nhs.uk/Id/ods-organization-code", "value": f"Y0{2000 + p['idx'] % 300:04d}"}}]
    res["managingOrganization"] = {"reference": "Organization/" + org_id(sim)}
    return res


def names(sim, p: dict) -> list[dict]:
    fl = sim.site["flavor"]
    if fl == "jp-core":
        rep = "http://hl7.org/fhir/StructureDefinition/iso21090-EN-representation"
        return [{"extension": [{"url": rep, "valueCode": "IDE"}], "use": "official", "text": p["text"], "family": p["family"], "given": p["given"]},
                {"extension": [{"url": rep, "valueCode": "SYL"}], "use": "official", "text": p["kana_text"], "family": p["kana_family"], "given": [p["kana_given"]]}]
    if fl == "kr-core":
        return [{"use": "official", "text": p["text"], "family": p["family"], "given": p["given"]}]
    if fl == "nl-zib":
        fam = {"use": "official", "family": p["family"],
               "_family": {"extension": ([{"url": "http://hl7.org/fhir/StructureDefinition/humanname-own-prefix", "valueString": p["family_prefix"]}] if p["family_prefix"] else []) +
                                        [{"url": "http://hl7.org/fhir/StructureDefinition/humanname-own-name", "valueString": p["family_own"]}]},
               "given": [p["initials"]], "_given": [{"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/iso21090-EN-qualifier", "valueCode": "IN"}]}]}
        return [fam, {"use": "usual", "given": p["given"]}]
    if fl == "sg":
        return [{"use": "official", "text": p["text"], "family": p["family"], "given": p["given"]}]
    if fl == "br-rnds":
        return [{"use": "official", "text": " ".join(p["given"] + [p["family"]]), "family": p["family"], "given": p["given"]}]
    n = {"use": "official", "text": p["text"], "family": p["family"], "given": p["given"]}
    if p.get("prefix"):
        n["prefix"] = [p["prefix"]]
    if p.get("title"):
        n["prefix"] = [p["title"]]
        n["_prefix"] = [{"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/iso21090-EN-qualifier", "valueCode": "AC"}]}]
    return [n]


def org_id(sim) -> str:
    return sim.fid("Organization", 0)


def organization(sim) -> dict:
    s, fl = sim.site, sim.site["flavor"]
    ident = []
    if fl == "uk-core":
        ident = [{"system": "https://fhir.nhs.uk/Id/ods-organization-code", "value": s["ods"]}]
    elif fl == "isik":
        ident = [{"type": _cc([_code(V2_0203, "PRN", "Provider number")]), "system": "http://fhir.de/sid/arge-ik/iknr", "value": s["ik"]}]
    elif fl == "jp-core":
        ident = [{"system": "http://jpfhir.jp/fhir/core/IdSystem/insurance-medical-institution-no", "value": s["medical_code"]}]
    elif fl == "kr-core":
        ident = [{"system": "http://www.hl7korea.or.kr/Identifier/hira-krnk", "value": s["hosp_code"]}]
    elif fl == "au-core":
        ident = [{"type": _cc([_code(V2_0203, "NOI", "National Organization Identifier")], "HPI-O"), "system": "http://ns.electronichealth.net.au/id/hi/hpio/1.0", "value": s["hpio"]}]
    elif fl == "br-rnds":
        ident = [{"system": "http://rnds.saude.gov.br/fhir/r4/NamingSystem/cnes", "value": s["cnes"]}]
    elif fl == "nl-zib":
        ident = [{"system": "http://fhir.nl/fhir/NamingSystem/agb-z", "value": s["agb"]}]
    elif fl in ("epic", "oracle"):
        ident = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": idmod.npi(__import__("random").Random(s["seed"]))}]
    return {"resourceType": "Organization", "id": org_id(sim), "meta": _meta(sim, "Organization", 1, sim.start), "identifier": ident, "active": True,
            "type": [_cc([_code("http://terminology.hl7.org/CodeSystem/organization-type", "prov", "Healthcare Provider")])], "name": s.get("name_local") or s["name"],
            **({"alias": [s["name"]]} if s.get("name_local") else {})}


# ------------------------------------------------------------------ Location (병동/병실/병상)
def loc_id(sim, kind: str, key) -> str:
    return sim.fid("Location", f"{kind}:{key}")


def location(sim, kind: str, key) -> dict:
    fl = sim.site["flavor"]
    if kind == "ward":
        w = sim.wards[key]
        name, pt, part = w["name"], ("wa", "Ward"), None
        ident = w["code"]
        status = None
    elif kind == "room":
        w_idx, room = key
        w = sim.wards[w_idx]
        name, pt, part, ident, status = f"{w['code']} {room}", ("ro", "Room"), loc_id(sim, "ward", w_idx), f"{w['code']}-{room}", None
    else:
        b = sim.beds[key]
        w = sim.wards[b["ward"]]
        name, pt, part, ident = f"{w['code']} {b['room']}-{b['bed']}", ("bd", "Bed"), loc_id(sim, "room", (b["ward"], b["room"])), f"{w['code']}-{b['room']}-{b['bed']}"
        occ = sim.bed_occ[key] is not None
        status = _code("http://terminology.hl7.org/CodeSystem/v2-0116", "O" if occ else "U", "Occupied" if occ else "Unoccupied")
    res = {"resourceType": "Location", "id": loc_id(sim, kind, key), "meta": _meta(sim, "Location", 1, sim.start),
           "identifier": [{"system": f"https://fhir.{_local_domain(sim)}/location", "value": ident}], "status": "active",
           **({"operationalStatus": status} if status else {}), "name": name, "mode": "instance",
           "physicalType": _cc([_code("http://terminology.hl7.org/CodeSystem/location-physical-type" if not stu3(sim) else "http://hl7.org/fhir/location-physical-type", pt[0], pt[1])]),
           "managingOrganization": {"reference": "Organization/" + org_id(sim)}}
    if part:
        res["partOf"] = {"reference": "Location/" + part}
    if fl == "isik" and kind == "bed":
        res["meta"]["profile"] = [ISIK + "ISiKStandortBettenstellplatz"]
    elif fl == "isik" and kind == "room":
        res["meta"]["profile"] = [ISIK + "ISiKStandortRaum"]
    elif fl == "isik":
        res["meta"]["profile"] = [ISIK + "ISiKStandort"]
    return res


# ------------------------------------------------------------------ Practitioner
def practitioner(sim, s: dict) -> dict:
    fl = sim.site["flavor"]
    i = s["ids"]
    ident = []
    if "npi" in i:
        ident.append({"system": "http://hl7.org/fhir/sid/us-npi", "value": i["npi"]})
    if "gmc" in i:
        ident.append({"system": "https://fhir.hl7.org.uk/Id/gmc-number", "value": i["gmc"]})
        ident.append({"system": "https://fhir.hl7.org.uk/Id/consultant-code", "value": i["consultant"]})
    if "lanr" in i:
        ident.append({"type": _cc([_code(V2_0203, "LANR")]), "system": "https://fhir.kbv.de/NamingSystem/KBV_NS_Base_ANR", "value": i["lanr"]})
    if "hpii" in i:
        ident.append({"type": _cc([_code(V2_0203, "NPI", "National provider identifier")], "HPI-I"), "system": "http://ns.electronichealth.net.au/id/hi/hpii/1.0", "value": i["hpii"]})
        ident.append({"type": _cc([_code(V2_0203, "AHPRA")]), "system": "http://hl7.org.au/id/ahpra-registration-number", "value": i["ahpra"]})
    if "ikiseki" in i:
        ident.append({"system": "urn:oid:1.2.392.100495.20.3.31", "value": i["ikiseki"]})
    if "license" in i:
        ident.append({"system": "http://www.hl7korea.or.kr/Identifier/mohw-license-md", "value": i["license"]})
    if "agb" in i:
        ident.append({"system": "http://fhir.nl/fhir/NamingSystem/agb-z", "value": i["agb"]})
        ident.append({"system": "http://fhir.nl/fhir/NamingSystem/big", "value": i["big"]})
    if "crm" in i:
        ident.append({"system": "http://rnds.saude.gov.br/fhir/r4/NamingSystem/cns", "value": i["cns"]})
        ident.append({"system": "http://www.saude.gov.br/fhir/r4/NamingSystem/crm-sp", "value": i["crm"]})
    if "mcr" in i:
        ident.append({"system": "https://ns.health.example.sg/id/mcr", "value": i["mcr"]})
    if "local" in i:
        ident.append({"system": f"https://fhir.{_local_domain(sim)}/staff", "value": i["local"]})
    nm = names(sim, s)[0] if fl not in ("jp-core", "nl-zib") else ({"use": "official", "text": s["text"], "family": s["family"], "given": s["given"]} if fl == "jp-core" else
                                                                 {"use": "official", "family": s["family"], "given": s["given"]})
    if fl in ("epic", "oracle", "uk-core", "au-core", "sg"):
        nm = {**nm, "prefix": ["Dr"] if fl != "epic" else [], "suffix": ["MD"] if fl in ("epic", "oracle") else []}
        nm = {k: v for k, v in nm.items() if v != []}
    dept = sim.dept(s["dept"])
    return {"resourceType": "Practitioner", "id": s["fhir_id"], "meta": _meta(sim, "Practitioner", 1, sim.start), "identifier": ident, "active": True, "name": [nm],
            "gender": "male" if s["sex"] == "M" else "female",
            "qualification": [{"code": _cc([], dept[1])}]}


# ------------------------------------------------------------------ Encounter / Condition / Allergy
def cond_coding(sim, key: str) -> dict:
    c = CONDITIONS[key]
    fl, lang = sim.site["flavor"], sim.site["lang"]
    text = c["names"].get(lang, c["names"]["en"]) if fl not in ("kr-core",) else c["names"]["ko"]
    if fl in ("epic", "oracle"):
        cod = [_code("http://hl7.org/fhir/sid/icd-10-cm", c["icd10cm"], c["names"]["en"]), _code(SCT, c["snomed"], c["names"]["en"])]
    elif fl == "jp-core":
        cod = [_code("http://medis.or.jp/CodeSystem/master-disease-keyNumber", c["medis"], text), _code("http://hl7.org/fhir/sid/icd-10", c["icd10"], text)]
    elif fl == "kr-core":
        cod = [_code("http://www.hl7korea.or.kr/CodeSystem/kostat-kcd-8", c["icd10"], text), _code(SCT, c["snomed"], c["names"]["en"])]
    elif fl == "isik":
        cod = [{"system": "http://fhir.de/CodeSystem/bfarm/icd-10-gm", "version": "2026", "code": c["icd10"], "display": text,
                "extension": [{"url": "http://fhir.de/StructureDefinition/icd-10-gm-diagnosesicherheit", "valueCoding": _code("https://fhir.kbv.de/CodeSystem/KBV_CS_SFHIR_ICD_DIAGNOSESICHERHEIT", "G", "gesicherte Diagnose")}]}]
    elif fl == "br-rnds":
        cod = [_code("http://www.saude.gov.br/fhir/r4/CodeSystem/BRCID10", c["icd10"].replace(".", ""), text)]
    elif fl == "sg":
        cod = [_code(SCT, c["snomed"], c["names"]["en"]), _code("http://hl7.org/fhir/sid/icd-10-am", c["icd10"], c["names"]["en"])]
    else:
        cod = [_code(SCT, c["snomed"], c["names"]["en"]), _code("http://hl7.org/fhir/sid/icd-10", c["icd10"], text)]
    return _cc(cod, text)


def condition(sim, e: dict, key: str | None = None, n: int = 0) -> dict:
    key = key or e["cond"]
    p = sim.people[e["person"]]
    cid = sim.fid("Condition", f"{e['no']}:{n}")
    res = {"resourceType": "Condition", "id": cid, "meta": _meta(sim, "Condition", 1, e["admit"])}
    if stu3(sim):
        res.update(clinicalStatus="active" if e["status"] == "in-progress" else "resolved", verificationStatus="confirmed" if e["status"] != "cancelled" else "entered-in-error",
                   category=[_cc([_code("http://hl7.org/fhir/condition-category", "encounter-diagnosis", "Encounter Diagnosis")])],
                   code=cond_coding(sim, key), subject={"reference": "Patient/" + p["fhir_id"]}, context={"reference": "Encounter/" + e["fhir_id"]},
                   onsetDateTime=sim.iso(e["admit"] - 3600 * 6), assertedDate=sim.iso(e["admit"]))
        return res
    res.update(clinicalStatus=_cc([_code("http://terminology.hl7.org/CodeSystem/condition-clinical", "active" if e["status"] == "in-progress" else "resolved")]),
               verificationStatus=_cc([_code("http://terminology.hl7.org/CodeSystem/condition-ver-status", "confirmed" if e["status"] != "cancelled" else "entered-in-error")]),
               category=[_cc([_code("http://terminology.hl7.org/CodeSystem/condition-category", "encounter-diagnosis", "Encounter Diagnosis")])],
               code=cond_coding(sim, key), subject={"reference": "Patient/" + p["fhir_id"]}, encounter={"reference": "Encounter/" + e["fhir_id"]},
               onsetDateTime=sim.iso(e["admit"] - 3600 * 6), recordedDate=sim.iso(e["admit"]))
    return res


def encounter_conditions(sim, e: dict) -> list[dict]:
    return [condition(sim, e)] + [condition(sim, e, k, i + 1) for i, k in enumerate(e["comorbid"])]


def allergy(sim, p: dict) -> dict:
    key, sct, rx, nm = ALLERGY_BY_KEY[p["allergy"]]
    lang = sim.site["lang"] if sim.site["flavor"] != "kr-core" else "ko"
    text = nm.get(lang, nm["en"])
    code = [_code(SCT, sct, nm["en"])] + ([_code("http://www.nlm.nih.gov/research/umls/rxnorm", rx, nm["en"])] if rx and sim.site["flavor"] in ("epic", "oracle") else [])
    cat = {"penicillin": "medication", "aspirin": "medication", "contrast": "medication", "shellfish": "food", "latex": "environment"}.get(key)
    res = {"resourceType": "AllergyIntolerance", "id": sim.fid("AllergyIntolerance", p["idx"]), "meta": _meta(sim, "AllergyIntolerance", 1, p["updated"])}
    if stu3(sim):
        res.update(clinicalStatus="active", verificationStatus="confirmed", **({"category": [cat]} if cat else {}), code=_cc(code, text), patient={"reference": "Patient/" + p["fhir_id"]})
    else:
        res.update(clinicalStatus=_cc([_code("http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "active")]),
                   verificationStatus=_cc([_code("http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", "confirmed")]),
                   **({"category": [cat], "criticality": "high" if key in ("penicillin", "contrast") else "low"} if cat else {}),
                   code=_cc(code, text), patient={"reference": "Patient/" + p["fhir_id"]}, recordedDate=sim.iso(p["updated"]))
    return res


def encounter(sim, e: dict) -> dict:
    fl = sim.site["flavor"]
    p = sim.people[e["person"]]
    w, b = sim.bed_path(e["bed"])
    status = {"in-progress": "in-progress", "finished": "finished", "cancelled": "cancelled"}[e["status"]]
    cls = ("AMB", "ambulatory") if sim.site.get("outpatient") else ("IMP", "inpatient encounter")
    dept = sim.dept(CONDITIONS[e["cond"]]["dept"])
    res = {"resourceType": "Encounter", "id": e["fhir_id"], "meta": _meta(sim, "Encounter", 1 + len(e["transfers"]) + (e["status"] != "in-progress"), e["updated"]),
           "identifier": [{"use": "usual" if fl != "isik" else "official", "type": _cc([_code(V2_0203, "VN", "Visit number")]),
                           "system": {"epic": f"urn:oid:{sim.site.get('oid')}.5.737384.8", "oracle": "https://fhir.cerner.com/" + sim.site.get("tenant", "") + "/encounter-fin"}.get(fl, f"https://fhir.{_local_domain(sim)}/encounter"),
                           "value": e["visit"]}],
           "status": status,
           "class": _code("http://terminology.hl7.org/CodeSystem/v3-ActCode" if not stu3(sim) else "http://hl7.org/fhir/v3/ActCode", *cls)}
    if fl == "isik":
        res["type"] = [_cc([_code("http://fhir.de/CodeSystem/Kontaktebene", "abteilungskontakt", "Abteilungskontakt")]),
                       _cc([_code("http://fhir.de/CodeSystem/kontaktart-de", "normalstationaer", "Normalstationär")])]
        res["serviceType"] = _cc([_code("http://fhir.de/CodeSystem/dkgev/Fachabteilungsschluessel", dept[0], dept[1])])
    else:
        res["serviceType"] = _cc([_code(f"https://fhir.{_local_domain(sim)}/department", dept[0], dept[1])], dept[1])
    res["subject"] = {"reference": "Patient/" + p["fhir_id"], "display": p["text"]}
    doc = sim.staff[e["attending"]]
    res["participant"] = [{"type": [_cc([_code("http://terminology.hl7.org/CodeSystem/v3-ParticipationType" if not stu3(sim) else "http://hl7.org/fhir/v3/ParticipationType", "ATND", "attender")])],
                           "individual": {"reference": "Practitioner/" + doc["fhir_id"], "display": doc["text"]}}]
    res["period"] = {"start": sim.iso(e["admit"]), **({"end": sim.iso(e["end"])} if e["end"] else {})}
    conds = encounter_conditions(sim, e)
    if stu3(sim):
        res["diagnosis"] = [{"condition": {"reference": "Condition/" + c["id"]}, "role": _cc([_code("http://hl7.org/fhir/diagnosis-role", "AD" if i == 0 else "CM")]), "rank": i + 1} for i, c in enumerate(conds)]
    else:
        res["diagnosis"] = [{"condition": {"reference": "Condition/" + c["id"]}, "use": _cc([_code("http://terminology.hl7.org/CodeSystem/diagnosis-role", "AD" if i == 0 else "CM")]), "rank": i + 1}
                            for i, c in enumerate(conds)]
    hosp = {"admitSource": _cc([_code("http://terminology.hl7.org/CodeSystem/admit-source" if not stu3(sim) else "http://hl7.org/fhir/admit-source",
                                      {"emergency": "emd", "referral": "gp", "elective": "other"}[e["source"]])])}
    if e.get("disposition"):
        hosp["dischargeDisposition"] = _cc([_code("http://terminology.hl7.org/CodeSystem/discharge-disposition" if not stu3(sim) else "http://hl7.org/fhir/discharge-disposition", e["disposition"])])
    res["hospitalization"] = hosp
    locs = []
    seg_start = e["admit"]
    beds = [tr["from"] for tr in e["transfers"]] + [e["bed"]]
    ends = [tr["t"] for tr in e["transfers"]] + [e["end"]]
    for bi, (bed, end) in enumerate(zip(beds, ends)):
        ww, bb = sim.bed_path(bed)
        locs.append({"location": {"reference": "Location/" + loc_id(sim, "bed", bed), "display": f"{ww['name']} {bb['room']}-{bb['bed']}"},
                     "status": "active" if end is None and e["status"] == "in-progress" else "completed",
                     **({"physicalType": _cc([_code("http://terminology.hl7.org/CodeSystem/location-physical-type", "bd", "Bed")])} if not stu3(sim) else {}),
                     "period": {"start": sim.iso(seg_start), **({"end": sim.iso(end)} if end else {})}})
        seg_start = end or seg_start
    res["location"] = locs
    res["serviceProvider"] = {"reference": "Organization/" + org_id(sim)}
    return res


# ------------------------------------------------------------------ Observation (바이탈)
def _cat(sim) -> list[dict]:
    sys = "http://terminology.hl7.org/CodeSystem/observation-category" if not stu3(sim) else "http://hl7.org/fhir/observation-category"
    return [_cc([_code(sys, "vital-signs", "Vital Signs")], "Vital Signs")]


def _qty(sim, kind: str, value: float) -> dict:
    v = VITALS[kind]
    if kind == "temp" and sim.site.get("units") == "us":
        return {"value": c_to_f(value), "unit": "degF", "system": UCUM, "code": "[degF]"}
    unit = {"hr": "beats/minute" if sim.site["flavor"] in ("epic", "oracle") else "/min", "rr": "breaths/minute" if sim.site["flavor"] in ("epic", "oracle") else "/min",
            "spo2": "%", "temp": "Cel" if sim.site["flavor"] not in ("jp-core", "kr-core") else "℃", "sbp": "mmHg", "dbp": "mmHg"}[kind]
    return {"value": value, "unit": unit, "system": UCUM, "code": v["ucum"]}


def _obs_code(sim, kind: str) -> dict:
    v = VITALS[kind]
    cod = [_code(LOINC, v["loinc"], v["display"])]
    if kind == "spo2" and sim.site["flavor"] in ("epic", "oracle", "au-core"):
        cod.append(_code(LOINC, "2708-6", "Oxygen saturation in Arterial blood"))
    if sim.site["flavor"] == "kr-core":
        cod.append(_code(f"urn:oid:1.2.410.100110.10.{sim.site['hosp_code']}.vs", v["kr"][0], v["kr"][1]))
    if sim.site["flavor"] == "isik":
        cod.append(_code(SCT, {"hr": "364075005", "rr": "86290005", "spo2": "442476006", "temp": "386725007", "sbp": "271649006", "dbp": "271650006"}[kind]))
    text = {"kr-core": v["kr"][1], "jp-core": v["jp"][1]}.get(sim.site["flavor"], v["display"])
    return _cc(cod, text)


def vital_resources(sim, rec: dict) -> list[dict]:
    """간호 바이탈 1세트 → Observation 5개(HR, RR, SpO2, Temp, BP 패널)."""
    p = sim.people[rec["person"]]
    e = sim.encounters[rec["enc"]]
    nurse = sim.staff[rec["performer"]]
    out = []
    for kind in ("hr", "rr", "spo2", "temp", "bp"):
        oid = sim.fid("Observation", f"{rec['id_base']}:{kind}")
        res = {"resourceType": "Observation", "id": oid, "meta": _meta(sim, kind, 1, rec["t"] + 120), "status": "final", "category": _cat(sim)}
        if kind == "bp":
            res["code"] = _cc([_code(LOINC, BP_PANEL[0], BP_PANEL[1])], "Blood pressure" if sim.site["lang"] == "en" else {"ko": "혈압", "ja": "血圧"}.get(sim.site["lang"], "Blood pressure"))
        else:
            res["code"] = _obs_code(sim, kind)
        res["subject"] = {"reference": "Patient/" + p["fhir_id"]}
        res["context" if stu3(sim) else "encounter"] = {"reference": "Encounter/" + e["fhir_id"]}
        res["effectiveDateTime"] = sim.iso(rec["t"])
        res["issued"] = dt.datetime.fromtimestamp(rec["t"] + 120, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        res["performer"] = [{"reference": "Practitioner/" + nurse["fhir_id"]}]
        if kind == "bp":
            res["component"] = [{"code": _obs_code(sim, "sbp"), "valueQuantity": _qty(sim, "sbp", rec["values"]["sbp"])},
                                {"code": _obs_code(sim, "dbp"), "valueQuantity": _qty(sim, "dbp", rec["values"]["dbp"])}]
        else:
            res["valueQuantity"] = _qty(sim, kind, rec["values"][kind])
        res["meta"]["profile"] = [profiles(sim)[kind]]
        res["_kind"] = kind
        out.append(res)
    return out


# ------------------------------------------------------------------ 검색 파라미터
def parse_token(v: str) -> tuple[str | None, str]:
    if "|" in v:
        s, c = v.split("|", 1)
        return (s or None), c
    return None, v


_PREFIX = re.compile(r"^(eq|ne|gt|lt|ge|le|sa|eb|ap)?(.+)$")


def parse_date(v: str, tz) -> tuple[str, float, float]:
    m = _PREFIX.match(v)
    pre, val = (m.group(1) or "eq"), m.group(2)
    try:
        if re.fullmatch(r"\d{4}", val):
            a = dt.datetime(int(val), 1, 1, tzinfo=tz); b = dt.datetime(int(val) + 1, 1, 1, tzinfo=tz)
        elif re.fullmatch(r"\d{4}-\d{2}", val):
            y, mo = map(int, val.split("-")); a = dt.datetime(y, mo, 1, tzinfo=tz); b = (a + dt.timedelta(days=32)).replace(day=1)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            a = dt.datetime.fromisoformat(val).replace(tzinfo=tz); b = a + dt.timedelta(days=1)
        else:
            a = dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
            if a.tzinfo is None:
                a = a.replace(tzinfo=tz)
            b = a + dt.timedelta(seconds=1)
    except ValueError:
        raise FhirError(400, "invalid", f"Invalid date parameter value '{v}'")
    return pre, a.timestamp(), b.timestamp()


def date_match(t: float, conds: list[tuple[str, float, float]]) -> bool:
    for pre, a, b in conds:
        ok = {"eq": a <= t < b, "ne": not (a <= t < b), "gt": t >= b, "lt": t < a, "ge": t >= a, "le": t < b, "sa": t >= b, "eb": t < a, "ap": a - 86400 <= t < b + 86400}[pre]
        if not ok:
            return False
    return True


# ------------------------------------------------------------------ 수신 검증
TZ_RE = re.compile(r"T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def resolve_patient_ref(sim, ref: dict | None, expr: str) -> int:
    if not ref:
        raise FhirError(422, "required", f"{expr} is required", expr)
    if ref.get("reference"):
        m = re.search(r"(?:^|/)Patient/([A-Za-z0-9\-\.]{1,64})(?:/_history/\d+)?$", ref["reference"])
        if not m:
            raise FhirError(422, "invalid", f"{expr}.reference must be a Patient reference, got '{ref['reference']}'", expr)
        pidx = sim.find_person(m.group(1))
        if pidx is None or sim.people[pidx]["fhir_id"] != m.group(1):
            raise FhirError(422, "not-found", f"Resource Patient/{m.group(1)} is not known", expr)
        return pidx
    ident = ref.get("identifier") or {}
    if ident.get("value"):
        pidx = sim.find_person(ident["value"])
        if pidx is None:
            raise FhirError(422, "not-found", f"No Patient with identifier {ident.get('system', '')}|{ident['value']}", expr + ".identifier")
        return pidx
    raise FhirError(422, "required", f"{expr} must have reference or identifier", expr)


def _extract_quantity(sim, kind: str, q: dict | None, expr: str) -> tuple[float, str, float]:
    if not isinstance(q, dict) or "value" not in q:
        raise FhirError(422, "required", f"{expr}.valueQuantity.value is required for {VITALS[kind]['display']}", expr)
    try:
        val = float(q["value"])
    except (TypeError, ValueError):
        raise FhirError(400, "value", f"{expr}.valueQuantity.value is not a number: {q.get('value')!r}", expr)
    if q.get("system") and q["system"] != UCUM:
        raise FhirError(422, "code-invalid", f"{expr}.valueQuantity.system must be {UCUM}", expr)
    std, err = normalize_unit(kind, val, q.get("code") or q.get("unit"))
    if err:
        raise FhirError(422, "code-invalid", f"{expr}: {err}", expr)
    if not in_range(kind, std):
        raise FhirError(422, "value", f"{expr}: {VITALS[kind]['display']} value {val} {q.get('code') or q.get('unit') or ''} is outside the plausible range {VITALS[kind]['range']}", expr)
    return std, (q.get("code") or q.get("unit") or ""), val


def validate_observation(sim, res: dict) -> tuple[int, int | None, float, list[tuple[str, float, str, float]], list]:
    """(person, enc_no, t, [(kind, value_std, unit_in, value_in)], warnings)."""
    warnings = []
    if not isinstance(res, dict) or res.get("resourceType") != "Observation":
        raise FhirError(400, "structure", "Body must be an Observation resource")
    if res.get("status") not in ("final", "amended", "corrected", "preliminary"):
        raise FhirError(422, "code-invalid", f"Observation.status '{res.get('status')}' is not accepted (final|amended|corrected|preliminary)", "Observation.status")
    cats = [c.get("code") for cc in res.get("category") or [] for c in cc.get("coding") or []]
    if "vital-signs" not in cats:
        raise FhirError(422, "required", "Observation.category must include vital-signs (" + profiles(sim)["hr"] + ")", "Observation.category")
    codings = (res.get("code") or {}).get("coding") or []
    loincs = [c.get("code") for c in codings if c.get("system") == LOINC]
    if not loincs:
        raise FhirError(422, "required", "Observation.code must contain a LOINC coding (http://loinc.org)", "Observation.code")
    pidx = resolve_patient_ref(sim, res.get("subject"), "Observation.subject")
    enc_no = None
    eref = res.get("context" if stu3(sim) else "encounter")
    if stu3(sim) and res.get("encounter"):
        raise FhirError(400, "structure", "Unknown element 'encounter' in STU3 Observation (use 'context')", "Observation.encounter")
    if eref and eref.get("reference"):
        eid = eref["reference"].rsplit("/", 1)[-1]
        e = next((x for x in list(sim.encounters.values()) if x["fhir_id"] == eid), None)
        if e is None:
            raise FhirError(422, "not-found", f"Resource Encounter/{eid} is not known", "Observation.encounter")
        if e["person"] != pidx:
            raise FhirError(422, "business-rule", "Observation.encounter does not belong to Observation.subject", "Observation.encounter")
        enc_no = e["no"]
    else:
        act = sim.active_by_person.get(pidx)
        enc_no = act
        if act is None:
            warnings.append(("warning", "business-rule", "Patient has no active encounter; observation filed to the chart without encounter", "Observation.encounter"))
    eff = res.get("effectiveDateTime") or (res.get("effectivePeriod") or {}).get("start") or res.get("effectiveInstant")
    if not eff:
        raise FhirError(422, "required", "Observation.effective[x] is required for vital signs", "Observation.effective")
    if not TZ_RE.search(eff):
        raise FhirError(400, "value", f"Observation.effectiveDateTime '{eff}' must include a time zone when a time is given", "Observation.effectiveDateTime")
    t = dt.datetime.fromisoformat(eff.replace("Z", "+00:00")).timestamp()
    if t > time.time() + 300:
        raise FhirError(422, "business-rule", f"Observation.effectiveDateTime {eff} is in the future", "Observation.effectiveDateTime")
    values = []
    if BP_PANEL[0] in loincs or "55284-4" in loincs:
        for comp in res.get("component") or []:
            ccodes = [c.get("code") for c in (comp.get("code") or {}).get("coding") or [] if c.get("system") == LOINC]
            kind = next((BY_LOINC[c] for c in ccodes if BY_LOINC.get(c) in ("sbp", "dbp")), None)
            if kind:
                std, u, raw = _extract_quantity(sim, kind, comp.get("valueQuantity"), f"Observation.component[{kind}]")
                values.append((kind, std, u, raw))
        if {v[0] for v in values} != {"sbp", "dbp"}:
            raise FhirError(422, "required", "Blood pressure panel needs components 8480-6 (systolic) and 8462-4 (diastolic)", "Observation.component")
    else:
        kind = next((BY_LOINC[c] for c in loincs if c in BY_LOINC), None)
        if kind is None:
            raise FhirError(422, "code-invalid", f"LOINC code(s) {loincs} are not a supported vital sign (8867-4, 9279-1, 59408-5, 2708-6, 8310-5, 85354-9)", "Observation.code")
        std, u, raw = _extract_quantity(sim, kind, res.get("valueQuantity"), "Observation")
        values.append((kind, std, u, raw))
    return pidx, enc_no, t, values, warnings


# ------------------------------------------------------------------ Bundle / Capability
def bundle(sim, entries: list[dict], base: str, total: int | None, self_url: str, next_url: str | None, btype: str = "searchset", include_ids: set | None = None) -> dict:
    b = {"resourceType": "Bundle", "id": sim.fid("Bundle", time.time_ns()), "meta": {"lastUpdated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")},
         "type": btype}
    if total is not None:
        b["total"] = total
    b["link"] = [{"relation": "self", "url": self_url}] + ([{"relation": "next", "url": next_url}] if next_url else [])
    b["entry"] = [{"fullUrl": f"{base}/{r['resourceType']}/{r['id']}", "resource": _clean(r),
                   "search": {"mode": "include" if include_ids and (r["resourceType"], r["id"]) in include_ids else "match"}} for r in entries]
    return b


INTERNAL = ("_kind", "_person", "_enc")


def _clean(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in INTERNAL}


def capability(sim, base: str) -> dict:
    s = sim.site
    rest_res = []
    types = {"Patient": ["_id", "identifier", "family", "given", "name", "birthdate", "gender"], "Encounter": ["_id", "patient", "subject", "status", "date", "location", "identifier", "_lastUpdated"],
             "Observation": ["patient", "subject", "category", "code", "date", "encounter", "_lastUpdated"], "Condition": ["patient", "encounter", "category", "clinical-status"],
             "AllergyIntolerance": ["patient"], "Practitioner": ["_id", "identifier", "name"], "Location": ["_id", "name", "type", "partof"], "Organization": ["_id"], "Group": ["_id"]}
    for t, params in types.items():
        inter = [{"code": "read"}, {"code": "search-type"}] + ([{"code": "create"}] if t == "Observation" else [])
        rest_res.append({"type": t, **({"supportedProfile": [profiles(sim)[t]]} if t in profiles(sim) and not stu3(sim) else {}), "interaction": inter,
                         "searchParam": [{"name": n, "type": "token" if n in ("_id", "identifier", "gender", "status", "category", "code", "clinical-status", "type") else
                                          "date" if n in ("birthdate", "date", "_lastUpdated") else "reference" if n in ("patient", "subject", "encounter", "location", "partof") else "string"}
                                         for n in params]})
    sec = {"cors": True, "service": [_cc([_code("http://terminology.hl7.org/CodeSystem/restful-security-service" if not stu3(sim) else "http://hl7.org/fhir/restful-security-service",
                                                  "SMART-on-FHIR" if s["auth"]["type"] not in ("basic", "api-key", "bearer-static", "rnds-token") else "Basic" if s["auth"]["type"] == "basic" else "OAuth")])]}
    cs = {"resourceType": "CapabilityStatement", "status": "active", "date": sim.iso(sim.start)[:10], "publisher": s["name"], "kind": "instance",
          "software": {"name": f"biosim EMR emulator ({s['flavor']})", "version": "1.0"},
          "implementation": {"description": f"{s['name']} — {s['style']}", "url": base},
          "fhirVersion": s["fhir_version"], "format": ["application/fhir+json", "json"],
          "rest": [{"mode": "server", "security": sec, "resource": rest_res, "interaction": [{"code": "transaction"}, {"code": "batch"}]}]}
    if stu3(sim):
        cs["acceptUnknown"] = "no"
    return cs


def page_token(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o={offset}".encode()).decode().rstrip("=")


def page_offset(tok: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)).decode().split("=", 1)[1])
    except Exception:
        raise FhirError(400, "invalid", "Invalid or expired page context")
