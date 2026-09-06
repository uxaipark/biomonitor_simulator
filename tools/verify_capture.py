#!/usr/bin/env python3
"""Compare the emulator's ground-truth capture with what the router received.

  python tools/verify_capture.py --capture data/capture [--received DIR]

--capture  : directory with w<worker>.bin written by the emulator tap ([gw_row u32][len u32][frame])
--received : directory with gw_<gw_id>.bin written by tools/receiver.py --save-frames ([len u32][frame])
Without --received the capture itself is checked (strict parser + per-gateway seq/ts order).
With --received every captured frame (gw_id, seq) is looked up in the received set: missing, extra, byte-mismatch
and out-of-order frames are reported per gateway.  Exit code 1 when anything is off.
"""
import argparse
import glob
import hashlib
import os
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from emulator.runtime.protocol import HEADER  # noqa: E402
from emulator.runtime.verify import StreamChecker  # noqa: E402


def read_capture(d):
    """-> list of (gw_row, frame_bytes) in send order."""
    out = []
    for path in sorted(glob.glob(os.path.join(d, "w*.bin"))):
        with open(path, "rb") as f:
            data = f.read()
        off = 0
        while off + 8 <= len(data):
            gw_row, n = struct.unpack_from("<II", data, off)
            off += 8
            out.append((gw_row, data[off: off + n]))
            off += n
    return out


def read_received(d):
    """-> dict gw_id -> list of frame_bytes in arrival order."""
    out = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(d, "gw_*.bin"))):
        with open(path, "rb") as f:
            data = f.read()
        off = 0
        while off + 4 <= len(data):
            (n,) = struct.unpack_from("<I", data, off)
            off += 4
            fr = data[off: off + n]
            off += n
            if len(fr) >= HEADER.size:
                out[HEADER.unpack_from(fr, 0)[3]].append(fr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", required=True)
    ap.add_argument("--received", default=None)
    a = ap.parse_args()
    cap = read_capture(a.capture)
    print(f"capture: {len(cap)} frames from {len(set(g for g, _ in cap))} gateway rows")
    # 1) the capture itself must be clean
    chk = StreamChecker()
    for _, fr in cap:
        chk.feed(fr)
    chk.close()
    summ = chk.summary()
    print("capture check:", {k: v for k, v in summ.items() if v})
    problems = sum(v for k, v in summ.items() if k not in ("frames", "gateways"))
    if a.received:
        rec = read_received(a.received)
        exp = defaultdict(dict)       # gw_id -> seq -> sha1
        order = defaultdict(list)
        for _, fr in cap:
            h = HEADER.unpack_from(fr, 0)
            exp[h[3]][h[4]] = hashlib.sha1(fr).hexdigest()
            order[h[3]].append(h[4])
        report = Counter()
        for gw_id, seqs in exp.items():
            got = rec.get(gw_id, [])
            got_map = {}
            arrival = []
            for fr in got:
                h = HEADER.unpack_from(fr, 0)
                got_map.setdefault(h[4], []).append(hashlib.sha1(fr).hexdigest())
                arrival.append(h[4])
            for seq, sha in seqs.items():
                if seq not in got_map:
                    report["missing"] += 1
                elif sha not in got_map[seq]:
                    report["mismatch"] += 1
                if len(got_map.get(seq, [])) > 1:
                    report["duplicate"] += 1
            report["extra"] += sum(1 for sq in got_map if sq not in seqs)
            # order: arrival sequence should be non-decreasing per gateway (late replay is allowed but flagged)
            report["out_of_order"] += sum(1 for i in range(1, len(arrival)) if arrival[i] < arrival[i - 1])
        for gw_id in rec:
            if gw_id not in exp:
                report["unknown_gateway"] += 1
        print("received vs capture:", dict(report) if report else "identical")
        problems += sum(report.values())
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
