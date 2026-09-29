#!/usr/bin/env python3
"""오프라인 QRS 검출 채점: 캡처 프레임(receiver --save-frames)의 정답 레코드 vs 검출 시각 목록(JSON) → Se/PPV/F1.

  python tools/qrs_score.py FRAMES_DIR detections.json [--tol 150] [--exclude lead_off,settling] [--json out.json]

정답은 프레임 안 TRUTH 레코드(채널 11)에서 (프레임 ts_ms + R-peak 오프셋) 으로 만들고, 채점 규칙은 서버의 /api/v1/truth/score 와 같다
(emulator.runtime.truthbench.score_beats).  라우터가 저장한 프레임과 자기 검출 결과로 그대로 쓸 수 있다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from emulator.config import CH_TRUTH                              # noqa: E402
from emulator.runtime.protocol import parse_truth, BEAT_KINDS      # noqa: E402
from emulator.runtime.truthbench import score_beats                # noqa: E402
from tools.truth_check import frames                              # noqa: E402


def truth_from_frames(d: str, fs: int = 250) -> list:
    out = []
    for fn in sorted(glob.glob(os.path.join(d, "gw_*.bin"))):
        for gw_id, seq, ts_ms, recs in frames(fn):
            for patch_id, patient_id, pseq, fl, batt, rssi, chans in recs:
                if CH_TRUTH not in chans:
                    continue
                t = parse_truth(bytes(chans[CH_TRUTH][2]))
                for b in t["beats"]:
                    kind = BEAT_KINDS.index(b["kind"]) if b["kind"] in BEAT_KINDS else 0
                    out.append((ts_ms + (b["offset"] * 1000) // fs, patch_id, kind, t["rhythm_code"], t["flags"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir")
    ap.add_argument("detections")
    ap.add_argument("--tol", type=int, default=150)
    ap.add_argument("--exclude", default="lead_off,settling,switching")
    ap.add_argument("--no-window", action="store_true", help="검출 시간창으로 정답을 자르지 않고 캡처 전체를 채점")
    ap.add_argument("--fs", type=int, default=250)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    with open(a.detections) as f:
        doc = json.load(f)
    det = doc.get("detections", doc)
    truth = truth_from_frames(a.frames_dir, a.fs)
    det = {int(k): sorted(int(x) for x in v) for k, v in det.items() if v}
    if det and not a.no_window:                                           # 서버(/truth/score)와 같은 규칙: 검출이 걸친 시간창 ±tol 만 채점
        lo = min(min(v) for v in det.values()) - a.tol; hi = max(max(v) for v in det.values()) + a.tol
        truth = [t for t in truth if lo <= t[0] <= hi]
    res = score_beats(truth, det, a.tol, tuple(x for x in a.exclude.split(",") if x))
    o = res["overall"]
    print(f"허용창 ±{a.tol} ms · 정답 박동 {len(truth):,} · 검출 {sum(len(v) for v in det.values()):,} · 패치 {len(res['patches'])}")
    print(f"TP {o['tp']:,} FP {o['fp']:,} FN {o['fn']:,} (제외 정답 {o['ignored_truth']:,} · 제외 검출 {o['ignored_det']:,})")
    print(f"민감도 Se {o['se_pct']} % · 정밀도 PPV {o['ppv_pct']} % · F1 {o['f1_pct']} %")
    print("조건별:", {k: (v["se_pct"], v["ppv_pct"]) for k, v in res["by_condition"].items()})
    print("리듬별 Se/PPV:", {k: (v["se_pct"], v["ppv_pct"]) for k, v in sorted(res["by_rhythm"].items())})
    print("박동 종류별 Se:", {k: v["se_pct"] for k, v in res["by_beat_kind"].items()})
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
