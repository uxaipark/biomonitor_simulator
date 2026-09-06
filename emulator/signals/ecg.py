"""ECG beat morphology and fast wrap-around rendering.

A beat is described relative to its R-peak time as a list of asymmetric
Gaussian waves (P, Q, R, S, T, extras).  Rendering uses modular indexing so a
loop of exactly N samples is seamless: the last T wave wraps into the start.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# wave kinds are used to pick a rendering window width
# (center_s, amplitude_mV, sigma_left_s, sigma_right_s)
Wave = tuple[float, float, float, float]


@dataclass
class Morphology:
    """Per-patient (per-variant) morphology 'personality'."""
    r_amp: float = 1.2
    q_amp: float = -0.1
    s_amp: float = -0.25
    p_amp: float = 0.15
    t_amp: float = 0.30
    r_sigma: float = 0.012
    p_sigma: float = 0.022
    t_sigma_l: float = 0.055
    t_sigma_r: float = 0.038
    pr: float = 0.16          # P centre to R (seconds)
    qtc: float = 0.40
    st_offset: float = 0.0    # + elevation / - depression (mV)
    t_polarity: float = 1.0
    u_amp: float = 0.0
    bbb: str = ""             # "", "lbbb", "rbbb"
    edr_gain: float = 0.08    # respiratory amplitude modulation of QRS
    extra: dict = field(default_factory=dict)

    @staticmethod
    def random(rng: np.random.Generator, age: int = 60, sex: str = "M") -> "Morphology":
        m = Morphology()
        m.r_amp = float(rng.uniform(0.7, 1.9)) * (1.1 if sex == "M" else 0.95)
        m.q_amp = float(-rng.uniform(0.03, 0.15))
        m.s_amp = float(-rng.uniform(0.08, 0.45))
        m.p_amp = float(rng.uniform(0.08, 0.22))
        m.t_amp = float(rng.uniform(0.15, 0.45))
        m.r_sigma = float(rng.uniform(0.010, 0.014))
        m.p_sigma = float(rng.uniform(0.019, 0.026))
        m.t_sigma_l = float(rng.uniform(0.045, 0.065))
        m.t_sigma_r = float(rng.uniform(0.030, 0.045))
        m.pr = float(rng.uniform(0.14, 0.19))
        m.qtc = float(rng.uniform(0.38, 0.44)) + (0.01 if sex == "F" else 0.0)
        m.u_amp = float(rng.uniform(0.0, 0.04)) if rng.random() < 0.3 else 0.0
        m.edr_gain = float(rng.uniform(0.05, 0.12))
        return m


def beat_waves(m: Morphology, rr_prev: float, kind: str = "N", has_p: bool = True) -> list[Wave]:
    """Return waves for one beat.  kind: N, A(PAC), V(PVC), Vp(paced vent),
    AVp(dual paced), J(junctional escape), E(vent escape), P(p-only), S(SVT beat)."""
    qt = m.qtc * math.sqrt(max(0.3, min(1.6, rr_prev)))
    t_c = -0.04 + 0.72 * qt            # T peak relative to R
    waves: list[Wave] = []
    if kind == "P":                    # non-conducted P wave (AV block)
        return [(0.0, m.p_amp, m.p_sigma, m.p_sigma * 1.1)]
    if kind == "Sp":                   # pacing spike without capture: no waves
        return []
    if kind == "CRT":                  # biventricular paced: intermediate width, mostly positive, small notch
        if has_p:
            waves.append((-m.pr, m.p_amp * 0.9, m.p_sigma, m.p_sigma))
        waves.append((-0.008, 0.9 * m.r_amp, 0.017, 0.012))
        waves.append((0.018, 0.55 * m.r_amp, 0.010, 0.018))
        waves.append((0.055, -0.35 * m.r_amp, 0.014, 0.016))
        waves.append((t_c + 0.02, -0.5 * abs(m.t_amp), 0.06, 0.045))
        return waves
    if kind == "F":                    # fusion beat: between intrinsic and paced morphology
        waves.append((-m.pr, m.p_amp * 0.7, m.p_sigma, m.p_sigma))
        waves.append((-0.02, 0.45 * m.r_amp, 0.010, 0.010))
        waves.append((0.008, 0.85 * m.r_amp, 0.018, 0.020))
        waves.append((0.05, -0.35 * m.r_amp, 0.016, 0.018))
        waves.append((t_c, -0.25 * abs(m.t_amp), 0.06, 0.045))
        return waves
    if kind == "AsVp":                 # intrinsic P sensed, ventricle paced (P-synchronous)
        kind = "AVp"
    if kind in ("V", "E"):             # wide, bizarre; discordant T
        pol = m.extra.get("pvc_pol", 1.0)
        waves.append((-0.02, 0.35 * pol * m.r_amp, 0.020, 0.018))
        waves.append((0.02, 1.35 * pol * m.r_amp, 0.028, 0.030))
        waves.append((0.075, -0.55 * pol * m.r_amp, 0.020, 0.022))
        waves.append((0.30, -0.55 * pol * m.r_amp * 0.6, 0.075, 0.055))
        return waves
    if kind in ("Vp", "AVp"):          # paced ventricular (RV apical, LBBB-like) beat
        if kind == "AVp" and has_p:
            waves.append((-m.pr, m.p_amp * 0.9, m.p_sigma, m.p_sigma))
        waves.append((-0.005, 1.1 * m.r_amp, 0.024, 0.026))
        waves.append((0.06, -0.45 * m.r_amp, 0.020, 0.022))
        waves.append((0.30, -0.35 * m.r_amp, 0.070, 0.050))
        return waves
    # --- supraventricular family: P (unless J/S) + narrow QRS + T
    if kind == "A":                    # PAC: abnormal, earlier & peaked P
        waves.append((-m.pr * 0.85, m.p_amp * 1.3 * (1 if m.extra.get("pac_p", 1) > 0 else -1), m.p_sigma * 0.8, m.p_sigma * 0.8))
    elif kind == "N" and has_p:
        waves.append((-m.pr, m.p_amp, m.p_sigma, m.p_sigma * 1.1))
    elif kind == "Ap":                 # atrial paced: P slightly different, then intrinsic narrow QRS
        waves.append((-m.pr, m.p_amp * 0.85, m.p_sigma * 0.9, m.p_sigma))
    elif kind == "J":                  # junctional: retrograde P hidden / inverted after QRS
        waves.append((0.09, -m.p_amp * 0.5, m.p_sigma * 0.8, m.p_sigma * 0.8))
    # QRS
    if m.bbb == "lbbb":
        waves.append((-0.012, 0.75 * m.r_amp, 0.020, 0.014))
        waves.append((0.022, 0.80 * m.r_amp, 0.016, 0.022))
        waves.append((0.062, -0.30 * m.r_amp, 0.016, 0.018))
        waves.append((t_c + 0.03, -abs(m.t_amp) * 0.9, m.t_sigma_l, m.t_sigma_r))
        return waves
    waves.append((-0.033, m.q_amp, 0.0075, 0.0075))
    waves.append((0.0, m.r_amp, m.r_sigma, m.r_sigma))
    if m.bbb == "rbbb":
        waves.append((0.028, m.s_amp * 1.2, 0.012, 0.014))
        waves.append((0.062, 0.45 * m.r_amp, 0.014, 0.016))   # R'
        waves.append((t_c + 0.02, -abs(m.t_amp) * 0.6, m.t_sigma_l, m.t_sigma_r))
        return waves
    waves.append((0.026, m.s_amp, 0.0085, 0.0095))
    # ST segment offset (elevation/depression), merged into T
    if abs(m.st_offset) > 1e-4:
        waves.append((0.10, m.st_offset, 0.045, 0.080))
    # T wave
    waves.append((t_c, m.t_amp * m.t_polarity, m.t_sigma_l, m.t_sigma_r))
    if m.u_amp > 0:
        waves.append((t_c + 0.18, m.u_amp, 0.04, 0.04))
    return waves


def render_waves(buf: np.ndarray, fs: int, centers_s: np.ndarray, amps: np.ndarray,
                 sig_l: np.ndarray, sig_r: np.ndarray, nsig: float = 3.5) -> None:
    """Add asymmetric Gaussians into circular buffer `buf` (float32, length N)."""
    n = buf.shape[0]
    if centers_s.size == 0:
        return
    # group by window size to keep matrices small
    half = np.ceil(nsig * np.maximum(sig_l, sig_r) * fs).astype(np.int64)
    order = np.argsort(half)
    centers_s, amps, sig_l, sig_r, half = (a[order] for a in (centers_s, amps, sig_l, sig_r, half))
    # bucket into <= 6 window classes
    edges = np.unique(np.quantile(half, [0, 0.2, 0.4, 0.6, 0.8, 1.0])).astype(np.int64)
    start = 0
    for w in edges:
        end = int(np.searchsorted(half, w, side="right"))
        if end <= start:
            continue
        c = centers_s[start:end]
        a = amps[start:end]
        sl = sig_l[start:end]
        sr = sig_r[start:end]
        ci = np.rint(c * fs).astype(np.int64)
        frac = c - ci / fs
        offs = np.arange(-w, w + 1, dtype=np.int64)
        t = (offs[None, :] / fs) - frac[:, None]          # time rel. to centre
        sig = np.where(t < 0, sl[:, None], sr[:, None])
        vals = a[:, None] * np.exp(-0.5 * (t / sig) ** 2)
        idx = (ci[:, None] + offs[None, :]) % n
        np.add.at(buf, idx.ravel(), vals.ravel().astype(np.float32))
        start = end


def periodic_wander(n: int, fs: int, rng: np.random.Generator, amp_mv: float,
                    fmin: float = 0.05, fmax: float = 0.4, k: int = 6) -> np.ndarray:
    """Baseline wander as a sum of sinusoids whose periods divide the loop length."""
    dur = n / fs
    out = np.zeros(n, dtype=np.float32)
    t = np.arange(n, dtype=np.float32) / fs
    for _ in range(k):
        cycles = max(1, int(round(rng.uniform(fmin, fmax) * dur)))
        f = cycles / dur
        out += (amp_mv * rng.uniform(0.3, 1.0) / k) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)).astype(np.float32)
    return out


def powerline(n: int, fs: int, amp_mv: float, hz: float = 60.0, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / fs
    return (amp_mv * np.sin(2 * np.pi * hz * t + phase)).astype(np.float32)


def colored_noise(n: int, rng: np.random.Generator, amp: float, smooth: int = 3) -> np.ndarray:
    """Cheap EMG-like noise: white noise smoothed with a short moving average (circular)."""
    w = rng.standard_normal(n).astype(np.float32)
    if smooth > 1:
        k = np.ones(smooth, dtype=np.float32) / smooth
        w = np.convolve(np.concatenate([w[-smooth:], w, w[:smooth]]), k, mode="same")[smooth:-smooth]
    return (amp * w).astype(np.float32)


def circular_smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return x
    ker = np.ones(k, dtype=np.float64) / k
    pad = np.concatenate([x[-k:], x, x[:k]])
    return np.convolve(pad, ker, mode="same")[k:-k].astype(x.dtype)
