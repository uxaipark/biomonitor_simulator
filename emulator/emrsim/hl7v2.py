"""HL7 v2 (2.3 / 2.4 / 2.5 / 2.5.1) ADT 생성, ORU^R01 수신 검증, ACK 생성, MLLP 프레이밍.

계열(flavor)별 차이
  meditech  2.5.1  대문자 이름, PID-18 계정번호, NPI 주치의, ICD-10-CM(I10C), IHE PCD-01(ORU, 2.6) 수신 허용
  uk-itk    2.4    PID-3 NHS number(NH)+병원번호, PD1 GP 진료소(ODS), 병동^베이^병상, ICD-10(I10)
  ss-mix2   2.5    ISO-2022-JP(MSH-18 ~ISO IR87), 한자/가나 이름 반복(I/P), 患者ID(PI), MEDIS 病名(MDCDX2), 로컬 바이탈 코드
  pam-fr    2.5    ISO 8859-1, IPP(PI)+INS(NIR), 출생성(L)/사용성(D), ZBE 이동 세그먼트, PID-32 신원 신뢰도, RPPS 의사번호
  ca-v23    2.3    MSH-9 두 구성요소, OHIP 건강카드(HC, CX-2 버전코드), ERR 구버전 형식, 시간대 없는 TS
  ae-malaffi 2.5.1 UTF-8, 영문/아랍 이름 반복, Emirates ID, PID-28 국적, ICD-10-CM
"""
from __future__ import annotations

import datetime as dt
import re
import time

from .clinical import CONDITIONS, ALLERGY_BY_KEY, VITALS, BY_LOINC, BY_MDC, BY_JP, c_to_f, normalize_unit, in_range

SB, EB, CR = b"\x0b", b"\x1c", b"\x0d"
ACCEPT_VERSIONS = {"2.3": {"2.3", "2.3.1"}, "2.4": {"2.4"}, "2.5": {"2.5", "2.5.1"}, "2.5.1": {"2.5.1", "2.6"}}


def charset(sim) -> str:
    return sim.site.get("charset") or "utf-8"


def frame(msg: str, enc: str) -> bytes:
    return SB + msg.encode(enc, errors="replace") + EB + CR


def esc(v) -> str:
    if v is None:
        return ""
    s = str(v)
    return s.replace("\\", "\\E\\").replace("|", "\\F\\").replace("^", "\\S\\").replace("~", "\\R\\").replace("&", "\\T\\")


def unesc(v: str) -> str:
    return v.replace("\\F\\", "|").replace("\\S\\", "^").replace("\\R\\", "~").replace("\\T\\", "&").replace("\\E\\", "\\")


def ts(sim, t: float, secs: bool = True) -> str:
    d = sim.local(t)
    s = d.strftime("%Y%m%d%H%M%S" if secs else "%Y%m%d%H%M")
    fl = sim.site["flavor"]
    if fl in ("ca-v23", "ss-mix2"):
        return s                                  # 구형/SS-MIX2: 현지 시각, 오프셋 없음
    return s + d.strftime("%z")


def seg(name: str, fields: dict[int, str], n: int | None = None) -> str:
    n = n or (max(fields) if fields else 0)
    return name + "|" + "|".join(fields.get(i, "") for i in range(1, n + 1))


