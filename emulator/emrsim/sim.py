"""기관 하나의 EMR 상태: 환자 마스터, 병상, 의료진, 입·전·퇴원(ADT) 이벤트, 간호 바이탈, 외부 수신 기록, 로그, 장애 주입.

ADT 는 결정적 이산 사건 시뮬레이션이다.  시작 시각은 UTC 자정 기준 3일 전으로 고정해서, 같은 날 안에서는 재시작해도
같은 환자·같은 내원번호·같은 이벤트가 다시 나온다.  요청이 올 때마다 현재 시각까지 사건을 처리한다(lazy).
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import heapq
import random
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from . import ids
from .clinical import CONDITIONS, chart_values, pick_allergy, los_days, COND_KEYS, COND_WEIGHT
from .people import make_person, fill_contact
from .sites import DEPTS

HISTORY_DAYS = 3
CHART_EVERY_H = 4
POOL = 420


def h64(*parts) -> int:
    return int.from_bytes(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest(), "big")


def norm_key(v) -> str:
    """식별자 조회 키: 하이픈·공백·점 제거, 대문자 (NHS '999 123 4567', CPF '123.456.789-09', EID '784-...' 모두 같은 키)."""
    return str(v).strip().replace("-", "").replace(" ", "").replace(".", "").upper()


B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class SiteSim:
    def __init__(self, site: dict, now: float | None = None):
        self.site = site
        self.id = site["id"]
        self.tz = ZoneInfo(site["tz"])
        self.lock = threading.RLock()
        now = now or time.time()
        day0 = dt.datetime.fromtimestamp(now, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.start = (day0 - dt.timedelta(days=HISTORY_DAYS)).timestamp()
        self.today = dt.datetime.fromtimestamp(self.start, self.tz).date()
        r = random.Random(site["seed"])
        self._build_beds()
        self._build_staff(r)
        self._build_people(r)
        # 상태
        self.encounters: dict[int, dict] = {}
        self.bed_occ: list[int | None] = [None] * len(self.beds)
        self.active_by_person: dict[int, int] = {}
        self.last_stay_end: dict[int, float] = {}
        self.events: list[dict] = []
        self.heap: list = []
        self._hseq = 0
        self.rng = random.Random(site["seed"] * 7919 + 1)
        self.enc_counter = 0
        self.clock = self.start
        self._initial_fill()
        # 외부 연동 상태
        self.inbound: list[dict] = []
        self.inbound_seq = 0
        self.log: collections.deque = collections.deque(maxlen=400)
        self.log_seq = 0
        self.counters = collections.Counter()
        self.faults = {"latency_ms": 0, "error_rate": 0.0, "down": False, "token_ttl_s": 3600}
        self.tokens: dict[str, float] = {}
        self.push: dict | None = None
        self.documents: list[dict] = []         # CDA 수신 문서
        self.athena_changed_cursor: dict[str, int] = {}
        self.fhir_store: dict[str, dict] = {}   # 외부에서 POST 한 Observation (원문 보존)
        self.fhir_obs_cache: dict[str, dict] = {}
        self.fhir_seq = 0

    # ------------------------------------------------------------------ 구조
    def _build_beds(self):
        s, loc = self.site, self.site["locale"]
        self.wards, self.beds = [], []
        for wi, (code, name, depts, floor, rooms) in enumerate(s["wards"]):
            self.wards.append({"idx": wi, "code": code, "name": name, "depts": depts, "floor": floor})
            n_multi = 0
            for ri, nbed in enumerate(rooms):
                room, labels = self._room_label(code, floor, ri, nbed, n_multi)
                if nbed > 1:
                    n_multi += 1
                for bi in range(nbed):
                    self.beds.append({"idx": len(self.beds), "ward": wi, "room": room, "bed": labels[bi], "single": nbed == 1, "room_beds": nbed})

    def _room_label(self, ward: str, floor: int, ri: int, nbed: int, n_multi: int) -> tuple[str, list[str]]:
        loc = self.site["locale"]
        n = ri + 1
        letters = "ABCDEF"
        if loc == "us":
            if self.site.get("outpatient"):
                return "RPM", [f"SLOT{n:02d}"]
            return f"{floor}{n:02d}", [letters[i] for i in range(nbed)] if nbed > 1 else ["A"]
        if loc == "uk":
            if nbed == 1:
                return f"SR{n}", ["1"]
            return f"BAY{n_multi + 1}", [str(i + 1) for i in range(nbed)]
        if loc == "jp":
            return f"{floor}{n:02d}", [str(i + 1) for i in range(nbed)]
        if loc == "kr":
            base = ward[:2] if ward[:2].isdigit() else f"{floor}"
            return f"{base}{n:02d}", [f"{i + 1:02d}" for i in range(nbed)]
        if loc == "de":
            return f"{floor}.{n:02d}", [str(i + 1) for i in range(nbed)]
        if loc == "fr":
            return f"{floor}{n:02d}", [str(i + 1) for i in range(nbed)] if nbed > 1 else ["1"]
        if loc == "nl":
            return f"{ward}.{n:02d}", [str(i + 1) for i in range(nbed)]
        if loc == "au":
            return f"{ward}-{n:02d}", [str(i + 1) for i in range(nbed)]
        if loc == "ca":
            return f"{floor}{n:02d}", [str(i + 1) for i in range(nbed)]
        if loc == "sg":
            return f"{ward[1:]}-{n:02d}", [f"{ward[1:]}{n:02d}{i + 1}" for i in range(nbed)]
        if loc == "br":
            return f"{floor}{n:02d}", [f"{floor}{n:02d}-{letters[i]}" for i in range(nbed)]
        if loc == "ae":
            return f"{floor}{n:03d}", [letters[i] for i in range(nbed)]
        return str(n), [str(i + 1) for i in range(nbed)]

    def dept(self, key: str) -> tuple[str, str]:
        return DEPTS[key][self.site["locale"]]

    def _build_staff(self, r: random.Random):
        loc = self.site["locale"]
        self.staff = []
        keys = ["card", "card", "card", "resp", "med", "med", "neuro", "ortho", "cts", "card", "resp", "med"]
        for i, dk in enumerate(keys):
            p = make_person(loc, r, self.today, age_mu=46)
            p["age"] = max(31, min(68, p["age"]))
            p["dept"] = dk
            p["idx"] = i
            p["ids"] = self._staff_ids(r)
            p["fhir_id"] = self.fid("Practitioner", i)
            self.staff.append(p)

    def _staff_ids(self, r: random.Random) -> dict:
        loc = self.site["locale"]
        if loc == "us":
            return {"npi": ids.npi(r), "local": f"{r.randint(10000, 99999)}"}
        if loc == "uk":
            return {"gmc": ids.gmc(r), "consultant": "C" + ids.gmc(r)}
        if loc == "jp":
            return {"ikiseki": ids.digits(r, 6), "local": f"D{r.randint(1000, 9999)}"}
        if loc == "kr":
            return {"license": ids.digits(r, 5), "local": f"{r.randint(10000, 99999)}"}
        if loc == "de":
            return {"lanr": ids.lanr(r), "local": f"{r.randint(1000, 9999)}"}
        if loc == "fr":
            return {"rpps": "10" + ids.digits(r, 9, first_nonzero=False), "local": f"{r.randint(1000, 9999)}"}
        if loc == "nl":
            return {"agb": "01" + ids.digits(r, 6, first_nonzero=False), "big": ids.digits(r, 11)}
        if loc == "au":
            return {"hpii": ids.ihi(r, "800361"), "ahpra": "MED000" + ids.digits(r, 7)}
        if loc == "ca":
            return {"cpso": ids.digits(r, 5), "local": f"{r.randint(1000, 9999)}"}
        if loc == "sg":
            return {"mcr": ids.mcr_sg(r)}
        if loc == "br":
            return {"crm": ids.digits(r, 6), "cns": ids.cns(r)}
        if loc == "ae":
            return {"doh": f"GD{ids.digits(r, 5)}", "local": f"{r.randint(1000, 9999)}"}
        return {}

    def _build_people(self, r: random.Random):
        loc, sid = self.site["locale"], self.id
        self.people = []
        self.person_by_key: dict[str, int] = {}
        pool = 300 if self.site.get("outpatient") else POOL
        for i in range(pool):
            p = make_person(loc, r, self.today)
            fill_contact(p, loc, r, self.site.get("region"))
            p["idx"] = i
            p["allergy"] = pick_allergy(r)
            p["ids"] = self._patient_ids(r, p, i)
            p["fhir_id"] = self.fid("Patient", i)
            p["version"] = 1
            p["updated"] = self.start - r.randint(30, 900) * 86400
            self.people.append(p)
            for k, v in p["ids"].items():
                if isinstance(v, str) and k not in ("ohip_vc",):
                    self.person_by_key[norm_key(v)] = i
            self.person_by_key[norm_key(p["fhir_id"])] = i

    def _patient_ids(self, r: random.Random, p: dict, i: int) -> dict:
        sid, loc = self.id, self.site["locale"]
        by = int(p["birth"][:4])
        if sid == "us-lakeshore":
            return {"mrn": f"{2030000 + i * 37 + r.randint(0, 30)}", "epi": f"Z{r.randint(100000, 999999)}"}
        if sid == "us-sierravista":
            return {"mrn": f"{10480000 + i * 13 + r.randint(0, 9):08d}", "cmrn": f"{r.randint(1000000, 9999999)}"}
        if sid == "us-pineridge":
            return {"mrn": f"M000{400000 + i * 7 + r.randint(0, 6):06d}"}
        if sid == "us-bayside":
            return {"patientid": str(30000 + i * 3 + r.randint(0, 2)), "enterpriseid": str(30000 + i * 3 + r.randint(0, 2))}
        if loc == "uk":
            pre = "K" if sid == "uk-kingsmere" else "W"
            return {"nhs": ids.nhs_number(r), "mrn": f"{pre}{1200000 + i * 211 + r.randint(0, 210)}"}
        if sid == "jp-toto":
            return {"mrn": f"{120000 + i * 53 + r.randint(0, 52):010d}"}
        if sid == "jp-kitahama":
            return {"mrn": f"{50000000 + i * 11 + r.randint(0, 10):08d}", "hoken": f"{r.choice(['06', '01', '31', '39'])}{r.randint(100000, 279999)}"}
        if loc == "kr":
            base = {"kr-hanbit": 10200000, "kr-saesol": 1500000, "kr-donghae": 300000, "kr-cheongram": 200100000}[sid]
            width = {"kr-hanbit": 8, "kr-saesol": 8, "kr-donghae": 7, "kr-cheongram": 9}[sid]
            return {"mrn": f"{base + i * 17 + r.randint(0, 16):0{width}d}", "rrn": ids.rrn(r, p["sex"], p["birth"])}
        if loc == "de":
            out = {"mrn": f"{3100000 + i * 97 + r.randint(0, 96)}"}
            if r.random() < 0.88:
                out["kvnr"] = ids.kvnr(r)
                out["ik_insurer"] = r.choice(["109519005", "101575519", "104940005", "108310400"])     # IK der Krankenkasse
            else:
                out["pkv"] = f"PKV{r.randint(100000, 999999)}"
            return out
        if loc == "fr":
            y, m = int(p["birth"][:4]), int(p["birth"][5:7])
            return {"mrn": f"{40100000 + i * 131 + r.randint(0, 130)}", "ins": ids.nir(r, p["sex"], y, m)}
        if loc == "nl":
            return {"mrn": f"{7000000 + i * 89 + r.randint(0, 88)}", "bsn": ids.bsn(r)}
        if loc == "au":
            return {"mrn": f"{1400000 + i * 71 + r.randint(0, 70)}", "ihi": ids.ihi(r), "medicare": ids.medicare_au(r), "medicare_irn": str(r.randint(1, 5))}
        if loc == "ca":
            hc, vc = ids.ohip(r)
            return {"mrn": f"M{2200000 + i * 61 + r.randint(0, 60)}", "ohip": hc, "ohip_vc": vc}
        if loc == "sg":
            return {"mrn": f"{60300000 + i * 157 + r.randint(0, 156)}", "nric": ids.nric(r, by)}
        if loc == "br":
            return {"mrn": f"{8800000 + i * 43 + r.randint(0, 42)}", "cns": ids.cns(r), "cpf": ids.cpf(r)}
        if loc == "ae":
            return {"mrn": f"AW{5500000 + i * 83 + r.randint(0, 82)}", "eid": ids.emirates_id(r, by)}
        return {"mrn": str(i)}

    # ------------------------------------------------------------------ ID 스타일
    def fid(self, kind: str, key) -> str:
        """FHIR 논리 id — 계열마다 모양이 다르다(Epic 불투명 문자열, Cerner 숫자, NHS UUID ...)."""
        fl = self.site.get("flavor")
        h = h64(self.id, kind, key)
        if fl == "epic":
            s = "".join(B64URL[(h >> (6 * i)) & 63] for i in range(10)) + "".join(B64URL[(h64(key, kind) >> (6 * i)) & 63] for i in range(10))
            return "e" + s.replace("-", "x").replace("_", "Q") + "3"
        if fl in ("oracle", "au-core", "jp-core", "kr-core"):
            base = {"Patient": 12700000, "Encounter": 97900000, "Observation": 0, "Practitioner": 4120000, "Location": 3210000, "Condition": 51000000, "AllergyIntolerance": 23000000}.get(kind, 100000)
            if kind == "Observation":
                return f"{'VS' if fl == 'oracle' else 'vs'}-{h % 10**12}"
            return str(base + (key if isinstance(key, int) else h % 900000))
        if fl == "isik":
            return f"{kind[:3].lower()}-{uuid.UUID(int=h << 64 | h64(kind, key, 1)).hex[:12]}"
        return str(uuid.UUID(int=(h << 64) | h64(self.id, key, kind)))

    # ------------------------------------------------------------------ 시간
    def local(self, t: float) -> dt.datetime:
        return dt.datetime.fromtimestamp(t, self.tz)

    def iso(self, t: float) -> str:
        return self.local(t).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ 시뮬레이션
    def _push(self, t: float, kind: str, arg):
        self._hseq += 1
        heapq.heappush(self.heap, (t, self._hseq, kind, arg))

    def _cond_for_ward(self, ward: dict, r: random.Random) -> str:
        allowed = [k for k in COND_KEYS if CONDITIONS[k]["dept"] in ward["depts"]] or COND_KEYS
        return r.choices(allowed, weights=[COND_WEIGHT[k] for k in allowed])[0]

    def _new_encounter(self, t: float, bed_idx: int, admit_t: float | None = None) -> dict:
        r = self.rng
        busy = set(self.active_by_person)
        free = [i for i in range(len(self.people)) if i not in busy and self.last_stay_end.get(i, 0) < t - 86400 * 2]
        pidx = free[r.randrange(len(free))] if free else r.randrange(len(self.people))
        ward = self.wards[self.beds[bed_idx]["ward"]]
        cond = self._cond_for_ward(ward, r)
        los = los_days(r, cond) * (5.0 if self.site.get("outpatient") else 1.0)
        docs = [s for s in self.staff if s["dept"] == CONDITIONS[cond]["dept"]] or self.staff
        self.enc_counter += 1
        no = self.enc_counter
        at = admit_t if admit_t is not None else t
        enc = {"no": no, "person": pidx, "bed": bed_idx, "admit": at, "planned_end": at + los * 86400, "end": None, "cond": cond, "status": "in-progress",
               "attending": docs[r.randrange(len(docs))]["idx"], "visit": self._visit_number(no, at), "fhir_id": self.fid("Encounter", no),
               "transfers": [], "updated": at, "source": r.choice(["emergency", "referral", "elective"]) if not self.site.get("outpatient") else "referral",
               "comorbid": r.sample([k for k in COND_KEYS if k != cond], r.choice([0, 0, 1, 1, 2]))}
        self.encounters[no] = enc
        self.bed_occ[bed_idx] = no
        self.active_by_person[pidx] = no
        return enc

    def _visit_number(self, no: int, t: float) -> str:
        sid, loc = self.id, self.site["locale"]
        yy = self.local(t).strftime("%y")
        if sid == "us-lakeshore":
            return f"{7300000000 + no * 97}"                         # CSN
        if sid == "us-sierravista":
            return f"{97000000 + no * 31}"                           # FIN
        if sid == "us-pineridge":
            return f"V{no + 12000000:010d}"
        if sid == "us-bayside":
            return str(88000 + no)
        if loc == "uk":
            return f"IP{yy}{no + 400000:07d}"
        if loc == "jp":
            return f"{yy}{no + 100000:07d}"
        if loc == "kr":
            return f"{self.local(t).strftime('%Y%m%d')}{no % 10000:04d}"
        if loc == "de":
            return f"1{yy}{no + 50000:07d}"                          # Fallnummer
        if loc == "fr":
            return f"{yy}{no + 5000000:07d}"                          # NDA
        return f"{loc.upper()}{yy}{no + 10000:06d}"

    def _schedule_stay(self, enc: dict, t_now: float):
        r = self.rng
        self._push(enc["planned_end"], "discharge", enc["no"])
        span = enc["planned_end"] - enc["admit"]
        if r.random() < 0.12 and not self.site.get("outpatient"):
            tt = enc["admit"] + span * r.uniform(0.25, 0.65)
            if tt > t_now:
                self._push(tt, "transfer", enc["no"])
        if r.random() < 0.18:
            tt = enc["admit"] + span * r.uniform(0.1, 0.9)
            if tt > t_now:
                self._push(tt, "update", enc["no"])

    def _initial_fill(self):
        r = self.rng
        for b in range(len(self.beds)):
            if r.random() < 0.82:
                enc = self._new_encounter(self.start, b)
                los = enc["planned_end"] - enc["admit"]
                back = los * r.uniform(0.05, 0.9)
                enc["admit"] = self.start - back
                enc["planned_end"] = enc["admit"] + los
                enc["visit"] = self._visit_number(enc["no"], enc["admit"])
                enc["updated"] = enc["admit"]
                self._schedule_stay(enc, self.start)
            else:
                self._push(self.start + r.uniform(0.2, 10) * 3600, "admit", b)

    def advance(self, now: float | None = None):
        now = now or time.time()
        with self.lock:
            while self.heap and self.heap[0][0] <= now:
                t, _, kind, arg = heapq.heappop(self.heap)
                getattr(self, "_ev_" + kind)(t, arg)
            self.clock = now
        self.prune(now)

    def _emit(self, t: float, code: str, enc: dict, prior_bed: int | None = None, note: str = ""):
        self.events.append({"seq": self.last_seq() + 1, "t": t, "code": code, "enc": enc["no"], "person": enc["person"], "bed": enc["bed"], "prior_bed": prior_bed, "note": note})

    def _ev_admit(self, t: float, bed: int):
        if self.bed_occ[bed] is not None:
            self._push(t + 2 * 3600, "admit", bed)
            return
        enc = self._new_encounter(t, bed)
        self._emit(t, "A01", enc)
        self._schedule_stay(enc, t)
        if self.rng.random() < 0.02:                             # 등록 착오 → 입원 취소(A11)
            self._push(t + self.rng.uniform(600, 2400), "cancel", enc["no"])

    def _free(self, enc: dict, t: float, gap_h: tuple[float, float]):
        self.bed_occ[enc["bed"]] = None
        self.active_by_person.pop(enc["person"], None)
        self.last_stay_end[enc["person"]] = t
        self._push(t + self.rng.uniform(*gap_h) * 3600, "admit", enc["bed"])

    def _ev_discharge(self, t: float, no: int):
        enc = self.encounters.get(no)                 # 끝나서 정리된 입원에 걸린 예약 이벤트는 버린다
        if enc is None or enc["status"] != "in-progress":
            return
        enc.update(status="finished", end=t, updated=t, disposition=self.rng.choices(["home", "home-health", "snf", "other-hcf", "exp"], weights=(78, 9, 7, 5, 1))[0])
        self._free(enc, t, (1, 8))
        self._emit(t, "A03", enc)

    def _ev_cancel(self, t: float, no: int):
        enc = self.encounters.get(no)                 # 끝나서 정리된 입원에 걸린 예약 이벤트는 버린다
        if enc is None or enc["status"] != "in-progress":
            return
        enc.update(status="cancelled", end=t, updated=t)
        self._free(enc, t, (0.5, 3))
        self._emit(t, "A11", enc, note="입원 등록 취소")

    def _ev_transfer(self, t: float, no: int):
        enc = self.encounters.get(no)                 # 끝나서 정리된 입원에 걸린 예약 이벤트는 버린다
        if enc is None or enc["status"] != "in-progress":
            return
        cur_w = self.beds[enc["bed"]]["ward"]
        free = [b for b in range(len(self.beds)) if self.bed_occ[b] is None]
        pref = [b for b in free if self.beds[b]["ward"] != cur_w] or free
        if not pref:
            return
        nb = pref[self.rng.randrange(len(pref))]
        old = enc["bed"]
        self.bed_occ[old] = None
        self._push(t + self.rng.uniform(1, 6) * 3600, "admit", old)
        self.bed_occ[nb] = no
        enc["bed"] = nb
        enc["transfers"].append({"t": t, "from": old, "to": nb})
        enc["updated"] = t
        self._emit(t, "A02", enc, prior_bed=old)

    def _ev_update(self, t: float, no: int):
        enc = self.encounters.get(no)                 # 끝나서 정리된 입원에 걸린 예약 이벤트는 버린다
        if enc is None or enc["status"] != "in-progress":
            return
        p = self.people[enc["person"]]
        r = random.Random(h64(self.id, "upd", no))
        p.pop("phone", None)
        fill_contact(p, self.site["locale"], r, self.site.get("region"))
        p["version"] += 1
        p["updated"] = t
        self._emit(t, "A08", enc, note="환자 정보 변경(주소·연락처)")

    # ------------------------------------------------------------------ 조회
    def census(self) -> list[dict]:
        self.advance()
        return [self.encounters[n] for n in self.bed_occ if n is not None]

    def last_seq(self) -> int:
        return self.events[-1]["seq"] if self.events else getattr(self, "events_base", 0)

    def events_since(self, seq: int, limit: int = 500) -> list[dict]:
        """seq 보다 뒤의 이벤트.  오래된 이벤트는 prune() 로 잘려 나가므로 목록 첫 seq 기준으로 찾는다."""
        self.advance()
        ev = self.events
        if not ev:
            return []
        start = max(0, seq - (ev[0]["seq"] - 1))
        return ev[start: start + limit]

    def enc_of_person(self, pidx: int, active_only: bool = False) -> list[dict]:
        self.advance()
        out = [e for e in list(self.encounters.values()) if e["person"] == pidx]
        if active_only:
            out = [e for e in out if e["status"] == "in-progress"]
        return sorted(out, key=lambda e: -e["admit"])

    def find_person(self, value: str) -> int | None:
        if value is None:
            return None
        return self.person_by_key.get(norm_key(value))

    def bed_path(self, bed_idx: int) -> tuple[dict, dict]:
        b = self.beds[bed_idx]
        return self.wards[b["ward"]], b

    def chart_times(self, enc: dict, until: float | None = None) -> list[float]:
        """간호 바이탈 기록 시각: 입원 15분 뒤 1회, 이후 현지 02/06/10/14/18/22시."""
        end = min(until or self.clock, enc["end"] or self.clock)
        if enc["status"] == "cancelled":
            return []
        out = []
        t = enc["admit"] + 900
        if t <= end:
            out.append(t)
        loc = self.local(enc["admit"])
        mark = loc.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=(2 - loc.hour) % CHART_EVERY_H or CHART_EVERY_H)
        tt = mark.timestamp()
        while tt <= end:
            if tt > enc["admit"] + 1800:
                out.append(tt + (h64(self.id, enc["no"], int(tt)) % 900))       # 실제 기록은 정시에서 조금씩 늦다
            tt += CHART_EVERY_H * 3600
        return out

    def vitals_for(self, enc: dict, since: float = 0, until: float | None = None) -> list[dict]:
        p = self.people[enc["person"]]
        out = []
        for i, t in enumerate(self.chart_times(enc, until)):
            if t < since:
                continue
            v = chart_values(enc["cond"], p["age"], f"{self.id}:{enc['no']}:{i}")
            nurse = self.staff[h64(self.id, enc["no"], i) % len(self.staff)]
            out.append({"t": t, "i": i, "values": v, "enc": enc["no"], "person": enc["person"], "source": "nursing", "performer": nurse["idx"],
                        "id_base": f"{enc['no']}.{i}"})
        return out

    # ------------------------------------------------------------------ 보관 한도 (장시간 가동 시 메모리가 늘지 않게)
    KEEP_S = 3 * 86400            # 끝난 입원·ADT 이벤트 보관 기간
    CAP = {"fhir_store": 20000, "fhir_obs_cache": 20000, "documents": 2000, "inbound": 50000}

    def prune(self, now: float | None = None):
        now = now or time.time()
        if now - getattr(self, "_pruned_at", 0) < 600:
            return
        self._pruned_at = now
        cut = now - self.KEEP_S
        with self.lock:
            old = [n for n, e in self.encounters.items() if e["status"] != "in-progress" and (e["end"] or 0) < cut]
            for n in old:
                del self.encounters[n]
            if self.events and self.events[0]["t"] < cut:
                keep = [e for e in self.events if e["t"] >= cut and e["enc"] in self.encounters]
                self.events_base = self.events[-1]["seq"] if not keep else keep[0]["seq"] - 1
                self.events[:] = keep
            for name in ("fhir_store", "fhir_obs_cache"):
                d = getattr(self, name, None)
                if d is not None and len(d) > self.CAP[name]:
                    for k in list(d)[: len(d) - self.CAP[name] // 2]:
                        del d[k]
            if len(self.documents) > self.CAP["documents"]:
                del self.documents[: len(self.documents) - self.CAP["documents"]]
            for k in [k for k, e in self.tokens.items() if e < now]:
                del self.tokens[k]

    # ------------------------------------------------------------------ 수신·로그
    def store_inbound(self, person: int, enc_no: int | None, t: float, kind: str, value: float, source: str, device: str | None = None,
                      unit_in: str | None = None, value_in=None, ref: str | None = None) -> dict:
        with self.lock:
            self.inbound_seq += 1
            rec = {"seq": self.inbound_seq, "id": f"{self.id[:2]}{self.inbound_seq:07d}", "person": person, "enc": enc_no, "t": t, "kind": kind, "value": value,
                   "unit_in": unit_in, "value_in": value_in, "source": source, "device": device, "received": time.time(), "ref": ref}
            self.inbound.append(rec)
            if len(self.inbound) > 50000:
                del self.inbound[:5000]
            self.counters["inbound_values"] += 1
            return rec

    def inbound_for(self, person: int | None = None, enc_no: int | None = None) -> list[dict]:
        return [x for x in self.inbound if (person is None or x["person"] == person) and (enc_no is None or x["enc"] == enc_no)]

    def add_log(self, direction: str, channel: str, summary: str, status: str | int = "", method: str = "", path: str = "", client: str = "", detail: str | None = None):
        with self.lock:
            self.log_seq += 1
            self.log.append({"seq": self.log_seq, "t": time.time(), "dir": direction, "channel": channel, "method": method, "path": path, "status": status,
                             "summary": summary, "client": client, "detail": (detail or "")[:4000]})
            self.counters[f"{direction}_{channel}"] += 1
            if isinstance(status, int) and status >= 400 or status in ("AE", "AR", "CE", "CR", "E"):
                self.counters["errors"] += 1

    # ------------------------------------------------------------------ 인증 토큰
    def issue_token(self) -> tuple[str, int]:
        ttl = int(self.faults.get("token_ttl_s") or 3600)
        tok = ("ey" if self.site.get("flavor") in ("epic", "uk-core") else "") + uuid.uuid4().hex + uuid.uuid4().hex[:8]
        with self.lock:
            now = time.time()
            for k in [k for k, e in self.tokens.items() if e < now]:
                del self.tokens[k]
            if len(self.tokens) > 5000:                    # 토큰을 매 요청 새로 받는 클라이언트 대비
                for k in sorted(self.tokens, key=self.tokens.get)[:2500]:
                    del self.tokens[k]
            self.tokens[tok] = now + ttl
        return tok, ttl

    def token_ok(self, tok: str) -> bool:
        e = self.tokens.get(tok)
        return bool(e and e > time.time())

    # ------------------------------------------------------------------ 요약
    def summary(self) -> dict:
        self.advance()
        occ = sum(1 for x in self.bed_occ if x is not None)
        return {"beds": len(self.beds), "occupied": occ, "patients_in_pool": len(self.people), "encounters": len(self.encounters), "adt_events": self.last_seq(),
                "inbound_values": self.counters["inbound_values"], "requests": sum(v for k, v in self.counters.items() if k.startswith("in_")),
                "errors": self.counters["errors"], "sim_start": self.iso(self.start), "faults": dict(self.faults),
                "push": ({k: v for k, v in self.push.items() if k not in ("thread", "stop")} if self.push else None)}


_SIMS: dict[str, SiteSim] = {}
_SIMS_LOCK = threading.Lock()


def get(site_id: str) -> SiteSim | None:
    from .sites import SITE_BY_ID
    s = SITE_BY_ID.get(site_id)
    if s is None:
        return None
    from . import link
    if link.current() == site_id:                     # 연동 선택된 병원 = 에뮬레이터 세계를 그 형식으로
        ls = link.linked_sim()
        if ls is not None:
            return ls
    with _SIMS_LOCK:
        sim = _SIMS.get(site_id)
        if sim is None:
            sim = _SIMS[site_id] = SiteSim(s)
    return sim


def all_sims() -> list[SiteSim]:
    from .sites import SITES
    return [get(s["id"]) for s in SITES]
