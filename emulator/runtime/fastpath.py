"""Fast path: per-tick vectorised sample gather, frame building and socket sending.

Runs in worker processes (or in-process when workers == 1).  Reads only the
shared tables; never touches Python-level world objects.
"""
from __future__ import annotations

import heapq
import json
import math
import os
import socket
import time
import traceback

import struct
import numpy as np

from ..config import (CH_ECG, CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE, CH_ACCEL, CH_PPG, CH_RESP_WAVE, CH_PACE, CHANNELS,
                      RESP_SOURCES, SPO2_SOURCES)
from ..signals.accel import POSTURES
from ..signals.loops import LoopBank
from .protocol import (record_dtype, fill_constants, pace_record, frame, gwstat_block, meta_block, F_META, F_GWSTAT, F_KEEPALIVE, HEADER)
from collections import deque
from .state import SharedState, CTL, FLAG_LEAD_OFF, FLAG_MOTION, FLAG_LOW_BATT, FLAG_SPO2_OFF, FLAG_PACED

POSTURE_G = np.array([POSTURES[k] for k in ("supine", "left_lateral", "right_lateral", "sitting", "standing", "prone")], dtype=np.float32) * 1000.0
POSTURE_NAMES = ["supine", "left_lateral", "right_lateral", "sitting", "standing", "prone"]
RAIL = 4000          # lead-off / ADC saturation rail (4 mV)
SETTLE_S = 20.0      # electrode settling time after attachment (real seconds)
DETACH_S = 3.0       # violent artefact right after detachment (real seconds)


