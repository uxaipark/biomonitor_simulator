#!/usr/bin/env python3
"""Reference TCP receiver / protocol decoder (라우터 서버 구현 참고용).

  python tools/receiver.py --port 9100 [--save DIR] [--save-frames DIR] [--strict]
Decodes frames from any number of gateway connections, prints a summary every
second and optionally appends raw records to <DIR>/<patch_id>.bin.
--save-frames DIR  : every raw frame as [len u32][frame] in <DIR>/gw_<gw_id>.bin (input for tools/verify_capture.py)
--strict           : run the protocol checker (emulator.runtime.verify) on each connection and report anomalies
                     (bad magic/version/length, garbage, seq gaps/dups/reorder, ts backwards, unknown channels)
"""
import argparse
import asyncio
import json
import struct
import time
from collections import Counter

HEADER = struct.Struct("<HBBIIQHI")
GWSTAT = struct.Struct("<BBBbBBIB")
ITEM = {1: 2, 2: 1, 3: 2, 4: 1, 5: 4}
AXES = {7: 3}
stats = Counter()
patches_seen = set()
gws_seen = set()
meta_by_gw = {}
save_dir = None
frames_dir = None
strict = False
checkers = []


def decode_records(buf: memoryview, n_rec: int):
    off = 0
    for _ in range(n_rec):
        patch_id, patient_id, pseq, flags, batt, rssi, n_ch = struct.unpack_from("<IIIBBbB", buf, off)
        off += 16
        chans = {}
        for _ in range(n_ch):
            ch, dt, n = struct.unpack_from("<BBH", buf, off)
            off += 4
            size = n * ITEM[dt] * AXES.get(ch, 1)
            chans[ch] = (dt, n, bytes(buf[off: off + size]))
            off += size
        yield patch_id, patient_id, pseq, flags, batt, rssi, chans


def save_frame(gw_id: int, frame: bytes) -> None:
    if frames_dir:
        with open(f"{frames_dir}/gw_{gw_id}.bin", "ab") as f:
            f.write(struct.pack("<I", len(frame)) + frame)


async def handle_strict(reader, writer):
    """Strict mode: raw chunks go through the protocol checker, which resynchronises after corrupt data."""
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from emulator.runtime.verify import StreamChecker
    stats["connections"] += 1

    def on_frame(gw_id, seq, ts_ms, flags, n_rec, recs, payload):
        gws_seen.add(gw_id)
        stats["frames"] += 1
        stats["bytes"] += HEADER.size + len(payload)
        if flags & 0x04:
            stats["keepalive"] += 1
        for patch_id, patient_id, pseq, fl, batt, rssi, chans in recs:
            patches_seen.add(patch_id)
            stats["records"] += 1
            for ch in chans:
                stats[f"ch{ch}"] += 1
        save_frame(gw_id, HEADER.pack(0x4742, 1, flags, gw_id, seq, ts_ms, n_rec, len(payload)) + bytes(payload))

    chk = StreamChecker(on_frame)
    checkers.append(chk)
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            chk.feed(data)
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        chk.close()
        stats["connections"] -= 1
        writer.close()


async def handle(reader, writer):
    if strict:
        return await handle_strict(reader, writer)
    peer = writer.get_extra_info("peername")
    stats["connections"] += 1
    try:
        while True:
            hdr = await reader.readexactly(HEADER.size)
            magic, ver, flags, gw_id, seq, ts_ms, n_rec, plen = HEADER.unpack(hdr)
            if magic != 0x4742:
                print("bad magic from", peer)
                break
            payload = memoryview(await reader.readexactly(plen))
            save_frame(gw_id, hdr + bytes(payload))
            gws_seen.add(gw_id)
            stats["frames"] += 1
            stats["bytes"] += HEADER.size + plen
            off = 0
            if flags & 0x02:
                cpu, mem, net, wan, nconn, status, uptime, temp = GWSTAT.unpack_from(payload, 0)
                off += GWSTAT.size
                stats["gwstat"] += 1
            if flags & 0x01:
                (mlen,) = struct.unpack_from("<I", payload, off)
                off += 4
                meta_by_gw[gw_id] = json.loads(bytes(payload[off: off + mlen]).decode("utf-8"))
                off += mlen
                stats["meta"] += 1
            if flags & 0x04:
                stats["keepalive"] += 1
            for patch_id, patient_id, pseq, fl, batt, rssi, chans in decode_records(payload[off:], n_rec):
                patches_seen.add(patch_id)
                stats["records"] += 1
                if fl & 0x01:
                    stats["lead_off_records"] += 1
                for ch in chans:
                    stats[f"ch{ch}"] += 1
                if save_dir:
                    with open(f"{save_dir}/{patch_id}.bin", "ab") as f:
                        f.write(struct.pack("<QI", ts_ms, gw_id))
                        for ch, (dt, n, data) in chans.items():
                            f.write(struct.pack("<BBH", ch, dt, n) + data)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        stats["connections"] -= 1
        writer.close()


async def report():
    last = Counter()
    while True:
        await asyncio.sleep(1)
        d = {k: stats[k] - last.get(k, 0) for k in ("frames", "bytes", "records")}
        last = Counter(stats)
        print(flush=True, end="")
        print(f"conn={stats['connections']} gws={len(gws_seen)} patches={len(patches_seen)} frames/s={d['frames']} rec/s={d['records']} "
              f"KB/s={d['bytes'] / 1024:.0f} meta={stats['meta']} gwstat={stats['gwstat']} keepalive={stats['keepalive']} "
              f"ch:{{{', '.join(f'{k[2:]}:{v}' for k, v in sorted(stats.items()) if k.startswith('ch'))}}}")
        if strict and checkers:
            tot = Counter()
            for c in checkers:
                for k, v in c.summary().items():
                    if k not in ("frames", "gateways"):
                        tot[k] += v
            bad = {k: v for k, v in tot.items() if v}
            print(f"  strict: {bad if bad else 'no anomalies'}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--save", default=None)
    ap.add_argument("--save-frames", default=None, help="raw frames per gateway ([len u32][frame]) for tools/verify_capture.py")
    ap.add_argument("--strict", action="store_true", help="protocol checker with anomaly counters (resyncs after corrupt frames)")
    a = ap.parse_args()
    global save_dir, frames_dir, strict
    import os
    if a.save:
        os.makedirs(a.save, exist_ok=True)
        save_dir = a.save
    if a.save_frames:
        os.makedirs(a.save_frames, exist_ok=True)
        frames_dir = a.save_frames
    strict = a.strict
    server = await asyncio.start_server(handle, a.host, a.port, backlog=4096)
    print(f"listening on {a.host}:{a.port}")
    asyncio.create_task(report())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
