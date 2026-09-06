"""Slow 1 Hz signals: body temperature (skin patch) and CGM glucose.

Loops are periodic by construction (sum-of-sinusoids with periods T/k) plus
white sensor noise, so start and end join seamlessly.
"""
from __future__ import annotations

import numpy as np

TEMP_PROFILES = ["normal", "low_grade", "fever", "high_fever", "hypothermia"]
GLUCOSE_PROFILES = ["normal", "prediabetic", "diabetic", "hypo_risk", "hyper"]


def _periodic(rng: np.random.Generator, seconds: int, amps: list[float]) -> np.ndarray:
    t = np.arange(seconds)
    out = np.zeros(seconds)
    for k, a in enumerate(amps, start=1):
        out += a * np.sin(2 * np.pi * k * t / seconds + rng.uniform(0, 2 * np.pi))
    return out


def generate_temperature(profile: str, seed: int, seconds: int = 3600) -> np.ndarray:
    """Skin-patch temperature in °C (float32).  Skin ≈ core − 0.4..0.9."""
    rng = np.random.default_rng(seed)
    core = {"normal": rng.uniform(36.3, 37.1), "low_grade": rng.uniform(37.3, 37.9),
            "fever": rng.uniform(38.0, 38.9), "high_fever": rng.uniform(39.0, 40.2),
            "hypothermia": rng.uniform(34.5, 35.6)}[profile]
    skin = core - rng.uniform(0.4, 0.9)
    # only small within-hour undulation: circadian swings come from the multi-day model at runtime
    x = skin + _periodic(rng, seconds, [rng.uniform(0.02, 0.06), rng.uniform(0.01, 0.04), 0.015, 0.01, 0.01])
    if profile in ("fever", "high_fever") and rng.random() < 0.5:
        # antipyretic response: 0.6-1.2 °C drop over ~40 min, periodic bump
        t = np.arange(seconds)
        c = rng.uniform(0, seconds)
        d = ((t - c + seconds / 2) % seconds) - seconds / 2
        x -= rng.uniform(0.6, 1.2) * np.exp(-0.5 * (d / 700.0) ** 2)
    x += rng.normal(0, 0.02, seconds)
    return x.astype(np.float32)


def generate_glucose(profile: str, seed: int, seconds: int = 3600) -> np.ndarray:
    """CGM interstitial glucose mg/dL (float32) with a periodic meal excursion."""
    rng = np.random.default_rng(seed)
    base = {"normal": rng.uniform(85, 110), "prediabetic": rng.uniform(105, 135),
            "diabetic": rng.uniform(140, 230), "hypo_risk": rng.uniform(62, 85),
            "hyper": rng.uniform(250, 340)}[profile]
    t = np.arange(seconds)
    # gentle within-hour variation only; meal excursions are added by the multi-day model at runtime
    x = base + _periodic(rng, seconds, [rng.uniform(2, 6), rng.uniform(1, 3), 1.5, 1, 0.8, 0.6, 0.5])
    x += rng.normal(0, 0.8, seconds)
    return np.clip(x, 35, 500).astype(np.float32)
