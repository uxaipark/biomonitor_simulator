"""Strict protocol checker for router-side verification.

feed() a byte stream; the checker parses frames, validates every field it can, resynchronises after corrupt data
(scanning for the next magic/version) and keeps per-gateway anomaly counters: bad_magic, bad_version, bad_len,
oversize, truncated, garbage/resync, seq_gap, seq_dup, seq_reorder, ts_backwards, unknown_channel, bad_dtype, bad_record.
Used by tools/receiver.py --strict, tools/verify_capture.py and the tests.
"""
from __future__ import annotations

import struct
from collections import Counter, defaultdict

from .protocol import HEADER, GWSTAT, CRC, MAGIC, VERSION, VERSION_V4, F_META, F_GWSTAT, F_KEEPALIVE, F_CTRL, F_RESEND, frame_crc_ok, rec_head, seq_mask
VERSIONS = (VERSION, VERSION_V4)
from ..config import CHANNELS

ITEM = {1: 2, 2: 1, 3: 2, 4: 1, 5: 4}
AXES = {7: 3}
MAX_PAYLOAD = 16 * 1024 * 1024
KNOWN_CH = set(CHANNELS.keys())


def decode_records(payload: memoryview, off: int, n_rec: int, ver: int = VERSION):
    """Yields (patch_id, patient_id, seq, flags, battery, rssi, {ch: (dtype, n, bytes)}); raises ValueError on malformed data.
    ver 4: 레코드 헤더의 seq 가 u16 (14 바이트), ver 3: u32 (16 바이트)."""
    rh = rec_head(ver)
    for _ in range(n_rec):
        if off + rh.size > len(payload):
            raise ValueError("record header beyond payload")
        patch_id, patient_id, seq, flags, batt, rssi, n_ch = rh.unpack_from(payload, off)
        off += rh.size
        chans = {}
        for _ in range(n_ch):
            if off + 4 > len(payload):
                raise ValueError("channel header beyond payload")
            ch, dt, n = struct.unpack_from("<BBH", payload, off)
            off += 4
            if ch not in KNOWN_CH:
                raise ValueError(f"unknown_channel:{ch}")
            if dt not in ITEM:
                raise ValueError(f"bad_dtype:{dt}")
            size = n * ITEM[dt] * AXES.get(ch, 1)
            if off + size > len(payload):
                raise ValueError("channel data beyond payload")
            chans[ch] = (dt, n, bytes(payload[off: off + size]))
            off += size
        yield patch_id, patient_id, seq, flags, batt, rssi, chans
    if off != len(payload):
        raise ValueError("trailing bytes after records")



PATCH_SEQ_RESTART_MAX = 8           # 이 번호 이하로 되돌아가면 '재시작' 후보
PATCH_SEQ_RESTART_MIN_BACK = 256     # ... 그리고 직전 번호보다 이만큼 이상 뒤일 때 (재전송·재정렬 창은 이보다 훨씬 짧다)

