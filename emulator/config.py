"""Emulator configuration: defaults, persistence and validation.

All options exposed in the web control panel live here as one nested dict so
that the UI, the discovery API and the engine share the same source of truth.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.environ.get("BIOSIM_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
LOOP_DIR = BASE_DIR / "loops"
PROFILE_DIR = BASE_DIR / "profiles"
CONFIG_PATH = BASE_DIR / "config.json"

# Channel ids used in the binary protocol (never renumber; append only)
CH_ECG = 1
CH_HR = 2
CH_TEMP = 3
CH_RESP_RATE = 4
CH_SPO2 = 5
CH_GLUCOSE = 6
CH_ACCEL = 7
CH_PPG = 8
CH_RESP_WAVE = 9
CH_PACE = 10          # pacemaker spike marker events (sample index list)

CHANNELS: dict[int, dict[str, Any]] = {
    CH_ECG:       {"key": "ecg", "name": "ECG", "unit": "mV", "dtype": "int16", "scale": 0.001, "kind": "waveform"},
    CH_HR:        {"key": "hr", "name": "Heart Rate", "unit": "bpm", "dtype": "uint8", "scale": 1, "kind": "numeric"},
    CH_TEMP:      {"key": "temp", "name": "Body Temperature", "unit": "°C", "dtype": "int16", "scale": 0.01, "kind": "numeric"},
    CH_RESP_RATE: {"key": "resp", "name": "Respiration Rate", "unit": "brpm", "dtype": "uint8", "scale": 1, "kind": "numeric"},
    CH_SPO2:      {"key": "spo2", "name": "SpO2", "unit": "%", "dtype": "uint8", "scale": 1, "kind": "numeric"},
    CH_GLUCOSE:   {"key": "glucose", "name": "Glucose", "unit": "mg/dL", "dtype": "uint16", "scale": 0.1, "kind": "numeric"},
    CH_ACCEL:     {"key": "accel", "name": "Accelerometer XYZ", "unit": "g", "dtype": "int16", "scale": 0.001, "kind": "waveform", "axes": 3},
    CH_PPG:       {"key": "ppg", "name": "PPG", "unit": "a.u.", "dtype": "int16", "scale": 0.001, "kind": "waveform"},
    CH_RESP_WAVE: {"key": "resp_wave", "name": "Respiration Waveform", "unit": "a.u.", "dtype": "int16", "scale": 0.001, "kind": "waveform"},
    CH_PACE:      {"key": "pace", "name": "Pacemaker Spike Marks", "unit": "sample_idx", "dtype": "uint16", "scale": 1, "kind": "event"},
}
CHANNEL_BY_KEY = {v["key"]: k for k, v in CHANNELS.items()}

RESP_SOURCES = ["capacitive", "edr", "spo2"]
SPO2_SOURCES = ["fingertip", "ring", "wrist_ptt"]

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "seed": 20240905,
        "profile_count": 10000,
        "bed_capacity": 2000,          # hospital beds (== max concurrent in-hospital patches)
        "active_patients": 120,        # currently admitted & monitored
        "outpatient_count": 10,        # MCOT (home) patients
        "heart_disease_ratio": 0.70,
        "korean_ratio": 0.90,
        "sim_speed": 1.0,              # scenario clock multiplier (battery drain, admissions...)
        "db_retention_days": 7,        # history older than this is pruned from emulator.db at startup (retired patches, closed admissions, events)
        "admissions_per_hour": 4,      # new admissions / discharges per (sim) hour
        "discharges_per_hour": 4,
    },
    "signals": {
        "ecg_fs": 250,                 # 250 or 500
        "ppg_fs": 100,
        "resp_fs": 25,
        "accel_fs": 50,
        "enabled": {"ecg": True, "hr": True, "temp": True, "resp": True, "spo2": True,
                    "glucose": False, "accel": True, "ppg": False, "resp_wave": False, "pace": True},
        "resp_source": "capacitive",
        "spo2_source": "fingertip",
        "pacemaker_ratio": 0.06,       # share of heart patients with a pacemaker
        "variants_per_rhythm": 12,     # pre-generated 1-hour loop files per rhythm class
        "loop_seconds": 3600,
    },
    "transport": {
        "target_ip": "",               # empty => generate only
        "target_port": 9100,
        "socket_mode": "per_gateway",  # per_gateway | shared
        "bundle_ms": 200,
        "meta_every_n_frames": 25,     # 25 x 200ms = 5 s
        "gw_status_every_n_frames": 5,
        "workers": 0,                  # 0 = auto (cpu_count-1), 1 = in-process
        "capture_max_mb": 256,         # ground-truth capture files, total across workers; the tap switches itself off at the cap
        "saf_worker_max_mb": 64,       # store-and-forward buffer per worker (on top of the 2 MB per gateway)
        "max_send_backlog_bytes": 262144,
        "reconnect_interval_s": 3.0,
        "router_status_url": "",       # optional: GET url of router status (part 2)
        # ---- router test drills
        "store_forward": {"enabled": True, "max_bytes_per_gw": 2097152, "burst_frames_per_cycle": 40},   # buffer frames while down/offline, replay in bursts after reconnect
        "fuzz": {"enabled": False, "rate_per_1000": 5,                                                  # corrupt frames on purpose (parser robustness)
                 "kinds": ["bad_magic", "bad_version", "bad_len", "truncated", "oversize", "garbage", "dup", "reorder", "seq_gap", "bad_record"]},
        "storm_smoothing": True,                                                                          # False = every gateway reconnects at once (connection storm)
        "capture": {"enabled": False},                                                                    # ground-truth tap: exact bytes sent -> data/capture/w<worker>.bin
    },
    "hospital": {
        "template": "auto",            # auto (by bed capacity) or one of layout.TEMPLATES
        "layout_file": "",             # optional imported layout JSON (data/layout_import.json)
        "size_by_patients": True,      # size the generated hospital to the scenario: beds = active_patients / (1 - headroom), 1..3 buildings
        "headroom_pct": 20,            # free beds kept on top of the monitored patients (20 % -> patients fill 80 % of the beds)
        "max_buildings": 3,
    },
    "scenario": {
        "site": "hospital",            # hospital | mcot | mixed
        "rhythm_episodes": True,       # spontaneous arrhythmia episodes (paroxysms) on top of the base rhythm
        "variant_hopping": True,       # rotate among same-rhythm loop variants every 8-25 min so no hour repeats
        "devices": {"policy": "auto",   # per-patient device set policy: auto | all | minimal
                    "spo2_mix": {"fingertip": 55, "ring": 35, "wrist_ptt": 10}},   # SpO2 sensor type weights (normalised; outpatients never get fingertip)
        "network": {
            "enabled": False,
            "intensity": 30,           # 0..100
            "wireless_noise": True,
            "wired_failure": True,
            "latency": True,
            "power_outage": True,
        },
        "exam_trip_ratio": 5.0,        # % of inpatients out of bed on a trip at any moment (exam rooms, clinic, dialysis, cafe, lounge...); independent of the artifact scenario
        "artifacts": {
            "enabled": True,
            "intensity": 40,
            "motion": True,
            "shower": True,
            "exam_trips": True,
            "patch_reattach": True,
            "transfer": True,
            "patch_replace": True,
            "home_interference": True,
        },
        "patch": {
            "battery_days": 14,
            "battery_drain_enabled": True,
            "lead_off_enabled": True,
            "replace_below_pct": 5,
        },
        "gateway": {
            "capacity": 32,
            "fault_enabled": True,
            "fault_intensity": 20,
            "outage": True,            # unresponsive gateway (all patches drop, reconnect elsewhere)
            "degrade": True,           # CPU overload / packet loss / latency
            "replace": True,           # hardware failure -> unit replaced with a new gateway number / MAC
            "corridor_gateways": True,
        },
    },
}


FUZZ_KINDS = ["bad_magic", "bad_version", "bad_len", "truncated", "oversize", "garbage", "dup", "reorder", "seq_gap", "bad_record"]


def _deep_update(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


class Config:
    """Thread-safe nested config with JSON persistence."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                with self._lock:
                    _deep_update(self._data, saved)
            except Exception:
                pass
        self._validate()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def get(self, *keys: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self._data
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return copy.deepcopy(cur)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            _deep_update(self._data, patch)
            self._validate()
            snap = copy.deepcopy(self._data)
        self.save()
        return snap

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._data = copy.deepcopy(DEFAULT_CONFIG)
        self.save()
        return self.snapshot()

    def _validate(self) -> None:
        d = self._data
        g = d["general"]
        g["bed_capacity"] = int(max(1, min(5000, g["bed_capacity"])))
        g["active_patients"] = int(max(0, min(g["bed_capacity"], g["active_patients"])))
        g["outpatient_count"] = int(max(0, min(2000, g["outpatient_count"])))
        g["profile_count"] = int(max(g["bed_capacity"] + g["outpatient_count"] + 100, min(50000, g["profile_count"])))
        g["heart_disease_ratio"] = float(min(1.0, max(0.0, g["heart_disease_ratio"])))
        g["korean_ratio"] = float(min(1.0, max(0.0, g["korean_ratio"])))
        g["sim_speed"] = float(min(1000.0, max(0.1, g["sim_speed"])))
        s = d["signals"]
        s["ecg_fs"] = 500 if int(s["ecg_fs"]) >= 500 else 250
        s["variants_per_rhythm"] = int(max(1, min(200, s["variants_per_rhythm"])))
        s["loop_seconds"] = int(max(60, min(3600, s["loop_seconds"])))
        if s["resp_source"] not in RESP_SOURCES:
            s["resp_source"] = "capacitive"
        if s["spo2_source"] not in SPO2_SOURCES:
            s["spo2_source"] = "fingertip"
        t = d["transport"]
        t["bundle_ms"] = int(max(50, min(2000, t["bundle_ms"])))
        t["meta_every_n_frames"] = int(max(1, min(1000, t["meta_every_n_frames"])))
        t["gw_status_every_n_frames"] = int(max(1, min(1000, t["gw_status_every_n_frames"])))
        t["workers"] = int(max(0, min(16, t["workers"])))
        t["target_port"] = int(max(1, min(65535, t["target_port"])))
        if t["socket_mode"] not in ("per_gateway", "shared"):
            t["socket_mode"] = "per_gateway"
        sf = t.setdefault("store_forward", {})
        sf["enabled"] = bool(sf.get("enabled", True))
        sf["max_bytes_per_gw"] = int(max(65536, min(64 * 1024 * 1024, sf.get("max_bytes_per_gw", 2097152))))
        sf["burst_frames_per_cycle"] = int(max(1, min(1000, sf.get("burst_frames_per_cycle", 40))))
        fz = t.setdefault("fuzz", {})
        fz["enabled"] = bool(fz.get("enabled", False))
        fz["rate_per_1000"] = float(max(0.0, min(1000.0, fz.get("rate_per_1000", 5))))
        fz["kinds"] = [k for k in (fz.get("kinds") or []) if k in FUZZ_KINDS] or list(FUZZ_KINDS)
        t["storm_smoothing"] = bool(t.get("storm_smoothing", True))
        t.setdefault("capture", {})["enabled"] = bool(t.get("capture", {}).get("enabled", False))
        hp = d.setdefault("hospital", {"template": "auto", "layout_file": ""})
        hp.setdefault("template", "auto")
        hp.setdefault("layout_file", "")
        hp["size_by_patients"] = bool(hp.get("size_by_patients", True))
        hp["headroom_pct"] = float(max(0.0, min(60.0, hp.get("headroom_pct", 20) or 0)))
        hp["max_buildings"] = int(max(1, min(8, hp.get("max_buildings", 3) or 3)))
        sc = d["scenario"]
        if sc["site"] not in ("hospital", "mcot", "mixed"):
            sc["site"] = "hospital"
        for sec in ("network", "artifacts"):
            sc[sec]["intensity"] = int(max(0, min(100, sc[sec]["intensity"])))
        sc["gateway"]["capacity"] = int(max(1, min(64, sc["gateway"]["capacity"])))
        sc["exam_trip_ratio"] = float(max(0.0, min(30.0, sc.get("exam_trip_ratio", 5.0) or 0.0)))
        if sc.setdefault("devices", {}).get("policy") not in ("auto", "all", "minimal"):
            sc["devices"]["policy"] = "auto"
        mix = sc["devices"].setdefault("spo2_mix", {"fingertip": 55, "ring": 35, "wrist_ptt": 10})
        for k in ("fingertip", "ring", "wrist_ptt"):
            mix[k] = float(max(0.0, min(100.0, mix.get(k, 0))))
        if sum(mix.values()) <= 0:
            mix.update(fingertip=55, ring=35, wrist_ptt=10)
        sc["gateway"]["fault_intensity"] = int(max(0, min(100, sc["gateway"]["fault_intensity"])))
        sc["patch"]["battery_days"] = float(max(0.01, min(30, sc["patch"]["battery_days"])))


def channel_mask(enabled: dict[str, bool]) -> int:
    mask = 0
    for key, on in enabled.items():
        if on and key in CHANNEL_BY_KEY:
            mask |= 1 << CHANNEL_BY_KEY[key]
    return mask
