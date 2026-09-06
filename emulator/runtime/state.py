"""Shared-memory state tables read by the fast path (worker processes) and
written by the world engine (main process).  Struct-of-arrays layout so the
per-tick sample gather is fully vectorised.
"""
from __future__ import annotations

from multiprocessing import shared_memory

import numpy as np

PATCH_FIELDS = [
    ("active", np.uint8),        # 0 idle, 1 streaming
    ("gw", np.int32),            # gateway row or -1 (no link)
    ("patch_id", np.uint32),
    ("patient_id", np.uint32),
    ("variant", np.int32),       # ecg/ppg/resp/sec bank row
    ("variant_prev", np.int32),
    ("switch_tick", np.int64),   # tick at which variant changed (crossfade)
    ("offset_ms", np.int32),     # loop time offset
    ("gain", np.float32),
    ("noise", np.float32),       # 0..1 fraction of shared noise loop
    ("activity", np.int32),      # activity template row
    ("act_offset_ms", np.int32),
    ("art_gain", np.float32),    # motion artefact gain (0 = none)
    ("posture", np.int32),
    ("lead_off", np.uint8),
    ("spo2_off", np.uint8),
    ("battery", np.uint8),
    ("rssi", np.int8),
    ("flags", np.uint8),
    ("temp_var", np.int32),
    ("gluc_var", np.int32),
    ("temp_bias", np.float32),
    ("spo2_bias", np.int8),
    ("resp_src", np.uint8),      # 0 capacitive 1 edr 2 spo2
    ("spo2_src", np.uint8),      # 0 fingertip 1 ring 2 wrist_ptt
    ("paced", np.uint8),         # 1 = device present (pacemaker/ICD/CRT)
    ("hr_override", np.uint8),   # 0 = use loop
    ("pace_amp", np.float32),    # spike amplitude in 0.001 mV units (signed); 0 = no visible spike
    ("pace_detect", np.uint8),   # hardware pace-detection probability (%) for the marker channel
    ("chan_mask", np.uint16),    # channels provided by the patient's device set (ANDed with the global mask)
    ("hr_scale", np.float32),    # slow multi-day modulation (circadian + drift): HR factor, also time-stretches ECG/PPG
    ("rr_add", np.float32),
    ("spo2_add", np.float32),
    ("temp_add", np.float32),
    ("gl_add", np.float32),
    ("attach_tick", np.int64),   # tick when the patch was (re)attached -> electrode settling transient (0 = none)
    ("detach_tick", np.int64),   # tick when the electrodes came off -> brief violent artefact before the rail
]
GW_FIELDS = [
    ("active", np.uint8),
    ("status", np.uint8),        # 0 ok, 1 degraded, 2 down (no frames, socket closed)
    ("loss", np.float32),        # emulated packet loss probability
    ("latency_ms", np.float32),
    ("jitter_ms", np.float32),
    ("cpu", np.uint8), ("mem", np.uint8), ("net", np.uint8),
    ("wan_rssi", np.int8),
    ("type", np.uint8),
    ("n_conn", np.uint8),
    ("gw_id", np.uint32),
    ("uptime_s", np.uint32),
    ("temp_c", np.uint8),
    ("battery", np.uint8),       # mobile gateways
    ("silent", np.uint8),        # 1 = half-open drill: TCP socket stays up but the gateway sends nothing (no keepalive)
]
STAT_FIELDS = [                 # per gateway, written by workers
    ("pkts", np.uint64), ("bytes", np.uint64), ("drop_emul", np.uint64), ("drop_backlog", np.uint64), ("drop_noconn", np.uint64),
    ("send_err", np.uint32), ("connected", np.uint8), ("last_tick", np.int64),
    ("saf_bytes", np.uint64),    # store-and-forward buffer currently held (bytes)
    ("saf_replayed", np.uint64), # frames replayed from the buffer after reconnect
    ("drop_saf", np.uint64),     # frames evicted from a full store-and-forward buffer
    ("fuzz", np.uint32),         # corrupt/duplicate/reordered frames injected (fuzz drill)
]
WSTAT_FIELDS = [                # per worker
    ("ticks", np.uint64), ("overruns", np.uint64), ("build_us", np.float64), ("send_us", np.float64),
    ("last_tick", np.int64), ("alive", np.uint8), ("n_patches", np.uint32), ("n_gw", np.uint32), ("pkts", np.uint64), ("bytes", np.uint64),
]
# control scalars (float64 array)
CTL = {"running": 0, "epoch_ns": 1, "bundle_ms": 2, "chan_mask": 3, "ecg_fs": 4, "ppg_fs": 5, "resp_fs": 6, "accel_fs": 7,
       "meta_every": 8, "gwstat_every": 9, "target_port": 10, "socket_mode": 11, "crossfade_ticks": 12, "cfg_version": 13,
       "loop_seconds": 14, "max_backlog": 15, "reconnect_s": 16, "generate_only": 17, "n_workers": 18, "stop_workers": 19,
       "saf_enabled": 20, "saf_max_bytes": 21, "saf_burst": 22, "fuzz_rate": 23, "fuzz_mask": 24, "storm_epoch": 25, "storm_until": 26,
       "tap": 27, "connect_budget": 28}
