"""실제 시그널 송출: 에뮬레이터 로컬의 ATF / CSV 심전도 파일을 환자(슬롯)별로 읽어 끝없이 반복 전송한다.

슬롯 i(0..19)는 재원 입원 환자를 병상 순으로 늘어놓은 i번째 환자다.  파일은 목표 표본율(signals.ecg_fs)로
리샘플해 µV int16 배열(.npy)과 초당 HR 배열로 굽고, 워커는 realsig_map.json 을 보고 그 환자의 ECG·HR 을 파일
값으로 바꾼다(파일 끝에서 처음으로 돌아감).  파일이 없거나 읽지 못한 슬롯은 '자동 생성'이 켜져 있으면 여러
부정맥을 차례로 돌며 바꿔 준다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

from ..config import BASE_DIR

N_SLOTS = 20
RUNTIME_DIR = BASE_DIR / "runtime"
MAP_PATH = RUNTIME_DIR / "realsig_map.json"
BAKE_DIR = RUNTIME_DIR / "realsig"
EXTS = (".atf", ".csv", ".txt", ".tsv")
MAX_BYTES = 512 * 1024 * 1024
# 자동 생성 순환: 페이싱 리듬은 뺀다 (슬롯 환자는 페이싱 스파이크를 끈다)
CYCLE = ["nsr", "afib", "pvc_bigeminy", "avb2_m1", "sinus_brady", "vt", "aflutter", "pvc", "avb3", "svt", "sinus_tachy", "nsvt",
         "lbbb", "afib_rvr", "stemi", "pac", "avb2_m2", "sinus_pause", "rbbb", "ischemia", "avb1", "vfib"]
# 파일 탐색 허용 루트 (에뮬레이터 로컬 디렉토리)
BROWSE_ROOTS = ["/home", "/media", "/mnt", "/srv", "/data", str(BASE_DIR)]


# ============================================================================ 파일 읽기
_UNIT = {"v": 1e6, "mv": 1e3, "uv": 1.0, "µv": 1.0, "μv": 1.0, "nv": 1e-3}


def _unit_of(title: str) -> float | None:
    m = re.search(r"\(([^)]*)\)|\[([^\]]*)\]", title or "")
    u = (m.group(1) or m.group(2)).strip().lower() if m else ""
    return _UNIT.get(u)


def _time_scale(title: str) -> float:
    t = (title or "").lower()
    if "(ms)" in t or "[ms]" in t or t.strip() in ("ms", "time_ms"):
        return 1e-3
    if "(us)" in t or "(µs)" in t:
        return 1e-6
    return 1.0


def _is_time(title: str) -> bool:
    return bool(re.match(r"\s*\"?\s*(time|t|sec|seconds|ms|timestamp|elapsed)\b", (title or "").lower()))


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


def _read_table(path: Path) -> tuple[list[str], np.ndarray, dict]:
    """ATF / CSV / TSV → (열 제목, 숫자 표 [n, m], 머리 정보)."""
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"파일이 너무 큽니다 ({len(raw) // 1_000_000} MB, 최대 {MAX_BYTES // 1_000_000} MB)")
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    lines = text.splitlines()
    info: dict = {}
    titles: list[str] = []
    if lines and lines[0].startswith("ATF"):                          # Axon Text File
        n_hdr = int(re.split(r"[\t ,]+", lines[1].strip())[0])
        for h in lines[2:2 + n_hdr]:
            h = h.strip().strip('"')
            if "=" in h:
                k, v = h.split("=", 1); info[k.strip()] = v.strip()
        titles = [t.strip().strip('"') for t in lines[2 + n_hdr].split("\t")]
        body = lines[3 + n_hdr:]
        delim = "\t"
    else:
        sample = "\n".join(lines[:20])
        delim = "\t" if sample.count("\t") > sample.count(",") else (";" if sample.count(";") > sample.count(",") else ",")
        body = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
        first = next(csv.reader([body[0]], delimiter=delim)) if body else []
        def num(x):
            try:
                float(x); return True
            except ValueError:
                return False
        if first and not all(num(x.strip().strip('"')) for x in first if x.strip()):
            titles = [t.strip().strip('"') for t in first]
            body = body[1:]
        if body and body[0].startswith("'"):                          # MIT-BIH 내보내기의 단위 줄 ('sec', 'mV')
            units = [u.strip().strip("'\"") for u in body[0].split(delim)]
            titles = [f"{t} ({u})" if u else t for t, u in zip(titles or [f"col{i}" for i in range(len(units))], units)]
            body = body[1:]
    arr = np.genfromtxt(io.StringIO("\n".join(body)), delimiter=delim, dtype=np.float64, invalid_raise=False)
    if arr.ndim == 1:
        arr = arr[:, None]
    arr = arr[~np.all(np.isnan(arr), axis=1)]
    if arr.size == 0 or arr.shape[0] < 50:
        raise ValueError("숫자 데이터가 없습니다")
    if len(titles) < arr.shape[1]:
        titles = titles + [f"col{i}" for i in range(len(titles), arr.shape[1])]
    return titles, arr, info


def parse_file(path: str, target_fs: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """파일 → (µV int16 @target_fs, 초당 HR uint8, 정보)."""
    p = Path(path)
    if p.suffix.lower() not in EXTS:
        raise ValueError(f"지원하지 않는 형식 ({p.suffix}); ATF · CSV · TSV · TXT")
    titles, arr, info = _read_table(p)
    ncol = arr.shape[1]
    cols = list(range(ncol))
    fs = None
    tcol = next((c for c in cols if _is_time(titles[c])), None)
    if tcol is None and ncol >= 2:                                    # 제목이 없어도 첫 열이 고르게 증가하면 시간 열
        d = np.diff(arr[: min(2000, len(arr)), 0])
        if d.size and np.all(d > 0) and np.std(d) < 0.05 * np.mean(d):
            tcol = 0
    if tcol is not None:
        t = arr[:, tcol] * _time_scale(titles[tcol])
        dt = np.median(np.diff(t[: min(20000, len(t))]))
        if dt > 0:
            fs = 1.0 / dt
        cols = [c for c in cols if c != tcol]
    if not cols:
        raise ValueError("신호 열이 없습니다")
    fs_src = "시간 열"
    if not fs or not (20 <= fs <= 100_000):
        fs = _hint_fs(p.name) or next((float(v) for k, v in info.items() if "rate" in k.lower() and re.match(r"^[\d.]+$", v)), None)
        fs_src = "파일 이름/머리" if fs else f"가정({target_fs} Hz)"
        fs = fs or float(target_fs)
    c = _pick_signal(titles, cols)
    x = arr[:, c].astype(np.float64)
    ok = ~np.isnan(x)
    if ok.sum() < 50:
        raise ValueError("신호 값이 비어 있습니다")
    x = np.interp(np.arange(len(x)), np.flatnonzero(ok), x[ok])
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
    xo = np.interp(np.arange(n_out) * (fs / target_fs), np.arange(len(x)), x)
    ecg = np.clip(np.rint(xo), -32000, 32000).astype(np.int16)
    hr = _hr_series(xo, target_fs)
    return ecg, hr, {"title": titles[c], "fs_in": round(float(fs), 2), "fs_src": fs_src, "unit": unit_src, "seconds": round(n_out / target_fs, 1),
                     "hr_mean": int(np.median(hr[hr > 0])) if (hr > 0).any() else 0}


def _hr_series(x: np.ndarray, fs: int) -> np.ndarray:
    """간이 R파 검출(미분 제곱 이동적분 + 적응 문턱 + 불응기) → 초당 HR."""
    n = len(x); secs = max(1, n // fs)
    k = max(1, int(0.03 * fs))
    base = np.convolve(x, np.ones(k * 10) / (k * 10), mode="same")
    d = np.diff(x - base, prepend=x[0])
    e = np.convolve(d * d, np.ones(max(1, int(0.12 * fs))), mode="same")
    peaks: list[int] = []
    win = 2 * fs
    refr = int(0.25 * fs)
    for s in range(0, n, win):                                        # 2초 창마다 문턱
        seg = e[s:s + win]
        if seg.size == 0:
            continue
        thr = 0.35 * np.percentile(seg, 99)
        i = s
        above = np.flatnonzero(seg > thr) + s
        for a in above:
            if peaks and a - peaks[-1] < refr:
                continue
            j = a + int(np.argmax(e[a:a + refr]))
            if not peaks or j - peaks[-1] >= refr:
                peaks.append(j)
    hr = np.zeros(secs, dtype=np.uint8)
    if len(peaks) >= 3:
        pk = np.asarray(peaks)
        inst = 60.0 * fs / np.diff(pk)
        inst = np.clip(inst, 20, 250)
        tt = pk[1:] / fs
        hr = np.clip(np.rint(np.interp(np.arange(secs) + 0.5, tt, inst)), 0, 255).astype(np.uint8)
    return hr


# ============================================================================ 파일 탐색 (에뮬레이터 로컬)
def browse(path: str) -> dict:
    roots = [r for r in BROWSE_ROOTS if os.path.isdir(r)]
    if not path:
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in roots], "files": [], "roots": roots}
    rp = os.path.realpath(path)
    if not any(rp == r or rp.startswith(r.rstrip("/") + "/") for r in map(os.path.realpath, roots)):
        raise PermissionError("허용된 위치가 아닙니다")
    if os.path.isfile(rp):
        rp = os.path.dirname(rp)
    dirs, files = [], []
    try:
        with os.scandir(rp) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": os.path.join(rp, e.name)})
                    elif e.name.lower().endswith(EXTS):
                        st = e.stat()
                        files.append({"name": e.name, "path": os.path.join(rp, e.name), "bytes": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    pass
    except OSError as ex:
        raise PermissionError(str(ex))
    parent = os.path.dirname(rp)
    at_root = any(rp == os.path.realpath(r) for r in roots)
    return {"path": rp, "parent": "" if at_root else parent, "dirs": dirs, "files": files, "roots": roots}


# ============================================================================ 월드 쪽 관리
class RealSignal:
    def __init__(self, world):
        self.w = world
        self.cache: dict[str, dict] = {}          # key(path|mtime|size|fs) -> {ok, npy, hr, info | error}
        self.busy: set[str] = set()
        self.slot_rows: list[int | None] = [None] * N_SLOTS
        self.slot_pid: list[int | None] = [None] * N_SLOTS
        self.cycling: dict[int, dict] = {}        # pid -> {i, next, rhythm, base_variant, paced}
        self.map_sig = None
        self._t = 0.0
        BAKE_DIR.mkdir(parents=True, exist_ok=True)
        self._write_map({})

    def cfg(self) -> dict:
        return self.w.cfg.get("scenario", "realsig", default={}) or {}

    # ---------------------------------------------------------------- 굽기
    def _key(self, path: str, fs: int) -> str | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return f"{os.path.realpath(path)}|{st.st_mtime_ns}|{st.st_size}|{fs}"

    def _bake(self, key: str, path: str, fs: int) -> None:
        try:
            ecg, hr, info = parse_file(path, fs)
            h = hashlib.sha1(key.encode()).hexdigest()[:12]
            npy, hrp = BAKE_DIR / f"{h}.ecg.npy", BAKE_DIR / f"{h}.hr.npy"
            np.save(npy, ecg); np.save(hrp, hr)
            self.cache[key] = {"ok": True, "npy": str(npy), "hr": str(hrp), "info": info}
            self.w.log.add("script", f"실제 시그널: {os.path.basename(path)} 읽음 — {info['seconds']} 초, 입력 {info['fs_in']} Hz({info['fs_src']}), "
                                     f"단위 {info['unit']}, 평균 HR {info['hr_mean']}")
        except Exception as e:
            self.cache[key] = {"ok": False, "error": str(e)[:200]}
            self.w.log.add("script", f"실제 시그널: {os.path.basename(path)} 읽기 실패 — {str(e)[:120]}")
        finally:
            self.busy.discard(key)

    def slot_state(self, path: str, fs: int) -> dict:
        if not path:
            return {"state": "empty"}
        key = self._key(path, fs)
        if key is None:
            return {"state": "error", "error": "파일이 없습니다"}
        c = self.cache.get(key)
        if c is None:
            if key not in self.busy:
                self.busy.add(key)
                threading.Thread(target=self._bake, args=(key, path, fs), daemon=True, name="realsig-bake").start()
            return {"state": "loading"}
        if not c["ok"]:
            return {"state": "error", "error": c["error"]}
        return {"state": "ready", "npy": c["npy"], "hr": c["hr"], "info": c["info"]}

    # ---------------------------------------------------------------- 매 초
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
            return
        fs = int(w.cfg.get("signals", "ecg_fs", default=250) or 250)
        slots = list(c.get("slots") or [])[:N_SLOTS] + [""] * max(0, N_SLOTS - len(c.get("slots") or []))
        ins = sorted(((rec.get("bed_idx", -1) if rec.get("bed_idx", -1) >= 0 else 10 ** 9, pid) for pid, rec in w.admitted.items() if not rec["outpatient"]))
        pids = [pid for _, pid in ins][:N_SLOTS]
        mp: dict = {}
        want_cycle: set[int] = set()
        ft_pids = {a["pid"] for a in getattr(getattr(w, "ft", None), "active", [])}   # 현장 테스트 중인 환자는 테스트가 우선
        auto = bool(c.get("auto_cycle", True))
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
                mp[str(row)] = {"ecg": s["npy"], "hr": s["hr"], "slot": i}
                self._pace_off(pid)
            elif auto:
                want_cycle.add(pid)
        for pid in list(self.cycling):                                 # 파일이 생긴 슬롯·빠진 환자는 순환 종료
            if pid not in want_cycle:
                self._end_cycle(pid)
        for pid in list(w.admitted):                                   # 슬롯에서 빠진 환자는 페이싱 원복
            if pid not in pids:
                self._pace_restore(pid)
        tick = w._tick_now()
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

    def _pace_off(self, pid: int) -> None:
        rec = self.w.admitted[pid]
        if "rs_paced" not in rec:
            rec["rs_paced"] = int(self.w.st.patch.arr["paced"][rec["row"]])
            self.w.st.patch.arr["paced"][rec["row"]] = 0

    def _pace_restore(self, pid: int) -> None:
        rec = self.w.admitted.get(pid)
        if rec is not None and "rs_paced" in rec:
            self.w.st.patch.arr["paced"][rec["row"]] = rec.pop("rs_paced")

    def _end_cycle(self, pid: int) -> None:
        cy = self.cycling.pop(pid, None)
        rec = self.w.admitted.get(pid)
        if cy and rec is not None:
            self.w._switch_variant(rec["row"], rec.get("base_variant", cy["base"]), self.w._tick_now())

    def _stop_all(self) -> None:
        for pid in list(self.cycling):
            self._end_cycle(pid)
        for pid in list(self.w.admitted):
            self._pace_restore(pid)
        self.w.log.add("script", "실제 시그널 송출 끔 — 슬롯 환자는 원래 리듬으로")
        self._write_map({})
        self.map_sig = None
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
        return {"enabled": bool(c.get("enabled")), "auto_cycle": bool(c.get("auto_cycle", True)), "cycle_s": c.get("cycle_s", 60), "slots": out}
