"""Engine: owns the loop bank, world thread, fast-path workers and statistics."""
from __future__ import annotations

import multiprocessing as mp
import os
import contextlib
import json
import threading
import time
from pathlib import Path

import numpy as np

from ..config import Config, channel_mask
from ..signals.loops import LoopBank
from .fastpath import FrameBuilder, Sender, Gather, worker_main
from .state import CTL
from .world import World, EventLog, META_PATH
CAPTURE_DIR = Path(META_PATH).parent / "capture"   # ground-truth tap files w<worker>.bin


class RWLock:
    """Readers/writer lock: API request threads read the world / shared-memory arrays under read(); a rebuild (new
    SharedState, new hospital) holds write(), so no request thread can touch an array whose memory is being unmapped.
    Writer-preferring: once a rebuild is waiting, new readers queue behind it."""

    def __init__(self):
        self._c = threading.Condition()
        self._r = 0
        self._w = False
        self._wq = 0

    @contextlib.contextmanager
    def read(self):
        with self._c:
            while self._w or self._wq:
                self._c.wait()
            self._r += 1
        try:
            yield
        finally:
            with self._c:
                self._r -= 1
                self._c.notify_all()

    @contextlib.contextmanager
    def write(self):
        with self._c:
            self._wq += 1
            while self._w or self._r:
                self._c.wait()
            self._wq -= 1
            self._w = True
        try:
            yield
        finally:
            with self._c:
                self._w = False
                self._c.notify_all()


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rw = RWLock()                                   # see RWLock: request threads read(), rebuild() write()s
        self.cfg.add_listener(self._on_config_change)         # every setting change (GUI, API, autotune, restore) -> config_history
        self.log = EventLog()
        self.bank = self._make_bank()
        self.world: World | None = None
        self.procs: list[mp.Process] = []
        self.inproc_thread: threading.Thread | None = None
        self.world_thread: threading.Thread | None = None
        self.running = False
        self.gen_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stats_hist: list[dict] = []
        self._last_totals: dict = {}
        self.gather: Gather | None = None
        self.started_at = 0.0
        if self.bank.is_ready():
            self.bank.load()
        self._init_world()

    # ---------------------------------------------------------------- setup
    def _make_bank(self) -> LoopBank:
        s = self.cfg.get("signals")
        g = self.cfg.get("general")
        return LoopBank(s["ecg_fs"], s["ppg_fs"], s["resp_fs"], s["accel_fs"], s["loop_seconds"], s["variants_per_rhythm"], g["seed"])

    def bank_args(self) -> dict:
        b = self.bank
        return {"ecg_fs": b.ecg_fs, "ppg_fs": b.ppg_fs, "resp_fs": b.resp_fs, "accel_fs": b.accel_fs, "seconds": b.seconds,
                "variants_per_rhythm": b.vpr, "seed": b.seed}

    def _init_world(self):
        self.world = World(self.cfg, self.bank, self.log)
        self.gather = Gather(self.bank, self.world.st) if self.bank.loaded else None
        self.world_thread = threading.Thread(target=self._world_loop, daemon=True, name="world")
        self.world_thread.start()

    def start_generation(self) -> bool:
        if self.gen_thread and self.gen_thread.is_alive():
            return False
        was_running = self.running
        if was_running:
            self.stop()

        def run():
            self.bank.generate()
            self.bank.load()
            self.world.rhythm_variants = dict(self.bank.rhythm_variants)
            self.gather = Gather(self.bank, self.world.st)
            self.log.add("system", f"루프 파일 생성 완료: {self.bank.ecg.shape[0]} 변형, {self.bank.size_bytes() / 1e6:.0f} MB")
            with self.world.lock:
                for rec in self.world.admitted.values():
                    prof = self.world.by_id[rec["id"]]
                    v = self.world._variant_for(prof["rhythm"], prof["age"])
                    self.world.st.patch["variant"][rec["row"]] = v
                    rec["base_variant"] = v
        self.gen_thread = threading.Thread(target=run, daemon=True, name="pregen")
        self.gen_thread.start()
        return True

    def rebuild(self) -> None:
        """Apply structural config (bed capacity, seed, fs...) - requires stop.  Exclusive: every API reader waits
        (the old SharedState is unmapped here; a reader still holding its arrays would segfault)."""
        with self.rw.write():
            self.stop()
            new_bank = self._make_bank()
            if new_bank.signature() != self.bank.signature():
                self.bank = new_bank
                if self.bank.is_ready():
                    self.bank.load()
            with self.world.lock:
                self.world.bank = self.bank
                self.world.build()
                self.gather = Gather(self.bank, self.world.st) if self.bank.loaded else None   # swapped under the lock: no window with the old arrays
            self.log.add("system", "월드 재구성 완료")

    # ---------------------------------------------------------------- run control
    def _on_config_change(self, changes, source: str) -> None:
        try:
            self.world.db.add_config_changes(time.time(), source, getattr(self, "run_id", None) if self.running else None, changes)
        except Exception:
            pass

    def start(self) -> str:
        if self.running:
            return "already running"
        if not self.bank.loaded:
            return "loop bank not generated"
        w = self.world
        st = w.st
        t = self.cfg.get("transport")
        w.apply_config()
        st.ctl[CTL["epoch_ns"]] = time.time_ns()
        st.ctl[CTL["stop_workers"]] = 0
        st.stat.arr[:] = 0
        st.wstat.arr[:] = 0
        w.write_meta(force=True)
        n_workers = t["workers"] or max(1, (os.cpu_count() or 2) - 1)
        n_workers = min(n_workers, 16, max(1, len(w.hospital.gateways)))
        st.ctl[CTL["n_workers"]] = n_workers
        n_gw = len(w.hospital.gateways)
        shards = [np.arange(k, n_gw, n_workers) for k in range(n_workers)]
        st.ctl[CTL["running"]] = 1
        if n_workers == 1 and t["workers"] == 1:
            os.environ["BIOSIM_CAPTURE_WORKER_MAX"] = str(int(self.cfg.get("transport").get("capture_max_mb", 256) * 1024 * 1024 / max(1, n_workers)))
            os.environ["BIOSIM_SAF_WORKER_MAX"] = str(int(self.cfg.get("transport").get("saf_worker_max_mb", 64) * 1024 * 1024))
            self.inproc_thread = threading.Thread(target=worker_main, args=(0, {**st.names(), "capture_dir": str(CAPTURE_DIR)}, shards[0].tolist(), self.bank_args(), str(META_PATH)),
                                                  daemon=True, name="fastpath")
            self.inproc_thread.start()
        else:
            ctx = mp.get_context("spawn")
            for k in range(n_workers):
                p = ctx.Process(target=worker_main, args=(k, {**st.names(), "capture_dir": str(CAPTURE_DIR)}, shards[k].tolist(), self.bank_args(), str(META_PATH)), daemon=True, name=f"fp{k}")
                p.start()
                self.procs.append(p)
        self.running = True
        w.running = True
        self.started_at = time.time()
        self._last_totals = {}
        self.log.add("system", f"전송 시작: 워커 {n_workers}, 대상 {t['target_ip'] or '(생성만)'}:{t['target_port']}, 채널 마스크 {int(st.ctl[CTL['chan_mask']])}")
        self.run_id = w.db.start_run(f"{t['target_ip'] or '-'}:{t['target_port']}", n_workers, len(w.admitted), len(w.hospital.gateways),
                                     json.dumps(self.cfg.snapshot(), ensure_ascii=False))          # full scenario snapshot: every run is reproducible
        return "started"

    def stop(self) -> str:
        if not self.running:
            return "not running"
        st = self.world.st
        st.ctl[CTL["running"]] = 0
        st.ctl[CTL["stop_workers"]] = 1
        for p in self.procs:
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()
        self.procs = []
        if self.inproc_thread:
            self.inproc_thread.join(timeout=3)
            self.inproc_thread = None
        self.running = False
        self.world.running = False
        self.world.stop_autotune()
        S = st.stat.arr
        self.world.db.stop_run(getattr(self, "run_id", None), int(S["pkts"].sum()), int(S["bytes"].sum()), int(S["drop_emul"].sum() + S["drop_backlog"].sum() + S["drop_noconn"].sum()))
        self.log.add("system", "전송 정지")
        return "stopped"

    def shutdown(self):
        self._stop.set()
        self.stop()
        try:
            self.world.db.close()
        except Exception:
            pass
        try:
            self.world.st.close()
            self.world.st.unlink()
        except Exception:
            pass

    # ---------------------------------------------------------------- loops
    def _world_loop(self):
        last = time.time()
        next_t = last + 1.0
        while not self._stop.is_set():
            time.sleep(max(0.05, next_t - time.time()))            # absolute 1 Hz schedule: the step time no longer stretches the period
            next_t += 1.0
            now = time.time()
            if now - next_t > 5.0:                                 # fell far behind (rebuild, sleep): resync instead of bursting
                next_t = now + 1.0
            t0 = time.perf_counter()
            try:
                with self.rw.read():
                    self.world.step(now - last)
                    self._collect_stats()
            except Exception as e:  # keep the loop alive
                self.log.add("error", f"world step error: {type(e).__name__}: {e}")
            self.world_step_ms = (time.perf_counter() - t0) * 1000.0
            self.world_step_max_ms = max(getattr(self, "world_step_max_ms", 0.0) * 0.98, self.world_step_ms)   # slowly decaying peak
            last = now

    def _collect_stats(self):
        st = self.world.st
        S = st.stat.arr
        W = st.wstat.arr
        tot = {"pkts": int(S["pkts"].sum()), "bytes": int(S["bytes"].sum()), "drop_emul": int(S["drop_emul"].sum()),
               "drop_backlog": int(S["drop_backlog"].sum()), "drop_noconn": int(S["drop_noconn"].sum()), "send_err": int(S["send_err"].sum()), "connected": int(S["connected"].sum()),
               "overruns": int(W["overruns"].sum()), "ticks": int(W["ticks"].sum()),
               "saf_bytes": int(S["saf_bytes"].sum()), "saf_replayed": int(S["saf_replayed"].sum()), "drop_saf": int(S["drop_saf"].sum()), "fuzz": int(S["fuzz"].sum()),
               "saf_gateways": int((S["saf_bytes"] > 0).sum())}
        now = time.time()
        prev = self._last_totals
        dt = now - prev.get("t", now - 1) if prev else 1.0
        rate = {k: (tot[k] - prev.get(k, tot[k])) / max(dt, 1e-3) for k in ("pkts", "bytes", "drop_emul", "drop_backlog", "drop_noconn", "overruns")}
        self._last_totals = {**tot, "t": now}
        alive = W["alive"] > 0
        P = st.patch.arr
        entry = {"t": now, **{f"{k}_ps": v for k, v in rate.items()}, "total": tot,
                 "workers": [{"id": i, "alive": bool(alive[i]), "ticks": int(W["ticks"][i]), "overruns": int(W["overruns"][i]),
                              "build_us": float(W["build_us"][i]), "send_us": float(W["send_us"][i]), "n_patches": int(W["n_patches"][i]),
                              "n_gw": int(W["n_gw"][i])} for i in range(int(st.ctl[CTL["n_workers"]]))] if self.running else [],
                 "active_patches": int(((P["active"] > 0) & (P["gw"] >= 0)).sum()), "unlinked_patches": int(((P["active"] > 0) & (P["gw"] < 0)).sum()),
                 "gw_down": int((st.gw["status"] == 2).sum()), "gw_degraded": int((st.gw["status"] == 1).sum())}
        self._stats_hist.append(entry)
        if len(self._stats_hist) > 300:
            self._stats_hist = self._stats_hist[-300:]

    # ---------------------------------------------------------------- queries
    def stats(self, history: bool = True) -> dict:
        last = self._stats_hist[-1] if self._stats_hist else {}
        w = self.world
        st = w.st
        return {"running": self.running, "uptime_s": (time.time() - self.started_at) if self.running else 0, "generate_only": bool(st.ctl[CTL["generate_only"]] > 0),
                "target": {"ip": st.get_target(), "port": int(st.ctl[CTL["target_port"]])}, "last": last,
                "history": [{"t": e["t"], "pkts_ps": e["pkts_ps"], "bytes_ps": e["bytes_ps"], "drop_backlog_ps": e["drop_backlog_ps"], "drop_emul_ps": e["drop_emul_ps"]} for e in self._stats_hist[-120:]] if history else [],
                "world_step_ms": round(getattr(self, "world_step_ms", 0.0), 2), "world_step_max_ms": round(getattr(self, "world_step_max_ms", 0.0), 2),
                "counters": dict(w.counters), "admitted": len(w.admitted), "inpatients": sum(1 for r in w.admitted.values() if not r["outpatient"]),
                "outpatients": sum(1 for r in w.admitted.values() if r["outpatient"]), "bed_capacity": w.hospital.bed_capacity,
                "gateways": len(w.hospital.gateways), "sim_time": w.sim_time, "autotune": w.autotune,
                "bank": {"loaded": self.bank.loaded, "variants": int(self.bank.ecg.shape[0]) if self.bank.loaded else 0, "size_mb": round(self.bank.size_bytes() / 1e6, 1),
                         "progress": self.bank.progress, "ready": self.bank.is_ready()},
                "tick": w._tick_now() if self.running else 0, "chan_mask": int(st.ctl[CTL["chan_mask"]]), "n_workers": int(st.ctl[CTL["n_workers"]])}

    def preview_many(self, rows: list[int], tick: int | None = None, numeric: bool = False) -> dict[int, dict | None]:
        """Live samples for several patches at once (central-monitor view of one gateway).  numeric=True skips the
        waveform channels (large numeric-only boards)."""
        return {r: self.preview(r, tick, numeric) for r in rows}

    def preview(self, row: int, tick: int | None = None, numeric: bool = False) -> dict | None:
        if not self.gather:
            return None
        w = self.world
        if tick is None:
            tick = w._tick_now() if self.running else int(time.time() * 1000 // int(w.st.ctl[CTL["bundle_ms"]]))
        with w.lock:
            if not (0 <= row < w.st.n_patches) or w.st.patch["active"][row] == 0:
                return None
            g = int(w.st.ctl[CTL["chan_mask"]])
            pm = int(w.st.patch["chan_mask"][row])
            mask = (g if pm == 0 else (g & pm)) | (1 << 1)
            if numeric:
                mask &= (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6)      # HR, temp, RR, SpO2, glucose only
            return self.gather.preview(row, tick, mask)
