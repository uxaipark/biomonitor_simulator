"""Pre-generation and loading of 1-hour loop banks.

Layout (data/loops/<ecg_fs>/):
  bank_ecg.npy   int16 (V, N_ecg)      bank_ppg.npy  int16 (V, N_ppg)
  bank_resp.npy  int16 (V, N_resp)     bank_sec.npy  uint8 (V, 7, seconds)
  bank_pace.npz  uint32 arrays per variant   bank_index.json  variant metadata
  bank_accel.npy int16 (A, N_acc, 3)   bank_art.npy  int16 (A, N_ecg)   bank_level.npy f32 (A, seconds)
  bank_temp.npy  f32 (T, seconds)      bank_glucose.npy f32 (G, seconds)   noise.npy int16 (N_ecg)
Banks are loaded with mmap so worker processes share the OS page cache.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import threading
import time
from pathlib import Path

import numpy as np

from ..config import LOOP_DIR
from .rhythms import RHYTHMS
from .accel import ACTIVITIES
from .slow import TEMP_PROFILES, GLUCOSE_PROFILES

SEC_ROWS = ["hr", "rr_cap", "rr_edr", "rr_spo2", "spo2_finger", "spo2_ring", "spo2_wrist"]
RESP_KINDS_BY_CLASS = {"normal": 0.72, "copd": 0.12, "tachypnea": 0.08, "apnea": 0.05, "brady": 0.03}


def _variant_task(args):
    rhythm, vi, seed, ecg_fs, ppg_fs, resp_fs, seconds, out_dir = args
    from .physio import generate_bundle
    rng = np.random.default_rng(seed)
    age = int(np.clip(rng.normal(66, 14), 20, 95))
    sex = "M" if rng.random() < 0.52 else "F"
    kinds = list(RESP_KINDS_BY_CLASS.keys())
    resp_kind = str(rng.choice(kinds, p=list(RESP_KINDS_BY_CLASS.values())))
    b = generate_bundle(rhythm, seed, ecg_fs, ppg_fs, resp_fs, seconds, age, sex, resp_kind)
    path = Path(out_dir) / f"{rhythm}_{vi:03d}.npz"
    np.savez(path, ecg=b["ecg"], ppg=b["ppg"], resp_wave=b["resp_wave"],
             sec=np.stack([b[k] for k in SEC_ROWS]).astype(np.uint8), pace=b["pace"], pace_type=b["pace_type"], rpeaks=b["rpeaks"],
             meta=json.dumps(b["meta"]))
    return rhythm, vi, b["meta"]


def _activity_task(args):
    act, seed, fs, seconds, ecg_fs, out_dir = args
    from .accel import generate_activity
    x = generate_activity(act, seed, fs, seconds, ecg_fs)
    np.savez(Path(out_dir) / f"act_{act}.npz", accel=x["accel"], ecg_art=x["ecg_art"], level=x["level"])
    return act


class LoopBank:
    """Owns pre-generation (with progress) and mmap loading of all loop banks."""

    def __init__(self, ecg_fs: int, ppg_fs: int, resp_fs: int, accel_fs: int, seconds: int,
                 variants_per_rhythm: int, seed: int, base_dir: Path = LOOP_DIR):
        self.ecg_fs, self.ppg_fs, self.resp_fs, self.accel_fs = ecg_fs, ppg_fs, resp_fs, accel_fs
        self.seconds = seconds
        self.vpr = variants_per_rhythm
        self.seed = seed
        self.dir = base_dir / f"{ecg_fs}"
        self.progress = {"state": "idle", "done": 0, "total": 0, "started": 0.0, "eta_s": 0.0, "message": ""}
        self.index: dict = {}
        self.loaded = False
        self._lock = threading.Lock()
        # arrays (filled by load())
        self.ecg = self.ppg = self.resp = self.sec = None
        self.pace: list[np.ndarray] = []
        self.pace_type: list[np.ndarray] = []
        self.accel = self.art = self.level = self.temp = self.glucose = self.noise = None
        self.rhythm_variants: dict[str, list[int]] = {}

    # ---------------- generation
    def signature(self) -> dict:
        return {"ecg_fs": self.ecg_fs, "ppg_fs": self.ppg_fs, "resp_fs": self.resp_fs, "accel_fs": self.accel_fs,
                "seconds": self.seconds, "vpr": self.vpr, "seed": self.seed, "rhythms": list(RHYTHMS.keys()),
                "activities": ACTIVITIES, "temp": TEMP_PROFILES, "glucose": GLUCOSE_PROFILES, "format": 4}

    def is_ready(self) -> bool:
        idx = self.dir / "bank_index.json"
        if not idx.exists():
            return False
        try:
            with open(idx) as f:
                saved = json.load(f)
            return saved.get("signature") == self.signature()
        except Exception:
            return False

    def generate(self, workers: int | None = None) -> None:
        """Blocking generation (run in a thread from the API)."""
        with self._lock:
            if self.progress["state"] == "running":
                return
            self.progress.update(state="running", done=0, started=time.time(), message="시작")
        tmp = self.dir / "_tmp"
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
            tasks = []
            rhythms = list(RHYTHMS.keys())
            for ri, r in enumerate(rhythms):
                for vi in range(self.vpr):
                    seed = (self.seed * 1000003 + ri * 1009 + vi * 7919) & 0x7FFFFFFF
                    tasks.append((r, vi, seed, self.ecg_fs, self.ppg_fs, self.resp_fs, self.seconds, str(tmp)))
            act_tasks = [(a, self.seed + 77 + i, self.accel_fs, self.seconds, self.ecg_fs, str(tmp)) for i, a in enumerate(ACTIVITIES)]
            self.progress["total"] = len(tasks) + len(act_tasks) + 3
            nproc = workers or max(1, (os.cpu_count() or 2) - 1)
            metas: dict[tuple[str, int], dict] = {}
            ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
            with ctx.Pool(nproc) as pool:
                for r, vi, meta in pool.imap_unordered(_variant_task, tasks, chunksize=1):
                    metas[(r, vi)] = meta
                    self._tick(f"{RHYTHMS[r]['label']} #{vi + 1}")
                for a in pool.imap_unordered(_activity_task, act_tasks):
                    self._tick(f"activity {a}")
            self._assemble(rhythms, metas, tmp)
            self.progress.update(state="done", message="완료", eta_s=0.0)
        except Exception as e:  # pragma: no cover
            self.progress.update(state="error", message=f"{type(e).__name__}: {e}")
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _tick(self, msg: str) -> None:
        p = self.progress
        p["done"] += 1
        el = time.time() - p["started"]
        p["eta_s"] = (el / p["done"]) * (p["total"] - p["done"]) if p["done"] else 0.0
        p["message"] = msg

    def _assemble(self, rhythms: list[str], metas: dict, tmp: Path) -> None:
        from .slow import generate_temperature, generate_glucose
        from .accel import generate_noise_loop
        V = len(rhythms) * self.vpr
        n_ecg, n_ppg, n_resp, n_acc = (self.seconds * f for f in (self.ecg_fs, self.ppg_fs, self.resp_fs, self.accel_fs))
        self.dir.mkdir(parents=True, exist_ok=True)
        ecg = np.lib.format.open_memmap(self.dir / "bank_ecg.npy.tmp", mode="w+", dtype=np.int16, shape=(V, n_ecg))
        ppg = np.lib.format.open_memmap(self.dir / "bank_ppg.npy.tmp", mode="w+", dtype=np.int16, shape=(V, n_ppg))
        resp = np.lib.format.open_memmap(self.dir / "bank_resp.npy.tmp", mode="w+", dtype=np.int16, shape=(V, n_resp))
        sec = np.zeros((V, len(SEC_ROWS), self.seconds), dtype=np.uint8)
        pace: dict[str, np.ndarray] = {}
        variants = []
        rv: dict[str, list[int]] = {r: [] for r in rhythms}
        i = 0
        for r in rhythms:
            for vi in range(self.vpr):
                with np.load(tmp / f"{r}_{vi:03d}.npz") as z:
                    ecg[i] = z["ecg"]
                    ppg[i] = z["ppg"]
                    resp[i] = z["resp_wave"]
                    sec[i] = z["sec"]
                    pace[f"v{i}"] = z["pace"]
                    pace[f"t{i}"] = z["pace_type"]
                    meta = json.loads(str(z["meta"]))
                meta["index"] = i
                variants.append(meta)
                rv[r].append(i)
                i += 1
        self._tick("bank ecg/ppg/resp")
        acc = np.zeros((len(ACTIVITIES), n_acc, 3), dtype=np.int16)
        art = np.zeros((len(ACTIVITIES), n_ecg), dtype=np.int16)
        lvl = np.zeros((len(ACTIVITIES), self.seconds), dtype=np.float32)
        for ai, a in enumerate(ACTIVITIES):
            with np.load(tmp / f"act_{a}.npz") as z:
                acc[ai] = z["accel"]
                art[ai] = z["ecg_art"]
                lvl[ai] = z["level"]
        self._tick("bank accel")
        n_slow = 24
        temp = np.stack([generate_temperature(p, self.seed + 500 + k * 31 + j, self.seconds)
                         for k, p in enumerate(TEMP_PROFILES) for j in range(n_slow)])
        gluc = np.stack([generate_glucose(p, self.seed + 900 + k * 37 + j, self.seconds)
                         for k, p in enumerate(GLUCOSE_PROFILES) for j in range(n_slow)])
        noise = generate_noise_loop(self.seed + 1234, self.ecg_fs, self.seconds)
        self._tick("bank slow/noise")
        for m in (ecg, ppg, resp):
            m.flush()
        del ecg, ppg, resp
        for name in ("bank_ecg", "bank_ppg", "bank_resp"):
            os.replace(self.dir / f"{name}.npy.tmp", self.dir / f"{name}.npy")
        np.save(self.dir / "bank_sec.npy", sec)
        np.savez(self.dir / "bank_pace.npz", **pace)
        np.save(self.dir / "bank_accel.npy", acc)
        np.save(self.dir / "bank_art.npy", art)
        np.save(self.dir / "bank_level.npy", lvl)
        np.save(self.dir / "bank_temp.npy", temp)
        np.save(self.dir / "bank_glucose.npy", gluc)
        np.save(self.dir / "noise.npy", noise)
        index = {"signature": self.signature(), "variants": variants, "rhythm_variants": rv,
                 "activities": ACTIVITIES, "temp_profiles": TEMP_PROFILES, "glucose_profiles": GLUCOSE_PROFILES,
                 "slow_per_profile": n_slow, "sec_rows": SEC_ROWS}
        with open(self.dir / "bank_index.json", "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)

    # ---------------- loading
    def load(self) -> None:
        with open(self.dir / "bank_index.json", encoding="utf-8") as f:
            self.index = json.load(f)
        self.ecg = np.load(self.dir / "bank_ecg.npy", mmap_mode="r")
        self.ppg = np.load(self.dir / "bank_ppg.npy", mmap_mode="r")
        self.resp = np.load(self.dir / "bank_resp.npy", mmap_mode="r")
        self.sec = np.load(self.dir / "bank_sec.npy", mmap_mode="r")
        with np.load(self.dir / "bank_pace.npz") as z:
            self.pace = [z[f"v{i}"] for i in range(self.ecg.shape[0])]
            self.pace_type = [z[f"t{i}"] for i in range(self.ecg.shape[0])]
        self.accel = np.load(self.dir / "bank_accel.npy", mmap_mode="r")
        self.art = np.load(self.dir / "bank_art.npy", mmap_mode="r")
        self.level = np.load(self.dir / "bank_level.npy")
        self.temp = np.load(self.dir / "bank_temp.npy")
        self.glucose = np.load(self.dir / "bank_glucose.npy")
        self.noise = np.load(self.dir / "noise.npy")
        self.rhythm_variants = self.index["rhythm_variants"]
        self.loaded = True

    def paths(self) -> dict:
        return {k: str(self.dir / v) for k, v in {
            "ecg": "bank_ecg.npy", "ppg": "bank_ppg.npy", "resp": "bank_resp.npy", "sec": "bank_sec.npy",
            "pace": "bank_pace.npz", "accel": "bank_accel.npy", "art": "bank_art.npy", "level": "bank_level.npy",
            "temp": "bank_temp.npy", "glucose": "bank_glucose.npy", "noise": "noise.npy", "index": "bank_index.json"}.items()}

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.dir.glob("*") if p.is_file()) if self.dir.exists() else 0
