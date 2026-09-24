"""연동 병원 선택: '에뮬레이터'(기본) 또는 가상 의료기관 20곳 중 하나.

에뮬레이터를 고르면 지금과 같다. 에뮬레이터 자체 EMR(/api/v1/emr/*)만 쓰고, 20곳은 각자의 가상 환자로 따로 돈다.
병원 X 를 고르면 X 의 EMR 이 에뮬레이터 세계를 X 의 형식으로 내보낸다.
  - 환자: 지금 모니터링 중인 에뮬레이터 환자. 성별·생년월일·진단·동반질환·알레르기는 그대로 옮긴다.
    이름·식별번호·주소·전화는 X 나라 관례로 만든다(한국 기관이면 에뮬레이터 이름을 그대로 쓴다).
    같은 환자는 늘 같은 번호를 받는다(프로필 id 로 결정).
  - 병동/병실/병상: 에뮬레이터 병원 구조(병상 id = 도면의 bed id). 원외(MCOT) 환자는 가상 'RPM' 병동에 둔다.
  - 입원/전동/퇴원: 에뮬레이터의 실제 입·퇴원과 병상 이동을 몇 초 간격으로 따라가며 A01/A02/A03 이벤트를 만든다.
  - 내원번호: 에뮬레이터 환자번호(patient_no)를 X 형식으로 바꾼 값.
나머지 19곳은 그대로 독립 가상 병원이다.  선택은 데이터 폴더의 emr_link.json 에 남아 재시작 후에도 유지된다.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import threading
import time
from pathlib import Path

from . import sim as S
from .clinical import CONDITIONS, EMU_ICD, EMU_COMORB, EMU_ALLERGY, EMU_DEPT
from .people import make_person, fill_contact
from .sites import SITE_BY_ID, DEPTS

_world_provider = None
_state_path: Path | None = None
_lock = threading.Lock()
_linked: "LinkedSim | None" = None
_site_id: str | None = None           # None = 에뮬레이터
_sync_thread = None
_sync_stop = threading.Event()


def configure(world_provider, data_dir: Path):
    """app 시작 시 호출: 에뮬레이터 world 를 돌려주는 함수와 선택 저장 위치."""
    global _world_provider, _state_path, _site_id
    _world_provider = world_provider
    _state_path = Path(data_dir) / "emr_link.json"
    try:
        sid = json.loads(_state_path.read_text()).get("site")
        _site_id = sid if sid in SITE_BY_ID else None
    except (OSError, ValueError):
        _site_id = None


def current() -> str | None:
    return _site_id


def select(site_id: str | None) -> dict:
    global _site_id, _linked
    if site_id in ("", "emulator", None):
        site_id = None
    elif site_id not in SITE_BY_ID:
        raise ValueError(f"unknown site {site_id}")
    with _lock:
        old = _site_id
        _site_id = site_id
        _linked = None                                   # 다음 조회 때 새로 만든다
        if _state_path:
            try:
                _state_path.write_text(json.dumps({"site": site_id, "since": time.time()}))
            except OSError:
                pass
    if old and old != site_id:
        s_old = S._SIMS.get(old)
        if s_old and s_old.push:
            from . import mllp
            mllp.stop_push(s_old)
    _ensure_sync()
    return status()


def linked_sim() -> "LinkedSim | None":
    """선택된 병원의 LinkedSim (에뮬레이터 world 가 바뀌었으면 다시 만든다)."""
    global _linked
    sid = _site_id
    if sid is None or _world_provider is None:
        return None
    try:
        world = _world_provider()
    except Exception:
        return None
    if world is None:
        return None
    with _lock:
        if _linked is None or _linked.id != sid or _linked.world_hospital is not world.hospital:
            _linked = LinkedSim(SITE_BY_ID[sid], world)
        return _linked


def status() -> dict:
    ls = linked_sim()
    return {"site": _site_id or "emulator", "name": SITE_BY_ID[_site_id]["name"] if _site_id else "에뮬레이터 자체 EMR",
            "linked": bool(_site_id), "patients": len(ls.census()) if ls else None, "adt_events": len(ls.events) if ls else None,
            "since": ls.iso(ls.start) if ls else None}


def _ensure_sync():
    """연동 중에는 3초마다 에뮬레이터 상태를 따라가 ADT 시각을 정확히 남긴다."""
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return

    def loop():
        while not _sync_stop.wait(3.0):
            try:
                ls = linked_sim()
                if ls:
                    ls.advance()
            except Exception:
                pass
    _sync_thread = threading.Thread(target=loop, daemon=True, name="emrsim-link-sync")
    _sync_thread.start()


def start():
    if _site_id:
        _ensure_sync()


def stop():
    _sync_stop.set()


# ---------------------------------------------------------------------------------------------------
def _split_korean(name: str) -> tuple[str, str]:
    if " " in name:                                      # 외국인 환자 'John Smith'
        g, f = name.rsplit(" ", 1)
        return f, g
    two = ("남궁", "선우", "제갈", "황보", "독고", "사공", "서문", "동방")
    if len(name) >= 3 and name[:2] in two:
        return name[:2], name[2:]
    return name[:1], name[1:]


class LinkedSim(S.SiteSim):
    """에뮬레이터 world 를 원본으로 하는 SiteSim.  렌더러(fhir/hl7v2/vendor)는 SiteSim 과 같은 필드를 본다."""

    def __init__(self, site: dict, world):
        # SiteSim.__init__ 의 가상 ADT 는 만들지 않는다 — 필요한 부분만 직접 채운다
        import collections
        from zoneinfo import ZoneInfo
        self.site = dict(site, linked=True)
        self.id = site["id"]
        self.tz = ZoneInfo(site["tz"])
        self.lock = threading.RLock()
        self.world = world
        self.world_hospital = world.hospital
        self.start = time.time()
        self.today = dt.datetime.now(self.tz).date()
        r = random.Random(site["seed"])
        self._build_staff(r)
        self._build_from_world()
        self.people, self.person_by_key, self.person_of_profile = [], {}, {}
        self.encounters, self.active_by_person, self.last_stay_end = {}, {}, {}
        self.events, self.heap = [], []
        self.enc_counter = 0
        self.clock = self.start
        self.rng = random.Random(site["seed"] * 7919 + 2)
        self._snap: dict[int, tuple] = {}
        self.inbound, self.inbound_seq = [], 0
        self.log = collections.deque(maxlen=400)
        self.log_seq = 0
        self.counters = collections.Counter()
        prev = S._SIMS.get(self.id)
        self.faults = dict(prev.faults) if prev else {"latency_ms": 0, "error_rate": 0.0, "down": False, "token_ttl_s": 3600}
        self.tokens = prev.tokens if prev else {}          # 연동 전환해도 이미 받은 토큰은 유효
        self.push = None
        self.documents, self.athena_changed_cursor = [], {}
        self.fhir_store, self.fhir_obs_cache, self.fhir_seq = {}, {}, 0
        self._sync(initial=True)

    # ------------------------------------------------------------------ 구조
    def _build_from_world(self):
        h = self.world_hospital
        loc = self.site["locale"]
        self.wards, self.beds = [], []
        self.ward_of_emu: dict[int, int] = {}
        for w in h.wards:
            dk = EMU_DEPT.get(w["specialty"], "med")
            code = w["id"]
            if loc == "kr":
                name = w["name"]
            else:
                dname = DEPTS[dk][loc][1]
                name = f"{dname} {code[1:]}"                  # 'Cardiology 103A', '循環器内科 103A'
            self.ward_of_emu[w["idx"]] = len(self.wards)
            self.wards.append({"idx": len(self.wards), "code": code, "name": name, "depts": [dk], "floor": w["floor"], "emu_ward": w["idx"],
                               "building": w.get("building", "")})
        self.bed_of_emu: dict[int, int] = {}
        for b in h.beds:
            room = h.rooms[b["room_idx"]]
            wi = self.ward_of_emu.get(room.get("ward_idx", -1))
            if wi is None:
                wi = next((self.ward_of_emu[w["idx"]] for w in h.wards if b["idx"] in w.get("bed_idxs", [])), None)
            if wi is None:
                wi = self._extra_ward(room.get("name", "기타"), "med")
            bid = b["id"]
            label = bid.split("-", 1)[1] if "-" in bid else bid
            self.bed_of_emu[b["idx"]] = len(self.beds)
            self.beds.append({"idx": len(self.beds), "ward": wi, "room": room["id"], "bed": label, "single": False, "room_beds": 1, "emu_bed": bid})
        self.bed_occ = [None] * len(self.beds)
        self.rpm_ward = None
        self.rpm_bed_of: dict[int, int] = {}

    def _extra_ward(self, name: str, dk: str) -> int:
        """병동 소속이 아닌 병상(응급실·투석실 등): 실 이름(번호 뗀 것)마다 한 '병동'."""
        import re
        key = re.sub(r"\s*\d+$", "", name) or "기타"
        cache = self.__dict__.setdefault("_extra", {})
        if key not in cache:
            self.wards.append({"idx": len(self.wards), "code": f"X{len(cache) + 1:02d}", "name": key, "depts": [dk], "floor": 1})
            cache[key] = len(self.wards) - 1
        return cache[key]

    def _rpm_bed(self, pid: int) -> int:
        """원외(MCOT) 환자는 가상 원격 모니터링 병동의 개인 슬롯에."""
        if self.rpm_ward is None:
            nm = {"kr": "원격모니터링(MCOT)", "jp": "遠隔モニタリング", "de": "Telemonitoring", "fr": "Télésurveillance", "nl": "Telemonitoring", "br": "Telemonitoramento"}.get(self.site["locale"],
                                                                                                                                                       "Remote Monitoring (MCOT)")
            self.rpm_ward = len(self.wards)
            self.wards.append({"idx": self.rpm_ward, "code": "RPM", "name": nm, "depts": ["card"], "floor": 0})
        if pid not in self.rpm_bed_of:
            self.rpm_bed_of[pid] = len(self.beds)
            self.beds.append({"idx": len(self.beds), "ward": self.rpm_ward, "room": "RPM", "bed": f"H{pid:05d}", "single": True, "room_beds": 1, "emu_bed": None})
            self.bed_occ.append(None)
        return self.rpm_bed_of[pid]

    # ------------------------------------------------------------------ 환자
    def _person(self, pid: int) -> int:
        if pid in self.person_of_profile:
            return self.person_of_profile[pid]
        prof = self.world.by_id[pid]
        loc = self.site["locale"]
        r = random.Random(S.h64(self.id, "emu-person", pid))
        birth = dt.date.fromisoformat(prof["birth_date"])
        p = make_person(loc, r, self.today, sex=prof["sex"], birth=birth)
        p["age"] = prof["age"]
        if loc == "kr":
            fam, giv = _split_korean(prof["name"])
            p.update(family=fam, given=[giv], text=prof["name"])
            a = prof.get("address") or {}
            fill_contact(p, loc, r, "서울특별시")
            if a:
                p["address"] = {**p["address"], "state": {"서울": "서울특별시", "경기": "경기도", "인천": "인천광역시", "부산": "부산광역시", "대구": "대구광역시", "대전": "대전광역시",
                                                           "광주": "광주광역시", "울산": "울산광역시", "세종": "세종특별자치시", "제주": "제주특별자치도"}.get(a.get("sido"), a.get("sido")),
                                "city": a.get("sigungu", ""), "district": a.get("dong", "")}
            p["phone"] = prof.get("phone") or p["phone"]
        else:
            fill_contact(p, loc, r, self.site.get("region"))
        idx = len(self.people)
        p["idx"] = idx
        p["profile_id"] = pid
        p["emu_mrn"] = prof["mrn"]
        p["allergy"] = EMU_ALLERGY.get(prof.get("allergies", "없음"), "nka")
        p["ids"] = self._patient_ids(r, p, pid)
        p["fhir_id"] = self.fid("Patient", 20000 + pid)
        p["version"] = 1
        p["updated"] = self.start
        self.people.append(p)
        self.person_of_profile[pid] = idx
        for k, v in p["ids"].items():
            if isinstance(v, str) and k != "ohip_vc":
                self.person_by_key[S.norm_key(v)] = idx
        self.person_by_key[S.norm_key(p["fhir_id"])] = idx
        return idx

    def find_person(self, value: str) -> int | None:
        return self.person_by_key.get(S.norm_key(value)) if value is not None else None

    # ------------------------------------------------------------------ 동기화
    def advance(self, now: float | None = None):
        self._sync()

    def _sync(self, initial: bool = False):
        now = time.time()
        with self.lock:
            w = self.world
            cur: dict[int, tuple] = {}
            for pid, rec in list(w.admitted.items()):
                cur[pid] = (rec.get("patient_no"), rec["bed_idx"], bool(rec["outpatient"]))
            # 퇴원
            for pid, (pno, bed, outp) in list(self._snap.items()):
                if pid not in cur or cur[pid][0] != pno:
                    enc = self.encounters.get(pno)
                    if enc and enc["status"] == "in-progress":
                        enc.update(status="finished", end=now, updated=now, disposition="home")
                        self.bed_occ[enc["bed"]] = None
                        self.active_by_person.pop(enc["person"], None)
                        self._emit(now, "A03", enc)
            # 입원·전동
            for pid, (pno, bed, outp) in cur.items():
                if pno is None:
                    continue
                prev = self._snap.get(pid)
                bi = self.bed_of_emu.get(bed) if bed is not None and bed >= 0 else self._rpm_bed(pid)
                if bi is None:
                    bi = self._rpm_bed(pid)
                enc = self.encounters.get(pno)
                if enc is None:
                    enc = self._new_linked(pid, pno, bi, outp, now)
                    if not initial:
                        self._emit(now, "A01", enc)
                elif enc["status"] == "in-progress" and enc["bed"] != bi:
                    old = enc["bed"]
                    if self.bed_occ[old] == pno:
                        self.bed_occ[old] = None
                    self.bed_occ[bi] = pno
                    enc["bed"] = bi
                    enc["transfers"].append({"t": now, "from": old, "to": bi})
                    enc["updated"] = now
                    self._emit(now, "A02", enc, prior_bed=old)
            self._snap = cur
            self.clock = now

    def _new_linked(self, pid: int, pno: int, bed: int, outp: bool, now: float) -> dict:
        prof = self.world.by_id[pid]
        pidx = self._person(pid)
        cond = EMU_ICD.get(prof.get("icd10"), "afib")
        comorb = [EMU_COMORB[c] for c in prof.get("comorbidities", []) if c in EMU_COMORB and EMU_COMORB[c] != cond]
        adm = (prof.get("admission") or {}).get("time")
        try:
            at = float(adm) if isinstance(adm, (int, float)) else dt.datetime.fromisoformat(adm).timestamp()
        except (TypeError, ValueError):
            at = now
        docs = [s for s in self.staff if s["dept"] == CONDITIONS[cond]["dept"]] or self.staff
        enc = {"no": pno, "person": pidx, "bed": bed, "admit": min(at, now), "planned_end": now + 86400 * 3, "end": None, "cond": cond, "status": "in-progress",
               "attending": docs[S.h64(pno) % len(docs)]["idx"], "visit": self._visit_number(pno, at), "fhir_id": self.fid("Encounter", pno), "transfers": [],
               "updated": now, "source": "referral" if outp else ("emergency" if S.h64(pno, "src") % 3 == 0 else "referral"), "comorbid": list(dict.fromkeys(comorb)),
               "profile_id": pid, "outpatient": outp}
        self.encounters[pno] = enc
        self.bed_occ[bed] = pno
        self.active_by_person[pidx] = pno
        return enc

    def _visit_number(self, no: int, t: float) -> str:
        if self.site["locale"] == "kr":                   # 가상 병원의 '일자+4자리'는 수천 명 규모에서 겹칠 수 있다
            return f"{self.local(t).strftime('%Y%m%d')}{no % 1000000:06d}"
        return super()._visit_number(no, t)

    # ------------------------------------------------------------------ 에뮬레이터 쪽 조회용
    def identity(self, pid: int) -> dict | None:
        """에뮬레이터 프로필 id → 이 EMR 에서의 신원(라우터가 연동 EMR 을 조회할 키)."""
        self.advance()
        idx = self.person_of_profile.get(pid)
        if idx is None:
            return None
        p = self.people[idx]
        enc = self.encounters.get(self.active_by_person.get(idx)) if idx in self.active_by_person else None
        ids = {k: v for k, v in p["ids"].items()}
        if "rrn" in ids:
            from .ids import rrn_masked
            ids["rrn"] = rrn_masked(ids["rrn"])
        out = {"site": self.id, "name": p["text"], "identifiers": ids, "fhir_patient_id": p["fhir_id"] if self.site["protocol"] == "fhir" else None,
               "visit": enc["visit"] if enc else None, "fhir_encounter_id": enc["fhir_id"] if enc and self.site["protocol"] == "fhir" else None}
        if enc:
            w, b = self.bed_path(enc["bed"])
            out["location"] = {"ward": w["code"], "ward_name": w["name"], "room": b["room"], "bed": b["bed"]}
        last = {}
        for x in self.inbound_for(person=idx)[-40:]:
            last[x["kind"]] = {"value": x["value"], "t": self.iso(x["t"]), "source": x["source"]}
        out["received"] = {"count": sum(1 for x in self.inbound if x["person"] == idx), "last": last}
        return out
