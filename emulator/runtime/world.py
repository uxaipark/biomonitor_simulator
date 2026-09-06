"""World engine: 1 Hz scenario simulation that drives the shared tables.

Owns patients, patches, gateways, admissions/discharges, activities and trips,
exam schedules, patch lifecycle (battery / lead-off / replacement), gateway
faults and network scenarios, MCOT home scenarios, arrhythmia episodes and the
gateway meta file consumed by the fast path workers.
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

from ..config import Config, channel_mask, CHANNELS, CHANNEL_BY_KEY, RESP_SOURCES, SPO2_SOURCES, BASE_DIR
from ..db import DB
from ..hospital.hospital import Hospital, EXAM_ROOMS, NO_BLE_ROOMS, GW_TYPE_ID
from ..hospital import layout as hospital_layout
from ..hospital.profiles import EXAM_TYPES, make_profiles, make_exam_schedule
from ..hospital.avatars import assign as assign_avatar
from ..hospital.devices import DEVICES, assign_devices, devices_mask, spo2_source
from ..signals.loops import LoopBank
from ..signals.rhythms import RHYTHMS
from ..signals.accel import ACTIVITIES
from ..signals.slow import TEMP_PROFILES, GLUCOSE_PROFILES
from ..signals import trend as trend_model
from .state import SharedState, CTL, FUZZ_KINDS, FLAG_LEAD_OFF, FLAG_MOTION, FLAG_LOW_BATT, FLAG_SPO2_OFF, FLAG_PACED, FLAG_NEW_PATCH, FLAG_CHARGING

ACT_ID = {a: i for i, a in enumerate(ACTIVITIES)}
POSTURE_ID = {"supine": 0, "left_lateral": 1, "right_lateral": 2, "sitting": 3, "standing": 4, "prone": 5}
RUNTIME_DIR = BASE_DIR / "runtime"
META_PATH = RUNTIME_DIR / "meta.json"
PATCH_FW = "bp-fw 3.2.0"


class EventLog:
    def __init__(self, n: int = 600):
        self.buf: collections.deque = collections.deque(maxlen=n)
        self.pending: list[dict] = []
        self.seq = 0

    def add(self, kind: str, msg: str, **kw):
        self.seq += 1
        e = {"seq": self.seq, "t": time.time(), "kind": kind, "msg": msg, **kw}
        self.buf.append(e)
        self.pending.append(e)

    def recent(self, since: int = 0, limit: int = 200) -> list[dict]:
        return [e for e in list(self.buf)[-limit:] if e["seq"] > since]


class Patch:
    __slots__ = ("row", "serial", "patch_id", "patient_id", "battery", "activated", "attached", "lead_off_until", "new_until", "replaced")

    def __init__(self, row, serial, patch_id):
        self.row, self.serial, self.patch_id = row, serial, patch_id
        self.patient_id = 0
        self.battery = 100.0
        self.activated = 0.0
        self.attached = True
        self.lead_off_until = 0.0
        self.new_until = 0.0
        self.replaced = 0


class World:
    def __init__(self, cfg: Config, bank: LoopBank, log: EventLog):
        self.cfg = cfg
        self.bank = bank
        self.log = log
        self.lock = threading.RLock()
        self.rng = np.random.default_rng(cfg.get("general", "seed") + 7)
        self.sim_time = time.time()
        self.cfg_start_time = time.time()
        self.running = False
        self.st: SharedState | None = None
        self.hospital: Hospital | None = None
        self.profiles: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.admitted: dict[int, dict] = {}       # patient_id -> runtime record
        self.patches: dict[int, Patch] = {}       # row -> Patch
        self.patch_registry: dict[str, dict] = {}  # serial -> lifecycle record (never deleted)
        self.free_rows: list[int] = []
        self.next_patch_serial = 1
        self.gw_state: list[dict] = []
        self.pool_cursor = 0
        self.stats_prev: dict = {}
        self.meta_dirty = True
        self.meta_last_write = 0.0
        self.meta_versions: dict[int, int] = {}
        self.counters = collections.Counter()
        self._exam_quota_t = 0.0
        self.exam_book: dict[str, list[tuple[float, float]]] = {}      # room -> booked (start, end) intervals, sorted by start
        self.exam_load: collections.Counter = collections.Counter()  # room -> patients on an exam trip to it right now
        self.autotune: dict | None = None
        self.net_events: list[dict] = []
        self.script: dict | None = None          # scheduled scenario script (test drills replayed at fixed sim times)
        self.rhythm_variants: dict[str, list[int]] = {}
        self.db = DB()
        self.build()

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        cfg = self.cfg.snapshot()
        g, s, sc = cfg["general"], cfg["signals"], cfg["scenario"]
        with self.lock:
            self.rng = np.random.default_rng(g["seed"] + 7)
            t0 = time.time()
            self.mobile_pool = max(200, g["outpatient_count"])          # pre-created mobile gateways (MCOT) so the count can change at runtime
            hp = cfg.get("hospital", {}) or {}
            layout_data = None
            if hp.get("layout_file"):
                try:
                    with open(hp["layout_file"], "r", encoding="utf-8") as f:
                        layout_data = json.load(f)
                    if hospital_layout.validate(layout_data):
                        layout_data = None
                except Exception:
                    layout_data = None
            # hospital size follows the scenario: the monitored patients fill (100 - headroom) % of the beds, so a 500-patient
            # scenario gets a ~625-bed hospital (1-3 buildings, floors sized to that), not the 2000-bed maximum
            beds = self.planned_beds()
            self.hospital = Hospital(beds, g["seed"], sc["gateway"]["capacity"], sc["gateway"]["corridor_gateways"],
                                     outpatients=self.mobile_pool, template=hp.get("template", "auto"), layout_data=layout_data,
                                     max_buildings=int(hp.get("max_buildings", 3) or 3))
            self.profiles = make_profiles(g["profile_count"], g["seed"], g["heart_disease_ratio"], g["korean_ratio"], s["pacemaker_ratio"])
            arng = np.random.default_rng(g["seed"] + 99)
            for p in self.profiles:
                p["avatar"] = assign_avatar(arng, p)
                p["status"] = "pool"
            self.by_id = {p["id"]: p for p in self.profiles}
            n_patch = self.hospital.bed_capacity + self.mobile_pool + 64
            n_gw = len(self.hospital.gateways)
            old = self.st
            self.st = SharedState(n_patch, n_gw, 16)
            if old is not None:
                old.close()
                old.unlink()
            self.patches = {}
            self.admitted = {}
            sig = {"seed": g["seed"], "profile_count": g["profile_count"], "bed_capacity": g["bed_capacity"], "mobile_pool": self.mobile_pool,
                   "template": self.hospital.template, "layout_file": hp.get("layout_file", ""),
                   "heart_ratio": g["heart_disease_ratio"], "korean_ratio": g["korean_ratio"], "pm_ratio": s["pacemaker_ratio"]}
            kept = self.db.begin_session(sig)
            if hasattr(self, "_bld_beds"):
                del self._bld_beds                                 # per-building bed counts are recomputed for the new hospital
            if kept:
                days = float(self.cfg.get("general").get("db_retention_days", 7) or 0)
                if days > 0:
                    pruned = self.db.prune(time.time() - days * 86400)
                    if sum(pruned.values()):
                        self.log.add("system", f"DB 정리: {days:.0f}일 지난 이력 삭제 " + ", ".join(f"{k} {v}" for k, v in pruned.items() if v))
            if not kept:
                self.db.upsert_patients(self.profiles)
            # patch numbering continues from the DB so serials/ids never repeat across restarts or rebuilds
            self.next_patch_serial = max(1, self.db.max_patch_serial() + 1)
            self.patch_registry = {}
            if kept:
                # active patches + the most recent retired ones only (memory stays flat across restarts; older history lives in the DB)
                for r in self.db.query("SELECT * FROM patches ORDER BY (status='active') DESC, patch_id DESC", limit=20000)["rows"]:
                    cols = ["serial", "patch_id", "patient_id", "patient_name", "status", "issued_at", "issued_reason", "retired_at", "retired_reason", "battery_at_retire"]
                    e = dict(zip(cols, r))
                    e["row"] = None
                    self.patch_registry[e["serial"]] = e
            # gateway hardware numbers: reuse the active unit per row when history is kept, else 1..n
            gws = self.hospital.gateways
            if kept and self.db.max_gateway_no() > 0:
                for row, gw_no, gw_id, mac in self.db.query("SELECT row, gw_no, gw_id, mac FROM gateways WHERE status='active' ORDER BY gw_no", limit=1000000)["rows"]:
                    if row is not None and 0 <= row < len(gws):
                        gws[row].update(gw_no=int(gw_no), id=gw_id, mac=mac)
                self.next_gw_no = self.db.max_gateway_no() + 1
                seen: set[int] = set()
                for g in gws:                                              # safeguard: every unit gets a unique number
                    if g["gw_no"] in seen:
                        g["gw_no"] = self.next_gw_no
                        self.next_gw_no += 1
                        self.db.upsert_gateway(self._gw_db_row(g, "번호 재발급"))
                    seen.add(g["gw_no"])
            else:
                self.next_gw_no = len(gws) + 1
                self.db.upsert_gateways([self._gw_db_row(g, "초기 설치") for g in gws])
            self.log.add("system", f"DB {'이력 유지' if kept else '새로 생성'}: {self.db.path.name} (다음 패치 번호 BP-{self.next_patch_serial:06d}, 다음 게이트웨이 번호 {self.next_gw_no})")
            self.free_rows = list(range(n_patch - 1, -1, -1))
            self.pool_cursor = 0
            self.gw_state = []
            G = self.st.gw.arr
            for gw in self.hospital.gateways:
                r = gw["idx"]
                G["active"][r] = 0 if gw["type"] == "mobile" else 1        # mobile gateways activate when handed to an MCOT patient
                G["gw_id"][r] = gw["gw_no"]                                  # unique hardware number (changes when the unit is replaced)
                G["type"][r] = gw["type_id"]
                G["cpu"][r] = int(self.rng.uniform(8, 20))
                G["mem"][r] = int(self.rng.uniform(30, 45))
                G["wan_rssi"][r] = int(self.rng.uniform(-70, -45))
                G["uptime_s"][r] = int(self.rng.uniform(3600, 86400 * 30))
                G["temp_c"][r] = int(self.rng.uniform(38, 52))
                G["battery"][r] = 100 if gw["type"] == "mobile" else 0
                self.gw_state.append({"fault_until": 0.0, "degraded_until": 0.0, "cpu_base": float(G["cpu"][r]), "mem_base": float(G["mem"][r]),
                                      "n_conn": 0, "boot_at": 0.0, "mobile_batt": 100.0, "charging": False, "reason": ""})
            self.rhythm_variants = dict(self.bank.rhythm_variants) if self.bank.loaded else {}
            self.apply_config()
            self.sim_time = time.time()
            self.log.add("system", f"월드 구성 완료: 병상 {self.hospital.bed_capacity}, 게이트웨이 {n_gw}, 프로필 {len(self.profiles)} ({time.time() - t0:.1f}s)")
            self.meta_dirty = True
            self._sync_population(initial=True)

    def apply_config(self) -> None:
        """Push transport/signal config into the control array (safe at runtime)."""
        cfg = self.cfg.snapshot()
        s, t = cfg["signals"], cfg["transport"]
        c = self.st.ctl
        c[CTL["bundle_ms"]] = t["bundle_ms"]
        c[CTL["chan_mask"]] = channel_mask(s["enabled"])
        c[CTL["ecg_fs"]] = s["ecg_fs"]
        c[CTL["ppg_fs"]] = s["ppg_fs"]
        c[CTL["resp_fs"]] = s["resp_fs"]
        c[CTL["accel_fs"]] = s["accel_fs"]
        c[CTL["meta_every"]] = t["meta_every_n_frames"]
        c[CTL["gwstat_every"]] = t["gw_status_every_n_frames"]
        c[CTL["target_port"]] = t["target_port"]
        c[CTL["socket_mode"]] = 0 if t["socket_mode"] == "per_gateway" else 1
        c[CTL["crossfade_ticks"]] = max(1, 1000 // t["bundle_ms"])
        c[CTL["loop_seconds"]] = s["loop_seconds"]
        c[CTL["max_backlog"]] = t["max_send_backlog_bytes"]
        c[CTL["reconnect_s"]] = t["reconnect_interval_s"]
        c[CTL["generate_only"]] = 0 if t["target_ip"] else 1
        sf, fz = t.get("store_forward", {}), t.get("fuzz", {})
        c[CTL["saf_enabled"]] = 1 if sf.get("enabled", True) else 0
        c[CTL["saf_max_bytes"]] = sf.get("max_bytes_per_gw", 2097152)
        c[CTL["saf_burst"]] = sf.get("burst_frames_per_cycle", 40)
        c[CTL["fuzz_rate"]] = fz.get("rate_per_1000", 0) if fz.get("enabled") else 0
        c[CTL["fuzz_mask"]] = sum(1 << FUZZ_KINDS.index(k) for k in fz.get("kinds", []) if k in FUZZ_KINDS)
        c[CTL["connect_budget"]] = 3 if t.get("storm_smoothing", True) else 100000
        c[CTL["tap"]] = 1 if t.get("capture", {}).get("enabled") else 0
        self.st.set_target(t["target_ip"])
        P = self.st.patch.arr
        rs = RESP_SOURCES.index(s["resp_source"])
        P["resp_src"][:] = rs                       # SpO2 source is per patient (device set)
        self.meta_dirty = True

    # ------------------------------------------------------------------ helpers
    def _new_patch(self, row: int, patient_id: int = 0, reason: str = "신규 발급") -> Patch:
        """Issue a brand-new patch: serial/patch_id are monotonic and never reused (registry keeps every patch)."""
        serial = f"BP-{self.next_patch_serial:06d}"
        p = Patch(row, serial, 0x10000 + self.next_patch_serial)
        self.next_patch_serial += 1
        p.activated = self.sim_time
        p.patient_id = patient_id
        self.patches[row] = p
        self.patch_registry[serial] = {"serial": serial, "patch_id": p.patch_id, "patient_id": patient_id,
                                       "patient_name": self.by_id[patient_id]["name"] if patient_id in self.by_id else None,
                                       "status": "active", "issued_at": self.sim_time, "issued_reason": reason,
                                       "retired_at": None, "retired_reason": None, "battery_at_retire": None, "row": row}
        self.db.upsert_patch(self.patch_registry[serial])
        self.db.add_patch_event(self.sim_time, serial, patient_id, "issued", reason)
        return p

    def _retire_patch(self, patch: "Patch | None", reason: str) -> None:
        if patch is None:
            return
        e = self.patch_registry.get(patch.serial)
        if e and e["status"] == "active":
            e.update(status="retired", retired_at=self.sim_time, retired_reason=reason, battery_at_retire=round(patch.battery, 1), row=None)
            self.counters["patches_retired"] += 1
            self.db.upsert_patch(e)
            self.db.add_patch_event(self.sim_time, patch.serial, patch.patient_id, "retired", reason)

    def patch_registry_view(self, status: str = "all", q: str = "", offset: int = 0, limit: int = 100, sort: str = "patch_id", direction: str = "desc") -> dict:
        items = list(self.patch_registry.values())
        if status in ("active", "retired"):
            items = [e for e in items if e["status"] == status]
        if q:
            ql = q.lower()
            items = [e for e in items if ql in e["serial"].lower() or (e["patient_name"] or "").lower().find(ql) >= 0 or ql == str(e["patient_id"])]
        def battery_of(e):
            live = self.patches.get(e["row"]) if e["row"] is not None else None
            return live.battery if live and live.serial == e["serial"] else (e["battery_at_retire"] if e["battery_at_retire"] is not None else -1)
        keys = {"serial": lambda e: e["serial"], "patch_id": lambda e: e["patch_id"], "status": lambda e: e["status"],
                "patient_name": lambda e: (e["patient_name"] or ""), "issued_at": lambda e: e["issued_at"] or 0, "issued_reason": lambda e: e["issued_reason"] or "",
                "retired_at": lambda e: e["retired_at"] or 0, "retired_reason": lambda e: e["retired_reason"] or "", "battery": battery_of}
        kf = keys.get(sort, keys["patch_id"])
        items.sort(key=lambda e: (kf(e), e["patch_id"]), reverse=(direction != "asc"))
        fmt = lambda t: dt.datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else None
        out = []
        for e in items[offset: offset + limit]:
            live = self.patches.get(e["row"]) if e["row"] is not None else None
            out.append({**e, "issued_at": fmt(e["issued_at"]), "retired_at": fmt(e["retired_at"]),
                        "battery": round(live.battery, 1) if live and live.serial == e["serial"] else e["battery_at_retire"]})
        n_active = sum(1 for e in self.patch_registry.values() if e["status"] == "active")
        return {"total": len(items), "active": n_active, "retired": len(self.patch_registry) - n_active, "next_serial": f"BP-{self.next_patch_serial:06d}", "patches": out}

    def _variant_for(self, rhythm: str, age: int) -> int:
        vs = self.rhythm_variants.get(rhythm) or self.rhythm_variants.get("nsr") or [0]
        return int(self.rng.choice(vs))

    def _similar_variant(self, rhythm: str, current: int, tol: float = 12.0) -> int:
        """Another variant of the same rhythm with a similar mean HR (falls back to any other)."""
        vs = [v for v in (self.rhythm_variants.get(rhythm) or []) if v != current]
        if not vs:
            return current
        meta = self.bank.index.get("variants", []) if self.bank.loaded else []
        try:
            hr0 = meta[current]["hr_mean"]
            near = [v for v in vs if abs(meta[v]["hr_mean"] - hr0) <= tol]
        except (IndexError, KeyError, TypeError):
            near = []
        return int(self.rng.choice(near or vs))

    def _slow_var(self, profiles: list[str], name: str) -> int:
        per = self.bank.index.get("slow_per_profile", 24) if self.bank.loaded else 24
        k = profiles.index(name) if name in profiles else 0
        return k * per + int(self.rng.integers(per))

    def _init_patch_row(self, row: int, prof: dict, patch: Patch) -> None:
        P = self.st.patch.arr
        P["active"][row] = 0
        P["gw"][row] = -1
        P["patch_id"][row] = patch.patch_id
        P["patient_id"][row] = prof["id"]
        v = self._variant_for(prof["rhythm"], prof["age"])
        P["variant"][row] = v
        P["variant_prev"][row] = -1
        P["switch_tick"][row] = -10 ** 9
        P["offset_ms"][row] = int(self.rng.integers(0, self.bank.seconds * 1000))
        P["gain"][row] = float(self.rng.uniform(0.75, 1.3)) * (0.85 if prof["bmi"] > 30 else 1.0)
        P["noise"][row] = float(self.rng.uniform(0.0, 0.03))
        P["activity"][row] = ACT_ID["still"]
        P["act_offset_ms"][row] = int(self.rng.integers(0, self.bank.seconds * 1000))
        P["art_gain"][row] = 0.0
        P["posture"][row] = POSTURE_ID["supine"]
        P["lead_off"][row] = 0
        P["spo2_off"][row] = 0
        P["battery"][row] = int(patch.battery)
        P["rssi"][row] = -60
        P["temp_var"][row] = self._slow_var(TEMP_PROFILES, prof["temp_profile"])
        P["gluc_var"][row] = self._slow_var(GLUCOSE_PROFILES, prof["glucose_profile"])
        P["temp_bias"][row] = float(self.rng.normal(0, 0.15))
        P["spo2_bias"][row] = int(self.rng.integers(-1, 2))
        P["paced"][row] = 1 if prof["pacemaker"] else 0
        self._apply_devices_row(row, prof)
        pmi = prof.get("pacemaker_info")
        P["pace_amp"][row] = pmi["spike_amp_mv"] * 1000.0 if pmi else 0.0
        P["pace_detect"][row] = pmi["detect_pct"] if pmi else 0
        P["hr_override"][row] = 0
        P["attach_tick"][row] = 0
        P["detach_tick"][row] = 0
        P["flags"][row] = FLAG_PACED if prof["pacemaker"] else 0
        st = trend_model.state(prof["id"], prof, self.sim_time)
        P["hr_scale"][row] = st["hr_scale"]
        P["rr_add"][row] = st["rr_add"]
        P["spo2_add"][row] = st["spo2_add"]
        P["temp_add"][row] = st["temp_add"]
        P["gl_add"][row] = st["gl_add"]

    def _apply_devices_row(self, row: int, prof: dict) -> None:
        """Write the patient's device set into the patch row (channel mask + SpO2 source)."""
        P = self.st.patch.arr
        devs = prof.get("devices") or ["ecg_patch"]
        P["chan_mask"][row] = devices_mask(devs)
        src = spo2_source(devs)
        P["spo2_src"][row] = src if src is not None else 0

    def set_devices(self, pid: int, devices: list[str]) -> list[str]:
        """Per-patient override from the UI/API."""
        with self.lock:
            prof = self.by_id[pid]
            devs = ["ecg_patch"] + [d for d in dict.fromkeys(devices) if d in DEVICES and d != "ecg_patch"]
            spo2 = [d for d in devs if d in ("spo2_fingertip", "spo2_ring", "bp_wrist")]
            if len(spo2) > 1:                                   # one SpO2 sensor at a time
                devs = [d for d in devs if d not in spo2[:-1]]
            prof["devices"] = devs
            self.db.update_patient(prof)
            rec = self.admitted.get(pid)
            if rec:
                self._apply_devices_row(rec["row"], prof)
                self.meta_dirty = True
            self.log.add("patch", f"{prof['name']} 기기 세트 변경: {', '.join(DEVICES[d]['short'] for d in devs)}", patient_id=pid)
            return devs

    def apply_device_policy(self, policy: str | None = None) -> dict:
        """Re-assign device sets of all monitored patients according to the policy."""
        with self.lock:
            dcfg = self.cfg.get("scenario", "devices", default={}) or {}
            policy = policy or dcfg.get("policy", "auto")
            counts: dict[str, int] = {k: 0 for k in DEVICES}
            for pid, rec in self.admitted.items():
                prof = self.by_id[pid]
                prof["devices"] = assign_devices(self.rng, prof, rec["outpatient"], policy, dcfg.get("spo2_mix"))
                self._apply_devices_row(rec["row"], prof)
                self.db.update_patient(prof)
                for d in prof["devices"]:
                    counts[d] += 1
            self.meta_dirty = True
            self.log.add("system", f"기기 세트 일괄 적용 ({policy}): 환자 {len(self.admitted)}명")
            return {"policy": policy, "patients": len(self.admitted), "counts": counts}

    def device_stats(self) -> dict:
        counts: dict[str, int] = {k: 0 for k in DEVICES}
        for pid in self.admitted:
            for d in (self.by_id[pid].get("devices") or ["ecg_patch"]):
                counts[d] += 1
        return {"patients": len(self.admitted), "counts": counts}

    def _pick_pool_patient(self, want_ward: bool = True) -> dict | None:
        n = len(self.profiles)
        for _ in range(n):
            p = self.profiles[self.pool_cursor % n]
            self.pool_cursor += 1
            if p["status"] == "pool":
                return p
        return None

    # ------------------------------------------------------------------ admissions
    def admit(self, prof: dict | None = None, outpatient: bool = False) -> dict | None:
        h = self.hospital
        prof = prof or self._pick_pool_patient()
        if prof is None or not self.free_rows:
            return None
        row = self.free_rows.pop()
        patch = self._new_patch(row, prof["id"], "입원 부착" if not outpatient else "MCOT 부착")
        rec = {"id": prof["id"], "row": row, "outpatient": outpatient, "admit_time": self.sim_time, "bed_idx": -1, "room_idx": -1,
               "ward_idx": -1, "doctor": None, "nurse": None, "exams": [], "location": -1, "activity": "still", "activity_until": 0.0,
               "trip": [], "trip_step_until": 0.0, "posture": "supine", "episode_until": 0.0, "base_variant": int(self.st.patch["variant"][row]),
               "gw": -1, "mobile_gw": -1, "home_state": "home", "next_rssi": 0.0, "last_move": self.sim_time, "sleeping": False,
               "shadow": False, "note": "", "next_hop": self.sim_time + self.rng.uniform(480, 1500), "next_slow_hop": self.sim_time + self.rng.uniform(1800, 5400),
               "gain0": 1.0}
        if outpatient:
            used = {r["mobile_gw"] for r in self.admitted.values() if r["outpatient"]}
            free = [g["idx"] for g in h.gateways if g["type"] == "mobile" and g["idx"] not in used]
            if not free:
                self.free_rows.append(row)
                del self.patches[row]
                return None
            rec["mobile_gw"] = free[0]
            self.st.gw["active"][free[0]] = 1
            self.gw_state[free[0]]["mobile_batt"] = float(self.rng.uniform(40, 100))
            self.st.gw["status"][free[0]] = 0
            rec["location"] = -1
            prof["status"] = "outpatient"
            rec["doctor"] = h.wards[0]["doctor_ids"][0] if h.wards else None
        else:
            widx = -1
            bidx = -1
            for w in h.ward_for(prof["ward_specialty"], self.rng):
                b = h.free_bed_in_ward(w)
                if b >= 0:
                    widx, bidx = w, b
                    break
            if bidx < 0:
                self.free_rows.append(row)
                del self.patches[row]
                return None
            bed = h.beds[bidx]
            bed["patient_id"] = prof["id"]
            rec.update(bed_idx=bidx, room_idx=bed["room_idx"], ward_idx=widx, location=bed["room_idx"])
            ward = h.wards[widx]
            rec["doctor"] = str(self.rng.choice(ward["doctor_ids"]))
            rec["nurse"] = str(self.rng.choice(ward["nurse_ids"]))
            rec["exams"] = make_exam_schedule(self.rng, prof, self.sim_time, available=set(h.exam_rooms.keys()))
            self._book_exams(rec, rec["exams"])                  # no two patients get the same station at the same time
            prof["status"] = "admitted"
        prof["admission"] = {"time": dt.datetime.fromtimestamp(self.sim_time).isoformat(timespec="seconds"),
                             "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else None,
                             "room": h.rooms[rec["room_idx"]]["id"] if rec["room_idx"] >= 0 else None,
                             "ward": h.wards[rec["ward_idx"]]["id"] if rec["ward_idx"] >= 0 else None,
                             "ward_name": h.wards[rec["ward_idx"]]["name"] if rec["ward_idx"] >= 0 else "원외 (MCOT)",
                             "doctor": rec["doctor"], "nurse": rec["nurse"], "patch": patch.serial, "patch_id": patch.patch_id,
                             "exams": rec["exams"], "mode": "mcot" if outpatient else "inpatient"}
        dcfg = self.cfg.get("scenario", "devices", default={}) or {}
        prof["devices"] = assign_devices(self.rng, prof, outpatient, dcfg.get("policy", "auto"), dcfg.get("spo2_mix"))
        rec["adm_id"] = self.db.add_admission(prof["id"], prof["admission"], self.sim_time)
        rec["patient_no"] = rec["adm_id"]                                 # 환자번호: new unique number per admission (never reused)
        prof["admission"]["patient_no"] = rec["patient_no"]
        self.db.update_patient(prof)
        self._init_patch_row(row, prof, patch)
        self.st.patch["patient_id"][row] = rec["patient_no"]              # frames carry the admission-scoped patient number
        if self.running:
            self.st.patch["attach_tick"][row] = self._tick_now()        # new admission: patch just applied
        rec["base_variant"] = int(self.st.patch["variant"][row])      # after the variant is assigned (episode revert target)
        rec["gain0"] = float(self.st.patch["gain"][row])
        self.admitted[prof["id"]] = rec
        self._relink(rec, force=True)
        self.st.patch["active"][row] = 1
        self.counters["admissions"] += 1
        self.meta_dirty = True
        return rec

    def discharge(self, pid: int, reason: str = "퇴원") -> None:
        rec = self.admitted.pop(pid, None)
        if rec is None:
            return
        prof = self.by_id[pid]
        row = rec["row"]
        patch = self.patches.pop(row, None)
        self._retire_patch(patch, reason + " (반납)")
        self._unlink(rec)
        if rec["outpatient"] and rec["mobile_gw"] >= 0:
            self.st.gw["active"][rec["mobile_gw"]] = 0
        self.st.patch["active"][row] = 0
        self.st.patch["patient_id"][row] = 0
        if rec["bed_idx"] >= 0:
            self.hospital.beds[rec["bed_idx"]]["patient_id"] = 0
        prof["status"] = "discharged"
        prof.setdefault("history", []).append({"discharged": dt.datetime.fromtimestamp(self.sim_time).isoformat(timespec="seconds"),
                                                "reason": reason, "patch_returned": patch.serial if patch else None})
        self.db.close_admission(rec.get("adm_id"), pid, self.sim_time, reason)
        self.free_rows.append(row)
        self.counters["discharges"] += 1
        self.meta_dirty = True
        self.log.add("adt", f"{reason}: {prof['name']} ({prof['mrn']}) 패치 {patch.serial if patch else '-'} 전원 OFF·반납", patient_id=pid)

    def _sync_population(self, initial: bool = False) -> None:
        g = self.cfg.get("general")
        site = self.cfg.get("scenario", "site")
        target_in = g["active_patients"] if site in ("hospital", "mixed") else 0
        target_out = g["outpatient_count"] if site in ("mcot", "mixed") else 0
        n_in = sum(1 for r in self.admitted.values() if not r["outpatient"])
        n_out = len(self.admitted) - n_in
        budget = 100000 if initial else 300
        while n_in < target_in and budget > 0:
            if self.admit() is None:
                break
            n_in += 1
            budget -= 1
        while n_out < target_out and budget > 0:
            if self.admit(outpatient=True) is None:
                break
            n_out += 1
            budget -= 1
        if n_in > target_in:
            for pid in [k for k, r in self.admitted.items() if not r["outpatient"]][: n_in - target_in]:
                self.discharge(pid, "퇴원(목표 인원 조정)")
        if n_out > target_out:
            for pid in [k for k, r in self.admitted.items() if r["outpatient"]][: n_out - target_out]:
                self.discharge(pid, "MCOT 종료")

    # ------------------------------------------------------------------ links / RSSI
    def _unlink(self, rec: dict) -> None:
        gw = rec["gw"]
        if gw >= 0:
            self.gw_state[gw]["n_conn"] = max(0, self.gw_state[gw]["n_conn"] - 1)
            self.st.gw["n_conn"][gw] = self.gw_state[gw]["n_conn"]
        rec["gw"] = -1
        self.st.patch["gw"][rec["row"]] = -1

    def _relink(self, rec: dict, force: bool = False) -> None:
        """Connect the patch to the best reachable gateway with free capacity."""
        h = self.hospital
        G = self.st.gw.arr
        row = rec["row"]
        if rec["outpatient"]:
            gw = rec["mobile_gw"]
            ok = gw >= 0 and G["status"][gw] != 2 and rec["home_state"] != "shadow"
            new = gw if ok else -1
            rssi = -55 - (25 if rec["home_state"] == "outside" else 0) + self.rng.normal(0, 3)
        else:
            new = -1
            rssi = -100
            if rec["location"] >= 0 and not rec["shadow"]:
                cands = h.candidate_gateways(rec["location"])
                for gw in cands[:6]:
                    gw = int(gw)
                    r = h.rssi_from(rec["location"], gw, self.rng)
                    if r < -92:
                        break
                    if G["status"][gw] == 2:
                        continue
                    if self.gw_state[gw]["n_conn"] >= h.gateways[gw]["capacity"] and gw != rec["gw"]:
                        continue
                    new, rssi = gw, r
                    break
        if new != rec["gw"] or force:
            old = rec["gw"]
            self._unlink(rec)
            if new >= 0:
                self.gw_state[new]["n_conn"] += 1
                G["n_conn"][new] = min(255, self.gw_state[new]["n_conn"])
            rec["gw"] = new
            self.st.patch["gw"][row] = new
            self.meta_dirty = True
            if not force and old != new:
                p = self.by_id[rec["id"]]
                if new < 0:
                    self.log.add("link", f"{p['name']} 패치 연결 끊김 ({h.gateways[old]['id'] if old >= 0 else '-'}) - 음영/게이트웨이 장애", patient_id=p["id"])
                    self.counters["link_lost"] += 1
                elif old >= 0:
                    self.log.add("link", f"{p['name']} 게이트웨이 이동 {h.gateways[old]['id']} → {h.gateways[new]['id']}", patient_id=p["id"])
                    self.counters["gw_handover"] += 1
                else:
                    self.log.add("link", f"{p['name']} 패치 재연결 → {h.gateways[new]['id']}", patient_id=p["id"])
        self.st.patch["rssi"][row] = int(np.clip(rssi, -120, 0))

    # ------------------------------------------------------------------ activities
    def _set_activity(self, rec: dict, act: str, dur: float, art: float, posture: str | None = None) -> None:
        P = self.st.patch.arr
        row = rec["row"]
        rec["activity"] = act
        rec["activity_until"] = self.sim_time + dur
        P["activity"][row] = ACT_ID[act]
        P["art_gain"][row] = art
        if posture:
            rec["posture"] = posture
            P["posture"][row] = POSTURE_ID[posture]
        f = int(P["flags"][row])
        f = (f | FLAG_MOTION) if art > 0.15 else (f & ~FLAG_MOTION)
        P["flags"][row] = f

    def _start_trip(self, rec: dict, steps: list, note: str) -> None:
        """steps: (room_idx or 'shadow'/'mri', duration_s, activity, art_gain[, label])"""
        steps = [tuple(st) + (self._stage_label(st),) if len(st) == 4 else tuple(st) for st in steps]
        rec["trip"] = list(steps)
        rec["trip_plan"] = [{"label": st[4], "dur": float(st[1]), "done": False} for st in steps]
        rec["trip_started"] = self.sim_time
        rec["note"] = note
        self._next_trip_step(rec)

    def _stage_label(self, st) -> str:
        loc, dur, act, art = st[:4]
        if loc == "mri":
            return "MRI 촬영 (패치 분리)"
        if loc == "shadow":
            return "엘리베이터 이동 (음영)" if act == "transfer" else "음영 구간"
        r = self.hospital.rooms[int(loc)]
        base = r["name"] if r["kind"] != "room" else f"병실 {r['id']}"
        return {"walking": f"{base} 보행", "transfer": f"{base} 이송", "shower": "샤워", "exercise": "재활 운동"}.get(act, f"{base} 대기" if r["kind"] in ("elevator", "lobby") else base)

    def _next_trip_step(self, rec: dict) -> None:
        if rec.get("mri_pending"):                              # MRI finished: reattach with a NEW patch
            rec["mri_pending"] = False
            rec["patch_removed_until"] = 0.0
            self._replace_patch(rec, "MRI 촬영 후 새 패치 부착")
        if not rec["trip"]:
            if rec.get("exam_room"):
                self.exam_load[rec["exam_room"]] = max(0, self.exam_load[rec["exam_room"]] - 1)
                rec["exam_room"] = None
            rec["note"] = ""
            rec["stage_label"] = ""
            rec["trip_plan"] = []
            rec["shadow"] = False
            rec["location"] = rec["room_idx"]
            self._set_activity(rec, "still", self.rng.uniform(60, 600), 0.0, "supine")
            self._relink(rec)
            return
        step = rec["trip"].pop(0)
        loc, dur, act, art = step[:4]
        rec["stage_label"] = step[4] if len(step) > 4 else self._stage_label(step)
        plan = rec.get("trip_plan") or []
        for pstep in plan:
            if not pstep["done"]:
                pstep["done"] = True
                break
        if loc == "mri":                                        # patch removed for the scan (lead-off), MRI room has no BLE
            rec["mri_pending"] = True
            rec["patch_removed_until"] = self.sim_time + dur
            self.log.add("patch", f"{self.by_id[rec['id']]['name']} MRI 촬영: 패치 분리", patient_id=rec["id"])
        rec["shadow"] = loc in ("shadow", "mri")
        rec["location"] = rec["room_idx"] if loc in ("shadow", "mri") else int(loc)
        rec["trip_step_until"] = self.sim_time + dur
        self._set_activity(rec, act, dur, art, "standing" if act in ("walking", "transfer") else ("sitting" if act == "still" else None))
        self._relink(rec)

    def _room_named(self, prefixes, bidx: int | None = None, floor: int | None = None) -> int:
        """First room whose name starts with one of `prefixes` (optionally on a given building/floor), else -1."""
        if isinstance(prefixes, str):
            prefixes = (prefixes,)
        for r in self.hospital.rooms:
            if (bidx is None or r["building_idx"] == bidx) and (floor is None or r["floor"] == floor) and any(r["name"].startswith(k) for k in prefixes):
                return r["idx"]
        return -1

    def _home_bidx(self, rec: dict) -> int:
        return self.hospital.rooms[rec["room_idx"]]["building_idx"] if rec["room_idx"] >= 0 else 0

    def _room_for(self, rec: dict, prefixes) -> int:
        """Room for this patient: the one in their own building's diagnostic floor when it exists (every building has its own
        ECG / blood-draw / lobby / clinic...), otherwise the first such room anywhere in the hospital."""
        r = self._room_named(prefixes, self._home_bidx(rec))
        return r if r >= 0 else self._room_named(prefixes)

    def _rkey(self, room_idx: int, name: str) -> str:
        """Load / booking key: room name scoped to the building the room is in (each building has its own stations)."""
        b = self.hospital.rooms[room_idx]["building_idx"] if room_idx >= 0 else 0
        return f"{b}:{name}"

    def display_location(self, rec: dict) -> int:
        """Room to draw the patient in: the MRI bore while the patch is off, the lift on the home floor while in the shadow
        zone, otherwise the current room (bed room when idle)."""
        h = self.hospital
        moving = bool(rec["trip"]) or rec["trip_step_until"] > self.sim_time
        if moving and rec["shadow"]:
            mri = self._room_for(rec, "MRI실")
            if rec.get("mri_pending") and mri >= 0:
                return mri
            home = h.rooms[rec["room_idx"]]
            ev = h.floor_rooms(home["building_idx"], home["floor"], "elevator")
            return ev[0]["idx"] if ev else rec["location"]
        return rec["location"]

    def _route_out(self, rec: dict, dest_room: int) -> tuple[list, list]:
        """Ward -> destination legs and the way back (corridor / lift hall / lift (shadow) / lobby), reused by every trip."""
        h = self.hospital
        room = h.rooms[rec["room_idx"]]
        corridor = [r["idx"] for r in h.floor_rooms(room["building_idx"], room["floor"], "corridor")]
        elevs = h.floor_rooms(room["building_idx"], room["floor"], "elevator")
        elev = elevs[0]["idx"] if elevs else rec["room_idx"]
        lobby = h.lobby_room(room["building_idx"])
        dest = h.rooms[dest_room] if dest_room >= 0 else None
        same_floor = dest is not None and dest["building_idx"] == room["building_idx"] and dest["floor"] == room["floor"]
        walk = self.rng.uniform(30, 90)
        out = [(corridor[0] if corridor else "shadow", walk, "walking", 0.55)]
        back = [(corridor[-1] if corridor else "shadow", walk, "walking", 0.55)]
        if not same_floor:
            out += [(elev, self.rng.uniform(15, 40), "still", 0.2), ("shadow", self.rng.uniform(20, 60), "transfer", 0.5)]
            back = [("shadow", self.rng.uniform(20, 60), "transfer", 0.5), (elev, self.rng.uniform(10, 30), "still", 0.2)] + back
            if dest is None or dest["floor"] <= 1 or lobby >= 0 and h.rooms[lobby]["floor"] == dest["floor"]:
                out.append((lobby if lobby >= 0 else "shadow", self.rng.uniform(20, 60), "walking", 0.5))
                back = [(lobby if lobby >= 0 else "shadow", self.rng.uniform(20, 60), "walking", 0.5)] + back
            elif dest is not None:
                dc = [r["idx"] for r in h.floor_rooms(dest["building_idx"], dest["floor"], "corridor")]
                if dc:
                    out.append((dc[0], self.rng.uniform(20, 50), "walking", 0.5))
                    back = [(dc[0], self.rng.uniform(20, 50), "walking", 0.5)] + back
        return out, back

    def _ward_corridors(self, rec: dict) -> list:
        room0 = self.hospital.rooms[rec["room_idx"]]
        return [x["idx"] for x in self.hospital.floor_rooms(room0["building_idx"], room0["floor"], "corridor")]

    def _trip_toilet(self, rec: dict) -> None:
        h = self.hospital; c = self._ward_corridors(rec)
        toilet = h.ward_room_of_kind(rec["ward_idx"], "toilet") if rec["ward_idx"] >= 0 else -1
        toilet = toilet if toilet >= 0 else rec["room_idx"]
        self._start_trip(rec, [(c[0] if c else "shadow", self.rng.uniform(20, 50), "walking", 0.5), (toilet, self.rng.uniform(120, 400), "still", 0.1),
                               (c[0] if c else "shadow", self.rng.uniform(20, 50), "walking", 0.5)], "화장실")

    def _trip_walk(self, rec: dict) -> None:
        c = self._ward_corridors(rec)
        self._start_trip(rec, [(x, self.rng.uniform(30, 120), "walking", 0.55) for x in (c + c[::-1] if c else ["shadow"])], "복도 보행")

    def _trip_shower(self, rec: dict) -> None:
        h = self.hospital; c = self._ward_corridors(rec)
        shower = h.ward_room_of_kind(rec["ward_idx"], "shower") if rec["ward_idx"] >= 0 else -1
        shower = shower if shower >= 0 else rec["room_idx"]
        self._start_trip(rec, [(c[0] if c else "shadow", self.rng.uniform(20, 50), "walking", 0.5), (shower, self.rng.uniform(400, 900), "shower", 0.9),
                               (c[0] if c else "shadow", self.rng.uniform(20, 50), "walking", 0.5)], "샤워")
        rec["spo2_off_until"] = self.sim_time + 1000
        self.st.patch["spo2_off"][rec["row"]] = 1

    def _trip_rehab(self, rec: dict) -> None:
        h = self.hospital
        rehab = self._room_for(rec, "재활치료실")
        out, back = self._route_out(rec, rehab)
        self._start_trip(rec, out + [(rehab if rehab >= 0 else "shadow", self.rng.uniform(600, 1500), "exercise", 0.85)] + back, "운동/재활")

    def _trip_shadow(self, rec: dict) -> None:
        """Ride the lift / stairwell to another floor and back: a pure shadow-zone excursion (link lost, then recovered)."""
        h = self.hospital; c = self._ward_corridors(rec)
        room0 = h.rooms[rec["room_idx"]]
        elevs = h.floor_rooms(room0["building_idx"], room0["floor"], "elevator")
        elev = elevs[0]["idx"] if elevs else "shadow"
        steps = [(c[0] if c else "shadow", self.rng.uniform(20, 60), "walking", 0.5), (elev, self.rng.uniform(10, 40), "still", 0.2),
                 ("shadow", self.rng.uniform(60, 240), "transfer", 0.5, "엘리베이터/계단실 (음영 구간)"), (elev, self.rng.uniform(10, 40), "still", 0.2),
                 (c[-1] if c else "shadow", self.rng.uniform(20, 60), "walking", 0.5)]
        self._start_trip(rec, steps, "음영 구간 진입")

    def _plan_visit_trip(self, rec: dict, visit: tuple) -> bool:
        """Send an inpatient to a non-exam destination (clinic, ultrasound, lounge, family room, cafe, pharmacy, billing)."""
        prefix, label, lo, hi, act = visit
        dest = self._room_for(rec, prefix)
        if dest < 0:
            return False
        h = self.hospital
        prof = self.by_id[rec["id"]]
        out, back = self._route_out(rec, dest)
        dur = self.rng.uniform(lo, hi) * 60
        steps = out + [(dest, dur, act, 0.35 if act == "walking" else 0.12, f"{h.rooms[dest]['name']} · {label}")]
        if self.rng.random() < 0.15:                                       # toilet on the way back
            wc = self._room_named(("로비 화장실", "화장실"), h.rooms[dest]["building_idx"], h.rooms[dest]["floor"])
            if wc >= 0:
                steps.append((wc, self.rng.uniform(120, 300), "still", 0.1, f"{h.rooms[wc]['name']}"))
        steps += back
        rec["exam_room"] = self._rkey(dest, prefix)
        self.exam_load[rec["exam_room"]] += 1
        self._start_trip(rec, steps, f"방문: {label}")
        self.log.add("adt", f"{prof['name']} {label} 이동 시작 ({h.rooms[dest]['name']}, {dur / 60:.0f}분)", patient_id=prof["id"])
        self.counters["visit_trips"] += 1
        return True

    def _plan_exam_trip(self, rec: dict, exam: dict) -> None:
        """Ward -> exam room -> ward, with the optional stops a real hospital visit has: billing desk, blood draw before a
        contrast CT/MRI, the imaging/blood-draw waiting area, a toilet afterwards, the cafe or pharmacy on the way back."""
        h = self.hospital
        prof = self.by_id[rec["id"]]
        exam_room = self._room_for(rec, exam["room"])
        ex_b, ex_f = (h.rooms[exam_room]["building_idx"], h.rooms[exam_room]["floor"]) if exam_room >= 0 else (None, None)
        out, back = self._route_out(rec, exam_room)
        steps: list = list(out)
        rn = lambda i: h.rooms[i]["name"]
        imaging = exam["room"] in ("X-ray실", "CT실", "MRI실", "심혈관조영실")
        if self.rng.random() < 0.3:
            desk = self._room_named(("원무과/접수", "트리아지/접수"), ex_b, ex_f)
            if desk >= 0:
                steps.append((desk, self.rng.uniform(40, 120), "still", 0.15, f"{rn(desk)} · 검사 접수"))
        if exam["room"] in ("CT실", "MRI실") and self.rng.random() < 0.35:   # contrast study: blood draw first
            wait = self._room_named("채혈 대기", ex_b, ex_f); draw = self._room_named("채혈실", ex_b)
            if wait >= 0:
                steps.append((wait, self.rng.uniform(60, 240), "still", 0.1, f"{rn(wait)} · 채혈 대기"))
            if draw >= 0 and draw != exam_room:
                steps.append((draw, self.rng.uniform(150, 360), "still", 0.1, f"{rn(draw)} · 조영제 검사 전 채혈"))
        wait_room = self._room_named("영상 대기", ex_b, ex_f) if imaging else (self._room_named("채혈 대기", ex_b, ex_f) if exam["room"] == "채혈실" else -1)
        if wait_room >= 0:
            steps.append((wait_room, self.rng.uniform(60, 480), "still", 0.1, f"{rn(wait_room)} · 검사 대기"))
        elif exam_room >= 0 and self.rng.random() < 0.5:
            dc = h.floor_rooms(ex_b, ex_f, "corridor")
            if dc:
                steps.append((dc[0]["idx"], self.rng.uniform(60, 300), "still", 0.1, f"{rn(dc[0]['idx'])} · 검사 대기"))
        dur = exam["duration_min"] * 60
        if exam["patch_policy"] == "remove" or exam_room < 0 or any(h.rooms[exam_room]["name"].startswith(k) for k in NO_BLE_ROOMS):
            steps.append(("mri", dur, "still", 0.05))          # MRI only: patch taken off for the scan, replaced afterwards
        else:
            steps.append((exam_room, dur, "still", 0.15, f"{rn(exam_room)} · {exam['type']}"))       # every other exam: patch stays on (handover / shadow only)
        if self.rng.random() < 0.12:
            wc = self._room_named(("영상 화장실", "로비 화장실", "화장실"), ex_b, ex_f)
            if wc >= 0:
                steps.append((wc, self.rng.uniform(120, 300), "still", 0.1, f"{rn(wc)}"))
        r = self.rng.random()
        if r < 0.15:
            shop = self._room_named("편의점/카페", ex_b)
            shop = shop if shop >= 0 else self._room_named("편의점/카페")
            if shop >= 0:
                steps.append((shop, self.rng.uniform(300, 600), "walking", 0.35, f"{rn(shop)} · 들르기"))
        elif r < 0.25:
            ph = self._room_named("약제부/외래약국", ex_b)
            ph = ph if ph >= 0 else self._room_named("약제부/외래약국")
            if ph >= 0:
                steps.append((ph, self.rng.uniform(180, 360), "still", 0.12, f"{rn(ph)} · 약 수령"))
        steps += back
        rec["exam_room"] = self._rkey(exam_room, exam["room"])
        self.exam_load[rec["exam_room"]] += 1
        self._start_trip(rec, steps, f"검사 이동: {exam['type']}")
        self.log.add("exam", f"{prof['name']} {exam['type']} 검사 이동 시작 ({exam['room']}, {exam['duration_min']}분{', MRI: 촬영 중 패치 분리 후 새 패치로 교체' if exam['patch_policy'] == 'remove' else ', 패치 유지'})", patient_id=prof["id"])
        self.counters["exam_trips"] += 1

    def _step_exam_quota(self, sc: dict) -> None:
        """Keep ~`scenario.exam_trip_ratio` % of inpatients on an exam trip: whenever the live count drops below the target,
        start a few new trips per second (staggered, so they never all leave at once).  Uses the patient's next scheduled
        exam when there is one, otherwise a random exam that exists in this hospital (dialysis excluded: 4 h)."""
        ratio = float(sc.get("exam_trip_ratio", 0.0) or 0.0) / 100.0
        if ratio <= 0 or not self.admitted or self.sim_time - self._exam_quota_t < 1.0:
            return
        self._exam_quota_t = self.sim_time
        h = self.hospital
        now = self.sim_time
        if now - getattr(self, "_exam_book_pruned", 0.0) > 600:                  # booking ledger: drop intervals older than a day
            self._exam_book_pruned = now
            for k, book in self.exam_book.items():
                if book and book[0][1] < now - 86400:
                    self.exam_book[k] = [b for b in book if b[1] >= now - 86400]
        inpat = [rec for rec in self.admitted.values() if not rec["outpatient"]]
        self.exam_load = collections.Counter(rec["exam_room"] for rec in inpat if rec.get("exam_room") and (rec["trip"] or rec["trip_step_until"] > now))
        on_exam = sum(1 for rec in inpat if rec["trip"] or rec["trip_step_until"] > now)      # every trip kind counts toward the moving share
        target = int(round(ratio * len(inpat)))
        missing = target - on_exam
        if missing <= 0:
            return
        pool = [e for e in EXAM_TYPES if e["room"] in h.exam_rooms]          # dialysis (4 h) only for renal patients, see below
        if not pool:
            return
        n_start = min(missing, max(1, target // 40 + 1))              # ~target/40 per second: fills in ~1 min, then just replaces finishing trips
        idle = [rec for rec in inpat if not rec["trip"] and rec["trip_step_until"] <= now and rec["activity"] not in ("shower",) and not rec.get("patch_removed_until", 0.0) > now]
        if not idle:
            return
        visits = [v for v in self.VISITS if self._room_named(v[0]) >= 0]
        hour = dt.datetime.fromtimestamp(now).hour
        for rec in self.rng.choice(len(idle), size=min(n_start, len(idle)), replace=False):
            rec = idle[int(rec)]
            mob = self.by_id[rec["id"]]["mobility"]
            r = self.rng.random()                                          # scenario mix for the moving share (exam / visit / ward life / shadow)
            if r < 0.20 and visits:                                        # 20 % visit (clinic, ultrasound, lounge, family, cafe, pharmacy, billing)
                free_v = [v for v in visits if self._free_for(rec, v[0])]
                if free_v and self._plan_visit_trip(rec, free_v[int(self.rng.integers(len(free_v)))]):
                    continue
            elif r < 0.32 and mob != "bedridden":                          # 12 % toilet
                self._trip_toilet(rec); continue
            elif r < 0.40 and mob == "ambulatory":                          # 8 % corridor walk
                self._trip_walk(rec); continue
            elif r < 0.45 and mob != "bedridden" and 7 <= hour <= 21:      # 5 % shower
                self._trip_shower(rec); continue
            elif r < 0.50 and mob == "ambulatory" and self._room_for(rec, "재활치료실") >= 0 and self._free_for(rec, "재활치료실"):
                rec["exam_room"] = self._rkey(self._room_for(rec, "재활치료실"), "재활치료실"); self.exam_load[rec["exam_room"]] += 1
                self._trip_rehab(rec); continue                             # 5 % rehab
            elif r < 0.53:                                                 # 3 % bed change to another ward (patch replaced)
                self._transfer(rec, allow_discharge=False)
                if rec["trip"]:
                    continue
            elif r < 0.56:                                                 # 3 % pure shadow-zone excursion (lift / stairwell)
                self._trip_shadow(rec); continue
            nxt = next((ex for ex in rec.get("exams") or [] if not ex["done"] and self._free_for(rec, ex["room"])), None)
            if nxt is not None:                                       # bring forward the next scheduled exam whose room has a free station
                nxt["done"] = True
                self.db.mark_exam_done(rec.get("adm_id"), nxt["type"], nxt["t"])
                exam = nxt
            else:
                renal = self.by_id[rec["id"]]["disease"].startswith("만성 신부전")
                free = [e for e in pool if self._free_for(rec, e["room"]) and (renal or e["type"] != "혈액투석")]
                if not free:
                    return                                            # every station busy: never crowd a room, try again next second
                e = free[int(self.rng.integers(len(free)))]
                exam = {"type": e["type"], "room": e["room"], "duration_min": e["min"], "patch_policy": e["patch"]}
            self._plan_exam_trip(rec, exam)

    def _step_inpatient(self, rec: dict, art_cfg: dict, intensity: float) -> None:
        h = self.hospital
        prof = self.by_id[rec["id"]]
        now = self.sim_time
        # exam schedule
        if rec["trip"] or rec["trip_step_until"] > now:
            if now >= rec["trip_step_until"]:
                self._next_trip_step(rec)
            return
        if art_cfg["exam_trips"]:                                # scheduled exams run even with the artifact scenario off
            for ex in rec["exams"]:
                if not ex["done"] and ex["t"] <= now:
                    if now - ex["t"] < 300 and not self._free_for(rec, ex["room"]):
                        ex["t"] = now + 120.0                       # room full: come back in 2 min instead of crowding it
                        break
                    ex["done"] = True
                    self.db.mark_exam_done(rec.get("adm_id"), ex["type"], ex["t"])
                    if now - ex["t"] < 300:                      # 5 min grace: a fresh start does not send every overdue exam of the day at once
                        self._plan_exam_trip(rec, ex)
                        return
        if now < rec["activity_until"]:
            return
        # choose next activity
        hour = dt.datetime.fromtimestamp(now).hour
        night = hour >= 22 or hour < 6
        mob = prof["mobility"]
        p_motion = intensity * (0.35 if night else 1.0)
        r = self.rng.random()
        room0 = h.rooms[rec["room_idx"]]
        corridors = [x["idx"] for x in h.floor_rooms(room0["building_idx"], room0["floor"], "corridor")]
        if r < 0.10 * p_motion and art_cfg["motion"]:
            self._set_activity(rec, "restless", self.rng.uniform(8, 40), self.rng.uniform(0.3, 0.8),
                               str(self.rng.choice(["left_lateral", "right_lateral", "supine", "sitting"])))
        elif r < 0.14 * p_motion and mob != "bedridden" and art_cfg["motion"]:
            self._trip_toilet(rec)
        elif r < 0.17 * p_motion and mob == "ambulatory" and not night and art_cfg["motion"]:
            self._trip_walk(rec)
        elif r < 0.185 * p_motion and mob != "bedridden" and not night and art_cfg["shower"] and 7 <= hour <= 21:
            self._trip_shower(rec)
        elif r < 0.19 * p_motion and mob == "ambulatory" and not night and art_cfg["motion"]:
            self._trip_rehab(rec)
        elif r < 0.20 * p_motion and art_cfg["motion"] and prof["age"] > 70:
            self._set_activity(rec, "tremor", self.rng.uniform(20, 120), self.rng.uniform(0.15, 0.3))
        elif r < 0.203 * p_motion and art_cfg["transfer"]:
            self._transfer(rec)
        else:
            self._set_activity(rec, "still", self.rng.uniform(30, 300), 0.0 if self.rng.random() < 0.8 else 0.05,
                               "supine" if night or self.rng.random() < 0.7 else "sitting")
        if rec.get("spo2_off_until", 0) and now > rec["spo2_off_until"] and rec["activity"] != "shower":
            rec["spo2_off_until"] = 0
            self.st.patch["spo2_off"][rec["row"]] = 0

    def _transfer(self, rec: dict, allow_discharge: bool = True) -> None:
        """Move to another ward (bed change) with patch replacement, or hospital transfer (discharge)."""
        h = self.hospital
        prof = self.by_id[rec["id"]]
        if allow_discharge and self.rng.random() < 0.25:
            self.discharge(rec["id"], "타병원 전원")
            return
        for w in h.ward_for(prof["ward_specialty"], self.rng):
            if w == rec["ward_idx"]:
                continue
            b = h.free_bed_in_ward(w)
            if b >= 0:
                h.beds[rec["bed_idx"]]["patient_id"] = 0
                h.beds[b]["patient_id"] = prof["id"]
                old_room = h.rooms[rec["room_idx"]]["id"]
                rec.update(bed_idx=b, room_idx=h.beds[b]["room_idx"], ward_idx=w)
                rec["nurse"] = str(self.rng.choice(h.wards[w]["nurse_ids"]))
                prof["admission"].update(bed=h.beds[b]["id"], room=h.rooms[rec["room_idx"]]["id"], ward=h.wards[w]["id"], ward_name=h.wards[w]["name"], nurse=rec["nurse"])
                self.db.update_admission_bed(rec.get("adm_id"), h.wards[w]["id"], h.wards[w]["name"], h.rooms[rec["room_idx"]]["id"], h.beds[b]["id"], rec["nurse"])
                self._replace_patch(rec, "병실 이동")
                self._start_trip(rec, [("shadow", self.rng.uniform(120, 400), "transfer", 0.6)], "병실 이동")
                self.log.add("adt", f"{prof['name']} 병실 이동 {old_room} → {h.rooms[rec['room_idx']]['id']} (패치 교체)", patient_id=prof["id"])
                self.counters["transfers"] += 1
                return

    def _replace_patch(self, rec: dict, reason: str) -> None:
        row = rec["row"]
        old = self.patches.get(row)
        prof = self.by_id[rec["id"]]
        self._retire_patch(old, reason)
        new = self._new_patch(row, prof["id"], f"교체: {reason}")
        new.replaced = (old.replaced + 1) if old else 0
        new.new_until = self.sim_time + 30
        P = self.st.patch.arr
        P["patch_id"][row] = new.patch_id
        P["battery"][row] = 100
        P["flags"][row] = int(P["flags"][row]) | FLAG_NEW_PATCH
        P["attach_tick"][row] = self._tick_now()                            # fresh electrodes settle in
        prof["admission"]["patch"] = new.serial
        prof["admission"]["patch_id"] = new.patch_id
        prof.setdefault("patch_history", []).append({"t": dt.datetime.fromtimestamp(self.sim_time).isoformat(timespec="seconds"),
                                                     "old": old.serial if old else None, "new": new.serial, "reason": reason})
        self.meta_dirty = True
        self.counters["patch_replaced"] += 1
        self.log.add("patch", f"{prof['name']} 패치 교체 {old.serial if old else '-'} → {new.serial} ({reason})", patient_id=prof["id"])

    def _step_outpatient(self, rec: dict, art_cfg: dict, intensity: float) -> None:
        """MCOT: home / outside / shadow zones, mobile gateway battery, patch fall-off."""
        now = self.sim_time
        G = self.st.gw.arr
        gw = rec["mobile_gw"]
        gs = self.gw_state[gw]
        # mobile gateway battery
        drain = 100.0 / (14 * 3600) * (1.6 if rec["home_state"] == "outside" else 1.0)
        if gs["charging"]:
            gs["mobile_batt"] = min(100.0, gs["mobile_batt"] + 100.0 / 5400)
            if gs["mobile_batt"] >= 99.5 and self.rng.random() < 0.01:
                gs["charging"] = False
        else:
            gs["mobile_batt"] = max(0.0, gs["mobile_batt"] - drain)
            if gs["mobile_batt"] < 15 and rec["home_state"] == "home" and self.rng.random() < 0.01:
                gs["charging"] = True
        G["battery"][gw] = int(gs["mobile_batt"])
        if gs["mobile_batt"] <= 0.5 and G["status"][gw] != 2:
            G["status"][gw] = 2
            gs["reason"] = "모바일 게이트웨이 배터리 방전"
            self.log.add("gateway", f"MCOT 모바일 게이트웨이 {self.hospital.gateways[gw]['id']} 배터리 방전 - 오프라인", gw=gw)
        elif gs["mobile_batt"] > 5 and G["status"][gw] == 2 and gs["reason"].startswith("모바일"):
            G["status"][gw] = 0
            gs["reason"] = ""
        if now < rec["activity_until"]:
            return
        hour = dt.datetime.fromtimestamp(now).hour
        night = hour >= 23 or hour < 6
        r = self.rng.random()
        if night:
            rec["home_state"] = "home"
            self._set_activity(rec, "still" if r > 0.1 else "restless", self.rng.uniform(300, 1800), 0.0 if r > 0.1 else 0.4, "supine")
        elif r < 0.25 * intensity and art_cfg["home_interference"]:
            rec["home_state"] = "outside"
            self._set_activity(rec, "walking", self.rng.uniform(300, 1500), 0.5, "standing")
        elif r < 0.30 * intensity and art_cfg["home_interference"]:
            rec["home_state"] = "shadow"            # basement / elevator / subway
            self._set_activity(rec, "walking", self.rng.uniform(60, 400), 0.5, "standing")
        elif r < 0.34 * intensity and art_cfg["home_interference"]:
            rec["home_state"] = "home"              # microwave / wifi interference
            G["loss"][gw] = float(self.rng.uniform(0.05, 0.3))
            G["latency_ms"][gw] = float(self.rng.uniform(100, 600))
            gs["degraded_until"] = now + self.rng.uniform(30, 180)
            G["status"][gw] = 1
            self._set_activity(rec, "still", self.rng.uniform(60, 300), 0.05, "sitting")
        elif r < 0.40 * intensity:
            rec["home_state"] = "home"
            self._set_activity(rec, "exercise", self.rng.uniform(300, 900), 0.8, "standing")
        elif r < 0.45 * intensity:
            rec["home_state"] = "home"
            self._set_activity(rec, "shower", self.rng.uniform(300, 700), 0.9, "standing")
        else:
            rec["home_state"] = "home"
            self._set_activity(rec, "still", self.rng.uniform(120, 900), 0.02, "sitting" if self.rng.random() < 0.6 else "supine")
        self._relink(rec)

    # ------------------------------------------------------------------ patches
    def _step_patches(self, dt_s: float) -> None:
        cfg = self.cfg.get("scenario")
        pc, ac = cfg["patch"], cfg["artifacts"]
        intensity = ac["intensity"] / 100.0 if ac["enabled"] else 0.0
        P = self.st.patch.arr
        now = self.sim_time
        drain = 100.0 / (pc["battery_days"] * 86400.0) * dt_s if pc["battery_drain_enabled"] else 0.0
        for pid, rec in list(self.admitted.items()):
            row = rec["row"]
            patch = self.patches.get(row)
            if patch is None:
                continue
            prof = self.by_id[pid]
            if drain:
                patch.battery = max(0.0, patch.battery - drain)
            P["battery"][row] = int(patch.battery)
            f = int(P["flags"][row])
            f = (f | FLAG_LOW_BATT) if patch.battery < 15 else (f & ~FLAG_LOW_BATT)
            if patch.new_until and now > patch.new_until:
                patch.new_until = 0.0
                f &= ~FLAG_NEW_PATCH
            # lead-off events
            removed = rec.get("patch_removed_until", 0.0)
            lead_off = removed > now
            if not lead_off and patch.lead_off_until > now:          # manual or scheduled lead-off (independent of the random setting)
                lead_off = True
            if not lead_off and pc["lead_off_enabled"]:
                if self.rng.random() < 0.00025 * (0.3 + intensity) * dt_s * (1.5 if rec["activity"] in ("shower", "restless", "exercise") else 1.0):
                    patch.lead_off_until = now + self.rng.uniform(20, 600)
                    lead_off = True
                    self.counters["lead_off"] += 1
                    self.log.add("patch", f"{prof['name']} 패치 {patch.serial} 리드 오프 (전극 분리)", patient_id=pid)
                    self.db.add_patch_event(now, patch.serial, pid, "lead_off", "전극 분리")
            if lead_off and not (f & FLAG_LEAD_OFF):
                f |= FLAG_LEAD_OFF
                P["lead_off"][row] = 1
                P["detach_tick"][row] = self._tick_now()                    # peeling artefact, then rail
            elif not lead_off and (f & FLAG_LEAD_OFF):
                f &= ~FLAG_LEAD_OFF
                P["lead_off"][row] = 0
                P["attach_tick"][row] = self._tick_now()                    # electrode settling transient
                self.db.add_patch_event(now, patch.serial, pid, "lead_on", "전극 재부착")
                if removed and removed <= now and not rec.get("mri_pending"):
                    rec["patch_removed_until"] = 0.0
            P["flags"][row] = f
            if patch.battery <= pc["replace_below_pct"] and rec["activity"] != "shower":
                self._replace_patch(rec, f"배터리 {patch.battery:.0f}%")

    # ------------------------------------------------------------------ slow multi-day modulation
    def _step_modulation(self) -> None:
        """Every 10 s (sim): refresh circadian + drift factors, slow amplitude drift, and rotate loop
        variants (variant hopping) so that no patient ever replays the same hour."""
        if self.sim_time - getattr(self, "_mod_last", 0.0) < 10.0:
            return
        self._mod_last = self.sim_time
        now = self.sim_time
        hop = bool(self.cfg.get("scenario", "variant_hopping", default=True)) and bool(self.rhythm_variants)
        P = self.st.patch.arr
        tick = self._tick_now()
        per = self.bank.index.get("slow_per_profile", 24) if self.bank.loaded else 24
        for pid, rec in self.admitted.items():
            prof = self.by_id[pid]
            st = trend_model.state(pid, prof, now)
            row = rec["row"]
            P["hr_scale"][row] = st["hr_scale"]
            P["rr_add"][row] = st["rr_add"]
            P["spo2_add"][row] = st["spo2_add"]
            P["temp_add"][row] = st["temp_add"]
            P["gl_add"][row] = st["gl_add"]
            P["gain"][row] = rec.get("gain0", 1.0) * (1.0 + trend_model._drift(pid * 7919 + 9, now, 0.08))
            if not hop:
                continue
            if now >= rec["next_hop"] and not rec["episode_until"]:
                new = self._similar_variant(prof["rhythm"], rec["base_variant"])
                if new != rec["base_variant"]:
                    rec["base_variant"] = new
                    self._switch_variant(row, new, tick)
                    self.counters["variant_hops"] += 1
                rec["next_hop"] = now + self.rng.uniform(480, 1500)
            if now >= rec["next_slow_hop"]:
                P["temp_var"][row] = (int(P["temp_var"][row]) // per) * per + int(self.rng.integers(per))
                P["gluc_var"][row] = (int(P["gluc_var"][row]) // per) * per + int(self.rng.integers(per))
                rec["next_slow_hop"] = now + self.rng.uniform(1800, 5400)

    # ------------------------------------------------------------------ arrhythmia episodes
    def _step_episodes(self) -> None:
        if not self.rhythm_variants or not self.cfg.get("scenario", "rhythm_episodes", default=True):
            return
        P = self.st.patch.arr
        now = self.sim_time
        tick = self._tick_now()
        for pid, rec in self.admitted.items():
            row = rec["row"]
            if rec["episode_until"] and now > rec["episode_until"]:
                rec["episode_until"] = 0.0
                self._switch_variant(row, rec["base_variant"], tick)
                self.log.add("rhythm", f"{self.by_id[pid]['name']} 리듬 에피소드 종료 → 기저 리듬 복귀", patient_id=pid)
                continue
            if rec["episode_until"] == 0.0 and self.rng.random() < 1 / 5400:       # ~ once per 1.5 h per patient
                prof = self.by_id[pid]
                base = prof["rhythm"]
                cls = RHYTHMS[base]["cls"]
                if prof["disease_group"] == "heart":
                    choices = {"normal": ["pac", "pvc", "afib", "sinus_tachy", "nsvt"], "afib": ["afib_rvr", "afib", "sinus_pause"],
                               "ectopy": ["nsvt", "pvc_bigeminy", "svt", "afib"], "brady": ["sinus_pause", "avb2_m1"],
                               "block": ["avb3", "sinus_brady"], "ischemia": ["nsvt", "vt", "pvc"], "tachy": ["svt", "afib_rvr"],
                               "bbb": ["pvc", "afib"], "paced": ["paced_malfunction", base], "vt": ["vfib", "nsvt"], "lethal": ["vfib"]}.get(cls, ["nsr"])
                else:
                    choices = ["sinus_tachy", "pac", "pvc", "nsr"]
                new = str(self.rng.choice(choices))
                v = self._variant_for(new, prof["age"])
                rec["episode_until"] = now + self.rng.uniform(60, 900)
                self._switch_variant(row, v, tick)
                self.counters["episodes"] += 1
                self.log.add("rhythm", f"{prof['name']} 리듬 에피소드: {RHYTHMS[base]['label']} → {RHYTHMS[new]['label']}", patient_id=pid)

    def _switch_variant(self, row: int, v: int, tick: int) -> None:
        P = self.st.patch.arr
        if int(P["variant"][row]) == v:
            return
        P["variant_prev"][row] = P["variant"][row]
        P["variant"][row] = v
        P["switch_tick"][row] = tick

    def _tick_now(self) -> int:
        c = self.st.ctl
        if c[CTL["epoch_ns"]] == 0:
            return 0
        return int((time.time() - c[CTL["epoch_ns"]] / 1e9) / (c[CTL["bundle_ms"]] / 1000.0))

    # ------------------------------------------------------------------ gateways & network
    def _step_gateways(self, dt_s: float) -> None:
        sc = self.cfg.get("scenario")
        gwc, net = sc["gateway"], sc["network"]
        h = self.hospital
        G = self.st.gw.arr
        S = self.st.stat.arr
        now = self.sim_time
        n = len(h.gateways)
        fi = gwc["fault_intensity"] / 100.0 if gwc["fault_enabled"] else 0.0
        ni = net["intensity"] / 100.0 if net["enabled"] else 0.0
        # ---- individual gateway faults
        if fi > 0:
            p_fault = 0.00004 * fi * dt_s          # per gateway per second (~1 event / 7h at 100%)
            hits = np.where(self.rng.random(h.n_fixed_gateways) < p_fault)[0]
            for gw in hits:
                gw = int(gw)
                gs = self.gw_state[gw]
                if G["status"][gw] == 2:
                    continue
                kinds = (["outage"] if gwc.get("outage", True) else []) + (["degrade"] if gwc.get("degrade", True) else []) + (["hw_failure"] if gwc.get("replace", True) else [])
                if not kinds:
                    continue
                kind = str(self.rng.choice(kinds, p=[{"outage": 0.5, "degrade": 0.4, "hw_failure": 0.1}[k] for k in kinds] / np.sum([{"outage": 0.5, "degrade": 0.4, "hw_failure": 0.1}[k] for k in kinds])))
                if kind == "hw_failure":                                   # dead unit: stays down until a replacement is installed
                    gs["fault_until"] = now + self.rng.uniform(300, 1500)
                    gs["reason"] = "하드웨어 고장 (교체 대기)"
                    G["status"][gw] = 2
                    self.counters["gw_hw_failures"] += 1
                    self.log.add("gateway", f"게이트웨이 {h.gateways[gw]['id']} 하드웨어 고장 - 연결된 패치 {gs['n_conn']}개 끊김, 교체 장비 대기", gw=gw)
                elif kind == "outage":
                    gs["fault_until"] = now + self.rng.uniform(20, 240)
                    gs["reason"] = "게이트웨이 장애(무응답)"
                    G["status"][gw] = 2
                    self.counters["gw_faults"] += 1
                    self.log.add("gateway", f"게이트웨이 {h.gateways[gw]['id']} 장애 - 연결된 패치 {gs['n_conn']}개 동시 끊김", gw=gw)
                else:
                    gs["degraded_until"] = now + self.rng.uniform(20, 180)
                    G["status"][gw] = 1
                    G["loss"][gw] = float(self.rng.uniform(0.02, 0.2))
                    G["latency_ms"][gw] = float(self.rng.uniform(50, 400))
                    G["jitter_ms"][gw] = float(self.rng.uniform(10, 150))
                    G["cpu"][gw] = int(self.rng.uniform(85, 99))
                    self.log.add("gateway", f"게이트웨이 {h.gateways[gw]['id']} 성능 저하 (CPU {int(G['cpu'][gw])}%, 지연 {int(G['latency_ms'][gw])}ms)", gw=gw)
        # ---- scenario switched off: clear ongoing (non-manual) faults immediately
        if fi == 0 or ni == 0:
            for gw in range(h.n_fixed_gateways):
                gs = self.gw_state[gw]
                if gs["reason"].startswith("수동"):
                    continue
                if gs["reason"].startswith("하드웨어"):
                    continue                                              # a dead unit only comes back through replacement
                if fi == 0 and ni == 0 or (gs["reason"] == "게이트웨이 장애(무응답)" and fi == 0) or (gs["reason"] not in ("", "게이트웨이 장애(무응답)") and ni == 0):
                    if G["status"][gw] == 2 and gs["fault_until"]:
                        gs["fault_until"] = 0.0
                        gs["reason"] = ""
                        G["status"][gw] = 0
                if G["status"][gw] == 1 and (fi == 0 and ni == 0):
                    G["status"][gw] = 0
                    G["loss"][gw] = 0.0
                    G["latency_ms"][gw] = 0.0
                    G["jitter_ms"][gw] = 0.0
                    gs["degraded_until"] = 0.0
            if ni == 0 and self.net_events:
                self.net_events = []
        # ---- network scenarios (area events)
        if ni > 0 and self.rng.random() < 0.004 * ni * dt_s:
            self._network_event(net, ni)
        for ev in list(self.net_events):
            if now > ev["until"]:
                for gw in ev["gws"]:
                    gs = self.gw_state[gw]
                    if ev["kind"] in ("wired", "power"):
                        gs["fault_until"] = 0.0
                        if G["status"][gw] == 2 and not gs["reason"].startswith("모바일"):
                            G["status"][gw] = 0
                            gs["reason"] = ""
                            if ev["kind"] == "power":
                                G["uptime_s"][gw] = 0
                                gs["boot_at"] = now
                    else:
                        gs["degraded_until"] = 0.0
                self.net_events.remove(ev)
                self.log.add("network", f"네트워크 이벤트 종료: {ev['label']}")
        # ---- recovery & metrics
        for gw in range(n):
            gs = self.gw_state[gw]
            st = int(G["status"][gw])
            if st == 2 and gs["fault_until"] and now > gs["fault_until"] and gs["reason"].startswith("하드웨어"):
                self._replace_gateway(gw, "하드웨어 고장")
                continue
            if st == 2 and gs["fault_until"] and now > gs["fault_until"] and not gs["reason"].startswith("모바일"):
                G["status"][gw] = 0
                gs["fault_until"] = 0.0
                gs["reason"] = ""
                G["uptime_s"][gw] = 0
                gs["boot_at"] = now
                self.log.add("gateway", f"게이트웨이 {h.gateways[gw]['id']} 복구 - 패치 자동 재접속", gw=gw)
            elif st == 1 and gs["degraded_until"] and now > gs["degraded_until"]:
                G["status"][gw] = 0
                G["loss"][gw] = 0.0
                G["latency_ms"][gw] = 0.0
                G["jitter_ms"][gw] = 0.0
                gs["degraded_until"] = 0.0
            if st != 2:
                G["uptime_s"][gw] = min(0xFFFFFFFF, int(G["uptime_s"][gw]) + int(dt_s))
        # vectorised resource metrics
        nconn = np.array([g["n_conn"] for g in self.gw_state], dtype=np.float32)
        base_cpu = np.array([g["cpu_base"] for g in self.gw_state], dtype=np.float32)
        base_mem = np.array([g["mem_base"] for g in self.gw_state], dtype=np.float32)
        boot = np.array([g["boot_at"] for g in self.gw_state], dtype=np.float64)
        booting = (now - boot) < 40
        cpu = base_cpu + 1.4 * nconn + self.rng.normal(0, 2.5, n) + booting * 45
        mem = base_mem + 0.9 * nconn + self.rng.normal(0, 0.8, n)
        netl = np.clip(nconn / max(1, gwc["capacity"]) * 70 + self.rng.normal(0, 3, n), 0, 100)
        degraded = G["status"] == 1
        cpu = np.where(degraded, np.maximum(cpu, 85 + self.rng.normal(0, 4, n)), cpu)
        G["cpu"][:] = np.clip(cpu, 1, 100).astype(np.uint8)
        G["mem"][:] = np.clip(mem, 1, 100).astype(np.uint8)
        G["net"][:] = netl.astype(np.uint8)
        G["wan_rssi"][:] = np.clip(G["wan_rssi"].astype(np.float32) + self.rng.normal(0, 1.0, n), -95, -35).astype(np.int8)
        G["temp_c"][:] = np.clip(38 + cpu * 0.18 + self.rng.normal(0, 0.5, n), 30, 90).astype(np.uint8)

    def layout_export(self) -> dict:
        """Complete plan (layout + derived gateways/wards/exam rooms + current occupancy) for the router."""
        h = self.hospital
        out = h.export_layout()
        occ = {}
        for pid, rec in self.admitted.items():
            if rec["bed_idx"] >= 0:
                occ[h.beds[rec["bed_idx"]]["id"]] = {"patient_no": rec.get("patient_no"), "profile_id": pid, "name": self.by_id[pid]["name"],
                                                    "patch": self.patches[rec["row"]].serial if rec["row"] in self.patches else None}
        out["occupancy"] = occ
        out["gateway_state"] = [{"row": g["idx"], "gw_no": g["gw_no"], "status": int(self.st.gw["status"][g["idx"]]), "n_conn": int(self.st.gw["n_conn"][g["idx"]])} for g in h.gateways]
        return out

    def _gw_db_row(self, g: dict, reason: str, status: str = "active") -> dict:
        h = self.hospital
        return {"gw_no": g["gw_no"], "row": g["idx"], "gw_id": g["id"], "mac": g["mac"], "type": g["type"], "building": g["building"], "floor": g["floor"],
                "room": h.rooms[g["room_idx"]]["id"] if g["room_idx"] >= 0 else "", "status": status, "installed_at": self.sim_time, "installed_reason": reason}

    def _replace_gateway(self, gw: int, reason: str = "하드웨어 고장") -> str:
        """Swap the physical unit: new unique gateway number / id / MAC; the old unit is retired in the DB."""
        h = self.hospital
        g = h.gateways[gw]
        old_id, old_no = g["id"], g["gw_no"]
        self.db.upsert_gateway({**self._gw_db_row(g, "", "retired"), "installed_at": None, "retired_at": self.sim_time, "retired_reason": reason,
                                "installed_reason": None})
        g["gw_no"] = self.next_gw_no
        self.next_gw_no += 1
        g["id"] = f"GW-{g['building_idx'] + 1}{g['floor']:02d}-{g['gw_no']:05d}" if g["type"] != "mobile" else f"MGW-{g['gw_no']:05d}"
        g["mac"] = f"C4:7F:{(g['gw_no'] >> 8) & 0xFF:02X}:{g['gw_no'] & 0xFF:02X}:{(g['gw_no'] * 37) & 0xFF:02X}:{(g['gw_no'] * 91) & 0xFF:02X}"
        self.db.upsert_gateway(self._gw_db_row(g, f"교체 ({reason}) ← {old_id}"))
        G = self.st.gw.arr
        G["gw_id"][gw] = g["gw_no"]
        G["uptime_s"][gw] = 0
        G["status"][gw] = 0
        gs = self.gw_state[gw]
        gs.update(fault_until=0.0, reason="", boot_at=self.sim_time, cpu_base=float(self.rng.uniform(8, 20)), mem_base=float(self.rng.uniform(30, 45)))
        self.counters["gw_replaced"] += 1
        self.meta_dirty = True
        self.log.add("gateway", f"게이트웨이 교체: {old_id} (#{old_no}) → {g['id']} (#{g['gw_no']}), MAC {g['mac']} - 패치 자동 재접속", gw=gw)
        return g["id"]

    def _network_event(self, net: dict, ni: float) -> None:
        h = self.hospital
        G = self.st.gw.arr
        now = self.sim_time
        kinds = []
        if net["wireless_noise"]:
            kinds.append("wireless")
        if net["wired_failure"]:
            kinds.append("wired")
        if net["latency"]:
            kinds.append("latency")
        if net["power_outage"]:
            kinds.append("power")
        if not kinds:
            return
        kind = str(self.rng.choice(kinds))
        # pick an area: a floor of a building
        fl = self.rng.choice(len(h.floors))
        floor = h.floors[int(fl)]
        gws = [g["idx"] for g in h.gateways if g["building_idx"] == floor["building_idx"] and g["floor"] == floor["floor"]]
        if kind == "power":
            gws = [g["idx"] for g in h.gateways if g["building_idx"] == floor["building_idx"]] if self.rng.random() < 0.3 else gws
        label = {"wireless": "무선 노이즈/간섭", "wired": "유선 네트워크 장애", "latency": "네트워크 지연", "power": "순간 정전"}[kind]
        dur = {"wireless": self.rng.uniform(15, 120), "wired": self.rng.uniform(10, 120), "latency": self.rng.uniform(10, 90), "power": self.rng.uniform(5, 40)}[kind]
        dur *= (0.5 + ni)
        for gw in gws:
            gs = self.gw_state[gw]
            if kind in ("wired", "power"):
                G["status"][gw] = 2
                gs["fault_until"] = now + dur
                gs["reason"] = label
            elif kind == "wireless":
                G["status"][gw] = 1
                G["loss"][gw] = float(self.rng.uniform(0.05, 0.35) * (0.5 + ni))
                G["latency_ms"][gw] = float(self.rng.uniform(20, 200))
                G["jitter_ms"][gw] = float(self.rng.uniform(20, 200))
                G["wan_rssi"][gw] = int(self.rng.uniform(-92, -80))
                gs["degraded_until"] = now + dur
            else:
                G["status"][gw] = 1
                G["latency_ms"][gw] = float(self.rng.uniform(150, 900) * (0.5 + ni))
                G["jitter_ms"][gw] = float(self.rng.uniform(20, 300))
                gs["degraded_until"] = now + dur
        self.net_events.append({"kind": kind, "label": f"{label} - {floor['building']} {floor['floor']}F ({len(gws)} GW, {dur:.0f}s)", "gws": gws, "until": now + dur})
        self.counters["net_events"] += 1
        self.log.add("network", f"네트워크 이벤트: {label} - {floor['building']} {floor['floor']}층 게이트웨이 {len(gws)}대, {dur:.0f}초")

    # ------------------------------------------------------------------ ADT (poisson)
    def _step_adt(self, dt_s: float) -> None:
        g = self.cfg.get("general")
        site = self.cfg.get("scenario", "site")
        if site == "mcot":
            return
        lam_a = g["admissions_per_hour"] / 3600.0 * dt_s
        lam_d = g["discharges_per_hour"] / 3600.0 * dt_s
        n_in = [k for k, r in self.admitted.items() if not r["outpatient"]]
        target = int(g["active_patients"])
        # the scenario's patient count is a hard target: a lower number discharges the surplus right away and a higher one admits
        # up to the target (500 per second so a 2000 -> 100 change settles in ~4 s without stalling the world loop); the hourly
        # ADT rates below only add the realistic churn around that level
        surplus = len(n_in) - (target + 5)
        if surplus > 0:
            for _ in range(min(surplus, 500)):
                pid = n_in.pop(int(self.rng.integers(len(n_in))))
                self.discharge(pid, "환자 수 조정 (퇴원)")
            if len(n_in) <= target + 5:
                self.log.add("adt", f"환자 수 조정 완료: 재원 {len(n_in)}명 (목표 {target})")
        deficit = target - len(n_in)
        if deficit > 0:
            for _ in range(min(deficit, 500)):
                rec = self.admit()
                if not rec:
                    break
                n_in.append(rec["id"])
            if len(n_in) >= target:
                self.log.add("adt", f"환자 수 조정 완료: 재원 {len(n_in)}명 (목표 {target})")
        for _ in range(int(self.rng.poisson(lam_d))):
            if len(n_in) > max(0, g["active_patients"] - 5):
                pid = n_in.pop(int(self.rng.integers(len(n_in))))
                self.discharge(pid)
        for _ in range(int(self.rng.poisson(lam_a))):
            if len(n_in) < g["active_patients"] + 5:
                rec = self.admit()
                if rec:
                    p = self.by_id[rec["id"]]
                    n_in.append(rec["id"])
                    self.log.add("adt", f"신규 입원: {p['name']} ({p['sex']}/{p['age']}) {p['disease']} → {p['admission']['ward_name']} {p['admission']['bed']} 패치 {p['admission']['patch']}", patient_id=p["id"])

    # ------------------------------------------------------------------ meta file for workers
    def write_meta(self, force: bool = False) -> None:
        if not (self.meta_dirty or force):
            return
        if time.time() - self.meta_last_write < 2.0 and not force:
            return
        h = self.hospital
        cfg = self.cfg.snapshot()
        s = cfg["signals"]
        en = [k for k, v in s["enabled"].items() if v]
        fs = {"ecg": s["ecg_fs"], "ppg": s["ppg_fs"], "resp_wave": s["resp_fs"], "accel": s["accel_fs"]}
        chans = []
        for key in en:
            cid = CHANNEL_BY_KEY[key]
            c = CHANNELS[cid]
            d = {"id": cid, "key": key, "dtype": c["dtype"], "scale": c["scale"], "unit": c["unit"], "kind": c["kind"]}
            if key in fs:
                d["fs"] = fs[key]
            if "axes" in c:
                d["axes"] = c["axes"]
            chans.append(d)
        by_gw: dict[int, list] = {}
        for pid, rec in self.admitted.items():
            if rec["gw"] < 0:
                continue
            patch = self.patches.get(rec["row"])
            prof = self.by_id[pid]
            pmi = prof.get("pacemaker_info")
            devs = prof.get("devices") or ["ecg_patch"]
            pmask = devices_mask(devs)
            p_chans = [c for c in chans if pmask & (1 << c["id"])]
            src = spo2_source(devs)
            by_gw.setdefault(rec["gw"], []).append({"patch_id": patch.patch_id, "serial": patch.serial, "patient_id": rec.get("patient_no", pid), "profile_id": pid, "mrn": prof["mrn"],
                                                    "fw": PATCH_FW, "resp_source": s["resp_source"], "spo2_source": (SPO2_SOURCES[src] if src is not None else None),
                                                    "devices": [{"key": d, "label": DEVICES[d]["label"]} for d in devs], "channels": p_chans,
                                                    "pacemaker": ({"type": pmi["type"], "mode": pmi["mode"], "lead": pmi["lead"], "detect_pct": pmi["detect_pct"]} if pmi else None)})
        out = {}
        for g in h.gateways:
            gw = g["idx"]
            room = h.rooms[g["room_idx"]]["id"] if g["room_idx"] >= 0 else ""
            patches = sorted(by_gw.get(gw, []), key=lambda x: x["patch_id"])
            sig = hash(json.dumps([[p["patch_id"], [d["key"] for d in p["devices"]]] for p in patches] + en + [s["resp_source"], g["gw_no"]]))
            ver = self.meta_versions.get(gw)
            if ver != sig:
                self.meta_versions[gw] = sig
            out[gw] = {"v": sig & 0x7FFFFFFF, "gw": g["id"], "gw_no": g["gw_no"], "gw_idx": gw, "type": g["type"], "mac": g["mac"], "ip": g["ip"], "fw": g["fw"],
                       "location": {"building": g["building"], "floor": g["floor"], "x": g["x"], "y": g["y"], "room": room},
                       "bundle_ms": cfg["transport"]["bundle_ms"], "channels_enabled": en, "patches": patches}
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        tmp = META_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, META_PATH)
        self.meta_dirty = False
        self.meta_last_write = time.time()

    # ------------------------------------------------------------------ main step
    def step(self, real_dt: float) -> None:
        with self.lock:
            speed = self.cfg.get("general", "sim_speed")
            dt_s = real_dt * speed
            self.sim_time += dt_s
            sc = self.cfg.get("scenario")
            ac = sc["artifacts"]
            intensity = ac["intensity"] / 100.0 if ac["enabled"] else 0.0
            self._sync_population()
            self._step_adt(dt_s)
            for pid, rec in list(self.admitted.items()):
                if pid not in self.admitted:
                    continue
                if rec["outpatient"]:
                    self._step_outpatient(rec, ac, intensity)
                else:
                    self._step_inpatient(rec, ac, intensity)
            self._step_exam_quota(sc)
            self._step_patches(dt_s)
            self._step_modulation()
            self._step_episodes()
            self._step_gateways(dt_s)
            self._step_drills()
            self._step_script()
            # periodic relink (gateway recovery / capacity / rssi jitter): 1/5 of patients per second
            G = self.st.gw.arr
            k = int(self.sim_time) % 5
            for pid, rec in self.admitted.items():
                if pid % 5 == k or (rec["gw"] >= 0 and G["status"][rec["gw"]] == 2) or (rec["gw"] < 0):
                    self._relink(rec)
            self.write_meta()
            self._step_autotune(real_dt)
            if self.log.pending:
                self.db.queue_events(self.log.pending)
                self.log.pending = []
            self.db.flush()

    # ------------------------------------------------------------------ autotune
    def start_autotune(self, step: int = 100, window_s: float = 12.0) -> None:
        g = self.cfg.get("general")
        self.autotune = {"state": "ramp", "step": step, "window": window_s, "t": 0.0, "current": g["active_patients"],
                         "best": 0, "history": [], "started": time.time(), "fail_streak": 0}
        self.log.add("autotune", f"자동 최적화 시작: {g['active_patients']}명부터 +{step}씩 증가")

    def stop_autotune(self) -> None:
        if self.autotune:
            self.log.add("autotune", f"자동 최적화 종료: 안정 최대 {self.autotune['best']}명")
        self.autotune = None

    def _step_autotune(self, real_dt: float) -> None:
        at = self.autotune
        if not at or not self.running:
            return
        at["t"] += real_dt
        if at["t"] < at["window"]:
            return
        at["t"] = 0.0
        m = self.health()
        ok = m["healthy"]
        at["history"].append({"n": at["current"], "ok": ok, "build_us": m["build_us"], "send_us": m["send_us"], "overruns": m["overruns_window"],
                              "backlog_drops": m["backlog_window"], "t": time.time()})
        cap = self.hospital.bed_capacity
        if ok:
            at["best"] = max(at["best"], at["current"])
            at["fail_streak"] = 0
            nxt = min(cap, at["current"] + at["step"])
            if nxt == at["current"]:
                self.log.add("autotune", f"병상 최대치 {cap}명에서도 안정 - 종료")
                self.autotune = None
                return
        else:
            at["fail_streak"] += 1
            at["step"] = max(5, at["step"] // 2)
            nxt = max(10, at["best"] if at["best"] else at["current"] - at["step"])
            if at["step"] <= 5 and at["fail_streak"] >= 2:
                self.cfg.update({"general": {"active_patients": at["best"] or nxt}})
                self.log.add("autotune", f"자동 최적화 완료: 안정적으로 수용 가능한 최대 채널 수 = {at['best']}명 (설정 반영)")
                self.autotune = None
                return
        at["current"] = nxt
        self.cfg.update({"general": {"active_patients": nxt}})
        self.log.add("autotune", f"자동 최적화: {'안정' if ok else '불안정'} → 환자 {nxt}명 (step {at['step']})")

    def health(self) -> dict:
        S = self.st.stat.arr
        W = self.st.wstat.arr
        alive = W["alive"] > 0
        n_w = int(alive.sum())
        bundle_us = float(self.st.ctl[CTL["bundle_ms"]]) * 1000
        build = float(W["build_us"][alive].max()) if n_w else 0.0
        send = float(W["send_us"][alive].max()) if n_w else 0.0
        overruns = int(W["overruns"][alive].sum()) if n_w else 0
        backlog = int(S["drop_backlog"].sum())
        prev = self.stats_prev
        ow = overruns - prev.get("overruns", overruns)
        bw = backlog - prev.get("backlog", backlog)
        self.stats_prev = {"overruns": overruns, "backlog": backlog}
        generate_only = self.st.ctl[CTL["generate_only"]] > 0
        healthy = n_w > 0 and ow == 0 and (build + send) < 0.7 * bundle_us and (generate_only or bw == 0)
        return {"healthy": healthy, "build_us": build, "send_us": send, "overruns_window": ow, "backlog_window": bw, "n_workers": n_w}

    # ------------------------------------------------------------------ queries for API
    def patient_view(self, pid: int) -> dict | None:
        prof = self.by_id.get(pid)
        if prof is None:
            return None
        h = self.hospital
        out = {k: v for k, v in prof.items() if k != "avatar"}
        rec = self.admitted.get(pid)
        if rec:
            P = self.st.patch.arr[rec["row"]]
            patch = self.patches.get(rec["row"])
            gw = h.gateways[rec["gw"]] if rec["gw"] >= 0 else None
            out["runtime"] = {"row": rec["row"], "patient_no": rec.get("patient_no"), "activity": rec["activity"], "posture": rec["posture"], "note": rec["note"],
                              "location": h.rooms[self.display_location(rec)]["id"] if rec["location"] >= 0 else ("원외" if rec["outpatient"] else "-"),
                              "location_name": (h.rooms[self.display_location(rec)]["name"] if rec["location"] >= 0 else rec["home_state"]),
                              "location_where": (lambda r: f"{r['building']} {r['floor']}F")(h.rooms[self.display_location(rec)]) if rec["location"] >= 0 else "",
                              "home_where": (lambda r: f"{r['building']} {r['floor']}F")(h.rooms[rec["room_idx"]]) if rec["room_idx"] >= 0 else "",
                              "home_room": h.rooms[rec["room_idx"]]["id"] if rec["room_idx"] >= 0 else None,
                              "home_room_name": h.rooms[rec["room_idx"]]["name"] if rec["room_idx"] >= 0 else None,
                              "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else None,
                              "shadow": rec["shadow"] or rec["home_state"] == "shadow",
                              "gateway": gw["id"] if gw else None, "gateway_idx": rec["gw"], "rssi": int(P["rssi"]), "battery": int(P["battery"]),
                              "lead_off": bool(P["lead_off"]), "spo2_off": bool(P["spo2_off"]), "flags": int(P["flags"]),
                              "patch": patch.serial if patch else None, "patch_id": int(P["patch_id"]), "variant": int(P["variant"]),
                              "rhythm_now": self._variant_rhythm(int(P["variant"])), "episode": bool(rec["episode_until"]),
                              "doctor": h.staff_by_id.get(rec["doctor"]) if rec["doctor"] else None,
                              "nurse": h.staff_by_id.get(rec["nurse"]) if rec["nurse"] else None,
                              "art_gain": float(P["art_gain"]), "outpatient": rec["outpatient"], "home_state": rec["home_state"],
                              "devices": prof.get("devices") or ["ecg_patch"],
                              "channels": [k for k, cid in CHANNEL_BY_KEY.items() if (int(P["chan_mask"]) & (1 << cid)) and (int(self.st.ctl[CTL["chan_mask"]]) & (1 << cid))],
                              "lead_off_remaining": max(0.0, max(patch.lead_off_until if patch else 0.0, rec.get("patch_removed_until", 0.0)) - self.sim_time),
                              "episode_remaining": max(0.0, rec["episode_until"] - self.sim_time) if rec["episode_until"] else 0.0,
                              "trip_active": bool(rec["trip"]) or rec["trip_step_until"] > self.sim_time,
                              "trip": ({"note": rec["note"], "stage": rec.get("stage_label", ""), "stage_remaining": max(0.0, rec["trip_step_until"] - self.sim_time),
                                        "total_remaining": max(0.0, rec["trip_step_until"] - self.sim_time) + sum(float(st[1]) for st in rec["trip"]),
                                        "plan": rec.get("trip_plan", []), "steps": self._trip_steps(rec)[0]} if (rec["trip"] or rec["trip_step_until"] > self.sim_time) else None),
                              "settling_remaining": max(0.0, (20.0 - (self._tick_now() - int(P["attach_tick"])) * (self.cfg.get("transport", "bundle_ms") / 1000.0))) if int(P["attach_tick"]) > 0 else 0.0,
                              "attach_tick": int(P["attach_tick"]), "tick_now": self._tick_now(),
                              "sim_speed": self.cfg.get("general", "sim_speed")}
        return out

    def _variant_rhythm(self, v: int) -> str:
        if self.bank.loaded and 0 <= v < len(self.bank.index["variants"]):
            m = self.bank.index["variants"][v]
            return m["label"]
        return "?"

    def planned_beds(self) -> int:
        """Beds the generated hospital should have: active_patients / (1 - headroom) when sizing by patients (min 20),
        otherwise the configured bed_capacity."""
        g = self.cfg.get("general"); hp = self.cfg.get("hospital", default={}) or {}
        if not hp.get("size_by_patients", True):
            return int(g["bed_capacity"])
        head = float(hp.get("headroom_pct", 20) or 0) / 100.0
        return max(20, int(math.ceil(g["active_patients"] / max(0.4, 1.0 - head))))

    def list_admitted_rows(self, rows: list[int]) -> list[dict]:
        want = set(rows)
        return self.list_admitted(only=lambda rec: rec["row"] in want)

    def list_admitted(self, only=None) -> list[dict]:
        h = self.hospital
        P = self.st.patch.arr
        out = []
        for pid, rec in self.admitted.items():
            if only is not None and not only(rec):
                continue
            prof = self.by_id[pid]
            row = rec["row"]
            out.append({"id": pid, "patient_no": rec.get("patient_no"), "mrn": prof["mrn"], "name": prof["name"], "sex": prof["sex"], "age": prof["age"], "disease": prof["disease"], "rhythm": prof["rhythm"],
                        "specialty": (h.wards[rec["ward_idx"]]["specialty"] if rec["ward_idx"] >= 0 else prof["ward_specialty"]), "ward_id": prof["admission"]["ward"] or "",
                        "patch_id": int(P["patch_id"][row]), "rhythm_label": self._variant_rhythm(int(P["variant"][row])),
                        "building_idx": (h.rooms[rec["location"]]["building_idx"] if rec["location"] >= 0 else None), "floor": (h.rooms[rec["location"]]["floor"] if rec["location"] >= 0 else None),
                        "devices": prof.get("devices") or ["ecg_patch"], "pacemaker": prof["pacemaker"], "pacemaker_mode": prof["pacemaker_info"]["mode"] if prof.get("pacemaker_info") else None,
                        "pacemaker_lead": prof["pacemaker_info"]["lead"] if prof.get("pacemaker_info") else None,
                        "pacemaker_type": prof["pacemaker_info"]["type"] if prof.get("pacemaker_info") else None,
                        "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else "MCOT", "ward": prof["admission"]["ward_name"],
                        "gateway": h.gateways[rec["gw"]]["id"] if rec["gw"] >= 0 else None, "activity": rec["activity"], "note": rec["note"],
                        "battery": int(P["battery"][row]), "rssi": int(P["rssi"][row]), "lead_off": bool(P["lead_off"][row]),
                        "patch": self.patches[row].serial if row in self.patches else None, "row": row, "outpatient": rec["outpatient"],
                        "episode": bool(rec["episode_until"])})
        return out

    # exam stations per room in a ~500-bed hospital; scaled up with bed count (a 2000-bed site has 4x the stations)
    EXAM_CAPACITY = {"MRI실": 1, "CT실": 1, "X-ray실": 2, "ECG실": 2, "심초음파실": 2, "채혈실": 4, "내시경실": 2, "폐기능검사실": 1, "재활치료실": 8, "투석실": 10, "심혈관조영실": 1,
                     "외래 진료실": 3, "초음파실": 2, "환자휴게실": 12, "주간 병실/가족실": 10, "주간 병실/가족 라운지": 10, "편의점/카페": 20, "약제부/외래약국": 6, "원무과/접수": 6}
    # non-exam destinations an inpatient may be sent to (prefix of the room name, label, minutes lo/hi, activity while there)
    VISITS = (("외래 진료실", "외래 진료/협진", 15, 30, "still"), ("초음파실", "초음파 검사", 15, 25, "still"), ("환자휴게실", "휴게실", 10, 20, "still"),
              ("주간 병실/가족", "가족 면회", 15, 40, "still"), ("편의점/카페", "편의점/카페", 8, 15, "walking"), ("약제부/외래약국", "약국 수령", 4, 8, "still"),
              ("원무과/접수", "원무과 수납", 4, 10, "still"))
    EXAM_OPEN, EXAM_CLOSE = 8, 17          # local hours the exam rooms take patients

    def exam_capacity(self, key: str) -> int:
        """Stations for a `building:room` key: the per-500-bed base scaled by that building's bed count."""
        b, _, name = key.partition(":")
        if not hasattr(self, "_bld_beds"):
            self._bld_beds = collections.Counter(self.hospital.rooms[bed["room_idx"]]["building_idx"] for bed in self.hospital.beds)
        scale = max(1, -(-self._bld_beds.get(int(b) if b.isdigit() else 0, 0) // 500))
        base = next((v for k, v in self.EXAM_CAPACITY.items() if name.startswith(k)), 1)
        return base * scale

    def _book_exam(self, key: str, dur_s: float, want_t: float) -> float:
        """Earliest start >= want_t (inside opening hours) at which fewer than `capacity` exams overlap in room `key`
        (building:room).  Keeps every patient's schedule from piling into the same room at the same time."""
        cap = self.exam_capacity(key)
        book = self.exam_book.setdefault(key, [])
        starts = [b[0] for b in book]
        t = want_t
        for _ in range(4000):
            d = dt.datetime.fromtimestamp(t)
            if d.hour < self.EXAM_OPEN:
                t = d.replace(hour=self.EXAM_OPEN, minute=0, second=0, microsecond=0).timestamp(); continue
            if d.hour >= self.EXAM_CLOSE or (d.replace(hour=self.EXAM_CLOSE, minute=0, second=0, microsecond=0).timestamp() - t) < dur_s:
                t = (d.replace(hour=self.EXAM_OPEN, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)).timestamp(); continue
            lo = bisect.bisect_left(starts, t - 6 * 3600)                  # only bookings that can still overlap (max exam 4 h)
            hi = bisect.bisect_right(starts, t + dur_s)
            busy = [b for b in book[lo:hi] if b[0] < t + dur_s and b[1] > t]
            if len(busy) < cap:
                i = bisect.bisect_right(starts, t)
                book.insert(i, (t, t + dur_s))
                return t
            t = min(b[1] for b in busy) + 1.0                              # jump to the first slot that frees up
        return t

    def _book_exams(self, rec: dict, exams: list[dict]) -> None:
        for ex in exams:
            ex["t"] = self._book_exam(self._rkey(self._room_for(rec, ex["room"]), ex["room"]), ex["duration_min"] * 60.0, ex["t"])
            ex["time"] = dt.datetime.fromtimestamp(ex["t"]).isoformat(timespec="minutes")
        exams.sort(key=lambda e: e["t"])

    def _exam_room_free(self, key: str) -> bool:
        return self.exam_load[key] < self.exam_capacity(key)

    def _free_for(self, rec: dict, name: str) -> bool:
        return self._exam_room_free(self._rkey(self._room_for(rec, name), name))

    TRIP_KINDS = (("검사 이동", "exam"), ("방문", "visit"), ("병실 이동", "transfer"), ("음영 구간", "shadowtrip"), ("화장실", "toilet"), ("복도 보행", "walk"), ("샤워", "shower"), ("운동/재활", "rehab"))

    def _trip_steps(self, rec: dict):
        """Timetable of the current trip: every planned step with its (simulated) start time and state
        (done / current / upcoming), plus the MRI re-patch and the return to the ward as final pseudo-steps."""
        h = self.hospital
        now = self.sim_time
        iso = lambda t: dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
        plan = rec.get("trip_plan") or []
        cur = max((i for i, st in enumerate(plan) if st["done"]), default=-1)
        cur_end = rec["trip_step_until"]
        cur_start = cur_end - (plan[cur]["dur"] if cur >= 0 else 0.0)
        steps = []
        for i, st in enumerate(plan):
            if i < cur:
                start = cur_start - sum(x["dur"] for x in plan[i:cur]); state = "done"
            elif i == cur:
                start = cur_start; state = "current"
            else:
                start = cur_end + sum(x["dur"] for x in plan[cur + 1:i]); state = "upcoming"
            steps.append({"label": st["label"], "dur": st["dur"], "start": iso(start), "start_t": start, "state": state,
                          "remaining": max(0.0, cur_end - now) if state == "current" else (0.0 if state == "done" else st["dur"])})
        end_t = cur_end + sum(x["dur"] for x in plan[cur + 1:])
        room = h.rooms[rec["room_idx"]] if rec["room_idx"] >= 0 else None
        if any("MRI" in st["label"] for st in plan):        # fixed MRI routine: scan without the patch, then a new patch is attached on return
            steps.append({"label": "새 패치 부착 · 게이트웨이 재연결", "dur": 0.0, "start": iso(end_t), "start_t": end_t, "state": "upcoming", "remaining": 0.0})
        steps.append({"label": f"병실 {room['id']} 복귀" if room else "복귀", "dur": 0.0, "start": iso(end_t), "start_t": end_t, "state": "upcoming", "remaining": 0.0})
        return steps, end_t, cur_start, plan, cur

    def list_trips(self, upcoming_h: float = 1.0) -> dict:
        """Patients currently on a trip (exam, toilet, walk, shower, rehab, bed change) with their step timetable, plus the
        next scheduled exams of every inpatient.  Times are simulated-clock ISO strings; remaining/elapsed are seconds."""
        h = self.hospital
        P = self.st.patch.arr
        now = self.sim_time
        iso = lambda t: dt.datetime.fromtimestamp(t).isoformat(timespec="seconds")
        trips: list[dict] = []
        kinds = {k: 0 for _, k in self.TRIP_KINDS}
        n_shadow = n_mri = 0
        exams_soon: list[dict] = []
        for pid, rec in self.admitted.items():
            prof = self.by_id[pid]
            for ex in rec.get("exams") or []:
                if not ex["done"] and now <= ex["t"] <= now + upcoming_h * 3600:
                    exams_soon.append({"id": pid, "name": prof["name"], "type": ex["type"], "room": ex["room"], "time": iso(ex["t"]), "in_s": ex["t"] - now,
                                       "patch_policy": ex["patch_policy"], "duration_min": ex["duration_min"], "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else "MCOT"})
            active = bool(rec["trip"]) or rec["trip_step_until"] > now
            if not active:
                continue
            note = rec.get("note") or ""
            kind = next((k for ko, k in self.TRIP_KINDS if note.startswith(ko)), "other")
            kinds[kind] = kinds.get(kind, 0) + 1
            steps, end_t, cur_start, plan, cur = self._trip_steps(rec)
            cur_end = rec["trip_step_until"]
            shadow = bool(rec["shadow"] or rec["home_state"] == "shadow")
            n_shadow += shadow
            n_mri += bool(rec.get("mri_pending"))
            gw = h.gateways[rec["gw"]] if rec["gw"] >= 0 else None
            next_exams = sorted((ex for ex in rec.get("exams") or [] if not ex["done"] and ex["t"] > now), key=lambda e: e["t"])[:3]
            started = rec.get("trip_started", cur_start)
            trips.append({"id": pid, "row": rec["row"], "patient_no": rec.get("patient_no"), "name": prof["name"], "sex": prof["sex"], "age": prof["age"],
                          "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else "MCOT", "ward": (prof.get("admission") or {}).get("ward_name") or "",
                          "kind": kind, "note": note, "stage": rec.get("stage_label", ""), "stage_remaining": max(0.0, cur_end - now),
                          "total_remaining": max(0.0, end_t - now), "elapsed": max(0.0, now - started), "started": iso(started), "ends": iso(end_t),
                          "progress": (cur + 1) / max(1, len(plan)), "n_steps": len(plan), "step_no": cur + 1,
                          "location": h.rooms[self.display_location(rec)]["id"] if rec["location"] >= 0 else "-",
                          "location_name": h.rooms[self.display_location(rec)]["name"] if rec["location"] >= 0 else rec["home_state"],
                          "building_idx": h.rooms[self.display_location(rec)]["building_idx"] if rec["location"] >= 0 else None,
                          "floor": h.rooms[self.display_location(rec)]["floor"] if rec["location"] >= 0 else None,
                          "shadow": shadow, "gateway": gw["id"] if gw else None, "gateway_idx": rec["gw"],
                          "mri": bool(rec.get("mri_pending")), "patch_removed": rec.get("patch_removed_until", 0.0) > now,
                          "lead_off": bool(P["lead_off"][rec["row"]]), "activity": rec["activity"], "outpatient": rec["outpatient"],
                          "steps": steps, "next_exams": [{"type": e["type"], "room": e["room"], "time": iso(e["t"]), "in_s": e["t"] - now, "patch_policy": e["patch_policy"], "duration_min": e["duration_min"]} for e in next_exams]})
        trips.sort(key=lambda t: t["total_remaining"])
        exams_soon.sort(key=lambda e: e["in_s"])
        return {"sim_time": iso(now), "trips": trips,
                "stats": {"moving": len(trips), "kinds": kinds, "shadow": n_shadow, "mri_patch_off": n_mri,
                          "exams_soon": len(exams_soon), "upcoming_h": upcoming_h, "admitted": len(self.admitted),
                          "rooms": [{"room": f"{h.buildings[b]['name']} {name}", "load": int(self.exam_load[f"{b}:{name}"]), "capacity": self.exam_capacity(f"{b}:{name}")}
                                    for b in range(len(h.buildings)) for name in list(h.exam_rooms) + [v[0] for v in self.VISITS] if self._room_named(name, b) >= 0]},
                "exams_soon": exams_soon[:200]}

    def gateway_view(self) -> list[dict]:
        G = self.st.gw.arr
        S = self.st.stat.arr
        h = self.hospital
        out = []
        for g in h.gateways:
            i = g["idx"]
            out.append({"idx": i, "id": g["id"], "gw_no": g["gw_no"], "type": g["type"], "building": g["building"], "floor": g["floor"], "room": h.rooms[g["room_idx"]]["id"] if g["room_idx"] >= 0 else "",
                        "status": int(G["status"][i]), "n_conn": int(G["n_conn"][i]), "capacity": g["capacity"], "cpu": int(G["cpu"][i]), "mem": int(G["mem"][i]),
                        "net": int(G["net"][i]), "wan_rssi": int(G["wan_rssi"][i]), "loss": float(G["loss"][i]), "latency_ms": float(G["latency_ms"][i]),
                        "uptime_s": int(G["uptime_s"][i]), "pkts": int(S["pkts"][i]), "bytes": int(S["bytes"][i]), "connected": bool(S["connected"][i]),
                        "battery": int(G["battery"][i]) if g["type"] == "mobile" else None, "reason": self.gw_state[i]["reason"], "ip": g["ip"], "mac": g["mac"]})
        return out

    def trend(self, pid: int, days: int = 1, step_s: int = 600, loop_now: float | None = None) -> dict | None:
        """Multi-day trend (10-min points): the patient's loop numerics under the same circadian + drift
        model that modulates the live stream, so the graph and the transmitted data agree."""
        rec = self.admitted.get(pid)
        prof = self.by_id.get(pid)
        if rec is None or prof is None or not self.bank.loaded:
            return None
        P = self.st.patch.arr[rec["row"]]
        v, tv, gv = int(rec["base_variant"]), int(P["temp_var"]), int(P["gluc_var"])
        sec = self.bank.sec
        n = int(days * 86400 // step_s)
        end = self.sim_time
        ts = end - np.arange(n)[::-1] * step_s
        # loop position: anchor to the live playback position when known (so the last point matches the live reading)
        if loop_now is None:
            loop_now = (int(P["offset_ms"]) / 1000.0 + (end - self.cfg_start_time)) % self.bank.seconds
        loop_i = (np.floor(loop_now - (end - ts)).astype(np.int64)) % self.bank.seconds
        w = max(1, min(300, step_s // 2))

        def loop_mean(arr, idx):
            out = np.empty(idx.size)
            for k, i in enumerate(idx):
                seg = arr[max(0, i - w): i + w]
                seg = seg[seg > 0] if arr.dtype == np.uint8 else seg
                out[k] = seg.mean() if seg.size else 0.0
            return out

        m = trend_model.series(pid, prof, ts)
        spo2_row = 4 + int(P["spo2_src"]) if int(P["spo2_src"]) in (0, 1, 2) else 4
        rr_row = 1 + int(P["resp_src"]) if int(P["resp_src"]) in (0, 1, 2) else 1
        # Older points: variant mean + model + small seeded noise (variant hopping makes the live history
        # non-periodic, so the loop's within-hour structure must not be replayed).  Recent points (last
        # 15 min, current variant) follow the actual loop position so the end matches the live reading.
        age = end - ts
        wrec = np.clip(1.0 - age / 900.0, 0.0, 1.0)
        nrng = np.random.default_rng(pid * 31 + days)

        def blend(arr, noise_sd):
            vals = arr[arr > 0] if arr.dtype == np.uint8 else arr
            const = float(vals.mean()) if vals.size else 0.0
            rec_part = loop_mean(arr, loop_i)
            smooth = nrng.normal(0, noise_sd, n)
            smooth = np.convolve(np.pad(smooth, 2, mode="edge"), np.ones(5) / 5, mode="valid")
            return (const + smooth) * (1 - wrec) + rec_part * wrec

        series = {"hr": blend(sec[v, 0], 2.0) * m["hr_scale"],
                  "spo2": blend(sec[v, spo2_row], 0.5) + int(P["spo2_bias"]) + m["spo2_add"],
                  "resp": blend(sec[v, rr_row], 0.7) + m["rr_add"],
                  "temp": blend(self.bank.temp[tv], 0.03) + float(P["temp_bias"]) + m["temp_add"],
                  "glucose": blend(self.bank.glucose[gv], 2.5) + m["gl_add"]}
        clip = {"hr": (20, 220), "spo2": (60, 100), "resp": (4, 50), "temp": (33, 42), "glucose": (40, 450)}
        out = {k: [round(float(x), 2 if k == "temp" else 1) for x in np.clip(arr, *clip[k])] for k, arr in series.items()}
        out["t"] = [int(t) for t in ts]
        nb = [trend_model.nibp(pid, prof, float(t)) for t in ts]
        out["bp_sys"] = [x["sys"] for x in nb]
        out["bp_dia"] = [x["dia"] for x in nb]
        out["bp_map"] = [x["map"] for x in nb]
        out["days"] = days
        out["step_s"] = step_s
        out["channels"] = [k for k, cid in CHANNEL_BY_KEY.items() if (int(P["chan_mask"]) & (1 << cid))]
        out["note"] = "생성 모델 추세: 환자 루프 데이터 + 일주기 변동 + 완만한 드리프트 (실시간 전송 데이터와 동일 모델)"
        return out

    # ------------------------------------------------------------------ router-test drills (timers)
    def _step_drills(self) -> None:
        G = self.st.gw.arr
        now = self.sim_time
        for gw, gs in enumerate(self.gw_state):
            if gs.get("silent_until", 0.0) and now >= gs["silent_until"]:
                gs["silent_until"] = 0.0
                G["silent"][gw] = 0
                self.log.add("gateway", f"[드릴] {self.hospital.gateways[gw]['id']} 반열림 해제 - 전송 재개", gw=gw)
            if gs.get("dup_until", 0.0) and now >= gs["dup_until"]:
                gs["dup_until"] = 0.0
                G["gw_id"][gw] = gs.get("dup_restore", G["gw_id"][gw])
                self.log.add("gateway", f"[드릴] {self.hospital.gateways[gw]['id']} gw_id 중복 해제 (#{int(G['gw_id'][gw])})", gw=gw)

    # ------------------------------------------------------------------ scenario scripts (deterministic drill sequences)
    def load_script(self, name: str, items: list[dict]) -> dict:
        """items: [{"at": seconds_from_start, "what": trigger, "target": optional, "params": {...}, "note": ""}], run at sim time."""
        norm = []
        for it in items:
            if "what" not in it:
                continue
            norm.append({"at": float(it.get("at", 0)), "what": str(it["what"]), "target": it.get("target"), "params": dict(it.get("params") or {}), "note": str(it.get("note", ""))})
        norm.sort(key=lambda x: x["at"])
        with self.lock:
            self.script = {"name": name, "t0": self.sim_time, "items": norm, "next": 0, "log": []}
            self.log.add("script", f"시나리오 스크립트 시작: {name} ({len(norm)}단계, {norm[-1]['at'] if norm else 0:.0f}초)")
        return self.script_status()

    def cancel_script(self) -> dict:
        with self.lock:
            if self.script:
                self.log.add("script", f"시나리오 스크립트 취소: {self.script['name']} ({self.script['next']}/{len(self.script['items'])} 단계 실행)")
            self.script = None
        return self.script_status()

    def script_status(self) -> dict:
        sc = self.script
        if not sc:
            return {"active": False}
        el = self.sim_time - sc["t0"]
        nxt = sc["items"][sc["next"]] if sc["next"] < len(sc["items"]) else None
        return {"active": True, "name": sc["name"], "elapsed_s": round(el, 1), "done": sc["next"], "total": len(sc["items"]),
                "next": nxt, "log": sc["log"][-20:], "finished": sc["next"] >= len(sc["items"])}

    def _step_script(self) -> None:
        sc = self.script
        if not sc:
            return
        el = self.sim_time - sc["t0"]
        while sc["next"] < len(sc["items"]) and sc["items"][sc["next"]]["at"] <= el:
            it = sc["items"][sc["next"]]
            sc["next"] += 1
            self.lock.release()                     # trigger() takes the lock itself
            try:
                res = self.trigger(it["what"], it["target"], it["params"])
            finally:
                self.lock.acquire()
            sc["log"].append({"at": it["at"], "what": it["what"], "result": res, "t": time.time()})
            self.log.add("script", f"[스크립트 {sc['name']}] +{it['at']:.0f}s {it['what']}{' ' + it['note'] if it['note'] else ''} → {res}")
        if sc["next"] >= len(sc["items"]) and not sc.get("_done_logged"):
            sc["_done_logged"] = True
            self.log.add("script", f"시나리오 스크립트 완료: {sc['name']}")

    # manual scenario triggers (UI buttons)
    def trigger(self, what: str, target: int | None = None, params: dict | None = None) -> str:
        params = params or {}
        with self.lock:
            G = self.st.gw.arr
            now = self.sim_time
            h = self.hospital
            if what == "storm":
                # connection storm: every worker drops all sockets now and reconnects without the per-cycle budget for `duration` s
                dur = float(params.get("duration", 20))
                self.st.ctl[CTL["storm_epoch"]] = time.time()
                self.st.ctl[CTL["storm_until"]] = time.time() + dur
                self.counters["storms"] = self.counters.get("storms", 0) + 1
                self.log.add("network", f"[드릴] 연결 폭주: 게이트웨이 {h.n_fixed_gateways}대 동시 재접속 ({dur:.0f}초 동안 접속 제한 해제)")
                return f"connection storm: {h.n_fixed_gateways} gateways reconnecting at once"
            if what == "half_open":
                # hung gateway(s): TCP stays established but nothing is sent (no keepalive) for `duration` s
                dur = float(params.get("duration", 60)); n = int(params.get("count", 1))
                gws = [target] if target is not None and 0 <= target < h.n_fixed_gateways else [int(x) for x in self.rng.choice(h.n_fixed_gateways, size=min(n, h.n_fixed_gateways), replace=False)]
                for gw in gws:
                    G["silent"][gw] = 1
                    self.gw_state[gw]["silent_until"] = now + dur
                self.log.add("gateway", f"[드릴] 반열림(half-open) 연결: {', '.join(h.gateways[g]['id'] for g in gws)} {dur:.0f}초 동안 무전송")
                return f"half-open: {len(gws)} gateway(s) silent for {dur:.0f}s"
            if what == "dup_id":
                # two live gateways report the same gw_id (cloned / misconfigured unit) for `duration` s
                dur = float(params.get("duration", 60))
                gw = target if target is not None and 0 <= target < h.n_fixed_gateways else int(self.rng.integers(h.n_fixed_gateways))
                other = int((gw + 1) % h.n_fixed_gateways)
                self.gw_state[gw]["dup_restore"] = int(G["gw_id"][gw])
                self.gw_state[gw]["dup_until"] = now + dur
                G["gw_id"][gw] = G["gw_id"][other]
                self.log.add("gateway", f"[드릴] gw_id 중복: {h.gateways[gw]['id']} 가 {h.gateways[other]['id']} 의 번호 #{int(G['gw_id'][other])} 로 전송 ({dur:.0f}초)", gw=gw)
                return f"dup id: {h.gateways[gw]['id']} sends as #{int(G['gw_id'][other])} for {dur:.0f}s"
            if what == "capture":
                on = bool(params.get("enabled", True))
                self.st.ctl[CTL["tap"]] = 1 if on else 0
                self.log.add("system", f"[드릴] 정답 데이터 캡처 {'시작' if on else '중지'}")
                return f"capture {'on' if on else 'off'}"
            if what == "gateway_fault":
                gw = target if target is not None and 0 <= target < len(h.gateways) else int(self.rng.integers(h.n_fixed_gateways))
                G["status"][gw] = 2
                self.gw_state[gw]["fault_until"] = now + 90
                self.gw_state[gw]["reason"] = "수동 장애"
                self.log.add("gateway", f"[수동] 게이트웨이 {h.gateways[gw]['id']} 장애 90초")
                return f"gateway {h.gateways[gw]['id']} down 90s"
            if what == "gateway_replace":
                gw = target if target is not None and 0 <= target < len(h.gateways) else int(self.rng.integers(h.n_fixed_gateways))
                G["status"][gw] = 2
                self.gw_state[gw]["fault_until"] = now + 30
                self.gw_state[gw]["reason"] = "하드웨어 고장 (교체 대기)"
                self.log.add("gateway", f"[수동] 게이트웨이 {h.gateways[gw]['id']} 하드웨어 고장 → 30초 후 교체", gw=gw)
                return f"gateway {h.gateways[gw]['id']} failed, replacement in 30s"
            if what == "network_event":
                net = self.cfg.get("scenario", "network")
                net = {**net, "wireless_noise": True, "wired_failure": True, "latency": True, "power_outage": True}
                self._network_event(net, max(0.3, net["intensity"] / 100))
                return "network event injected"
            if what in ("lead_off_off", "episode_off", "exam_off") and target in self.admitted:
                rec = self.admitted[target]
                prof = self.by_id[target]
                if what == "lead_off_off":
                    patch = self.patches.get(rec["row"])
                    if patch:
                        patch.lead_off_until = 0.0
                    rec["patch_removed_until"] = 0.0
                    self.log.add("patch", f"[수동] {prof['name']} 리드 오프 해제", patient_id=target)
                    return "lead-off cleared"
                if what == "episode_off":
                    rec["episode_until"] = 0.0
                    self._switch_variant(rec["row"], rec["base_variant"], self._tick_now())
                    self.log.add("rhythm", f"[수동] {prof['name']} 에피소드 종료 → 기저 리듬", patient_id=target)
                    return "episode ended"
                if what == "exam_off":
                    rec["trip"] = []
                    rec["trip_step_until"] = 0.0
                    self._next_trip_step(rec)                   # replaces the patch if the scan had started
                    self.log.add("exam", f"[수동] {prof['name']} 검사 이동 취소 → 병실 복귀", patient_id=target)
                    return "exam trip cancelled"
            if what in ("lead_off", "episode", "exam", "replace_patch", "discharge", "admit", "vfib"):
                if what == "admit":
                    rec = self.admit()
                    if rec:
                        p = self.by_id[rec["id"]]
                        self.log.add("adt", f"[수동] 신규 입원: {p['name']} → {p['admission']['ward_name']} {p['admission']['bed']}")
                        return f"admitted {p['name']}"
                    return "no bed/profile"
                ids = [k for k, r in self.admitted.items() if not r["outpatient"]] or list(self.admitted.keys())
                if not ids:
                    return "no patients"
                pid = target if target in self.admitted else int(self.rng.choice(ids))
                rec = self.admitted[pid]
                prof = self.by_id[pid]
                if what == "lead_off":
                    self.patches[rec["row"]].lead_off_until = now + 60
                    return f"lead-off {prof['name']} 60s"
                if what == "episode":
                    rec["episode_until"] = 0.0
                    new = str(self.rng.choice(["afib", "nsvt", "svt", "pvc_bigeminy", "avb3", "vt"]))
                    self._switch_variant(rec["row"], self._variant_for(new, prof["age"]), self._tick_now())
                    rec["episode_until"] = now + 180
                    self.log.add("rhythm", f"[수동] {prof['name']} 에피소드 {RHYTHMS[new]['label']} 180초", patient_id=pid)
                    return f"episode {new} on {prof['name']}"
                if what == "vfib":
                    self._switch_variant(rec["row"], self._variant_for("vfib", prof["age"]), self._tick_now())
                    rec["episode_until"] = now + 120
                    self.log.add("rhythm", f"[수동] {prof['name']} 심실세동 120초", patient_id=pid)
                    return f"vfib on {prof['name']}"
                if what == "exam":
                    ex = {"type": "MRI", "room": "MRI실", "duration_min": 3, "patch_policy": "remove"}
                    if rec["outpatient"]:
                        return "outpatient"
                    self._plan_exam_trip(rec, ex)
                    return f"exam trip {prof['name']}"
                if what == "replace_patch":
                    self._replace_patch(rec, "수동 교체")
                    return f"patch replaced {prof['name']}"
                if what == "discharge":
                    self.discharge(pid, "[수동] 퇴원")
                    return f"discharged {prof['name']}"
            return "unknown"
