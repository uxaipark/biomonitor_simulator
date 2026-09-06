"""Coherent physiological 'patient-hour' bundle generator.

One bundle = respiration -> heart rhythm (RSA coupled) -> ECG (EDR coupled)
-> PPG (PTT delayed, pulse deficit) -> per-second numerics (HR, RR by three
sources, SpO2 by three sensors).  Everything is periodic over `seconds`.
"""
from __future__ import annotations

import math

import numpy as np

from .ecg import (Morphology, beat_waves, render_waves, periodic_wander, powerline,
                  colored_noise, circular_smooth)
from .rhythms import RHYTHMS, generate_beats, pick_hr, CONDUCTED


def _resp_profile(rng: np.random.Generator, seconds: int, resp_fs: int, kind: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (phase[rad] at resp_fs, rate_per_sec[brpm], wave at resp_fs in a.u.)."""
    n = seconds * resp_fs
    t = np.arange(n) / resp_fs
    if kind == "copd":
        base = rng.uniform(18, 26)
    elif kind == "tachypnea":
        base = rng.uniform(22, 30)
    elif kind == "brady":
        base = rng.uniform(8, 11)
    else:
        base = rng.uniform(11, 19)
    rate = np.full(n, base)
    # slow periodic variability (periods seconds/k)
    for k in range(1, 8):
        rate += rng.uniform(0.2, 1.2) * np.sin(2 * np.pi * k * t / seconds + rng.uniform(0, 2 * np.pi))
    # sighs: brief slow deep breaths every few minutes
    n_sigh = int(rng.integers(6, 20))
    for _ in range(n_sigh):
        c = rng.uniform(0, seconds)
        w = rng.uniform(4, 8)
        rate -= 4.0 * np.exp(-0.5 * (((t - c + seconds / 2) % seconds - seconds / 2) / w) ** 2)
    apnea = np.ones(n)
    if kind == "apnea":
        for _ in range(int(rng.integers(8, 25))):
            c = rng.uniform(0, seconds)
            w = rng.uniform(8, 25)
            apnea *= 1 - 0.95 * np.exp(-0.5 * (((t - c + seconds / 2) % seconds - seconds / 2) / w) ** 8)
    rate = np.clip(rate, 4, 40) * apnea
    phase = np.cumsum(2 * np.pi * rate / 60.0 / resp_fs)
    cycles = round(phase[-1] / (2 * np.pi))
    phase *= (2 * np.pi * max(1, cycles)) / phase[-1]
    rate *= (2 * np.pi * max(1, cycles)) / (phase[-1] if phase[-1] else 1)
    # asymmetric breath waveform: inspiration 40 % / expiration 60 %
    s = (phase / (2 * np.pi)) % 1.0
    wave = np.where(s < 0.4, 0.5 * (1 - np.cos(np.pi * s / 0.4)), 0.5 * (1 + np.cos(np.pi * (s - 0.4) / 0.6)))
    depth = 1.0
    for k in range(3, 12):
        depth = depth + rng.uniform(0.02, 0.08) * np.sin(2 * np.pi * k * t / seconds + rng.uniform(0, 2 * np.pi))
    wave = (wave - 0.5) * 2.0 * depth * apnea
    rate_sec = rate.reshape(seconds, resp_fs).mean(axis=1)
    return phase, rate_sec, wave.astype(np.float32)


def _overlay(buf: np.ndarray, fs: int, rng: np.random.Generator, info: dict, morph: Morphology, seconds: int) -> None:
    ov = info.get("overlay")
    if not ov:
        return
    n = buf.shape[0]
    t = np.arange(n, dtype=np.float32) / fs
    typ = ov["type"]
    if typ == "fwave":
        cyc = round(ov["freq"] * seconds)
        f = cyc / seconds
        am = 1 + 0.5 * np.sin(2 * np.pi * max(1, round(0.3 * seconds)) / seconds * t + rng.uniform(0, 6))
        fm = 0.4 * np.sin(2 * np.pi * max(1, round(0.07 * seconds)) / seconds * t)
        buf += (ov["amp"] * am * np.sin(2 * np.pi * f * t + fm)).astype(np.float32)
    elif typ == "flutter":
        cyc = round(ov["freq"] * seconds)
        ph = (cyc * t / seconds) % 1.0
        saw = np.where(ph < 0.7, -1 + 2 * ph / 0.7, 1 - 2 * (ph - 0.7) / 0.3)   # sawtooth F waves
        buf += (ov["amp"] * saw).astype(np.float32)
    elif typ == "ptrain":
        p_int = 60.0 / ov["rate"]
        n_p = int(seconds / p_int)
        p_int = seconds / n_p
        centers = (np.arange(n_p) * p_int + rng.uniform(0, p_int)).astype(np.float64)
        render_waves(buf, fs, centers, np.full(n_p, morph.p_amp), np.full(n_p, morph.p_sigma), np.full(n_p, morph.p_sigma * 1.1))
    elif typ == "vfib":
        base = ov["freq"]
        out = np.zeros(n, dtype=np.float32)
        for k in range(1, 5):
            cyc = round((base * rng.uniform(0.8, 1.25)) * seconds)
            fm = 0.35 * np.sin(2 * np.pi * max(1, round(0.05 * seconds * k)) / seconds * t + rng.uniform(0, 6))
            am = 1 + 0.6 * np.sin(2 * np.pi * max(1, round(0.11 * seconds * k)) / seconds * t + rng.uniform(0, 6))
            out += (am * np.sin(2 * np.pi * cyc / seconds * t + fm) / k).astype(np.float32)
        buf += ov["amp"] * out / np.max(np.abs(out))


def generate_bundle(rhythm: str, seed: int, ecg_fs: int = 250, ppg_fs: int = 100, resp_fs: int = 25,
                    seconds: int = 3600, age: int = 65, sex: str = "M", resp_kind: str = "normal",
                    spo2_base: float | None = None) -> dict:
    rng = np.random.default_rng(seed)
    n_ecg = seconds * ecg_fs
    n_ppg = seconds * ppg_fs
    spec = RHYTHMS[rhythm]
    morph = Morphology.random(rng, age, sex)
    if rhythm == "lbbb":
        morph.bbb = "lbbb"
    elif rhythm == "rbbb":
        morph.bbb = "rbbb"
    elif rhythm == "stemi":
        morph.st_offset = float(rng.uniform(0.15, 0.40))
        morph.t_amp *= 1.6
    elif rhythm == "ischemia":
        morph.st_offset = float(-rng.uniform(0.08, 0.22))
        morph.t_polarity = -1.0 if rng.random() < 0.5 else 0.4
    if spec["cls"] == "afib":
        morph.edr_gain *= 1.3
    morph.extra["pvc_pol"] = 1.0 if rng.random() < 0.6 else -1.0
    morph.extra["pac_p"] = 1 if rng.random() < 0.6 else -1

    # ---- respiration
    phase, resp_rate_sec, resp_wave = _resp_profile(rng, seconds, resp_fs, resp_kind)

    def resp_phase(tt: float) -> float:
        return float(phase[min(int(tt * resp_fs), phase.shape[0] - 1)])

    hr_mean = pick_hr(rhythm, rng)
    hrv = 1.0 if age < 50 else 0.75 if age < 70 else 0.55
    if spec["cls"] in ("afib", "vt", "lethal"):
        hrv = 0.3
    beats, info = generate_beats(rhythm, rng, seconds, hr_mean, resp_phase, hrv)
    # exact loop closure: scale times so that last RR completes at `seconds`
    if beats:
        last_gap = beats[0]["t"] + (seconds - beats[-1]["t"]) if len(beats) > 1 else 1.0
        # the wrap gap (from last beat to first beat of next loop) should look like a typical RR
        typical = np.median(np.diff([b["t"] for b in beats])) if len(beats) > 2 else 1.0
        scale = (seconds) / (beats[-1]["t"] + typical - beats[0]["t"] + beats[0]["t"])
        # only stretch if wrap gap is unreasonable (>1.4x or <0.6x typical)
        if not (0.6 * typical <= last_gap <= 1.4 * typical):
            for b in beats:
                b["t"] *= scale
    times = np.array([b["t"] for b in beats], dtype=np.float64)
    kinds = [b["kind"] for b in beats]
    rr_prev = np.diff(times, prepend=times[0] - (np.median(np.diff(times)) if len(times) > 2 else 1.0)) if len(times) else np.array([])

    # ---- render ECG
    ecg = np.zeros(n_ecg, dtype=np.float32)
    centers, amps, sl, sr = [], [], [], []
    pace_marks: list[tuple[int, int]] = []
    for i, b in enumerate(beats):
        k = b["kind"]
        pr_ov = b["extra"].get("pr")
        has_p = b["extra"].get("has_p", True) and not b["extra"].get("nop", False)   # AF/flutter/VT: no P wave
        if pr_ov:
            m2 = Morphology(**{**morph.__dict__})
            m2.pr = pr_ov
            waves = beat_waves(m2, rr_prev[i], k, has_p)
        else:
            waves = beat_waves(morph, rr_prev[i], k, has_p)
        # EDR: respiratory amplitude modulation of QRS
        rp = resp_phase(b["t"] % seconds)
        edr = 1.0 + morph.edr_gain * math.sin(rp)
        for (c, a, l, r) in waves:
            centers.append(b["t"] + c)
            amps.append(a * (edr if abs(c) < 0.08 else 1.0))
            sl.append(l)
            sr.append(r)
        for off, typ in b["extra"].get("spikes", []):
            pace_marks.append((int(round((b["t"] + off) * ecg_fs)) % n_ecg, int(typ)))
    if centers:
        render_waves(ecg, ecg_fs, np.array(centers), np.array(amps), np.array(sl), np.array(sr))
    _overlay(ecg, ecg_fs, rng, info, morph, seconds)
    # pacing spikes are NOT baked in: the runtime adds them per patient (unipolar mV-scale vs bipolar sub-mV lead)
    pace_marks.sort()
    # respiration baseline coupling + slow wander + faint noise
    resp_on_ecg = np.interp(np.arange(n_ecg) / ecg_fs, np.arange(resp_wave.shape[0]) / resp_fs, resp_wave)
    ecg += (rng.uniform(0.02, 0.06) * resp_on_ecg).astype(np.float32)
    ecg += periodic_wander(n_ecg, ecg_fs, rng, amp_mv=rng.uniform(0.03, 0.10))
    ecg += powerline(n_ecg, ecg_fs, amp_mv=rng.uniform(0.0, 0.006), phase=rng.uniform(0, 6))
    ecg += colored_noise(n_ecg, rng, amp=rng.uniform(0.004, 0.012), smooth=2)
    ecg_i16 = np.clip(np.rint(ecg * 1000.0), -32000, 32000).astype(np.int16)

    # ---- PPG (AC component), PTT-delayed pulses with pulse deficit
    ppg = np.zeros(n_ppg, dtype=np.float32)
    ptt = rng.uniform(0.20, 0.32) + (0.03 if age > 70 else 0)
    conducted_idx = [i for i, k in enumerate(kinds) if k in CONDUCTED]
    if conducted_idx:
        ct = times[conducted_idx]
        ck = [kinds[i] for i in conducted_idx]
        crr = np.diff(ct, prepend=ct[0] - np.median(np.diff(ct)) if len(ct) > 2 else 1.0)
        med = np.median(crr) if len(crr) else 1.0
        amp = np.clip(crr / med, 0.35, 1.6) ** 0.6
        for j, k in enumerate(ck):
            if k in ("V", "E"):
                amp[j] *= 0.15 if crr[j] < 0.75 * med else 0.5
            elif k == "S":
                amp[j] *= 0.7
        rp = np.array([phase[min(int((x % seconds) * resp_fs), phase.shape[0] - 1)] for x in ct])
        amp = amp * (1 + 0.15 * np.sin(rp))
        notch = rng.uniform(0.15, 0.45) if age < 60 else rng.uniform(0.05, 0.2)
        c1 = ct + ptt + 0.15
        c2 = ct + ptt + rng.uniform(0.38, 0.46)
        render_waves(ppg, ppg_fs, np.concatenate([c1, c2]), np.concatenate([amp, amp * notch]),
                     np.concatenate([np.full(len(ct), 0.06), np.full(len(ct), 0.08)]),
                     np.concatenate([np.full(len(ct), 0.11), np.full(len(ct), 0.16)]))
    ppg -= ppg.mean()
    resp_on_ppg = np.interp(np.arange(n_ppg) / ppg_fs, np.arange(resp_wave.shape[0]) / resp_fs, resp_wave)
    ppg += (0.25 * resp_on_ppg).astype(np.float32)
    ppg += colored_noise(n_ppg, rng, amp=0.01, smooth=3)
    ppg_i16 = np.clip(np.rint(ppg * 1000.0), -32000, 32000).astype(np.int16)

    # ---- per-second numerics
    sec = np.arange(seconds)
    hr_sec = np.zeros(seconds, dtype=np.uint8)
    if conducted_idx:
        ct = times[conducted_idx]
        ext = np.concatenate([ct - seconds, ct, ct + seconds])
        for s in sec:
            lo = np.searchsorted(ext, s + 1 - 8.0)
            hi = np.searchsorted(ext, s + 1.0)
            cnt = hi - lo
            if cnt >= 2:
                hr = 60.0 * (cnt - 1) / (ext[hi - 1] - ext[lo])
            elif cnt == 1:
                hr = 60.0 / max(0.3, (s + 1.0) - ext[lo]) if (s + 1.0 - ext[lo]) > 1.5 else 60.0 / np.median(np.diff(ct))
            else:
                hr = 0.0
            hr_sec[s] = int(np.clip(round(hr), 0, 250))
    # respiration rate: capacitive (accurate, smoothed), EDR (noisier, fails with irregular rhythm), SpO2/PPG derived
    rr_true = circular_smooth(resp_rate_sec, 15)
    irregular = spec["cls"] in ("afib", "vt", "lethal", "ectopy")
    rr_cap = np.clip(np.rint(rr_true + rng.normal(0, 0.4, seconds)), 0, 60).astype(np.uint8)
    edr_noise = 2.5 if irregular else 1.0
    rr_edr = np.clip(np.rint(circular_smooth(rr_true + rng.normal(0, edr_noise, seconds), 5)), 0, 60).astype(np.uint8)
    if irregular:
        rr_edr[rng.random(seconds) < 0.12] = 0        # invalid
    rr_spo2 = np.clip(np.rint(circular_smooth(rr_true + rng.normal(0, 1.5, seconds), 9)), 0, 60).astype(np.uint8)
    if spec["cls"] == "lethal":
        hr_sec[:] = 0
    # SpO2
    if spo2_base is None:
        spo2_base = rng.uniform(95.5, 99.0) if resp_kind == "normal" else rng.uniform(88, 94)
    t1 = np.arange(seconds)
    spo2 = np.full(seconds, spo2_base)
    for k in range(1, 6):
        spo2 += rng.uniform(0.1, 0.5) * np.sin(2 * np.pi * k * t1 / seconds + rng.uniform(0, 6))
    if resp_kind == "apnea":
        # desaturation follows apnea (rate ~ 0)
        apn = (resp_rate_sec < 3).astype(np.float64)
        desat = circular_smooth(apn, 20) * rng.uniform(6, 12)
        spo2 -= np.roll(desat, 12)
    if spec["cls"] == "lethal":
        spo2 = spo2 - 25 * np.clip(t1 / 60.0, 0, 1)
    spo2 = np.clip(spo2, 50, 100)
    spo2_finger = np.clip(np.rint(spo2 + rng.normal(0, 0.45, seconds)), 0, 100).astype(np.uint8)
    spo2_ring = np.clip(np.rint(spo2 + rng.normal(0, 0.9, seconds) - 0.3), 0, 100).astype(np.uint8)
    spo2_ring[rng.random(seconds) < 0.015] = 0
    spo2_wrist = np.clip(np.rint(np.roll(circular_smooth(spo2, 5), 5) + rng.normal(0, 1.6, seconds) + rng.uniform(-1, 1)), 0, 100).astype(np.uint8)
    spo2_wrist[rng.random(seconds) < 0.03] = 0

    resp_i16 = np.clip(np.rint(resp_wave * 1000.0), -32000, 32000).astype(np.int16)
    r_idx = np.array([int(round(times[i] * ecg_fs)) % n_ecg for i in conducted_idx], dtype=np.int32)
    return {
        "ecg": ecg_i16, "ppg": ppg_i16, "resp_wave": resp_i16,
        "hr": hr_sec, "rr_cap": rr_cap, "rr_edr": rr_edr, "rr_spo2": rr_spo2,
        "spo2_finger": spo2_finger, "spo2_ring": spo2_ring, "spo2_wrist": spo2_wrist,
        "pace": np.array([m for m, _ in pace_marks], dtype=np.uint32), "pace_type": np.array([t for _, t in pace_marks], dtype=np.uint8), "rpeaks": r_idx,
        "meta": {"rhythm": rhythm, "label": spec["label"], "cls": spec["cls"], "hr_mean": float(hr_sec[hr_sec > 0].mean()) if (hr_sec > 0).any() else 0.0,
                 "resp_kind": resp_kind, "age": age, "sex": sex, "seed": seed, "ecg_fs": ecg_fs, "ppg_fs": ppg_fs,
                 "resp_fs": resp_fs, "seconds": seconds, "n_beats": len(beats), "spo2_base": float(spo2_base), "pm": info.get("pm", {})},
    }
