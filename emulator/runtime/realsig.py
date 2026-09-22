"""실제 시그널 송출: 에뮬레이터 로컬의 ATF / CSV 심전도 파일을 환자(슬롯)별로 읽어 끝없이 반복 전송한다.

슬롯 i(0..19)는 재원 입원 환자를 병상 순으로 늘어놓은 i번째 환자다.  파일은 목표 표본율(signals.ecg_fs)로
리샘플해 µV int16 배열(.npy)과 초당 HR 배열로 굽고, 워커는 realsig_map.json 을 보고 그 환자의 ECG·HR 을 파일
값으로 바꾼다(파일 끝에서 처음으로 돌아감).  파일이 없거나 읽지 못한 슬롯은 '자동 생성'이 켜져 있으면 여러
부정맥을 차례로 돌며 바꿔 준다.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import io
import itertools
import json
import os
import re
import threading
import time
import warnings
from pathlib import Path

import numpy as np

from ..config import BASE_DIR
from .fieldtest import EVENTS as FT_EVENTS

N_SLOTS = 20
RUNTIME_DIR = BASE_DIR / "runtime"
MAP_PATH = RUNTIME_DIR / "realsig_map.json"
BAKE_DIR = RUNTIME_DIR / "realsig"
EXTS = (".atf", ".csv", ".txt", ".tsv")
# 읽기는 메인 프로세스(월드·API)에서 한다: 신호 한 열만 C 파서로 묶음씩 읽고 리샘플 · R파 검출까지 해도 최고 메모리가
# 보통 파일 크기의 2배, 값만 한 열로 촘촘한 파일은 8배쯤이다 — 16 GB 장비에서 월드를 밀어내지 않을 선.
MAX_BYTES = 256 * 1024 * 1024
CHUNK_LINES = 50_000          # 한 번에 C 파서로 넘기는 줄 수 (GIL 을 오래 잡지 않도록)
SAMPLE_LINES = 20_000         # 열 고르기 · 표본율 추정에 쓰는 앞부분 줄 수
STABLE_S = 3.0                # 파일이 이만큼 바뀌지 않아야 굽는다 (복사 · 녹화 중인 파일을 매초 다시 읽지 않도록)
ASYS_S = 5.0                  # 마지막 R파 뒤 이만큼 박동이 없으면 HR 0 (무수축)
# 자동 생성 순환: 페이싱 리듬은 뺀다 (슬롯 환자는 페이싱 스파이크를 끈다)
CYCLE = ["nsr", "afib", "pvc_bigeminy", "avb2_m1", "sinus_brady", "vt", "aflutter", "pvc", "avb3", "svt", "sinus_tachy", "nsvt",
         "lbbb", "afib_rvr", "stemi", "pac", "avb2_m2", "sinus_pause", "rbbb", "ischemia", "avb1", "vfib"]
# 파형 전용 폴더: 에뮬레이터(서비스 계정)가 읽고 쓰는 곳.  업로드도 여기로, 슬롯에는 파일 이름만 저장한다.
WAVE_DIR = BASE_DIR / "waveforms"


# ============================================================================ 파일 읽기
_UNIT = {"v": 1e6, "mv": 1e3, "uv": 1.0, "µv": 1.0, "μv": 1.0, "nv": 1e-3}


def _unit_of(title: str) -> float | None:
    m = re.search(r"\(([^)]*)\)|\[([^\]]*)\]", title or "")
    u = (m.group(1) or m.group(2)).strip().lower() if m else ""
    return _UNIT.get(u)


_MS = {"ms", "msec", "msecs", "millisec", "millisecs", "millisecond", "milliseconds"}
_US = {"us", "µs", "μs", "usec", "usecs", "microsec", "microsecs", "microsecond", "microseconds"}


def _time_scale(title: str) -> float:
    toks = set(re.findall(r"[a-zµμ]+", (title or "").lower()))       # 'Time (ms)' · 'time_ms' · 'Time (msec)' · 't [µs]'
    if toks & _MS:
        return 1e-3
    if toks & _US:
        return 1e-6
    return 1.0


def _is_time(title: str) -> bool:
    return bool(re.match(r"\s*[\"']?\s*(time|t|sec|secs|seconds|ms|msec|timestamp|elapsed)(\b|_)", (title or "").lower()))


_CLOCK = re.compile(r"^\d+(?::\d{1,2}){1,2}(?:\.\d*)?$")             # PhysioBank 'm:ss.mmm' · 'h:mm:ss.mmm'


def _clock_s(s) -> float:
    """'0:00.003' · '1:02:03.5' → 초."""
    if isinstance(s, bytes):
        s = s.decode("ascii", "replace")
    try:
        v = 0.0
        for part in s.strip().strip("'\"").split(":"):
            v = v * 60.0 + float(part)
        return v
    except ValueError:
        return float("nan")


def _is_num(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return bool(_CLOCK.match(x))


def _fields(line: str, delim: str | None, dec: bool) -> list[str]:
    """데이터 줄 → 칸 (따옴표 제거, 소수점 쉼표는 점으로).  delim None = 공백 구분."""
    s = line.replace('"', "").replace("'", "")
    if dec:
        s = s.replace(",", ".")
    return [x.strip() for x in (s.split(delim) if delim else s.split())]


# (구분자, 소수점 쉼표): 같은 점수면 앞의 것
_DELIMS = (("\t", False), ("\t", True), (";", False), (";", True), (",", False), (None, False), (None, True))


def _sniff_delim(lines: list[str]) -> tuple[str | None, bool]:
    """숫자 칸은 +1, 숫자가 아닌 칸은 -1 로 세어 가장 잘 쪼개지는 구분자.  '0,0020;0,1234' 는 ';' + 소수점 쉼표
    (쉼표로 자르면 '0020;0' 같은 칸이 생겨 점수가 낮다)."""
    def score(d, dec):
        return sum((1 if _is_num(x) else -1) for l in lines for x in _fields(l, d, dec) if x)
    return max(_DELIMS, key=lambda dd: score(*dd))


def _pick_signal(titles: list[str], cols: list[int]) -> int:
    pref = ("ecg", "ii", "mlii", "lead ii", "lead_ii", "i", "v1", "v5", "trace", "signal", "value")
    for p in pref:
        for c in cols:
            if re.search(rf"(^|[^a-z]){re.escape(p)}([^a-z]|$)", titles[c].lower() if c < len(titles) else ""):
                return c
    return cols[0]


def _hint_fs(name: str) -> float | None:
    m = re.search(r"(\d{2,5})\s*hz", name.lower())
    return float(m.group(1)) if m else None


def _sniff(path: Path) -> dict:
    """앞부분만 읽어 형식을 정한다: 열 제목, 구분자, 소수점 쉼표, 데이터가 시작하는 줄, 열 수, 시각 문자열 열, 머리 정보."""
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"파일이 너무 큽니다 ({size // 1_000_000} MB, 최대 {MAX_BYTES // 1_000_000} MB)")
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        head = list(itertools.islice(f, 400))
    info: dict = {}
    titles: list[str] = []
    if head and head[0].startswith("ATF"):                            # Axon Text File
        try:
            n_hdr = int(re.split(r"[\t ,]+", head[1].strip())[0])
        except (IndexError, ValueError):
            raise ValueError("ATF 머리를 읽지 못했습니다") from None
        if len(head) < 3 + n_hdr:
            raise ValueError("숫자 데이터가 없습니다 (ATF 머리만 있습니다)")
        for h in head[2:2 + n_hdr]:
            h = h.strip().strip('"')
            if "=" in h:
                k, v = h.split("=", 1); info[k.strip()] = v.strip()
        titles = [t.strip().strip('"') for t in head[2 + n_hdr].rstrip("\r\n").split("\t")]
        delim, dec, skip = "\t", False, 3 + n_hdr
    else:
        idx = [i for i, l in enumerate(head) if l.strip() and not l.lstrip().startswith("#")]
        cand = [head[i] for i in idx[:24]]
        delim, dec = _sniff_delim(cand[2:] if len(cand) > 4 else cand)    # 앞 두 줄은 제목 · 단위 줄일 수 있다
        k = 0
        if cand and not all(_is_num(x) for x in _fields(cand[0], delim, dec) if x):
            row = next(csv.reader([cand[0].rstrip("\r\n")], delimiter=delim)) if delim else cand[0].split()
            titles = [t.strip().strip("'\"").strip() for t in row]
            k = 1
            if len(cand) > 1 and cand[1].lstrip()[:1] in ("'", '"'):
                units = _fields(cand[1], delim, False)
                if not any(_is_num(u) for u in units if u):           # MIT-BIH 내보내기의 단위 줄 ('seconds', 'mV')
                    titles += [f"col{i}" for i in range(len(titles), len(units))]
                    titles = [f"{t} ({units[i]})" if i < len(units) and units[i] else t for i, t in enumerate(titles)]
                    k = 2
        skip = idx[k] if k < len(idx) else len(head)
    rows = [_fields(l, delim, dec) for l in head[skip:] if l.strip() and not l.lstrip().startswith("#")][:50]
    counts = [len(r) - next((i for i, x in enumerate(reversed(r)) if x), len(r)) for r in rows]   # 줄 끝 구분자의 빈 칸은 빼고
    ncol = max(collections.Counter(counts).most_common(1)[0][0] if counts else len(titles), 1)
    clock = {c for c in range(ncol) if any(c < len(r) and r[c] for r in rows)
             and all(_CLOCK.match(r[c]) for r in rows if c < len(r) and r[c])}
    return {"titles": titles, "delim": delim, "dec": dec, "skip": skip, "ncol": ncol, "clock": clock, "info": info}


def _load(path: Path, sn: dict, usecols: list[int], max_lines: int | None = None) -> np.ndarray:
    """데이터 줄을 CHUNK_LINES 씩 C 파서(np.loadtxt)로 읽어 usecols 열만 [n, len(usecols)] 로 모은다.  숫자가 아닌 칸이나
    열이 모자란 줄이 섞인 묶음만 너그러운 np.genfromtxt 로 다시 읽는다(숫자 아닌 칸은 NaN, 열이 모자란 줄은 버림).
    따옴표는 떼고, 소수점 쉼표는 점으로 바꾼다.  파일 전체를 문자열 · 파이썬 리스트로 들고 있지 않는다."""
    conv = {c: _clock_s for c in usecols if c in sn["clock"]} or None
    out: list[np.ndarray] = []
    n = 0
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        collections.deque(itertools.islice(f, sn["skip"]), maxlen=0)
        while max_lines is None or n < max_lines:
            raw = list(itertools.islice(f, CHUNK_LINES if max_lines is None else min(CHUNK_LINES, max_lines - n)))
            if not raw:
                break
            n += len(raw)
            text = "".join(raw).replace('"', "").replace("'", "")
            if sn["dec"]:
                text = text.replace(",", ".")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")                          # 빈 묶음 · 건너뛴 줄 경고
                try:
                    a = np.loadtxt(io.StringIO(text), delimiter=sn["delim"], usecols=usecols, converters=conv, dtype=np.float64,
                                   ndmin=2, comments="#")
                except (ValueError, IndexError):
                    a = np.genfromtxt(io.StringIO(text), delimiter=sn["delim"], usecols=usecols, converters=conv, dtype=np.float64,
                                      invalid_raise=False, comments="#").reshape(-1, len(usecols))
            out.append(a)
    return np.concatenate(out) if out else np.zeros((0, len(usecols)))


def _rate(t: np.ndarray) -> float | None:
    """시간 열 → 표본율.  간격의 중앙값이 아니라 구간 전체(표본 수 / 걸린 시간)로 구한다 — 0.000, 0.003, 0.006, 0.008 처럼
    ms 로 반올림된 시간(360 Hz 파일)에서도 맞다.  시간이 되돌아가거나 크게 건너뛰는 곳에서 구간을 끊는다."""
    i = np.flatnonzero(np.isfinite(t))
    if i.size < 10:
        return None
    tv = t[i]
    d = np.diff(tv)
    pos = d[d > 0]
    if pos.size == 0:
        return None
    bad = np.flatnonzero((d < 0) | (d > 10 * np.median(pos)))
    if bad.size:
        i, tv = i[:bad[0] + 1], tv[:bad[0] + 1]
    if i.size < 10 or tv[-1] <= tv[0]:
        return None
    return float((i[-1] - i[0]) / (tv[-1] - tv[0]))


def parse_file(path: str, target_fs: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """파일 → (µV int16 @target_fs, 초당 HR uint8, 정보).  HR 배열이 비어 있으면 박동을 찾지 못한 것(HR 은 덮어쓰지 않음)."""
    p = Path(path)
    if p.suffix.lower() not in EXTS:
        raise ValueError(f"지원하지 않는 형식 ({p.suffix}); ATF · CSV · TSV · TXT")
    sn = _sniff(p)
    ncol = sn["ncol"]
    head = _load(p, sn, list(range(ncol)), SAMPLE_LINES)              # 앞부분 전체 열: 열 고르기 · 표본율
    head = head[~np.all(np.isnan(head), axis=1)]
    if head.shape[0] < 50:
        raise ValueError("숫자 데이터가 없습니다 (심전도 값 열이 있는 ATF/CSV 인지 확인하세요)")
    titles = sn["titles"] + [f"col{i}" for i in range(len(sn["titles"]), ncol)]
    cols = [c for c in range(ncol) if np.count_nonzero(np.isfinite(head[:, c])) >= 0.5 * head.shape[0]]   # 숫자 열만
    fs = None
    tcol = next((c for c in cols if _is_time(titles[c])), None)
    named = tcol is not None
    if tcol is None and len(cols) >= 2 and cols[0] == 0:              # 제목이 없어도 첫 열이 고르게 늘면 시간 열 (반올림된 시간 포함)
        d = np.diff(head[:2000, 0])
        d = d[np.isfinite(d)]
        if d.size >= 10 and np.all(d >= 0) and d.mean() > 0 and d.max() <= 3 * d.mean():
            tcol = 0
    if tcol is not None:
        sc = _time_scale(titles[tcol])
        fs = _rate(head[:, tcol] * sc)
        secs_unit = {"s", "sec", "secs", "second", "seconds"} & set(re.findall(r"[a-z]+", titles[tcol].lower()))
        if named and sc == 1.0 and not secs_unit and fs and fs < 20 and 20 <= fs * 1000 <= 100_000:
            fs *= 1000.0                                               # 단위 없는 'Time' 열에 ms 값
        cols = [c for c in cols if c != tcol]
    if not cols:
        raise ValueError("신호 열이 없습니다")
    fs_src = "시간 열"
    if not fs or not (20 <= fs <= 100_000):
        fs = _hint_fs(p.name) or next((float(v) for k, v in sn["info"].items() if "rate" in k.lower() and re.match(r"^[\d.]+$", v)), None)
        fs_src = "파일 이름/머리" if fs else f"가정({target_fs} Hz)"
        fs = fs or float(target_fs)
    c = _pick_signal(titles, cols)
    x = _load(p, sn, [c])[:, 0]                                       # 신호 한 열만 끝까지
    fin = np.isfinite(x)
    if not fin.all():                                                 # 앞뒤 꼬리말은 버리고 가운데 빈 값은 잇기
        ok = np.flatnonzero(fin)
        if ok.size < 50:
            raise ValueError("신호 값이 비어 있습니다")
        x = np.interp(np.arange(ok[-1] - ok[0] + 1), ok - ok[0], x[ok])
    elif x.size < 50:
        raise ValueError("신호 값이 비어 있습니다")
    del fin
    scale = _unit_of(titles[c])
    unit_src = "열 제목"
    if scale is None:                                                 # 단위 추정: 진폭 범위로
        p2p = float(np.percentile(x, 99.5) - np.percentile(x, 0.5))
        scale, unit_src = ((1e3, "추정(mV)") if p2p < 20 else (1.0, "추정(µV)")) if p2p > 1e-6 else (1e6, "추정(V)")
        if p2p < 0.02:
            scale, unit_src = 1e6, "추정(V)"
    x = x * scale
    x -= np.median(x)
    n_out = int(round(len(x) * target_fs / fs))
    if n_out < target_fs:
        raise ValueError(f"너무 짧습니다 ({len(x) / fs:.2f} 초)")
    xo = x if n_out == len(x) else np.interp(np.arange(n_out) * (fs / target_fs), np.arange(len(x)), x)
    del x
    ecg = np.clip(np.rint(xo), -32000, 32000).astype(np.int16)
    hr = _hr_series(xo, target_fs)
    if not (hr > 0).any() and float(np.percentile(xo, 99.5) - np.percentile(xo, 0.5)) >= 200:
        hr = np.zeros(0, dtype=np.uint8)                              # 박동을 못 찾았는데 평탄선(무수축)도 아님 → HR 은 덮어쓰지 않음
    return ecg, hr, {"title": titles[c], "fs_in": round(float(fs), 2), "fs_src": fs_src, "unit": unit_src, "seconds": round(n_out / target_fs, 1),
                     "hr_mean": int(np.median(hr[hr > 0])) if (hr > 0).any() else 0}


def _hr_series(x: np.ndarray, fs: int) -> np.ndarray:
    """간이 R파 검출(미분 제곱 이동적분 + 적응 문턱 + 불응기) → 초당 HR.
    · 파일은 끝에서 처음으로 이어 되풀이 재생되므로 앞뒤에 반대쪽 끝을 10초씩(짧은 파일은 여러 번 되풀이해) 붙여 검출한다 —
      몇 초짜리 파일도 되풀이 재생될 때의 HR 이 나온다.
    · 문턱은 2초 창마다 그 창의 에너지로 정하되 기록 전체의 QRS 에너지 수준(창별 99백분위의 75백분위)의 20 % 아래로는
      내리지 않고, 진폭이 150 µV 가 안 되는 봉우리는 R파로 치지 않는다 — 박동 없는 창(휴지 · 무수축 · 고도 방실차단)에서
      문턱이 잡음 수준까지 내려가 잡음을 R파로 세면 빈맥으로 거꾸로 나온다.
    · 마지막 R파 뒤로 평소 RR 보다 오래 비면 그 간격만큼 HR 을 낮추고, ASYS_S 이상 비면 0."""
    n = len(x); secs = max(1, n // fs)
    m = 10 * fs
    x = x.astype(np.float32)
    xx = np.concatenate((x[n - m:], x, x[:m])) if n >= m else x[np.arange(-m, n + m) % n]
    del x
    k = max(1, int(0.03 * fs))
    base = np.convolve(xx, np.full(k * 10, 1.0 / (k * 10), dtype=np.float32), mode="same")
    y = xx - base
    del xx, base
    d = np.diff(y, prepend=y[0])
    e = np.convolve(d * d, np.ones(max(1, int(0.12 * fs)), dtype=np.float32), mode="same")
    del d
    win, refr, half = 2 * fs, int(0.25 * fs), int(0.1 * fs)
    nf = len(e) // win
    lv = np.percentile(e[:nf * win].reshape(nf, win), 99, axis=1) if nf else np.zeros(0, dtype=np.float32)
    if len(e) > nf * win:
        lv = np.append(lv, np.percentile(e[nf * win:], 99))
    thr = np.maximum(0.35 * lv, 0.2 * np.percentile(lv, 75)).astype(np.float32)
    above = np.flatnonzero(e > np.repeat(thr, win)[:len(e)])
    peaks: list[int] = []
    i = 0
    while i < above.size:
        a = int(above[i])
        j = a + int(np.argmax(e[a:a + refr]))
        seg = y[max(0, j - half):j + half]
        if float(seg.max() - seg.min()) >= 150.0:
            peaks.append(j)
            i = int(np.searchsorted(above, j + refr))                 # 불응기 뒤의 첫 문턱 초과 표본
        else:
            i = int(np.searchsorted(above, j + refr // 2))
    hr = np.zeros(secs, dtype=np.uint8)
    if len(peaks) < 3:
        return hr
    pk = np.asarray(peaks, dtype=np.int64) - m                        # 원래 파일 좌표 (앞 여백은 음수)
    inst = np.clip(60.0 * fs / np.diff(pk), 20, 250)
    tc = (np.arange(secs) + 0.5) * fs
    h = np.interp(tc, pk[1:], inst)
    li = np.searchsorted(pk, tc, side="right") - 1
    gap = np.where(li >= 0, (tc - pk[np.clip(li, 0, None)]) / fs, np.inf)
    h = np.minimum(h, 60.0 / np.maximum(gap, 1e-3))                   # 마지막 박동 뒤로 오래 비면 그만큼 느리게
    h = np.where(gap >= ASYS_S, 0.0, np.clip(h, 20, 250))
    return np.rint(h).astype(np.uint8)


# ============================================================================ 파형 전용 폴더
_NAME_OK = re.compile(r"[^\w.\- ()\[\]가-힣]")


def safe_name(name: str) -> str:
    """업로드 파일 이름: 경로 성분 제거, 허용 문자만, 확장자 검사."""
    n = os.path.basename((name or "").replace("\\", "/")).strip()
    n = _NAME_OK.sub("_", n)[:120].lstrip(".")
    if not n or os.path.splitext(n)[1].lower() not in EXTS:
        raise ValueError(f"지원하지 않는 형식입니다 (ATF · CSV · TSV · TXT): {name}")
    return n


def resolve(name: str) -> Path | None:
    """슬롯 값(파일 이름, 또는 예전 설정의 전체 경로) → 파형 폴더 안의 실제 경로.  폴더 밖이면 None."""
    if not name:
        return None
    p = Path(name) if os.path.isabs(name) else WAVE_DIR / name
    try:
        rp = p.resolve()
        rp.relative_to(WAVE_DIR.resolve())
    except (ValueError, OSError):
        return None
    return rp


def list_waves() -> list[dict]:
    WAVE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for e in sorted(os.scandir(WAVE_DIR), key=lambda e: e.name.lower()):
        if e.is_file() and e.name.lower().endswith(EXTS) and not e.name.startswith("."):
            st = e.stat()
            out.append({"name": e.name, "bytes": st.st_size, "mtime": st.st_mtime})
    return out


# ============================================================================ 월드 쪽 관리
class RealSignal:
    def __init__(self, world):
        self.w = world
        self.cache: dict[str, dict] = {}          # key(path|mtime|size|fs) -> {ok, npy, hr, info | error}
        self.busy: set[str] = set()               # 굽는 중인 path|fs (파일 하나에 굽기 하나)
        self.last: dict[str, str] = {}            # path|fs -> 마지막으로 다 구운 key (새 판을 굽는 동안에도 이것을 쓴다)
        self.seen: dict[str, tuple] = {}          # path|fs -> (key, 처음 본 시각): 파일이 멈췄는지 판단
        self.trash: list[tuple] = []              # (지울 시각, [npy 경로]) — 워커가 새 지도를 읽은 뒤에 지운다
        self.t0: dict[int, tuple] = {}            # row -> (pid, npy, 재생 시작 tick): 재생 위치 = (tick - t0) * spt
        self.slot_rows: list[int | None] = [None] * N_SLOTS
        self.slot_pid: list[int | None] = [None] * N_SLOTS
        self.cycling: dict[int, dict] = {}        # pid -> {i, next, rhythm, base_variant, paced}
        self.map_sig = None
        self._t = 0.0
        BAKE_DIR.mkdir(parents=True, exist_ok=True)
        self._write_map({})
        for f in BAKE_DIR.iterdir():                                    # 이전 실행 · 이전 월드가 구운 파일 (캐시는 메모리에만 있다)
            if f.name.endswith((".npy", ".tmp")):
                try:
                    f.unlink()
                except OSError:
                    pass

    def cfg(self) -> dict:
        return self.w.cfg.get("scenario", "realsig", default={}) or {}

    # ---------------------------------------------------------------- 굽기
    def _bake(self, key: str, pk: str, path: str, fs: int) -> None:
        try:
            ecg, hr, info = parse_file(path, fs)
            h = hashlib.sha1(key.encode()).hexdigest()[:12]
            npy, hrp = BAKE_DIR / f"{h}.ecg.npy", BAKE_DIR / f"{h}.hr.npy"
            for dst, arr in ((npy, ecg), (hrp, hr)):                   # 새 inode 로 바꿔 끼운다: 워커가 mmap 한 이전 파일을 덮어쓰지 않도록
                tmp = dst.with_name(f".{dst.name}.tmp")
                with open(tmp, "wb") as f:
                    np.save(f, arr)
                os.replace(tmp, dst)
            self.cache[key] = {"ok": True, "npy": str(npy), "hr": str(hrp), "info": info}
            self.w.log.add("script", f"실제 시그널: {os.path.basename(path)} 읽음 — {info['seconds']} 초, 입력 {info['fs_in']} Hz({info['fs_src']}), "
                                     f"단위 {info['unit']}, 평균 HR {info['hr_mean'] if hr.size else '검출 못 함(HR 은 덮어쓰지 않음)'}")
        except Exception as e:
            self.cache[key] = {"ok": False, "error": str(e)[:200]}
            self.w.log.add("script", f"실제 시그널: {os.path.basename(path)} 읽기 실패 — {str(e)[:120]}")
        finally:
            self.busy.discard(pk)

    def _evict(self, pk: str, keep: str) -> None:
        """같은 파일의 이전 판(내용 · 표본율이 바뀌기 전) 캐시를 버리고 구운 파일은 잠시 뒤에 지운다."""
        path = pk.rsplit("|", 1)[0]
        for k in [k for k in list(self.cache) if k != keep and k.rsplit("|", 3)[0] == path]:
            c = self.cache.pop(k, None)
            if c and c.get("ok"):
                self.trash.append((time.time() + 10.0, [c["npy"], c["hr"]]))

    def slot_state(self, name: str, fs: int) -> dict:
        if not name:
            return {"state": "empty"}
        rp = resolve(name)
        if rp is None:
            return {"state": "error", "error": "파형 폴더 밖의 파일입니다 — 파형 폴더에 올려서 고르세요"}
        path = str(rp)
        try:
            st = os.stat(path)
        except OSError:
            return {"state": "error", "error": "파일이 없습니다"}
        pk = f"{path}|{fs}"
        key = f"{path}|{st.st_mtime_ns}|{st.st_size}|{fs}"
        c = self.cache.get(key)
        if c is None:
            now = time.time()
            seen = self.seen.get(pk)
            if seen is None or seen[0] != key:
                seen = self.seen[pk] = (key, now)
            stable = now - st.st_mtime >= STABLE_S or now - seen[1] >= STABLE_S     # 복사 · 녹화 중인 파일은 멈출 때까지 기다린다
            if stable and pk not in self.busy:
                self.busy.add(pk)
                threading.Thread(target=self._bake, args=(key, pk, path, fs), daemon=True, name="realsig-bake").start()
            c = self.cache.get(self.last.get(pk, ""))                   # 새 판을 기다리는 동안은 이전 판을 계속 튼다
            if c is None or not c["ok"]:
                return {"state": "loading"}
        elif self.last.get(pk) != key:
            self.last[pk] = key
            self._evict(pk, key)
        if not c["ok"]:
            return {"state": "error", "error": c["error"]}
        return {"state": "ready", "npy": c["npy"], "hr": c["hr"], "info": c["info"]}

    # ---------------------------------------------------------------- 매 초
    def _ft_pids(self) -> set[int]:
        """리듬을 바꾸는 현장 테스트(부정맥 · 무수축) 중인 환자: 테스트가 우선이라 파일 재생 · 순환을 멈춘다.
        배터리 · SpO2 · 링크 테스트는 리듬과 상관없어 파일을 계속 틀고, 리드 오프는 워커가 파일보다 먼저 레일로 보낸다."""
        ft = getattr(self.w, "ft", None)
        return {a["pid"] for a in getattr(ft, "active", []) if a.get("pid") is not None
                and (FT_EVENTS.get(a["ev"], {}).get("rhythm") or a["ev"] == "asystole")}

    def step(self) -> None:
        now = time.time()
        if now - self._t < 1.0:
            return
        self._t = now
        w = self.w
        c = self.cfg()
        if not c.get("enabled"):
            if self.cycling or self.map_sig:
                self._stop_all()
            self._purge(set())
            return
        fs = int(w.cfg.get("signals", "ecg_fs", default=250) or 250)
        slots = list(c.get("slots") or [])[:N_SLOTS] + [""] * max(0, N_SLOTS - len(c.get("slots") or []))
        ins = sorted(((rec.get("bed_idx", -1) if rec.get("bed_idx", -1) >= 0 else 10 ** 9, pid) for pid, rec in w.admitted.items() if not rec["outpatient"]))
        pids = [pid for _, pid in ins][:N_SLOTS]
        mp: dict = {}
        want_cycle: set[int] = set()
        idle: set[int] = set()                                          # 파일도 순환도 없는 슬롯 환자 → 원래 시뮬레이션으로
        ft_pids = self._ft_pids()
        auto = bool(c.get("auto_cycle", True))
        tick = w._tick_now()
        for i in range(N_SLOTS):
            pid = pids[i] if i < len(pids) else None
            self.slot_pid[i] = pid
            self.slot_rows[i] = w.admitted[pid]["row"] if pid is not None else None
            s = self.slot_state(str(slots[i] or ""), fs)
            if pid is None:
                continue
            row = w.admitted[pid]["row"]
            if pid in ft_pids:
                if pid in self.cycling:
                    want_cycle.add(pid)                                    # 순환 상태는 유지하되 이번엔 바꾸지 않음
                continue
            if s["state"] == "ready":
                e = self.t0.get(row)
                if e is None or e[0] != pid or e[1] != s["npy"] or e[2] > tick:   # 새 파일 · 새 환자 · 전송 재시작 → 처음부터
                    e = self.t0[row] = (pid, s["npy"], tick)
                mp[str(row)] = {"ecg": s["npy"], "hr": s["hr"], "slot": i, "t0": e[2]}
                self._pace_off(pid)
            elif auto:
                want_cycle.add(pid)
            else:
                idle.add(pid)
        self.t0 = {r: e for r, e in self.t0.items() if str(r) in mp or e[0] in ft_pids}   # 리듬 테스트 뒤에는 이어서 재생
        for pid in list(self.cycling):                                 # 파일이 생긴 슬롯·빠진 환자는 순환 종료
            if pid not in want_cycle:
                self._end_cycle(pid, ft_pids)
        for pid in list(w.admitted):                                   # 슬롯에서 빠졌거나 파일도 순환도 없는 환자는 페이싱 원복
            if pid not in pids or pid in idle:
                self._pace_restore(pid)
        period = max(10.0, float(c.get("cycle_s", 60)))
        for pid in want_cycle:
            if pid in ft_pids:
                continue
            rec = w.admitted[pid]
            cy = self.cycling.get(pid)
            if cy is None:
                i = pids.index(pid)
                cy = self.cycling[pid] = {"i": i % len(CYCLE), "next": 0.0, "rhythm": None, "base": int(w.st.patch.arr["variant"][rec["row"]])}
                self._pace_off(pid)
            if w.sim_time >= cy["next"] and w.rhythm_variants:
                r = CYCLE[cy["i"] % len(CYCLE)]
                cy["i"] += 1
                if r in w.rhythm_variants:
                    w._switch_variant(rec["row"], w._variant_for(r, w.by_id[pid]["age"]), tick)
                    cy["rhythm"] = r
                cy["next"] = w.sim_time + period
        sig = json.dumps(mp, sort_keys=True)
        if sig != self.map_sig:
            self._write_map(mp)
            self.map_sig = sig
        self._purge({v["ecg"] for v in mp.values()} | {v["hr"] for v in mp.values()})

    def _purge(self, used: set[str]) -> None:
        """이전 판의 구운 파일: 지도에서 빠지고 10초가 지나면 지운다 (워커는 1초마다 지도를 다시 읽는다)."""
        now, keep = time.time(), []
        live = {f for c in list(self.cache.values()) if c.get("ok") for f in (c["npy"], c["hr"])}   # 같은 판을 다시 구웠으면 살린다
        for t, files in self.trash:
            if live.intersection(files):
                continue
            if t > now or used.intersection(files):
                keep.append((t, files))
                continue
            for f in files:
                try:
                    os.unlink(f)
                except OSError:
                    pass
        self.trash = keep

    def _pace_off(self, pid: int) -> None:
        rec = self.w.admitted[pid]
        if "rs_paced" not in rec:
            rec["rs_paced"] = int(self.w.st.patch.arr["paced"][rec["row"]])
            self.w.st.patch.arr["paced"][rec["row"]] = 0

    def _pace_restore(self, pid: int) -> None:
        rec = self.w.admitted.get(pid)
        if rec is not None and "rs_paced" in rec:
            self.w.st.patch.arr["paced"][rec["row"]] = rec.pop("rs_paced")

    def _end_cycle(self, pid: int, ft_pids: set[int] | None = None) -> None:
        cy = self.cycling.pop(pid, None)
        rec = self.w.admitted.get(pid)
        if cy and rec is not None and pid not in (self._ft_pids() if ft_pids is None else ft_pids):   # 리듬 테스트 중이면 테스트가 끝날 때 기저 리듬으로
            self.w._switch_variant(rec["row"], rec.get("base_variant", cy["base"]), self.w._tick_now())

    def _stop_all(self) -> None:
        ft_pids = self._ft_pids()
        for pid in list(self.cycling):
            self._end_cycle(pid, ft_pids)
        for pid in list(self.w.admitted):
            self._pace_restore(pid)
        self.w.log.add("script", "실제 시그널 송출 끔 — 슬롯 환자는 원래 리듬으로")
        self._write_map({})
        self.map_sig = None
        self.t0 = {}
        self.slot_rows = [None] * N_SLOTS
        self.slot_pid = [None] * N_SLOTS

    def _write_map(self, mp: dict) -> None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            tmp = MAP_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(mp), "utf-8")
            tmp.replace(MAP_PATH)
        except OSError:
            pass

    # ---------------------------------------------------------------- 상태
    def status(self) -> dict:
        from ..signals.rhythms import RHYTHMS
        w, c = self.w, self.cfg()
        fs = int(w.cfg.get("signals", "ecg_fs", default=250) or 250)
        slots = list(c.get("slots") or []) + [""] * N_SLOTS
        out = []
        for i in range(N_SLOTS):
            path = str(slots[i] or "")
            s = self.slot_state(path, fs) if path else {"state": "empty"}
            pid = self.slot_pid[i]
            prof = w.by_id.get(pid) if pid is not None else None
            cy = self.cycling.get(pid) if pid is not None else None
            out.append({"slot": i, "path": path, "name": os.path.basename(path) if path else "", "state": s["state"], "error": s.get("error"),
                        "info": s.get("info"), "patient_id": pid, "patient": prof["name"] if prof else None,
                        "cycling": (RHYTHMS.get(cy["rhythm"], {}).get("label", cy["rhythm"]) if cy and cy["rhythm"] else None)})
        return {"enabled": bool(c.get("enabled")), "auto_cycle": bool(c.get("auto_cycle", True)), "cycle_s": c.get("cycle_s", 60), "slots": out,
                "folder": str(WAVE_DIR)}
