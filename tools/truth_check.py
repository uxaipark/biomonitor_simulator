#!/usr/bin/env python3
"""정답 레코드(채널 11) 자체 검증: tools/receiver.py --save-frames DIR 로 받아 둔 프레임에서 정답 패치의 ECG 와 정답 블록을 같은 seq 로 짝지어
R-peak 오프셋 위치에 실제 QRS(절대값 국소 최대, 진폭 문턱)가 있는지, HR·리듬·플래그가 말이 되는지 센다.

  python tools/truth_check.py DIR [--tol 6] [--min-mv 0.25]

라우터 채점기와 같은 방식으로 정답을 읽는 레퍼런스이기도 하다.  결과: 정답 레코드 수, 박동 수, 정합률(tol 표본 안에 QRS 피크가 있는 비율),
리듬별 집계, 플래그(아티팩트·잡음·리드오프·전환·settling) 빈도, 페이스 스파이크 정합률.
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from emulator.config import CH_ECG, CH_TRUTH                     # noqa: E402
from emulator.runtime.protocol import HEADER, GWSTAT, F_GWSTAT, F_META, F_CTRL, parse_truth   # noqa: E402
from emulator.runtime.verify import decode_records               # noqa: E402


def frames(path: str):
    with open(path, "rb") as f:
        data = f.read()
    off = 0
    while off + 4 <= len(data):
        (n,) = struct.unpack_from("<I", data, off)
        off += 4
        fr = data[off: off + n]
        off += n
        if len(fr) < HEADER.size + 4:
            continue
        magic, ver, flags, gw_id, seq, ts_ms, n_rec, plen = HEADER.unpack_from(fr, 0)
        if flags & F_CTRL:
            continue
        payload = memoryview(fr)[HEADER.size: HEADER.size + plen]
        p = 0
        if flags & F_GWSTAT:
            p += GWSTAT.size
        if flags & F_META:
            (ml,) = struct.unpack_from("<I", payload, p)
            p += 4 + ml
        try:
            recs = list(decode_records(payload, p, n_rec))
        except ValueError:
            continue
        yield gw_id, seq, ts_ms, recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--tol", type=int, default=6, help="R-peak 허용 오차 (표본; 250 Hz 에서 6 = 24 ms)")
    ap.add_argument("--min-mv", type=float, default=0.25, help="QRS 로 인정할 최소 |진폭| (mV)")
    a = ap.parse_args()
    n_truth = n_beats = n_hit = n_pace = n_pace_hit = 0
    rhythms: collections.Counter = collections.Counter()
    flags: collections.Counter = collections.Counter()
    kinds: collections.Counter = collections.Counter()
    patches: set = set()
    hr_diff: list[float] = []
    for fn in sorted(glob.glob(os.path.join(a.dir, "gw_*.bin"))):
        for gw_id, seq, ts_ms, recs in frames(fn):
            ecg_by = {(r[0], r[2]): r[6].get(CH_ECG) for r in recs if CH_ECG in r[6]}
            for patch_id, patient_id, pseq, fl, batt, rssi, chans in recs:
                if CH_TRUTH not in chans:
                    continue
                dt, n, blob = chans[CH_TRUTH]
                t = parse_truth(bytes(blob))
                n_truth += 1
                patches.add(patch_id)
                rhythms[t["rhythm"]] += 1
                for k in ("lead_off", "artifact", "noise", "paced", "switching", "settling", "beats_unavailable"):
                    if t[k]:
                        flags[k] += 1
                e = ecg_by.get((patch_id, pseq))
                if e is None:
                    flags["no_ecg_pair"] += 1
                    continue
                x = np.frombuffer(bytes(e[2]), dtype="<i2").astype(np.float32) / 1000.0     # mV
                x = x - np.median(x)
                for b in t["beats"]:
                    n_beats += 1
                    kinds[b["kind"]] += 1
                    o = b["offset"]
                    lo, hi = max(0, o - a.tol), min(x.size, o + a.tol + 1)
                    if hi > lo and np.abs(x[lo:hi]).max() >= a.min_mv and (not t["lead_off"]):
                        n_hit += 1
                for pc in t["pace"]:
                    n_pace += 1
                    o = pc["offset"]
                    lo, hi = max(0, o - 2), min(x.size, o + 3)
                    if hi > lo and np.abs(np.diff(x[lo:hi])).max() >= 0.15:     # 스파이크: 급한 기울기
                        n_pace_hit += 1
    print(f"정답 레코드 {n_truth:,}개 · 정답 패치 {len(patches)}개 · 리듬 {len(rhythms)}종")
    print("리듬별:", dict(rhythms.most_common()))
    print("플래그:", dict(flags))
    if n_beats:
        print(f"R-peak {n_beats:,}개 중 QRS 정합 {n_hit:,} ({100.0 * n_hit / n_beats:.1f} %) · 박동 종류 {dict(kinds)}")
    else:
        print("R-peak 없음 (beats_unavailable — 루프 은행 재생성 필요)" if flags.get("beats_unavailable") else "R-peak 없음")
    if n_pace:
        print(f"페이스 스파이크 {n_pace:,}개 중 정합 {n_pace_hit:,} ({100.0 * n_pace_hit / n_pace:.1f} %)")
    return 0 if n_truth else 1


if __name__ == "__main__":
    sys.exit(main())
