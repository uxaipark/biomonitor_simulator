#!/usr/bin/env python3
"""레퍼런스 QRS 검출기 (Pan-Tompkins 계열, 단순화) — tools/receiver.py --save-frames DIR 로 받아 둔 프레임의 ECG 로 R-peak 를 찾아
채점 입력(검출 시각 목록)을 만든다.  라우터 알고리즘의 대체가 아니라 채점 파이프라인을 시연하고 형식을 보여 주는 용도.

  python tools/qrs_detect.py DIR [--truth-only] [--out detections.json]

출력 JSON: {"detections": {"<patch_id>": [ts_ms, ...]}, "patches": n, "fs": 250}  → POST /api/v1/truth/score 또는 tools/qrs_score.py 입력.
--truth-only : 정답 레코드가 있는(벤치마크) 패치만 (기본).  검출 시각 = 그 표본이 속한 프레임의 ts_ms + 프레임 내 오프셋 — 정답과 같은 규칙.
프레임 ts_ms 는 200 ms 격자가 아니라 지터가 있으므로(워커 시계) 표본 수로 시간을 세지 말고 반드시 프레임 ts 를 기준으로 해야 한다.
재전송으로 중복된 프레임은 패치 seq 로 걸러낸다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from emulator.config import CH_ECG, CH_TRUTH                     # noqa: E402
from tools.truth_check import frames                             # noqa: E402


def detect(x: np.ndarray, fs: int) -> np.ndarray:
    """R-peak 표본 인덱스.  5-15 Hz 대역(간단한 IIR), 미분·제곱·150 ms 이동 적분, 적응 문턱, 200 ms 불응기, 원신호 |x| 국소 최대로 정렬."""
    if x.size < fs:
        return np.zeros(0, dtype=np.int64)
    # 대역 통과: 1차 고역(5 Hz) + 1차 저역(15 Hz) 를 앞뒤로 (영위상)
    def lp(sig, fc):
        a = np.exp(-2 * np.pi * fc / fs); y = np.empty_like(sig); acc = 0.0
        for i, v in enumerate(sig):
            acc = a * acc + (1 - a) * v; y[i] = acc
        return y
    def filt(sig):
        y = sig - lp(sig, 5.0); y = lp(y, 15.0); return y
    y = filt(x.astype(np.float64)); y = filt(y[::-1])[::-1]
    d = np.diff(y, prepend=y[0]); e = d * d
    w = max(1, int(0.15 * fs)); e = np.convolve(e, np.ones(w) / w, mode="same")
    thr = 0.0; spki = 0.0; npki = 0.0
    peaks = []
    refr = int(0.2 * fs); last = -refr
    # 초기 문턱: 처음 2 초의 최대 절반
    init = e[: 2 * fs].max() if e.size >= 2 * fs else e.max()
    spki, npki = init, init * 0.1; thr = npki + 0.25 * (spki - npki)
    i = 1
    while i < e.size - 1:
        if e[i] >= e[i - 1] and e[i] >= e[i + 1] and e[i] > thr and i - last >= refr:
            peaks.append(i); last = i
            spki = 0.125 * e[i] + 0.875 * spki
        elif e[i] >= e[i - 1] and e[i] >= e[i + 1]:
            npki = 0.125 * e[i] + 0.875 * npki
        thr = npki + 0.25 * (spki - npki)
        i += 1
    out = []
    half = int(0.06 * fs)
    ax = np.abs(x - np.median(x))
    for p in peaks:
        lo, hi = max(0, p - half), min(x.size, p + half + 1)
        out.append(lo + int(np.argmax(ax[lo:hi])))
    return np.array(sorted(set(out)), dtype=np.int64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out", default="")
    ap.add_argument("--fs", type=int, default=250)
    ap.add_argument("--all", action="store_true", help="정답 레코드가 없는 환자 패치도 검출")
    a = ap.parse_args()
    segs: dict[int, list] = {}
    truth_pids: set = set()
    for fn in sorted(glob.glob(os.path.join(a.dir, "gw_*.bin"))):
        for gw_id, seq, ts_ms, recs in frames(fn):
            for patch_id, patient_id, pseq, fl, batt, rssi, chans in recs:
                if CH_TRUTH in chans:
                    truth_pids.add(patch_id)
                if CH_ECG in chans:
                    dt, n, data = chans[CH_ECG]
                    segs.setdefault(patch_id, []).append((ts_ms, pseq, np.frombuffer(bytes(data), dtype="<i2")))
    det: dict[str, list] = {}
    for pid, lst in segs.items():
        if not a.all and pid not in truth_pids:
            continue
        # 패치 seq 로 중복(재전송) 제거 후 seq 순으로; seq 가 1 씩 이어지는 프레임만 한 구간으로 붙인다
        uniq = {}
        for ts, pseq, x in lst:
            uniq.setdefault(pseq, (ts, x))
        seqs = sorted(uniq)
        chunks: list[list[tuple[int, int, np.ndarray]]] = []
        for q in seqs:
            ts, x = uniq[q]
            if chunks and q == chunks[-1][-1][0] + 1:
                chunks[-1].append((q, ts, x))
            else:
                chunks.append([(q, ts, x)])
        out = []
        for ch in chunks:
            x = np.concatenate([c[2] for c in ch])
            starts = np.cumsum([0] + [len(c[2]) for c in ch[:-1]])          # 각 프레임의 시작 표본 인덱스
            for i in detect(x, a.fs):
                k = int(np.searchsorted(starts, i, side="right") - 1)      # 검출 표본이 속한 프레임
                out.append(int(ch[k][1] + ((int(i) - int(starts[k])) * 1000) // a.fs))   # 그 프레임 ts_ms + 프레임 내 오프셋 (정답과 같은 규칙)
        det[str(pid)] = sorted(out)
    doc = {"detections": det, "patches": len(det), "fs": a.fs, "n": sum(len(v) for v in det.values())}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(doc, f)
    print(f"검출: 패치 {len(det)}개, R-peak {doc['n']:,}개" + (f" → {a.out}" if a.out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
