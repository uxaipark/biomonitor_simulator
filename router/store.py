"""Per-patch record store.

Layout:  <root>/patches/<patch_id 8 digits>/<YYYYMMDD-HH>.rec   (hourly files, append-only)
         <root>/patches/<patch_id>/index.json                   (first/last ts, record & byte counts, files)
         <root>/meta/gw_<gw_id>.json                            (last META block of every gateway)

Record file format (little-endian), one entry per patch record received:
  [ts_ms u64][gw_id u32][patient_id u32][flags u8][battery u8][rssi i8][n_ch u8]  then n_ch x ([ch u8][dtype u8][n u16][data])
Writes are buffered per patch and flushed once a second (one write per patch per second instead of one per frame: SD-card
friendly); file handles are kept in a small LRU so thousands of patches do not mean thousands of open files.
"""
from __future__ import annotations

import collections
import json
import os
import struct
import threading
import time
from pathlib import Path

ENTRY = struct.Struct("<QIIBBbB")
CH_HDR = struct.Struct("<BBH")


def encode_entry(ts_ms: int, gw_id: int, patient_id: int, flags: int, battery: int, rssi: int, chans: dict) -> bytes:
    parts = [ENTRY.pack(ts_ms, gw_id, patient_id, flags & 0xFF, battery & 0xFF, max(-128, min(127, rssi)), len(chans))]
    for ch, (dt, n, data) in chans.items():
        parts.append(CH_HDR.pack(ch, dt, n))
        parts.append(data)
    return b"".join(parts)


def iter_entries(path: str | os.PathLike):
    """Yield (ts_ms, gw_id, patient_id, flags, battery, rssi, {ch: (dtype, n, bytes)}) from a .rec file."""
    with open(path, "rb") as f:
        buf = f.read()
    off = 0
    while off + ENTRY.size <= len(buf):
        ts, gw, pid, fl, bat, rssi, n_ch = ENTRY.unpack_from(buf, off)
        off += ENTRY.size
        chans = {}
        for _ in range(n_ch):
            ch, dt, n = CH_HDR.unpack_from(buf, off)
            off += CH_HDR.size
            size = n * {1: 2, 2: 1, 3: 2, 4: 1, 5: 4}.get(dt, 1) * (3 if ch == 7 else 1)
            chans[ch] = (dt, n, buf[off: off + size])
            off += size
        yield ts, gw, pid, fl, bat, rssi, chans


class PatchStore:
    def __init__(self, root: str | os.PathLike, flush_interval: float = 1.0, max_open: int = 256, rotate_s: int = 3600):
        self.root = Path(root)
        (self.root / "patches").mkdir(parents=True, exist_ok=True)
        (self.root / "meta").mkdir(parents=True, exist_ok=True)
        self.flush_interval = flush_interval
        self.max_open = max_open
        self.rotate_s = rotate_s
        self.lock = threading.Lock()
        self.pending: dict[int, bytearray] = collections.defaultdict(bytearray)
        self.index: dict[int, dict] = {}
        self.dirty_index: set[int] = set()
        self.files: collections.OrderedDict[tuple[int, str], object] = collections.OrderedDict()
        self.bytes_written = 0
        self.records_written = 0
        self.last_flush = time.time()
        self.last_index_save = time.time()
        self._load_index()

    # ---- ingest (called from the network thread; cheap: append to a per-patch buffer)
    def write(self, patch_id: int, ts_ms: int, gw_id: int, patient_id: int, flags: int, battery: int, rssi: int, chans: dict) -> None:
        entry = encode_entry(ts_ms, gw_id, patient_id, flags, battery, rssi, chans)
        with self.lock:
            self.pending[patch_id] += entry
            ix = self.index.get(patch_id)
            if ix is None:
                ix = self.index[patch_id] = {"first_ts": ts_ms, "last_ts": ts_ms, "records": 0, "bytes": 0, "patient_id": patient_id, "gw_id": gw_id, "files": []}
            ix["last_ts"] = max(ix["last_ts"], ts_ms)
            ix["records"] += 1
            ix["bytes"] += len(entry)
            ix["patient_id"] = patient_id
            ix["gw_id"] = gw_id
            self.dirty_index.add(patch_id)

    def set_meta(self, gw_id: int, meta: dict) -> None:
        p = self.root / "meta" / f"gw_{gw_id}.json"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, p)

    # ---- flushing (once a second from the server loop, or on demand)
    def flush(self, force: bool = False) -> int:
        now = time.time()
        if not force and now - self.last_flush < self.flush_interval:
            return 0
        with self.lock:
            batch, self.pending = self.pending, collections.defaultdict(bytearray)
        self.last_flush = now
        n = 0
        fname = time.strftime("%Y%m%d-%H", time.localtime(now)) + ".rec" if self.rotate_s >= 3600 else time.strftime("%Y%m%d-%H%M", time.localtime(now)) + ".rec"
        for pid, buf in batch.items():
            f = self._file(pid, fname)
            f.write(buf)
            f.flush()                                              # one OS write per patch per flush interval
            n += len(buf)
        self.bytes_written += n
        if force or now - self.last_index_save > 10:
            self._save_index()
        return n

    def _file(self, pid: int, fname: str):
        key = (pid, fname)
        f = self.files.get(key)
        if f is not None:
            self.files.move_to_end(key)
            return f
        d = self.root / "patches" / f"{pid:08d}"
        d.mkdir(parents=True, exist_ok=True)
        # close other-hour handles of this patch and evict the LRU tail
        for k in [k for k in self.files if k[0] == pid]:
            self.files.pop(k).close()
        while len(self.files) >= self.max_open:
            _, old = self.files.popitem(last=False)
            old.close()
        f = open(d / fname, "ab")
        self.files[key] = f
        ix = self.index.setdefault(pid, {"first_ts": 0, "last_ts": 0, "records": 0, "bytes": 0, "patient_id": 0, "gw_id": 0, "files": []})
        if fname not in ix["files"]:
            ix["files"].append(fname)
            self.dirty_index.add(pid)
        return f

    def _save_index(self) -> None:
        with self.lock:
            dirty, self.dirty_index = self.dirty_index, set()
            snap = {pid: dict(self.index[pid]) for pid in dirty if pid in self.index}
        for pid, ix in snap.items():
            p = self.root / "patches" / f"{pid:08d}" / "index.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ix, f)
            os.replace(tmp, p)
        self.last_index_save = time.time()

    def _load_index(self) -> None:
        for p in (self.root / "patches").glob("*/index.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    self.index[int(p.parent.name)] = json.load(f)
            except Exception:
                pass

    def close(self) -> None:
        self.flush(force=True)
        for f in self.files.values():
            try:
                f.close()
            except Exception:
                pass
        self.files.clear()

    # ---- read side (API)
    def patches(self) -> list[dict]:
        with self.lock:
            return [{"patch_id": pid, **ix} for pid, ix in sorted(self.index.items())]

    def patch(self, pid: int) -> dict | None:
        with self.lock:
            ix = self.index.get(pid)
            return {"patch_id": pid, **ix} if ix else None

    def disk_bytes(self) -> int:
        total = 0
        for p in (self.root / "patches").glob("*/*.rec"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total
