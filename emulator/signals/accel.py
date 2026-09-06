"""3-axis accelerometer activity templates (50 Hz) and matching ECG motion artifact.

Templates are per *activity* (not per patient); the runtime adds the posture
gravity vector and picks the template according to the patient's activity
state, so the ECG artifact and the accelerometer are physically coherent.
Values are in units of 0.001 g (int16).
"""
from __future__ import annotations

import numpy as np

from .ecg import colored_noise, circular_smooth

ACTIVITIES = ["still", "restless", "walking", "exercise", "shower", "transfer", "tremor"]
POSTURES = {"supine": (0.0, 0.0, 1.0), "left_lateral": (0.95, 0.0, 0.3), "right_lateral": (-0.95, 0.0, 0.3),
            "sitting": (0.0, -0.7, 0.7), "standing": (0.0, -1.0, 0.05), "prone": (0.0, 0.0, -1.0)}


def _bursts(rng: np.random.Generator, n: int, fs: int, rate_per_min: float, dur_s: tuple[float, float], amp: float) -> np.ndarray:
    """Random motion bursts (turning in bed, reaching), circular."""
    out = np.zeros((n, 3), dtype=np.float32)
    t = np.arange(n) / fs
    n_b = int(rate_per_min * n / fs / 60)
    for _ in range(n_b):
        c = rng.uniform(0, n / fs)
        w = rng.uniform(*dur_s)
        d = ((t - c + n / fs / 2) % (n / fs)) - n / fs / 2
        env = np.exp(-0.5 * (d / (w / 2)) ** 4)
        f = rng.uniform(0.5, 2.5)
        for ax in range(3):
            out[:, ax] += (amp * rng.uniform(0.3, 1.0) * env * np.sin(2 * np.pi * f * t + rng.uniform(0, 6))).astype(np.float32)
    return out


def generate_activity(activity: str, seed: int, fs: int = 50, seconds: int = 3600, ecg_fs: int = 250) -> dict:
    rng = np.random.default_rng(seed)
    n = seconds * fs
    t = np.arange(n) / fs
    acc = np.zeros((n, 3), dtype=np.float32)
    for ax in range(3):
        acc[:, ax] += colored_noise(n, rng, 0.004, smooth=2)      # sensor noise
    # breathing-induced chest motion (tiny) on all templates
    br = 0.006 * np.sin(2 * np.pi * round(0.25 * seconds) / seconds * t)
    acc[:, 2] += br.astype(np.float32)
    if activity == "still":
        acc += _bursts(rng, n, fs, 0.4, (1.5, 5.0), 0.25)
    elif activity == "restless":
        acc += _bursts(rng, n, fs, 4.0, (1.0, 6.0), 0.5)
        for ax in range(3):
            acc[:, ax] += colored_noise(n, rng, 0.02, smooth=4)
    elif activity in ("walking", "transfer"):
        step_f = rng.uniform(1.6, 2.0) if activity == "walking" else rng.uniform(1.3, 1.7)
        cyc = round(step_f * seconds)
        ph = 2 * np.pi * cyc * t / seconds
        gait = 1 + 0.25 * np.sin(2 * np.pi * round(0.02 * seconds) / seconds * t)
        # walking bouts: on/off envelope (periodic)
        env = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * round(seconds / 90) / seconds * t + 0.3))
        env = circular_smooth(env, fs * 2)
        if activity == "transfer":
            env = np.ones(n)
        vert = 0.35 * gait * (np.sin(ph) + 0.35 * np.sin(2 * ph + 0.4) + 0.12 * np.sin(3 * ph))
        ap = 0.18 * gait * np.sin(ph + 1.2)
        ml = 0.14 * gait * np.sin(ph / 2 + 0.5)
        acc[:, 0] += (ml * env).astype(np.float32)
        acc[:, 1] += (vert * env).astype(np.float32)
        acc[:, 2] += (ap * env).astype(np.float32)
        if activity == "transfer":                       # bed/wheelchair bumps
            acc += _bursts(rng, n, fs, 8.0, (0.3, 1.2), 0.9)
    elif activity == "exercise":
        f = rng.uniform(2.2, 2.8)
        cyc = round(f * seconds)
        ph = 2 * np.pi * cyc * t / seconds
        env = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * round(seconds / 120) / seconds * t))
        env = circular_smooth(env, fs * 3)
        acc[:, 1] += (0.7 * env * (np.sin(ph) + 0.4 * np.sin(2 * ph))).astype(np.float32)
        acc[:, 0] += (0.25 * env * np.sin(ph / 2)).astype(np.float32)
        acc[:, 2] += (0.3 * env * np.sin(ph + 0.8)).astype(np.float32)
    elif activity == "shower":
        for ax in range(3):
            acc[:, ax] += colored_noise(n, rng, 0.12, smooth=3)
        acc += _bursts(rng, n, fs, 20.0, (0.5, 3.0), 0.6)
    elif activity == "tremor":
        f = rng.uniform(4.0, 6.0)
        cyc = round(f * seconds)
        am = 1 + 0.5 * np.sin(2 * np.pi * round(0.1 * seconds) / seconds * t)
        acc[:, 0] += (0.08 * am * np.sin(2 * np.pi * cyc * t / seconds)).astype(np.float32)
        acc[:, 2] += (0.05 * am * np.sin(2 * np.pi * cyc * t / seconds + 1)).astype(np.float32)
    # ---- motion level (per second, 0..1) and ECG motion artifact at ecg_fs
    mag = np.sqrt((acc ** 2).sum(axis=1))
    level_sec = mag.reshape(seconds, fs).std(axis=1)
    level_sec = np.clip(level_sec / 0.35, 0, 1).astype(np.float32)
    n_e = seconds * ecg_fs
    # electrode motion artefact: low-frequency (0.5-5 Hz) skin-electrode impedance change proportional to motion + EMG
    up = np.interp(np.arange(n_e) / ecg_fs, t, mag - np.median(mag))
    art = np.zeros(n_e, dtype=np.float32)
    art += (1.2 * circular_smooth(up.astype(np.float32), max(1, ecg_fs // 6))).astype(np.float32)
    emg_env = np.interp(np.arange(n_e) / ecg_fs, np.arange(seconds), level_sec)
    art += (0.25 * emg_env * colored_noise(n_e, rng, 1.0, smooth=1)).astype(np.float32)
    art_i16 = np.clip(np.rint(art * 1000), -32000, 32000).astype(np.int16)
    acc_i16 = np.clip(np.rint(acc * 1000), -32000, 32000).astype(np.int16)
    return {"accel": acc_i16, "ecg_art": art_i16, "level": level_sec,
            "meta": {"activity": activity, "fs": fs, "ecg_fs": ecg_fs, "seconds": seconds, "seed": seed}}


def generate_noise_loop(seed: int, ecg_fs: int = 250, seconds: int = 3600) -> np.ndarray:
    """Shared generic noise loop (EMG + powerline + wander), unit amplitude ≈ 1 mV -> int16."""
    rng = np.random.default_rng(seed)
    n = seconds * ecg_fs
    t = np.arange(n) / ecg_fs
    x = colored_noise(n, rng, 0.6, smooth=2)
    x += (0.35 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)                    # 60 Hz mains (KR)
    x += (0.25 * np.sin(2 * np.pi * round(0.3 * seconds) / seconds * t)).astype(np.float32)
    return np.clip(np.rint(x * 1000), -32000, 32000).astype(np.int16)