class Ctl:
    """메시지 제어번호."""
    def __init__(self):
        self.n = 0

    def next(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{int(time.time()) % 100000000:08d}{self.n % 10000:04d}"


CTL = Ctl()


def msh(sim, mtype: str, trigger: str, t: float, ctrl: str, rcv_app: str = "BIOMON", rcv_fac: str = "BIOMON") -> str:
    s, fl, ver = sim.site, sim.site["flavor"], sim.site["hl7_version"]
    struct = {"A01": "ADT_A01", "A02": "ADT_A02", "A03": "ADT_A03", "A08": "ADT_A01", "A11": "ADT_A09", "R01": "ORU_R01"}.get(trigger, "")
    if mtype == "ACK":
        struct = "ACK"
    typ = f"{mtype}^{trigger}" if ver == "2.3" else f"{mtype}^{trigger}^{struct}"
    f = {2: "^~\\&", 3: s["app"], 4: s["fac"], 5: rcv_app, 6: rcv_fac, 7: ts(sim, t), 9: typ, 10: ctrl, 11: "P", 12: ver}
    if fl == "meditech":
        f.update({15: "AL", 16: "NE"})
    elif fl == "uk-itk":
        f.update({15: "AL", 16: "NE", 17: "GBR", 18: "UNICODE UTF-8"})
    elif fl == "ss-mix2":
        f.update({18: "~ISO IR87", 20: "ISO 2022-1994"})
    elif fl == "pam-fr":
        f.update({15: "AL", 16: "NE", 17: "FRA", 18: "8859/1", 19: "FRA"})
    elif fl == "ae-malaffi":
        f.update({15: "AL", 16: "NE", 17: "ARE", 18: "UNICODE UTF-8"})
    # MSH-1 은 필드 구분자 자체라 seg() 로 만들면 한 칸 밀린다
    n = max(f)
    return "MSH|" + "|".join(f.get(i, "") for i in range(2, n + 1))


def _name(sim, p: dict) -> str:
    fl = sim.site["flavor"]
    if fl == "ss-mix2":
        return f"{esc(p['family'])}^{esc(p['given'][0])}^^^^^L^I~{esc(p['kana_family'])}^{esc(p['kana_given'])}^^^^^L^P"
    if fl == "pam-fr":
        birth = f"{esc(p['birth_family'].upper())}^{esc(p['given'][0].upper())}^^^{'M' if p['sex'] == 'M' else 'MME'}^^L"
        if p["birth_family"] != p["family"]:
            return birth + f"~{esc(p['family'].upper())}^{esc(p['given'][0].upper())}^^^MME^^D"
        return birth
    if fl == "ae-malaffi":
        latin = f"{esc(p['family'].upper())}^{esc(p['given'][0].upper())}^{esc(p['given'][1].upper()) if len(p['given']) > 1 else ''}^^^^L"
        if p.get("arabic_family"):
            return latin + f"~{esc(p['arabic_family'])}^{esc(p['arabic_given'][0])}^{esc(p['arabic_given'][1])}^^^^L"
        return latin
    if fl == "uk-itk":
        return f"{esc(p['family'].upper())}^{esc(p['given'][0].upper())}^{esc(p['given'][1].upper()) if len(p['given']) > 1 else ''}^^{esc((p.get('prefix') or '').upper())}^^L"
    mid = p["given"][1] if len(p["given"]) > 1 else ""
    return f"{esc(p['family'].upper())}^{esc(p['given'][0].upper())}^{esc(mid.upper())}^^^^L"


def _pid(sim, p: dict, enc: dict | None) -> str:
    fl, s, i = sim.site["flavor"], sim.site, p["ids"]
    a = p["address"]
    f = {1: "1", 5: _name(sim, p), 7: p["birth"].replace("-", ""), 8: p["sex"]}
    if fl == "meditech":
        f[3] = f"{i['mrn']}^^^{s['fac']}^MR"
        f[10] = f"{p['race'][0]}^{esc(p['race'][1])}^CDCREC"
        f[11] = f"{esc(a['line'][0].upper())}^{esc(a['line'][1].upper()) if len(a['line']) > 1 else ''}^{esc(a['city'].upper())}^{a['state']}^{a['postal']}^USA^H"
        digits = re.sub(r"\D", "", p["phone"])
        f[13] = f"^PRN^PH^^1^{digits[:3]}^{digits[3:]}"
        f[16] = "M^Married^HL70002" if p["idx"] % 3 else "S^Single^HL70002"
        if enc:
            f[18] = f"{enc['visit']}^^^{s['fac']}^AN"
        f[22] = f"{p['ethnicity'][0]}^{esc(p['ethnicity'][1])}^CDCREC"
    elif fl == "uk-itk":
        f[3] = f"{i['nhs']}^^^NHS^NH~{i['mrn']}^^^{s['ods']}^MR"
        f[11] = f"{esc(a['line'][0].upper())}^^{esc(a['city'].upper())}^{esc(a['district'].upper())}^{a['postal']}^GBR^H"
        f[13] = f"{p['phone']}^PRN^CP"
        f[22] = f"{p['ethnic'][0]}^{esc(p['ethnic'][1])}^NHSETHNIC"
    elif fl == "ss-mix2":
        f[1] = "0001"
        f[3] = f"{i['mrn']}^^^^PI"
        f[11] = f"{esc(a['line'][0])}^^{esc(a['city'])}^{esc(a['state'])}^{a['postal'].replace('-', '')}^JPN^H"
        f[13] = f"^PRN^PH^^^^^^^^^{p['phone']}"
    elif fl == "pam-fr":
        f[3] = f"{i['mrn']}^^^{s['fac']}&1.2.250.1.71.4.2.2.{s['finess']}&ISO^PI~{i['ins']}^^^ASIP-SANTE-INS-NIR&1.2.250.1.213.1.4.8&ISO^INS"
        f[11] = f"{esc(a['line'][0])}^^{esc(a['city'])}^^{a['postal']}^FRA^H"
        f[13] = f"^PRN^CP^^^^^^^^^{p['phone']}"
        f[32] = "VALI"
    elif fl == "ca-v23":
        f[3] = f"{i['mrn']}^^^{s['fac']}^MR~{i['ohip']}^{i['ohip_vc']}^^ON^HC"
        f[11] = f"{esc(a['line'][0].upper())}^^{esc(a['city'].upper())}^ON^{a['postal']}^CAN^H"
        f[13] = p["phone"]
        f[15] = "FRE" if p.get("language") == "fr" else "ENG"
    elif fl == "ae-malaffi":
        f[3] = f"{i['mrn']}^^^{s['fac']}^MR~{i['eid'].replace('-', '')}^^^EmiratesID^NNARE"
        f[11] = f"{esc(a['line'][0])}^{esc(a['line'][1]) if len(a['line']) > 1 else ''}^{esc(a['city'])}^^^ARE^H"
        f[13] = f"^PRN^CP^^^^^^^^^{p['phone']}"
        f[28] = f"{p['nationality']}^^ISO3166"
    return seg("PID", f)


def _xcn(sim, doc: dict) -> str:
    fl, i = sim.site["flavor"], doc["ids"]
    fam, giv = esc(doc["family"].upper() if fl != "ss-mix2" else doc["family"]), esc(doc["given"][0].upper() if fl != "ss-mix2" else doc["given"][0])
    if fl == "meditech":
        return f"{i['npi']}^{fam}^{giv}^^^DR^^^NPI^L^^^NPI"
    if fl == "uk-itk":
        return f"{i['consultant']}^{fam}^{giv}^^^DR^^^NHSCONSULTANTCODE"
    if fl == "ss-mix2":
        return f"{i['local']}^{fam}^{giv}"
    if fl == "pam-fr":
        return f"{i['rpps']}^{fam}^{giv}^^^DR^^^ASIP-SANTE-PS&1.2.250.1.71.4.2.1&ISO^D^^^RPPS"
    if fl == "ca-v23":
        return f"{i['cpso']}^{fam}^{giv}^^^DR^^^CPSO"
    return f"{i['doh']}^{fam}^{giv}^^^DR^^^DOH^^^^LN"


def _pl(sim, bed_idx: int) -> str:
    w, b = sim.bed_path(bed_idx)
    fl, s = sim.site["flavor"], sim.site
    if fl == "uk-itk":
        return f"{w['code']}^{b['room']}^{b['bed']}^{s['ods']}^^^{s['ods']}01"
    if fl == "ss-mix2":
        return f"{w['code']}^{b['room']}^{b['bed']}"
    if fl == "pam-fr":
        return f"{w['code']}^{b['room']}^{b['bed']}^{s['fac']}^^^^{w['floor']}"
    return f"{w['code']}^{b['room']}^{b['bed']}^{s['fac']}"


def _pv1(sim, enc: dict, code: str, prior: int | None = None) -> str:
    fl, s = sim.site["flavor"], sim.site
    dept = sim.dept(CONDITIONS[enc["cond"]]["dept"])
    f = {1: "1" if fl != "ss-mix2" else "0001", 2: "I", 3: _pl(sim, enc["bed"]), 7: _xcn(sim, sim.staff[enc["attending"]]), 10: dept[0],
         14: {"emergency": "7", "referral": "1", "elective": "2"}[enc["source"]], 19: f"{enc['visit']}^^^{s['fac']}^VN", 44: ts(sim, enc["admit"])}
    if prior is not None:
        f[6] = _pl(sim, prior)
    if code == "A03" and enc["end"]:
        f[45] = ts(sim, enc["end"])
        f[36] = {"home": "01", "home-health": "06", "snf": "03", "other-hcf": "05", "exp": "20"}.get(enc.get("disposition"), "01")
    if fl == "pam-fr":
        f[2] = "I"
        f[4] = "R" if enc["source"] == "emergency" else "U"          # PAM FR: mode d'entrée
    if fl == "uk-itk":
        f[18] = "NHS"
    return seg("PV1", f)


def _zbe(sim, enc: dict, code: str, t: float) -> str:
    """IHE PAM FR: 이동(mouvement) 세그먼트."""
    w, _ = sim.bed_path(enc["bed"])
    action = {"A11": "CANCEL", "A08": "UPDATE"}.get(code, "INSERT")
    orig = "A01" if code == "A11" else ""
    return seg("ZBE", {1: f"MVT{enc['no']:06d}{len(enc['transfers'])}^{sim.site['fac']}", 2: ts(sim, t), 4: action, 5: "N", 6: orig,
                       7: f"^^^^^^UF^^^{w['code']}", 8: f"^^^^^^UF^^^{w['code']}", 9: "HMS"})


def _dg1(sim, enc: dict) -> list[str]:
    fl = sim.site["flavor"]
    out = []
    for i, key in enumerate([enc["cond"]] + enc["comorbid"]):
        c = CONDITIONS[key]
        if fl == "ss-mix2":
            code = f"{c['medis']}^{esc(c['names']['ja'])}^MDCDX2^{c['icd10'].replace('.', '')}^{esc(c['names']['ja'])}^I10"
        elif fl in ("meditech", "ae-malaffi"):
            code = f"{c['icd10cm']}^{esc(c['names']['en'])}^I10C"
        elif fl == "pam-fr":
            code = f"{c['icd10'].replace('.', '')}^{esc(c['names']['fr'])}^I10"
        elif fl == "ca-v23":
            code = f"{c['icd10']}^{esc(c['names']['en'])}^I10CA"
        else:
            code = f"{c['icd10']}^{esc(c['names']['en'])}^I10"
        f = {1: str(i + 1), 3: code, 5: ts(sim, enc["admit"]), 6: "A" if i == 0 else "W"}
        if sim.site["hl7_version"] == "2.3":
            f[2] = "I10"
        out.append(seg("DG1", f))
    return out


def _al1(sim, p: dict) -> list[str]:
    key, sct, rx, nm = ALLERGY_BY_KEY[p["allergy"]]
    if key == "nka":
        return []
    lang = sim.site["lang"]
    typ = {"shellfish": "FA", "latex": "EA"}.get(key, "DA")
    return [seg("AL1", {1: "1", 2: typ, 3: f"{sct}^{esc(nm.get(lang, nm['en']))}^SCT", 4: "SV" if key in ("penicillin", "contrast") else "MO"})]


def adt(sim, ev: dict) -> str:
    """ADT 이벤트 1건 → ER7 문자열(세그먼트 구분 \\r)."""
    enc = sim.encounters[ev["enc"]]
    p = sim.people[ev["person"]]
    code = ev["code"]
    ctrl = f"{sim.site['fac'][:4]}{ev['seq']:08d}"
    segs = [msh(sim, "ADT", code, ev["t"], ctrl)]
    evn = {1: code, 2: ts(sim, ev["t"]), 6: ts(sim, ev["t"])}
    if sim.site["flavor"] == "uk-itk":
        evn[5] = "PAS^INTERFACE"
    segs.append(seg("EVN", evn))
    segs.append(_pid(sim, p, enc))
    if sim.site["flavor"] == "uk-itk":
        segs.append(seg("PD1", {3: f"THE SURGERY^^Y0{2000 + p['idx'] % 300:04d}", 4: f"G{8200000 + p['idx'] * 7:07d}^GP^{esc(sim.staff[0]['family'].upper())}^^^DR^^^NATGP"}))
    # 전동 이벤트 PV1 은 '당시' 병상으로 — 나중에 또 전동됐어도 이벤트 시점 값을 내보낸다
    bed_then = ev["bed"]
    enc_view = {**enc, "bed": bed_then}
    segs.append(_pv1(sim, enc_view, code, ev.get("prior_bed")))
    if sim.site["flavor"] == "pam-fr":
        segs.append(_zbe(sim, enc_view, code, ev["t"]))
    if code == "A01":
        segs += _al1(sim, p)
        segs += _dg1(sim, enc)
    return "\r".join(segs) + "\r"


def census_messages(sim) -> list[str]:
    """현재 재원 환자 전체를 A01 형태로 (초기 동기화용 bulk load)."""
    out = []
    for enc in sim.census():
        ev = {"seq": 0, "t": enc["admit"], "code": "A01", "enc": enc["no"], "person": enc["person"], "bed": enc["bed"], "prior_bed": None}
        out.append(adt(sim, ev))
    return out


# ------------------------------------------------------------------ 파싱
class Parsed:
    def __init__(self, text: str):
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        self.segments = [s for s in text.split("\r") if s.strip()]
        if not self.segments or not self.segments[0].startswith("MSH"):
            raise ValueError("message must start with MSH")
        m = self.segments[0]
        self.fs = m[3]
        self.cs, self.rs = m[4], m[5]
        self.sub = m[7] if len(m) > 7 and m[7] != self.fs else "&"

    def fields(self, seg: str) -> list[str]:
        f = seg.split(self.fs)
        if f[0] == "MSH":                    # MSH-1 = 구분자 자체 → 번호를 맞춘다
            f = ["MSH", self.fs] + f[1:]
        return f

    def get(self, name: str) -> list[list[str]]:
        return [self.fields(s) for s in self.segments if s.startswith(name + self.fs) or s == name]

    def comp(self, v: str, i: int) -> str:
        c = v.split(self.cs)
        return unesc(c[i - 1]) if len(c) >= i else ""


def fld(f: list[str], i: int) -> str:
    return f[i] if len(f) > i else ""


def parse_ts(sim, v: str) -> float | None:
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d+)?([+-]\d{4})?$", v or "")
    if not m:
        return None
    y, mo, d, h, mi, s, off = m.groups()
    base = dt.datetime(int(y), int(mo or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0))
    if off:
        sign = 1 if off[0] == "+" else -1
        tz = dt.timezone(sign * dt.timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        return base.replace(tzinfo=tz).timestamp()
    return base.replace(tzinfo=sim.tz).timestamp()


def peek_facility(raw: bytes) -> tuple[str, str]:
    """MLLP 라우팅용: 인코딩 모르는 상태에서 MSH-5/6(수신 앱/기관)만 ASCII 로 읽는다."""
    head = raw.split(b"\r", 1)[0].split(b"\n", 1)[0].decode("latin-1", errors="replace")
    if not head.startswith("MSH") or len(head) < 4:
        return "", ""
    f = head.split(head[3])
    return (f[4].split("^")[0] if len(f) > 4 else ""), (f[5].split("^")[0] if len(f) > 5 else "")


def decode(sim, raw: bytes) -> str:
    enc = charset(sim)
    try:
        return raw.decode(enc)
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _obs_kind(sim, p: Parsed, code_field: str) -> tuple[str | None, str]:
    """OBX-3 → kind.  1차 코드와 대체 코드(4~6 구성요소) 모두 본다."""
    for base in (1, 4):
        code, text, system = p.comp(code_field, base), p.comp(code_field, base + 1), p.comp(code_field, base + 2).upper()
        if not code and not text:
            continue
        if system in ("LN", "LOINC") and code in BY_LOINC:
            return BY_LOINC[code], f"{code}^{system}"
        if system in ("MDC",) and (code in BY_MDC or text in BY_MDC):
            return BY_MDC.get(code) or BY_MDC.get(text), f"{code}^MDC"
        if sim.site["flavor"] == "ss-mix2" and system.startswith("99") and code in BY_JP:
            return BY_JP[code], f"{code}^{system}"
    return None, code_field


def ack(sim, p: Parsed | None, code: str, text: str, errs: list[tuple[str, str, str, str]], recv_ctrl: str = "", ver_in: str | None = None) -> str:
    """errs: (위치 'PID^1^3', HL7 0357 코드, 설명, 심각도 E|W)."""
    ver = sim.site["hl7_version"]
    snd_app = snd_fac = ""
    if p:
        m = p.fields(p.segments[0])
        snd_app, snd_fac = p.comp(fld(m, 3), 1), p.comp(fld(m, 4), 1)
    hdr = msh(sim, "ACK", "R01", time.time(), CTL.next("ACK"), snd_app or "BIOMON", snd_fac or "BIOMON")
    msa = {1: code, 2: recv_ctrl}
    if ver in ("2.3", "2.4") or code != "AA":
        msa[3] = esc(text)
    segs = [hdr, seg("MSA", msa)]
    for loc, c, desc, sev in errs:
        if ver in ("2.3", "2.4"):
            segs.append(seg("ERR", {1: f"{loc.replace('^', '^')}^{c}&{esc(desc)}"}))
        else:
            segs.append(seg("ERR", {2: loc, 3: f"{c}^{esc(desc)}^HL70357", 4: sev, 8: esc(desc)}))
    return "\r".join(segs) + "\r"


ERR_TEXT = {"100": "Segment sequence error", "101": "Required field missing", "102": "Data type error", "103": "Table value not found", "200": "Unsupported message type",
            "201": "Unsupported event code", "203": "Unsupported version id", "204": "Unknown key identifier", "207": "Application internal error"}


def process_inbound(sim, raw: bytes | str, via: str) -> tuple[str, str, str, int]:
    """수신 ORU^R01 처리 → (ACK 문자열, MSA 코드, 요약, 저장 건수)."""
    text = raw if isinstance(raw, str) else decode(sim, raw)
    try:
        p = Parsed(text)
    except (ValueError, IndexError) as e:
        return ack(sim, None, "AR", f"Unparseable message: {e}", [("MSH", "100", str(e), "E")]), "AR", f"파싱 실패: {e}", 0
    m = p.fields(p.segments[0])
    ctrl, ver = fld(m, 10), p.comp(fld(m, 12), 1)
    mtype, trig = p.comp(fld(m, 9), 1), p.comp(fld(m, 9), 2)
    if ver not in ACCEPT_VERSIONS[sim.site["hl7_version"]]:
        return ack(sim, p, "AR", f"Unsupported version id {ver}", [("MSH^1^12", "203", f"version {ver} not accepted (expects {sim.site['hl7_version']})", "E")], ctrl), "AR", f"버전 불일치 {ver}", 0
    if mtype != "ORU" or trig != "R01":
        return ack(sim, p, "AR", f"Unsupported message type {mtype}^{trig}", [("MSH^1^9", "200" if mtype != "ORU" else "201", "only ORU^R01 is accepted on this interface", "E")], ctrl), "AR", f"미지원 {mtype}^{trig}", 0
    errs, warns = [], []
    rcv_fac = p.comp(fld(m, 6), 1)
    if rcv_fac and rcv_fac != sim.site["fac"]:
        warns.append(("MSH^1^6", "103", f"receiving facility {rcv_fac} != {sim.site['fac']}", "W"))
    pids = p.get("PID")
    if not pids:
        return ack(sim, p, "AE", "PID segment missing", [("PID", "100", "PID segment required", "E")], ctrl), "AE", "PID 없음", 0
    pid3 = fld(pids[0], 3)
    person, used = None, ""
    for rep in pid3.split(p.rs):
        val, typ = p.comp(rep, 1), p.comp(rep, 5)
        cand = sim.find_person(val)
        if cand is not None:
            person, used = cand, f"{val}^{typ}"
            break
    if person is None:
        return ack(sim, p, "AE", f"Unknown patient identifier {pid3}", [("PID^1^3", "204", f"patient {pid3} not found in {sim.site['fac']} MPI", "E")], ctrl), "AE", f"환자 없음 {pid3}", 0
    enc_no = sim.active_by_person.get(person)
    pv1 = p.get("PV1")
    if pv1 and fld(pv1[0], 19):
        vn = p.comp(fld(pv1[0], 19), 1)
        e = next((x for x in list(sim.encounters.values()) if x["visit"] == vn), None)
        if e is None or e["person"] != person:
            errs.append(("PV1^1^19", "204", f"visit number {vn} not found for patient", "E"))
        else:
            enc_no = e["no"]
    if enc_no is None:
        warns.append(("PV1^1^19", "204", "no active visit; results filed to patient chart", "W"))
    obr = p.get("OBR")
    obr_t = parse_ts(sim, fld(obr[0], 7).split(p.cs)[0]) if obr else None
    obxs = p.get("OBX")
    if not obxs:
        return ack(sim, p, "AE", "No OBX segments", [("OBX", "101", "at least one OBX required", "E")], ctrl), "AE", "OBX 없음", 0
    stored = []
    for n, o in enumerate(obxs, 1):
        vt, code_f, value, units, status = fld(o, 2), fld(o, 3), fld(o, 5), fld(o, 6), fld(o, 11)
        kind, used_code = _obs_kind(sim, p, code_f)
        if kind is None:
            warns.append((f"OBX^{n}^3", "103", f"observation identifier {code_f} not mapped (LN/MDC{'/99L01' if sim.site['flavor'] == 'ss-mix2' else ''})", "W"))
            continue
        if vt not in ("NM", "SN", "ST"):
            errs.append((f"OBX^{n}^2", "102", f"value type {vt} not numeric", "E"))
            continue
        if status and status not in ("F", "C", "P", "R"):
            warns.append((f"OBX^{n}^11", "103", f"result status {status} ignored", "W"))
            continue
        raw_v = value.split(p.cs)[1] if vt == "SN" and p.cs in value else value
        try:
            v = float(raw_v)
        except ValueError:
            errs.append((f"OBX^{n}^5", "102", f"value '{value}' is not numeric", "E"))
            continue
        unit = p.comp(units, 1) or p.comp(units, 2)
        std, uerr = normalize_unit(kind, v, unit)
        if uerr:
            errs.append((f"OBX^{n}^6", "103", uerr, "E"))
            continue
        if not in_range(kind, std):
            errs.append((f"OBX^{n}^5", "102", f"{VITALS[kind]['display']} {v} {unit} out of range", "E"))
            continue
        t = parse_ts(sim, fld(o, 14).split(p.cs)[0]) or obr_t or time.time()
        dev = p.comp(fld(o, 18), 1) or None
        stored.append((kind, std, unit, v, t, dev, used_code))
    if errs:
        return ack(sim, p, "AE", "Message rejected: " + "; ".join(e[2] for e in errs[:3]), errs + warns, ctrl), "AE", f"거부 ({len(errs)}건 오류)", 0
    if not stored:
        return ack(sim, p, "AE", "No usable observations", warns, ctrl), "AE", "저장할 값 없음", 0
    for kind, std, unit, v, t, dev, used_code in stored:
        sim.store_inbound(person, enc_no, t, kind, std, f"hl7v2/{via}", dev, unit, v, ctrl)
    pn = sim.people[person]
    summ = f"ORU {ctrl} → {pn['text']} ({used}) {len(stored)}개 저장" + (f", 경고 {len(warns)}" if warns else "")
    return ack(sim, p, "AA", "Message accepted", warns, ctrl), "AA", summ, len(stored)


def sample_oru(sim, person: int | None = None, pcd: bool = False) -> str:
    """연동 테스트용 예시 ORU^R01 — 이 기관이 받아들이는 모양 그대로."""
    enc = sim.census()[0] if person is None else sim.encounters[sim.active_by_person[person]]
    p = sim.people[enc["person"]]
    fl, s = sim.site["flavor"], sim.site
    now = time.time()
    ver = "2.6" if pcd and fl == "meditech" else s["hl7_version"]
    typ = "ORU^R01" if ver == "2.3" else "ORU^R01^ORU_R01"
    mshf = {2: "^~\\&", 3: "BIOMON", 4: "BIOMON", 5: s["app"], 6: s["fac"], 7: ts(sim, now), 9: typ, 10: CTL.next("BM"), 11: "P", 12: ver}
    if fl == "ss-mix2":
        mshf.update({18: "~ISO IR87", 20: "ISO 2022-1994"})
    elif fl == "pam-fr":
        mshf.update({17: "FRA", 18: "8859/1"})
    elif fl in ("uk-itk", "ae-malaffi"):
        mshf.update({18: "UNICODE UTF-8"})
    if pcd:
        mshf.update({15: "AL", 16: "NE", 21: "IHE_PCD_001^IHE PCD^1.3.6.1.4.1.19376.1.6.1.1.1^ISO"})
    n = max(mshf)
    segs = ["MSH|" + "|".join(mshf.get(i, "") for i in range(2, n + 1))]
    pid3 = {"meditech": f"{p['ids'].get('mrn')}^^^{s['fac']}^MR", "uk-itk": f"{p['ids'].get('nhs')}^^^NHS^NH", "ss-mix2": f"{p['ids'].get('mrn')}^^^^PI",
            "pam-fr": f"{p['ids'].get('mrn')}^^^{s['fac']}^PI", "ca-v23": f"{p['ids'].get('mrn')}^^^{s['fac']}^MR", "ae-malaffi": f"{p['ids'].get('mrn')}^^^{s['fac']}^MR"}[fl]
    segs.append(seg("PID", {1: "1", 3: pid3, 5: _name(sim, p).split("~")[0], 7: p["birth"].replace("-", ""), 8: p["sex"]}))
    segs.append(seg("PV1", {1: "1", 2: "I", 3: _pl(sim, enc["bed"]), 19: f"{enc['visit']}^^^{s['fac']}^VN"}))
    segs.append(seg("OBR", {1: "1", 3: f"BM{int(now)}^BIOMON", 4: ("182777000^monitoring of patient^SCT" if pcd else "85353-1^Vital signs panel^LN"), 7: ts(sim, now)}))
    vals = [("hr", 78), ("rr", 16), ("spo2", 97), ("temp", 36.8)]
    for i, (k, v) in enumerate(vals, 1):
        vv = VITALS[k]
        if pcd:
            code = f"{vv['mdc'][0]}^{vv['mdc'][1]}^MDC"
            unit = {"hr": "264864^MDC_DIM_BEAT_PER_MIN^MDC", "rr": "264928^MDC_DIM_RESP_PER_MIN^MDC", "spo2": "262688^MDC_DIM_PERCENT^MDC", "temp": "268192^MDC_DIM_DEGC^MDC"}[k]
        elif fl == "ss-mix2":
            code = f"{vv['jp'][0]}^{esc(vv['jp'][1])}^99L01^{vv['loinc']}^{vv['display']}^LN"
            unit = {"hr": "/min^/min^UCUM", "rr": "/min^/min^UCUM", "spo2": "%^%^UCUM", "temp": "Cel^Cel^UCUM"}[k]
        else:
            code = f"{vv['loinc']}^{vv['display']}^LN"
            if k == "temp" and s.get("units") == "us":
                v = c_to_f(v)
                unit = "[degF]^degF^UCUM"
            else:
                unit = {"hr": "/min^/min^UCUM", "rr": "/min^/min^UCUM", "spo2": "%^%^UCUM", "temp": "Cel^Cel^UCUM"}[k]
        segs.append(seg("OBX", {1: str(i), 2: "NM", 3: code, 4: f"1.0.0.{i}" if pcd else "", 5: str(v), 6: unit, 11: "F", 14: ts(sim, now), 18: "BIOSIM-GW-01^BIOMON" if pcd else ""}))
    return "\r".join(segs) + "\r"