class Gather:
    """Vectorised sample gathering for a set of patch rows at a tick."""

    def __init__(self, bank: LoopBank, st: SharedState):
        self.bank = bank
        self.st = st
        self.seconds = bank.seconds
        self.n_ecg = bank.ecg.shape[1]
        self.n_ppg = bank.ppg.shape[1]
        self.n_resp = bank.resp.shape[1]
        self.n_acc = bank.accel.shape[1]
        # loop position (in ECG samples) per patch row, advanced by spt * hr_scale each tick -> slow HR modulation
        self.pos = np.zeros(st.n_patches, dtype=np.float64)
        self.pos_off = np.full(st.n_patches, -1, dtype=np.int64)     # offset_ms seen when pos was initialised
        self.tick_start = np.zeros(st.n_patches, dtype=np.float64)
        self.last_tick = np.full(st.n_patches, -1, dtype=np.int64)

    def advance(self, rows: np.ndarray, tick: int, spt: int, fs: int) -> np.ndarray:
        """Set tick start positions for `rows` and advance them by one bundle (idempotent per tick)."""
        if rows.size == 0:
            return np.zeros(0, dtype=np.float64)
        P = self.st.patch.arr
        off = P["offset_ms"][rows].astype(np.int64)
        fresh = (self.pos_off[rows] != off)
        if fresh.any():
            r = rows[fresh]
            self.pos[r] = ((off[fresh] * fs) // 1000 + tick * spt) % self.n_ecg
            self.pos_off[r] = off[fresh]
            self.last_tick[r] = -1
        todo = self.last_tick[rows] != tick
        if todo.any():
            r = rows[todo]
            rate = P["hr_scale"][r].astype(np.float64)
            rate = np.where(rate <= 0.1, 1.0, rate)
            self.tick_start[r] = self.pos[r]
            self.pos[r] = (self.pos[r] + spt * rate) % self.n_ecg
            self.last_tick[r] = tick
        return self.tick_start[rows]

    def _wave(self, arr2d, n_loop: int, fs: int, rows: np.ndarray, offset_ms: np.ndarray, tick: int, spt: int, bundle_ms: int) -> np.ndarray:
        base = ((offset_ms.astype(np.int64) * fs) // 1000 + tick * spt) % n_loop
        idx = (base[:, None] + np.arange(spt, dtype=np.int64)[None, :]) % n_loop
        return arr2d[rows[:, None], idx]

    def _stretched(self, arr2d, n_loop: int, rows_vals, start_pos: np.ndarray, spt: int, scale: float) -> np.ndarray:
        """Gather spt samples starting at (start_pos * scale) advancing hr_scale per ECG sample.
        Linear interpolation between neighbouring loop samples keeps the waveform smooth when the
        rate is not exactly 1 (nearest-neighbour would duplicate/drop samples inside the QRS)."""
        P = self.st.patch.arr
        rate = P["hr_scale"][rows_vals].astype(np.float64)
        rate = np.where(rate <= 0.1, 1.0, rate)
        pos = (start_pos[:, None] + np.arange(spt, dtype=np.float64)[None, :] * rate[:, None]) * scale
        i0 = np.floor(pos).astype(np.int64)
        frac = (pos - i0).astype(np.float32)
        v = P["variant"][rows_vals][:, None]
        a = arr2d[v, i0 % n_loop].astype(np.float32)
        if np.all(rate == 1.0) and scale == 1.0:
            return a
        b = arr2d[v, (i0 + 1) % n_loop].astype(np.float32)
        return a + (b - a) * frac

    def ecg(self, rows: np.ndarray, tick: int, spt: int, bundle_ms: int, fs: int, crossfade_ticks: int) -> np.ndarray:
        p = self.st.patch.arr[rows]
        off = p["offset_ms"]
        var = p["variant"]
        start = self.advance(rows, tick, spt, fs)
        x = self._stretched(self.bank.ecg, self.n_ecg, rows, start, spt, 1.0).astype(np.float32)
        # crossfade from previous variant
        age = tick - p["switch_tick"]
        cf = np.where((age >= 0) & (age < crossfade_ticks) & (p["variant_prev"] >= 0) & (p["variant_prev"] != var))[0]
        if cf.size:
            idx = np.rint(start[cf][:, None] + np.arange(spt)[None, :]).astype(np.int64) % self.n_ecg
            xprev = self.bank.ecg[p["variant_prev"][cf][:, None], idx].astype(np.float32)
            w = (age[cf][:, None] + np.arange(spt)[None, :] / spt) / crossfade_ticks
            x[cf] = x[cf] * w + xprev * (1 - w)
        x *= p["gain"][:, None]
        # motion artefact from the activity template + generic noise
        ag = p["art_gain"]
        m = np.where(ag > 0.001)[0]
        if m.size:
            art = self._wave(self.bank.art, self.n_ecg, fs, p["activity"][m], p["act_offset_ms"][m], tick, spt, bundle_ms).astype(np.float32)
            x[m] += art * ag[m][:, None]
        nz = p["noise"]
        m = np.where(nz > 0.001)[0]
        if m.size:
            base = ((p["offset_ms"][m].astype(np.int64) * 7 * fs) // 1000 + tick * spt) % self.n_ecg
            idx = (base[:, None] + np.arange(spt)[None, :]) % self.n_ecg
            x[m] += self.bank.noise[idx].astype(np.float32) * nz[m][:, None]
        # pacing spikes rendered per patient: unipolar leads give mV-scale spikes, bipolar sub-mV
        pr = np.where((p["paced"] > 0) & (np.abs(p["pace_amp"]) > 0.5))[0]
        for i in pr:
            marks, _ = self.pace_marks(int(rows[i]), tick, spt, fs, detect=False)
            if marks.size:
                amp = float(p["pace_amp"][i])
                x[i, marks] += amp
                nxt = marks + 1
                nxt = nxt[nxt < spt]
                x[i, nxt] -= 0.3 * amp
        # ---- electrode transients: settling after (re)attachment, violent artefact right after detachment
        ticks_per_s = 1000.0 / bundle_ms
        age_a = tick - p["attach_tick"]
        settling = (p["attach_tick"] > 0) & (age_a >= 0) & (age_a < SETTLE_S * ticks_per_s) & (p["lead_off"] == 0)
        m = np.where(settling)[0]
        if m.size:
            pid = p["patch_id"][m].astype(np.int64)
            amp = (3000.0 + (pid % 5) * 1200.0)[:, None]                       # 3..8 mV initial offset
            tau = (6.0 + (pid % 7))[:, None]                                    # 6..12 s time constant
            sign = np.where(pid % 2 == 0, 1.0, -1.0)[:, None]
            t = (age_a[m][:, None] / ticks_per_s) + np.arange(spt)[None, :] / fs
            env = np.exp(-t / tau) * np.clip(1.0 - t / SETTLE_S, 0.0, 1.0)     # decays and reaches exactly 0 at SETTLE_S
            rng = np.random.default_rng(tick + 7)
            wander = 1500.0 * env * np.sin(2 * np.pi * 0.4 * t + pid[:, None] % 6)   # slow baseline swing that dies out
            noise = rng.normal(0, 400.0, (m.size, spt)) * env                        # impedance noise that dies out
            x[m] = np.clip(x[m] + (sign * amp * env + wander + noise).astype(np.float32), -RAIL, RAIL)   # ADC saturates at the rails
        age_d = tick - p["detach_tick"]
        lo = np.where(p["lead_off"] > 0)[0]
        if lo.size:
            rng = np.random.default_rng(tick)
            x[lo] = RAIL + rng.normal(0, 40, (lo.size, spt)).astype(np.float32)
            fresh = lo[(p["detach_tick"][lo] > 0) & (age_d[lo] >= 0) & (age_d[lo] < DETACH_S * ticks_per_s)]
            if fresh.size:                                                        # first seconds: electrodes peeling -> huge swings between the rails
                sw = rng.normal(0, 2500.0, (fresh.size, spt)).astype(np.float32)
                sw = np.cumsum(sw, axis=1) / 6.0
                x[fresh] = np.clip(rng.choice([-1.0, 1.0], fresh.size)[:, None] * RAIL * 0.5 + sw, -RAIL, RAIL)
        return np.clip(x, -32000, 32000).astype(np.int16)

    def ppg(self, rows, tick, spt, bundle_ms, fs):
        p = self.st.patch.arr[rows]
        ecg_fs = int(self.st.ctl[CTL["ecg_fs"]]) or 250
        start = self.advance(rows, tick, ecg_fs * bundle_ms // 1000, ecg_fs)
        x = self._stretched(self.bank.ppg, self.n_ppg, rows, start * (fs / ecg_fs), spt, 1.0)
        ag = p["art_gain"]
        m = np.where(ag > 0.001)[0]
        if m.size:
            # PPG is very motion sensitive: use the level as an envelope on noise
            x[m] += np.random.default_rng(tick + 1).normal(0, 300, (m.size, spt)).astype(np.float32) * ag[m][:, None]
        off = np.where(p["spo2_off"] > 0)[0]
        if off.size:
            x[off] = 0
        return np.clip(x, -32000, 32000).astype(np.int16)

    def resp_wave(self, rows, tick, spt, bundle_ms, fs):
        p = self.st.patch.arr[rows]
        x = self._wave(self.bank.resp, self.n_resp, fs, p["variant"], p["offset_ms"], tick, spt, bundle_ms).astype(np.float32)
        ag = p["art_gain"]
        m = np.where(ag > 0.001)[0]
        if m.size:
            x[m] += np.random.default_rng(tick + 2).normal(0, 400, (m.size, spt)).astype(np.float32) * ag[m][:, None]
        x[p["lead_off"] > 0] = 0
        return np.clip(x, -32000, 32000).astype(np.int16)

    def accel(self, rows, tick, spt, bundle_ms, fs):
        p = self.st.patch.arr[rows]
        base = ((p["act_offset_ms"].astype(np.int64) * fs) // 1000 + tick * spt) % self.n_acc
        idx = (base[:, None] + np.arange(spt)[None, :]) % self.n_acc
        x = self.bank.accel[p["activity"][:, None], idx, :].astype(np.float32)          # (n, spt, 3)
        x += POSTURE_G[np.clip(p["posture"], 0, len(POSTURE_G) - 1)][:, None, :]
        return np.clip(x, -32000, 32000).astype(np.int16)

    def sec_index(self, rows, tick, bundle_ms):
        fs = int(self.st.ctl[CTL["ecg_fs"]]) or 250
        start = self.advance(rows, tick, fs * bundle_ms // 1000, fs)
        return (start.astype(np.int64) // fs) % self.seconds

    def numerics(self, rows: np.ndarray, tick: int, bundle_ms: int) -> dict:
        p = self.st.patch.arr[rows]
        si = self.sec_index(rows, tick, bundle_ms)
        var = p["variant"]
        sec = self.bank.sec
        hr = sec[var, 0, si].astype(np.int16)
        rr = np.choose(np.clip(p["resp_src"], 0, 2), [sec[var, 1, si], sec[var, 2, si], sec[var, 3, si]]).astype(np.int16)
        sp = np.choose(np.clip(p["spo2_src"], 0, 2), [sec[var, 4, si], sec[var, 5, si], sec[var, 6, si]]).astype(np.int16)
        sp = np.where(sp > 0, np.clip(sp + p["spo2_bias"], 0, 100), 0)
        lead_off = p["lead_off"] > 0
        age_a = tick - p["attach_tick"]
        settling = (p["attach_tick"] > 0) & (age_a >= 0) & (age_a < 8.0 * (1000.0 / bundle_ms))
        lead_off = lead_off | settling                                            # HR/RR search phase after attachment
        hr = np.where(lead_off, 0, hr)
        rr = np.where(lead_off & (p["resp_src"] != 2), 0, rr)
        sp = np.where(p["spo2_off"] > 0, 0, sp)
        motion = p["art_gain"] > 0.35
        rng = np.random.default_rng(tick + 3)
        rr = np.where(motion & (rng.random(rows.size) < 0.4), 0, rr)         # RR unreliable during motion
        sp = np.where(motion & (rng.random(rows.size) < 0.25), 0, sp)
        hr_ov = p["hr_override"]
        # slow multi-day modulation (circadian + drift) shared with the trend API
        hs = np.where(p["hr_scale"] <= 0.1, 1.0, p["hr_scale"]).astype(np.float32)
        hr = np.where(hr > 0, np.rint(hr * hs), 0).astype(np.int16)
        rr = np.where(rr > 0, np.rint(rr + p["rr_add"]), 0).astype(np.int16)
        sp = np.where(sp > 0, np.clip(np.rint(sp + p["spo2_add"]), 0, 100), 0).astype(np.int16)
        hr = np.where(hr_ov > 0, hr_ov, hr)
        temp = self.bank.temp[p["temp_var"], si] + p["temp_bias"] + p["temp_add"]
        temp = np.where(lead_off, 0.0, temp)                                     # patch off body -> no temp
        gl = self.bank.glucose[p["gluc_var"], si] + p["gl_add"]
        return {CH_HR: np.clip(hr, 0, 255).astype(np.uint8), CH_RESP_RATE: np.clip(rr, 0, 255).astype(np.uint8),
                CH_SPO2: np.clip(sp, 0, 255).astype(np.uint8), CH_TEMP: np.rint(temp * 100).astype(np.int16),
                CH_GLUCOSE: np.clip(np.rint(gl * 10), 0, 65535).astype(np.uint16)}

    def pace_marks(self, row: int, tick: int, spt: int, fs: int, detect: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """Spike sample offsets within the frame and their types (0 A, 1 V, 2 LV).
        With detect=True, marks are dropped according to the patch's hardware detection probability
        (deterministic per spike so retransmissions agree)."""
        p = self.st.patch.arr[row]
        v = int(p["variant"])
        marks = self.bank.pace[v]
        empty = (np.zeros(0, dtype=np.uint16), np.zeros(0, dtype=np.uint8))
        if marks.size == 0:
            return empty
        types = self.bank.pace_type[v]
        start = self.advance(np.array([row]), tick, spt, fs)
        base = int(start[0]) % self.n_ecg
        rate = float(p["hr_scale"]) if p["hr_scale"] > 0.1 else 1.0
        spt = int(math.ceil(spt * rate))                       # window covered by this frame at the stretched rate
        lo = np.searchsorted(marks, base)
        hi = np.searchsorted(marks, base + spt)
        out = marks[lo:hi] - base
        typ = types[lo:hi]
        absidx = marks[lo:hi]
        if base + spt > self.n_ecg:                       # wrap
            hi2 = np.searchsorted(marks, base + spt - self.n_ecg)
            out = np.concatenate([out, marks[:hi2] + (self.n_ecg - base)])
            typ = np.concatenate([typ, types[:hi2]])
            absidx = np.concatenate([absidx, marks[:hi2]])
        if out.size and rate != 1.0:
            out = np.rint(out / rate).astype(np.int64)
            keep_w = out < int(self.st.ctl[CTL["ecg_fs"]] * self.st.ctl[CTL["bundle_ms"]] // 1000)
            out, typ, absidx = out[keep_w], typ[keep_w], absidx[keep_w]
        if detect and out.size:
            det = int(p["pace_detect"])
            if det < 100:
                h = (absidx.astype(np.uint64) * np.uint64(2654435761) + np.uint64(int(p["patch_id"]) * 97)) % np.uint64(100)
                keep = h < det
                out, typ = out[keep], typ[keep]
        return out.astype(np.uint16), typ.astype(np.uint8)

    def preview(self, row: int, tick: int, chan_mask: int) -> dict:
        """Samples for the web live view (one patch)."""
        rows = np.array([row])
        c = self.st.ctl
        bundle_ms = int(c[CTL["bundle_ms"]])
        fs = {"ecg": int(c[CTL["ecg_fs"]]), "ppg": int(c[CTL["ppg_fs"]]), "resp": int(c[CTL["resp_fs"]]), "accel": int(c[CTL["accel_fs"]])}
        out: dict = {}
        cft = int(c[CTL["crossfade_ticks"]])
        if chan_mask & (1 << CH_ECG):
            out["ecg"] = self.ecg(rows, tick, fs["ecg"] * bundle_ms // 1000, bundle_ms, fs["ecg"], cft)[0].tolist()
            pm, pt = self.pace_marks(row, tick, fs["ecg"] * bundle_ms // 1000, fs["ecg"])
            if pm.size:
                out["pace"] = pm.tolist()
                out["pace_type"] = pt.tolist()
        if chan_mask & (1 << CH_PPG):
            out["ppg"] = self.ppg(rows, tick, fs["ppg"] * bundle_ms // 1000, bundle_ms, fs["ppg"])[0].tolist()
        if chan_mask & (1 << CH_RESP_WAVE):
            out["resp_wave"] = self.resp_wave(rows, tick, fs["resp"] * bundle_ms // 1000, bundle_ms, fs["resp"])[0].tolist()
        if chan_mask & (1 << CH_ACCEL):
            out["accel"] = self.accel(rows, tick, fs["accel"] * bundle_ms // 1000, bundle_ms, fs["accel"])[0].tolist()
        num = self.numerics(rows, tick, bundle_ms)
        has = lambda ch: bool(chan_mask & (1 << ch))
        out["num"] = {"hr": int(num[CH_HR][0]) if has(CH_HR) else 0, "resp": int(num[CH_RESP_RATE][0]) if has(CH_RESP_RATE) else 0,
                      "spo2": int(num[CH_SPO2][0]) if has(CH_SPO2) else 0,
                      "temp": round(float(num[CH_TEMP][0]) / 100.0, 2) if has(CH_TEMP) else 0.0,
                      "glucose": round(float(num[CH_GLUCOSE][0]) / 10.0, 1) if has(CH_GLUCOSE) else 0.0}
        return out


class FrameBuilder:
    """Builds per-gateway frames for a subset of gateway rows."""

    def __init__(self, bank: LoopBank, st: SharedState, gw_rows: np.ndarray, meta_provider=None):
        self.st = st
        self.g = Gather(bank, st)
        self.gw_rows = np.asarray(gw_rows, dtype=np.int64)
        self.in_set = np.zeros(st.n_gateways, dtype=bool)
        self.in_set[self.gw_rows] = True
        self.seq = np.zeros(st.n_gateways, dtype=np.int64)
        self._fuzz_rate = 0.0; self._fuzz_mask = 0
        self.meta_provider = meta_provider          # callable(gw_row) -> dict, or None (worker loads json)
        self.meta_cache: dict[int, bytes] = {}
        self.meta_version = -1
        self.force_meta = np.ones(st.n_gateways, dtype=bool)
        self.start_ns = time.time_ns()
        self.rng = np.random.default_rng(int(time.time_ns() % (2 ** 31)))

    def invalidate_meta(self):
        self.meta_cache.clear()
        self.force_meta[:] = True

    def build(self, tick: int, ts_ms: int) -> dict[int, bytes]:
        st = self.st
        c = st.ctl
        bundle_ms = int(c[CTL["bundle_ms"]])
        chan_mask = int(c[CTL["chan_mask"]])
        fs = {"ecg": int(c[CTL["ecg_fs"]]), "ppg": int(c[CTL["ppg_fs"]]), "resp": int(c[CTL["resp_fs"]]), "accel": int(c[CTL["accel_fs"]])}
        meta_every = max(1, int(c[CTL["meta_every"]]))
        gwstat_every = max(1, int(c[CTL["gwstat_every"]]))
        self._fuzz_rate = float(c[CTL["fuzz_rate"]]); self._fuzz_mask = int(c[CTL["fuzz_mask"]])   # read once per tick, not per gateway
        cft = int(c[CTL["crossfade_ticks"]])
        ticks_per_sec = max(1, 1000 // bundle_ms)
        P = st.patch.arr
        active = np.where((P["active"] > 0) & (P["gw"] >= 0))[0]
        if active.size:
            active = active[self.in_set[P["gw"][active]]]
        gw_of = P["gw"][active]
        # effective per-patient channel set = global selection ∩ patient's device channels (0 = unset -> all)
        pm = P["chan_mask"][active].astype(np.int64)
        eff = np.where(pm == 0, chan_mask, pm & chan_mask)
        uniq, inv = (np.unique(eff, return_inverse=True) if active.size else (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)))
        order = np.lexsort((inv, gw_of)) if active.size else np.zeros(0, dtype=np.int64)
        active, gw_of, inv, eff = active[order], gw_of[order], inv[order], eff[order]
        # ---- gather all waveform samples for all active patches at once (global mask decides what to compute)
        wave: dict[int, np.ndarray] = {}
        if active.size:
            if chan_mask & (1 << CH_ECG):
                wave[CH_ECG] = self.g.ecg(active, tick, fs["ecg"] * bundle_ms // 1000, bundle_ms, fs["ecg"], cft)
            if chan_mask & (1 << CH_PPG):
                wave[CH_PPG] = self.g.ppg(active, tick, fs["ppg"] * bundle_ms // 1000, bundle_ms, fs["ppg"])
            if chan_mask & (1 << CH_RESP_WAVE):
                wave[CH_RESP_WAVE] = self.g.resp_wave(active, tick, fs["resp"] * bundle_ms // 1000, bundle_ms, fs["resp"])
            if chan_mask & (1 << CH_ACCEL):
                wave[CH_ACCEL] = self.g.accel(active, tick, fs["accel"] * bundle_ms // 1000, bundle_ms, fs["accel"])
        numeric_sel = ((tick + active) % ticks_per_sec == 0) if active.size else np.zeros(0, dtype=bool)
        num_bits = sum(1 << ch for ch in (CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE))
        nums = self.g.numerics(active[numeric_sel], tick, bundle_ms) if (chan_mask & num_bits and numeric_sel.any()) else None
        if active.size:
            P["seq"][active] += 1                                    # one packet per patch per frame; SAF replays keep their original numbers
        pa = P[active]
        flags = pa["flags"].astype(np.uint8)
        # ---- structured record arrays per (effective mask, with numerics)
        rec_all: dict[tuple[int, bool], np.ndarray] = {}
        pos_all: dict[tuple[int, bool], np.ndarray] = {}
        sel_all: dict[tuple[int, bool], np.ndarray] = {}
        for k, m in enumerate(uniq):
            m = int(m)
            for with_num in (False, True):
                if with_num and not (m & num_bits):
                    continue
                sel = (inv == k) & (numeric_sel if with_num else ~numeric_sel)
                if with_num is False and (m & num_bits) == 0:
                    sel = (inv == k)                                 # no numerics in this set: every frame is a plain frame
                n = int(sel.sum())
                if n == 0:
                    continue
                dt = record_dtype(m, with_num, bundle_ms, fs["ecg"], fs["ppg"], fs["resp"], fs["accel"])
                rec = np.zeros(n, dtype=dt)
                fill_constants(rec, m, with_num, bundle_ms, fs)
                rec["patch_id"] = pa["patch_id"][sel]
                rec["patient_id"] = pa["patient_id"][sel]
                rec["seq"] = pa["seq"][sel]
                rec["flags"] = flags[sel]
                rec["battery"] = pa["battery"][sel]
                rec["rssi"] = pa["rssi"][sel]
                for ch, arr in wave.items():
                    if m & (1 << ch):
                        rec[f"c{ch}"] = arr[sel]
                if with_num and nums is not None:
                    idx_in_num = np.cumsum(numeric_sel) - 1            # row -> index into nums arrays
                    for ch, arr in nums.items():
                        if m & (1 << ch):
                            rec[f"c{ch}"] = arr[idx_in_num[sel]]
                key = (k, with_num)
                rec_all[key] = rec
                sel_all[key] = sel
                pos_all[key] = np.cumsum(sel) - sel
        # ---- per gateway slicing
        out: dict[int, bytes] = {}
        G = st.gw.arr
        if active.size:
            bounds = np.flatnonzero(np.diff(gw_of)) + 1
            starts = np.concatenate([[0], bounds])
            ends = np.concatenate([bounds, [active.size]])
        else:
            starts = ends = np.zeros(0, dtype=np.int64)
        gw_with_patches = set()
        spt_ecg = fs["ecg"] * bundle_ms // 1000
        pace_by_gw: dict[int, list[bytes]] = {}
        if active.size:
            paced_rows = active[(pa["paced"] > 0) & ((eff & (1 << CH_ECG)) > 0) & ((eff & (1 << CH_PACE)) > 0)]
            for r in paced_rows:
                marks, types = self.g.pace_marks(int(r), tick, spt_ecg, fs["ecg"])
                if marks.size:
                    pr = P[r]
                    enc = (marks.astype(np.uint16) & 0x3FFF) | (types.astype(np.uint16) << 14)     # bits 0-13 offset, 14-15 type
                    pace_by_gw.setdefault(int(pr["gw"]), []).append(
                        pace_record(int(pr["patch_id"]), int(pr["patient_id"]), int(pr["seq"]), int(pr["flags"]), int(pr["battery"]), int(pr["rssi"]), enc))
        # per-gateway byte ranges of every record array, computed with numpy once per tick: for each key the selected rows
        # are contiguous per gateway (rows are sorted by gateway), so a gateway's block is one slice of the array's bytes
        blocks: list[tuple[memoryview, int, np.ndarray, np.ndarray, np.ndarray]] = []
        for key, rec in rec_all.items():
            idx = np.flatnonzero(sel_all[key])                    # positions (in the gw-sorted active list) that belong to this key
            if idx.size == 0:
                continue
            lo = np.searchsorted(idx, starts)
            hi = np.searchsorted(idx, ends)
            cnt = hi - lo
            pos = pos_all[key]
            first = np.where(cnt > 0, pos[idx[np.minimum(lo, idx.size - 1)]], 0)
            last = np.where(cnt > 0, pos[idx[np.maximum(hi - 1, 0)]] + 1, 0)
            blocks.append((memoryview(rec.tobytes()), rec.dtype.itemsize, cnt, first, last))
        gw_status = G["status"]
        gws = gw_of[starts] if active.size else np.zeros(0, dtype=np.int64)
        for i in range(gws.size):
            gw = int(gws[i])
            if gw_status[gw] == 2:
                continue
            gw_with_patches.add(gw)
            parts = []
            n_rec = 0
            for mv, isz, cnt, first, last in blocks:
                c_ = int(cnt[i])
                if c_:
                    parts.append(mv[int(first[i]) * isz: int(last[i]) * isz])
                    n_rec += c_
            pr = pace_by_gw.get(gw)
            if pr:
                parts += pr
                n_rec += len(pr)
            out[gw] = self._wrap(gw, tick, ts_ms, n_rec, b"".join(parts), meta_every, gwstat_every, G)
        # keepalive for active gateways without patches
        for gw in self.gw_rows:
            gw = int(gw)
            if gw in gw_with_patches or G["active"][gw] == 0 or G["status"][gw] == 2:
                continue
            if (tick + gw) % ticks_per_sec == 0:
                out[gw] = self._wrap(gw, tick, ts_ms, 0, b"", meta_every, gwstat_every, G, keepalive=True)
        return out

    def _wrap(self, gw: int, tick: int, ts_ms: int, n_rec: int, payload: bytes, meta_every: int, gwstat_every: int, G, keepalive=False) -> bytes:
        flags = F_KEEPALIVE if keepalive else 0
        pre = b""
        if (tick + gw) % gwstat_every == 0:
            flags |= F_GWSTAT
            pre += gwstat_block(int(G["cpu"][gw]), int(G["mem"][gw]), int(G["net"][gw]), int(G["wan_rssi"][gw]), int(G["n_conn"][gw]),
                                int(G["status"][gw]), int(G["uptime_s"][gw]), int(G["temp_c"][gw]))
        if self.force_meta[gw] or (tick + gw * 7) % meta_every == 0:
            mb = self._meta(gw, tick)
            if mb is not None:
                flags |= F_META
                pre += mb
                self.force_meta[gw] = False
        self.seq[gw] = (self.seq[gw] + 1) & 0xFFFFFFFF
        fr = self._fuzz_rate
        if fr > 0 and (self._fuzz_mask >> 8) & 1 and self.rng.random() < fr / 1000.0:
            self.seq[gw] = (self.seq[gw] + int(self.rng.integers(2, 11))) & 0xFFFFFFFF      # fuzz 'seq_gap': skip a few sequence numbers
            self.st.stat["fuzz"][gw] += 1
        return frame(int(G["gw_id"][gw]), int(self.seq[gw]), ts_ms, n_rec, pre + payload, flags)

    def _meta(self, gw: int, tick: int) -> bytes | None:
        if self.meta_provider is None:
            return None
        try:
            m = self.meta_provider(gw)
        except Exception:
            return None
        if m is None:
            return None
        m["tick"] = tick
        return meta_block(m)


class Sender:
    """Non-blocking TCP sender with per-gateway (or shared) sockets, latency heap and backlog accounting."""

    def __init__(self, st: SharedState, gw_rows: np.ndarray, worker_id: int, fb: "FrameBuilder | None" = None, capture_dir: str | None = None):
        self.st = st
        self.fb = fb
        self.gw_rows = [int(g) for g in gw_rows]
        self.worker_id = worker_id
        self.socks: dict[int, socket.socket | None] = {}
        self.bufs: dict[int, bytearray] = {}
        self.last_try: dict[int, float] = {}
        self.delayed: list[tuple[float, int, int, bytes]] = []
        self._seq = 0
        self.target = ("", 0)
        self.cfg_version = -1
        self.rng = np.random.default_rng(1000 + worker_id)
        self.connect_budget = 0
        self.max_connects_per_cycle = 3
        # store-and-forward: frames buffered per gateway while the unit is down / has no connection, replayed after reconnect
        self.saf: dict[int, deque] = {}
        self.saf_bytes: dict[int, int] = {}
        self.saf_total = 0                                   # worker-wide SAF bytes (capped: a whole-site outage must not eat RAM)
        self.saf_worker_max = int(names_saf_max) if (names_saf_max := os.environ.get("BIOSIM_SAF_WORKER_MAX", "")) else 64 * 1024 * 1024
        # fuzz drill: a held frame per gateway for the 'reorder' kind
        self.held: dict[int, bytes] = {}
        self.storm_seen = 0.0
        # ground-truth capture tap (exact bytes handed to the socket, before any fuzz mutation)
        self.capture_dir = capture_dir
        self.cap_file = None
        self.cap_bytes = 0
        self.cap_max = int(os.environ.get("BIOSIM_CAPTURE_WORKER_MAX", str(128 * 1024 * 1024)))   # set by the engine from transport.capture_max_mb / workers

    def _refresh_target(self):
        v = self.st.ctl[CTL["cfg_version"]]
        if v != self.cfg_version:
            self.cfg_version = v
            new = (self.st.get_target(), int(self.st.ctl[CTL["target_port"]]))
            if new != self.target:
                self.close_all()
                self.target = new

    def close_all(self):
        for k, s in list(self.socks.items()):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self.socks.clear()
        self.bufs.clear()
        self.st.stat["connected"][self.gw_rows] = 0

    def _key(self, gw: int) -> int:
        return gw if int(self.st.ctl[CTL["socket_mode"]]) == 0 else -1 - self.worker_id

    def _connect(self, key: int, now: float) -> socket.socket | None:
        s = self.socks.get(key)
        if s is not None:
            return s
        if now - self.last_try.get(key, 0) < max(0.5, float(self.st.ctl[CTL["reconnect_s"]])):
            return None
        if self.connect_budget <= 0:          # smooth connection storms (listen backlog)
            return None
        self.connect_budget -= 1
        self.last_try[key] = now
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                s.connect(self.target)
            except (BlockingIOError, InterruptedError):
                pass
            self.socks[key] = s
            self.bufs[key] = bytearray()
            if self.fb is not None:                       # a (re)connected device announces itself with META first
                for g in ([key] if key >= 0 else self.gw_rows):
                    self.fb.force_meta[g] = True
            return s
        except OSError:
            self.socks[key] = None
            return None

    def _drop_conn(self, key: int, gws: list[int]):
        s = self.socks.pop(key, None)
        if s:
            try:
                s.close()
            except Exception:
                pass
        self.bufs.pop(key, None)
        self.st.stat["connected"][gws] = 0

    # ---------------------------------------------------------------- drills
    def _storm_check(self, now: float) -> None:
        """Connection storm: on a new storm epoch every socket is dropped at once; while the storm lasts the per-cycle
        connect budget is unlimited so all gateways reconnect simultaneously (listen backlog / accept-rate test)."""
        c = self.st.ctl
        ep = float(c[CTL["storm_epoch"]])
        if ep > self.storm_seen:
            self.storm_seen = ep
            self.close_all()
            for k in list(self.last_try):
                self.last_try[k] = 0.0
        in_storm = now < float(c[CTL["storm_until"]])
        budget = int(c[CTL["connect_budget"]]) or 3
        self.max_connects_per_cycle = 100000 if in_storm else budget

    def _fuzz(self, gw: int, data: bytes) -> list[bytes]:
        """Corrupt-frame drill: returns the byte chunks to put on the wire instead of `data`."""
        c = self.st.ctl
        rate = float(c[CTL["fuzz_rate"]])
        if rate <= 0:
            return [data]
        held = self.held.pop(gw, None)
        pre = [held] if held is not None else []
        if self.rng.random() >= rate / 1000.0:
            return pre + [data]
        mask = int(c[CTL["fuzz_mask"]])
        kinds = [i for i in range(8) if (mask >> i) & 1]           # bits 0..7 are wire-level kinds (seq_gap = bit 8 lives in the builder)
        if not kinds:
            return pre + [data]
        kind = int(self.rng.choice(kinds))
        self.st.stat["fuzz"][gw] += 1
        h = bytearray(data[:HEADER.size])
        magic, ver, flags, gw_id, seq, ts_ms, n_rec, plen = HEADER.unpack(bytes(h))
        if kind == 0:      # bad_magic
            return pre + [HEADER.pack(0x4D4D, ver, flags, gw_id, seq, ts_ms, n_rec, plen) + data[HEADER.size:]]
        if kind == 1:      # bad_version
            return pre + [HEADER.pack(magic, 0xFF, flags, gw_id, seq, ts_ms, n_rec, plen) + data[HEADER.size:]]
        if kind == 2:      # bad_len: header claims a different payload length than what follows
            return pre + [HEADER.pack(magic, ver, flags, gw_id, seq, ts_ms, n_rec, max(0, plen + int(self.rng.integers(-64, 65)) or 1)) + data[HEADER.size:]]
        if kind == 3:      # truncated: the connection delivers only part of the frame (the next frame follows immediately)
            return pre + [data[: max(HEADER.size, len(data) // 2)]]
        if kind == 4:      # oversize: absurd payload_len with the normal payload behind it
            return pre + [HEADER.pack(magic, ver, flags, gw_id, seq, ts_ms, n_rec, 0x7FFFFFF0) + data[HEADER.size:]]
        if kind == 5:      # garbage: random bytes between two frames
            return pre + [bytes(self.rng.integers(0, 256, size=int(self.rng.integers(16, 400)), dtype=np.uint8)), data]
        if kind == 6:      # dup: the same frame twice
            return pre + [data, data]
        if kind == 7:      # reorder: hold this frame until the next one has gone out
            self.held[gw] = data
            return pre
        return pre + [data]

    def _fuzz_record(self, gw: int, data: bytes) -> bytes:
        """bad_record kind (bit 9): append a bogus record with an unknown channel id / dtype and bump n_rec."""
        c = self.st.ctl
        if not ((int(c[CTL["fuzz_mask"]]) >> 9) & 1) or self.rng.random() >= float(c[CTL["fuzz_rate"]]) / 1000.0:
            return data
        magic, ver, flags, gw_id, seq, ts_ms, n_rec, plen = HEADER.unpack(data[:HEADER.size])
        if flags & F_KEEPALIVE:
            return data
        bogus = struct.pack("<IIIBBbB", 0xFFFFFFF0, 0, 0, 0, 100, -40, 1) + struct.pack("<BBH", 250, 9, 4) + b"\xde\xad\xbe\xef\x00\x01\x02\x03"
        self.st.stat["fuzz"][gw] += 1
        return HEADER.pack(magic, ver, flags, gw_id, seq, ts_ms, n_rec + 1, plen + len(bogus)) + data[HEADER.size:] + bogus

    def _tap(self, gw: int, data: bytes) -> None:
        """Ground-truth capture: [gw_row u32][len u32][frame] appended to data/capture/w<worker>.bin (512 MB cap)."""
        if self.cap_file is None:
            if not self.capture_dir:
                return
            try:
                os.makedirs(self.capture_dir, exist_ok=True)
                self.cap_file = open(os.path.join(self.capture_dir, f"w{self.worker_id}.bin"), "ab")
                self.cap_bytes = self.cap_file.tell()
            except OSError:
                self.capture_dir = None
                return
        if self.cap_bytes > self.cap_max:                      # per-worker share of transport.capture_max_mb: stop the tap for everyone
            self.st.ctl[CTL["tap"]] = 0
            self._tap_close()
            return
        self.cap_file.write(struct.pack("<II", gw, len(data)) + data)
        self.cap_bytes += 8 + len(data)

    def _tap_close(self) -> None:
        if self.cap_file is not None:
            try:
                self.cap_file.close()
            except Exception:
                pass
            self.cap_file = None

    def _saf_push(self, gw: int, data: bytes) -> None:
        S = self.st.stat.arr
        cap = int(self.st.ctl[CTL["saf_max_bytes"]]) or 2097152
        q = self.saf.setdefault(gw, deque())
        q.append(data)
        self.saf_bytes[gw] = self.saf_bytes.get(gw, 0) + len(data)
        self.saf_total += len(data)
        while self.saf_bytes[gw] > cap and q:                     # oldest frames are evicted first, like a ring buffer
            old = q.popleft()
            self.saf_bytes[gw] -= len(old)
            self.saf_total -= len(old)
            S["drop_saf"][gw] += 1
        S["saf_bytes"][gw] = self.saf_bytes[gw]
        if self.saf_total > self.saf_worker_max:                  # worker-wide cap: evict oldest frames of the fullest queues
            for g2, n2 in sorted(self.saf_bytes.items(), key=lambda kv: -kv[1]):
                q2 = self.saf.get(g2)
                while q2 and self.saf_total > self.saf_worker_max * 0.9:
                    old = q2.popleft(); self.saf_bytes[g2] -= len(old); self.saf_total -= len(old); S["drop_saf"][g2] += 1
                S["saf_bytes"][g2] = self.saf_bytes[g2]
                if self.saf_total <= self.saf_worker_max * 0.9:
                    break

    def _saf_drain(self, now: float, max_backlog: int) -> None:
        """After reconnect: replay buffered frames oldest-first, `burst` frames per cycle, ahead of the live frames."""
        if not self.saf:
            return
        S = self.st.stat.arr
        G = self.st.gw.arr
        burst = int(self.st.ctl[CTL["saf_burst"]]) or 40
        for gw in list(self.saf):
            q = self.saf[gw]
            if not q:
                del self.saf[gw]
                self.saf_bytes.pop(gw, None)
                S["saf_bytes"][gw] = 0
                continue
            if G["status"][gw] == 2 or G["silent"][gw]:
                continue
            key = self._key(gw)
            sock = self._connect(key, now)
            if sock is None:
                continue
            buf = self.bufs[key]
            n = 0
            while q and n < burst and len(buf) + len(q[0]) <= max_backlog:
                d = q.popleft()
                self.saf_total -= len(d)
                self.saf_bytes[gw] -= len(d)
                buf += d
                S["saf_replayed"][gw] += 1
                n += 1
            S["saf_bytes"][gw] = max(0, self.saf_bytes.get(gw, 0))

    def send_frames(self, frames: dict[int, bytes], now: float) -> None:
        self._refresh_target()
        st = self.st
        G = st.gw.arr
        S = st.stat.arr
        generate_only = (not self.target[0]) or st.ctl[CTL["generate_only"]] > 0
        max_backlog = int(st.ctl[CTL["max_backlog"]])
        self._storm_check(now)
        self.connect_budget = self.max_connects_per_cycle
        tap = st.ctl[CTL["tap"]] > 0
        if not tap and self.cap_file is not None:
            self._tap_close()
        if not generate_only:
            self._saf_drain(now, max_backlog)
        elif self.saf:                                            # no router target: nothing to replay to, drop the buffers
            for gw in self.saf:
                S["saf_bytes"][gw] = 0
            self.saf.clear(); self.saf_bytes.clear()
        # emulated loss / latency
        for gw, data in frames.items():
            S["pkts"][gw] += 1
            S["bytes"][gw] += len(data)
            S["last_tick"][gw] = int(now)
            if generate_only:
                continue
            if tap:
                self._tap(gw, data)
            if G["silent"][gw]:                                     # half-open drill: socket up, nothing sent
                S["drop_backlog"][gw] += 1
                continue
            loss = float(G["loss"][gw])
            if loss > 0 and self.rng.random() < loss:
                S["drop_emul"][gw] += 1
                continue
            lat = float(G["latency_ms"][gw])
            if lat > 0.5:
                jit = float(G["jitter_ms"][gw])
                due = now + (lat + (self.rng.random() * jit if jit > 0 else 0.0)) / 1000.0
                self._seq += 1
                heapq.heappush(self.delayed, (due, self._seq, gw, data))
            else:
                self._enqueue(gw, data, now, max_backlog)
        while self.delayed and self.delayed[0][0] <= now:
            _, _, gw, data = heapq.heappop(self.delayed)
            self._enqueue(gw, data, now, max_backlog)
        self.flush(now)

    def _enqueue(self, gw: int, data: bytes, now: float, max_backlog: int):
        S = self.st.stat.arr
        saf_on = self.st.ctl[CTL["saf_enabled"]] > 0
        if self.st.gw["status"][gw] == 2:          # gateway down: connection dropped; frames go to the local buffer
            key = self._key(gw)
            if key >= 0:
                self._drop_conn(key, [gw])
            if saf_on:
                self._saf_push(gw, data)
            else:
                S["drop_backlog"][gw] += 1
            return
        key = self._key(gw)
        s = self._connect(key, now)
        if s is None:
            if saf_on:
                self._saf_push(gw, data)
            else:
                S["drop_noconn"][gw] += 1
            return
        if gw in self.saf and self.saf[gw]:           # replay still in progress: keep order, append behind the backlog
            self._saf_push(gw, data)
            return
        buf = self.bufs[key]
        chunks = self._fuzz(gw, self._fuzz_record(gw, data))
        for d in chunks:
            if len(buf) + len(d) > max_backlog:
                if saf_on:
                    self._saf_push(gw, d)
                else:
                    S["drop_backlog"][gw] += 1
                continue
            buf += d

    def flush(self, now: float):
        S = self.st.stat.arr
        for key, s in list(self.socks.items()):
            if s is None:
                continue
            buf = self.bufs.get(key)
            gws = [key] if key >= 0 else self.gw_rows
            if not buf:
                continue
            try:
                n = s.send(buf)
                if n > 0:
                    del buf[:n]
                    S["connected"][gws] = 1
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                S["send_err"][gws] += 1
                self._drop_conn(key, gws)

    def close(self):
        self.close_all()
        self._tap_close()


def worker_main(worker_id: int, names: dict, gw_rows: list[int], bank_args: dict, meta_path: str | None):
    """Entry point for a worker process."""
    try:
        os.nice(-5) if hasattr(os, "nice") and os.geteuid() == 0 else None
    except Exception:
        pass
    st = SharedState.attach(names)
    bank = LoopBank(**bank_args)
    bank.load()
    meta: dict = {}
    meta_mtime = 0.0

    def meta_provider(gw: int):
        return meta.get(gw)

    def reload_meta():
        nonlocal meta, meta_mtime
        if not meta_path or not os.path.exists(meta_path):
            return
        m = os.path.getmtime(meta_path)
        if m != meta_mtime:
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                meta = {int(k): v for k, v in raw.items()}
                meta_mtime = m
                fb.invalidate_meta()
            except Exception:
                pass

    gw_rows = np.asarray(gw_rows, dtype=np.int64)
    fb = FrameBuilder(bank, st, gw_rows, meta_provider)
    sender = Sender(st, gw_rows, worker_id, fb, names.get("capture_dir"))
    W = st.wstat.arr
    W["alive"][worker_id] = 1
    W["n_gw"][worker_id] = len(gw_rows)
    last_tick = -1
    last_meta_check = 0.0
    bt = 0.0
    sdt = 0.0
    try:
        while st.ctl[CTL["stop_workers"]] == 0:
            if st.ctl[CTL["running"]] == 0:
                sender.close_all()
                last_tick = -1
                time.sleep(0.05)
                continue
            bundle = st.ctl[CTL["bundle_ms"]] / 1000.0
            epoch = st.ctl[CTL["epoch_ns"]] / 1e9
            now = time.time()
            tick = int((now - epoch) / bundle)
            if tick <= last_tick:
                nxt = epoch + (last_tick + 1) * bundle
                dt = nxt - now
                if dt > 0:
                    # flush pending delayed frames while waiting
                    sender.send_frames({}, now)
                    time.sleep(min(dt, 0.02))
                continue
            if last_tick >= 0 and tick > last_tick + 1:
                W["overruns"][worker_id] += tick - last_tick - 1
            last_tick = tick
            if now - last_meta_check > 1.0:
                reload_meta()
                last_meta_check = now
            t0 = time.perf_counter()
            frames = fb.build(tick, int(now * 1000))
            t1 = time.perf_counter()
            sender.send_frames(frames, now)
            t2 = time.perf_counter()
            bt = 0.9 * bt + 0.1 * (t1 - t0) * 1e6
            sdt = 0.9 * sdt + 0.1 * (t2 - t1) * 1e6
            W["ticks"][worker_id] += 1
            W["build_us"][worker_id] = bt
            W["send_us"][worker_id] = sdt
            W["last_tick"][worker_id] = tick
            W["pkts"][worker_id] += len(frames)
            W["bytes"][worker_id] += sum(len(f) for f in frames.values())
            P = st.patch.arr
            W["n_patches"][worker_id] = int(np.sum((P["active"] > 0) & (P["gw"] >= 0) & fb.in_set[np.clip(P["gw"], 0, st.n_gateways - 1)]))
    except Exception:
        traceback.print_exc()
    finally:
        W["alive"][worker_id] = 0
        sender.close()
        st.close()