# frame-corruption drill kinds (bit i of CTL fuzz_mask enables FUZZ_KINDS[i])
FUZZ_KINDS = ["bad_magic", "bad_version", "bad_len", "truncated", "oversize", "garbage", "dup", "reorder", "seq_gap", "bad_record"]
CTL_SIZE = 32
TARGET_BUF = 64

FLAG_LEAD_OFF = 0x01
FLAG_MOTION = 0x02
FLAG_LOW_BATT = 0x04
FLAG_SPO2_OFF = 0x08
FLAG_PACED = 0x10
FLAG_CHARGING = 0x20
FLAG_NEW_PATCH = 0x40


class Table:
    """A struct-of-arrays table living in one shared memory block."""

    def __init__(self, fields, n: int, name: str | None = None, create: bool = True):
        self.fields = fields
        self.n = n
        self.dtype = np.dtype([(f, t) for f, t in fields])
        nbytes = self.dtype.itemsize * n
        if create:
            self.shm = shared_memory.SharedMemory(create=True, size=max(1, nbytes))
        else:
            self.shm = shared_memory.SharedMemory(name=name)
        self.arr = np.ndarray((n,), dtype=self.dtype, buffer=self.shm.buf)
        if create:
            self.arr[:] = 0
        self.name = self.shm.name

    def __getitem__(self, field: str) -> np.ndarray:
        return self.arr[field]

    def close(self) -> None:
        try:
            self.shm.close()
        except Exception:
            pass

    def unlink(self) -> None:
        try:
            self.shm.unlink()
        except Exception:
            pass


class SharedState:
    """All shared tables.  Created by the main process; attached by workers."""

    def __init__(self, n_patches: int, n_gateways: int, n_workers: int, names: dict | None = None):
        create = names is None
        names = names or {}
        self.n_patches, self.n_gateways, self.n_workers = n_patches, n_gateways, n_workers
        self.patch = Table(PATCH_FIELDS, n_patches, names.get("patch"), create)
        self.gw = Table(GW_FIELDS, n_gateways, names.get("gw"), create)
        self.stat = Table(STAT_FIELDS, n_gateways, names.get("stat"), create)
        self.wstat = Table(WSTAT_FIELDS, max(1, n_workers), names.get("wstat"), create)
        if create:
            self.ctl_shm = shared_memory.SharedMemory(create=True, size=CTL_SIZE * 8)
            self.target_shm = shared_memory.SharedMemory(create=True, size=TARGET_BUF)
        else:
            self.ctl_shm = shared_memory.SharedMemory(name=names["ctl"])
            self.target_shm = shared_memory.SharedMemory(name=names["target"])
        self.ctl = np.ndarray((CTL_SIZE,), dtype=np.float64, buffer=self.ctl_shm.buf)
        self.target = np.ndarray((TARGET_BUF,), dtype=np.uint8, buffer=self.target_shm.buf)
        if create:
            self.ctl[:] = 0
            self.target[:] = 0

    def names(self) -> dict:
        return {"patch": self.patch.name, "gw": self.gw.name, "stat": self.stat.name, "wstat": self.wstat.name,
                "ctl": self.ctl_shm.name, "target": self.target_shm.name,
                "n_patches": self.n_patches, "n_gateways": self.n_gateways, "n_workers": self.n_workers}

    @staticmethod
    def attach(names: dict) -> "SharedState":
        return SharedState(names["n_patches"], names["n_gateways"], names["n_workers"], names)

    def set_target(self, ip: str) -> None:
        b = ip.encode("ascii", "ignore")[: TARGET_BUF - 1]
        self.target[:] = 0
        self.target[: len(b)] = np.frombuffer(b, dtype=np.uint8)
        self.ctl[CTL["cfg_version"]] += 1

    def get_target(self) -> str:
        raw = bytes(self.target.tobytes())
        return raw.split(b"\x00", 1)[0].decode("ascii", "ignore")

    def close(self) -> None:
        for t in (self.patch, self.gw, self.stat, self.wstat):
            t.close()
        self.ctl_shm.close()
        self.target_shm.close()

    def unlink(self) -> None:
        for t in (self.patch, self.gw, self.stat, self.wstat):
            t.unlink()
        for s in (self.ctl_shm, self.target_shm):
            try:
                s.unlink()
            except Exception:
                pass