class StreamChecker:
    """One per TCP connection (or per capture file).  Counters are global and per gw_id."""

    def __init__(self, on_frame=None):
        self.buf = bytearray()
        self.counts = Counter()
        self.per_gw: dict[int, Counter] = defaultdict(Counter)
        self.last_seq: dict[int, int] = {}
        self.last_ts: dict[int, int] = {}
        self.seen_seq: dict[int, set] = defaultdict(set)
        self.patch_seq: dict[int, int] = {}                  # patch_id -> highest per-patch packet seq seen (protocol v2)
        self.per_patch: dict[int, Counter] = defaultdict(Counter)
        self.on_frame = on_frame
        self.on_corrupt = None                                # callback(gw_id, seq) for CRC failures
        self.expected: dict[int, set] = defaultdict(set)     # gw -> seqs we asked to be resent (arrive "late" on purpose)
        self.frames = 0

    def expect(self, gw_id: int, seq_from: int, seq_to: int) -> None:
        """Mark a seq range as requested (NACK): when those frames arrive they count as resend_ok, not seq_reorder."""
        self.expected[gw_id].update(range(seq_from, seq_to + 1))

    # -- stream handling
    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            if len(self.buf) < HEADER.size:
                return
            magic, ver, flags, gw_id, seq, ts_ms, n_rec, plen = HEADER.unpack_from(self.buf, 0)
            if magic != MAGIC or ver not in VERSIONS or plen > MAX_PAYLOAD:
                if magic != MAGIC:
                    self.counts["bad_magic"] += 1
                elif ver not in VERSIONS:
                    self.counts["bad_version"] += 1
                    self.per_gw[gw_id]["bad_version"] += 1
                else:
                    self.counts["oversize"] += 1
                    self.per_gw[gw_id]["oversize"] += 1
                self._resync()
                continue
            tot = HEADER.size + plen + CRC.size
            if len(self.buf) < tot:
                return                                        # wait for the rest (or EOF -> truncated)
            body = bytes(self.buf[: HEADER.size + plen])
            if not frame_crc_ok(body, bytes(self.buf[HEADER.size + plen: tot])):
                # corrupt bytes (or a wrong length): count, tell the owner (it may NACK this seq) and resync on the next header
                self.counts["bad_crc"] += 1
                self.per_gw[gw_id]["bad_crc"] += 1
                if self.on_corrupt:
                    self.on_corrupt(gw_id, seq)
                self._resync()
                continue
            payload = memoryview(body[HEADER.size:])
            del self.buf[:tot]
            if flags & F_CTRL:
                self.counts["ctrl"] += 1                      # control frames only travel router -> gateway; harmless if seen
                continue
            self.cur_ver = ver                                # 콜백(라우터 저장소)이 레코드 seq 폭(v3 u32 / v4 u16)을 알 수 있게
            self._frame(flags, gw_id, seq, ts_ms, n_rec, payload, ver)

    def close(self) -> None:
        """EOF: a leftover that starts like a frame header (or any partial header) is a truncated frame."""
        if len(self.buf) >= 2 and self.buf[0] == 0x42 and self.buf[1] == 0x47:
            self.counts["truncated"] += 1
        elif self.buf:
            self.counts["trailing_bytes"] += 1
        self.buf.clear()

    def _resync(self) -> None:
        """Drop bytes until the next plausible header (magic + version) - counts the skipped run as garbage."""
        self.counts["resync"] += 1
        i = 1
        while i + 3 <= len(self.buf):
            j = self.buf.find(b"\x42\x47", i)
            if j < 0:
                self.counts["garbage_bytes"] += len(self.buf)
                self.buf.clear()
                return
            if j + 2 < len(self.buf) and self.buf[j + 2] in VERSIONS:
                self.counts["garbage_bytes"] += j
                del self.buf[:j]
                return
            i = j + 1
        self.counts["garbage_bytes"] += len(self.buf)
        self.buf.clear()

    # -- frame validation
    def _frame(self, flags, gw_id, seq, ts_ms, n_rec, payload, ver: int = VERSION) -> None:
        g = self.per_gw[gw_id]
        self.frames += 1
        g["frames"] += 1
        off = 0
        try:
            if flags & F_GWSTAT:
                if len(payload) < GWSTAT.size:
                    raise ValueError("gwstat block short")
                off += GWSTAT.size
            if flags & F_META:
                if off + 4 > len(payload):
                    raise ValueError("meta length missing")
                (mlen,) = struct.unpack_from("<I", payload, off)
                off += 4
                if off + mlen > len(payload):
                    raise ValueError("meta beyond payload")
                off += mlen
                g["meta"] += 1
            if flags & F_KEEPALIVE and n_rec != 0:
                raise ValueError("keepalive with records")
            recs = list(decode_records(payload, off, n_rec, ver))
        except ValueError as ex:
            kind = str(ex).split(":")[0]
            key = kind if kind in ("unknown_channel", "bad_dtype") else "bad_record"
            self.counts[key] += 1
            g[key] += 1
            recs = []
        # sequence / time order per gateway: `hi` is the highest seq seen; a frame below `hi` that was never seen is a
        # reorder (late arrival), one already seen is a duplicate, one above hi+1 opens a gap
        s = self.seen_seq[gw_id]
        hi = self.last_seq.get(gw_id)
        if hi is not None:
            if seq in s:
                self.counts["seq_dup"] += 1
                g["seq_dup"] += 1
            elif seq > hi or (hi - seq) > 0x80000000:          # forward (or 32-bit wrap)
                d = (seq - hi) & 0xFFFFFFFF
                if d > 1:
                    self.counts["seq_gap"] += 1
                    g["seq_gap"] += 1
                    g["seq_missing"] += d - 1
                self.last_seq[gw_id] = seq
            elif seq in self.expected.get(gw_id, ()):
                self.expected[gw_id].discard(seq)
                self.counts["resend_ok"] += 1
                g["resend_ok"] += 1
            else:
                self.counts["seq_reorder"] += 1
                g["seq_reorder"] += 1
        else:
            self.last_seq[gw_id] = seq
        resend = bool(flags & F_RESEND)                       # v4 패치 재전송 프레임: ts_ms 는 원래(과거) 시각이라 시간 역행이 아니다
        if ts_ms < self.last_ts.get(gw_id, 0) and not resend:
            self.counts["ts_backwards"] += 1
            g["ts_backwards"] += 1
        if not resend:
            self.last_ts[gw_id] = max(ts_ms, self.last_ts.get(gw_id, 0))
        s.add(seq)
        if len(s) > 4096:
            s.clear()
        # per-patch packet sequence: the number a patch stamps on every packet it produces.  A gap here means packets were
        # lost somewhere between patch and router (BLE dropout, gateway drop, SAF eviction); the gateway frame seq above only
        # covers gateway -> router.  Pace records repeat the data record's seq, so a repeat inside one frame is not a dup.
        seen_now = set()
        mask = seq_mask(ver)
        half = (mask >> 1) + 1
        for patch_id, _pid, pseq, *_ in recs:
            if patch_id in seen_now:
                continue
            seen_now.add(patch_id)
            pp = self.per_patch[patch_id]
            pp["records"] += 1
            if resend:                                         # 재전송분: 빠졌던 번호를 채우는 것 — 순서·공백 판정에 넣지 않는다
                self.counts["patch_resend"] += 1; pp["resend"] += 1
                continue
            last = self.patch_seq.get(patch_id)
            if last is not None:
                d = (pseq - last) & mask
                if d == 0:
                    self.counts["patch_seq_dup"] += 1; pp["dup"] += 1
                elif d >= half and pseq <= PATCH_SEQ_RESTART_MAX and ((last - pseq) & mask) >= PATCH_SEQ_RESTART_MIN_BACK:
                    # 패치 번호가 처음(0 근처)으로 돌아감 = 오류.  패치는 배터리가 다할 때까지 꺼지지 않으므로 재부팅·재시작은 정상 동작이 아니다
                    # (새 패치는 새 patch_id 로 온다).  장애로 보고 이후 번호는 새 기준으로 이어서 센다.
                    self.counts["patch_seq_restart"] += 1; pp["restart"] += 1
                elif d >= half:
                    self.counts["patch_seq_reorder"] += 1; pp["reorder"] += 1
                    continue
                elif d > 1:
                    self.counts["patch_seq_gap"] += 1; pp["gap"] += 1
                    self.counts["patch_seq_missing"] += d - 1; pp["missing"] += d - 1
            self.patch_seq[patch_id] = pseq
        if self.on_frame:
            self.on_frame(gw_id, seq, ts_ms, flags, n_rec, recs, payload)

    def summary(self) -> dict:
        keys = ["bad_magic", "bad_version", "bad_len", "oversize", "truncated", "resync", "garbage_bytes", "seq_gap", "seq_dup", "seq_reorder", "ts_backwards",
                "patch_seq_gap", "patch_seq_missing", "patch_seq_dup", "patch_seq_reorder", "patch_seq_restart", "patch_resend", "bad_crc", "resend_ok", "unknown_channel", "bad_dtype", "bad_record", "trailing_bytes"]
        return {"frames": self.frames, "gateways": len(self.per_gw), **{k: int(self.counts.get(k, 0)) for k in keys}}
