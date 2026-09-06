"""Protocol round trip + strict checker detects every fuzz kind the emulator can inject."""
import struct
import numpy as np
from emulator.runtime.protocol import frame, HEADER, gwstat_block, meta_block, F_GWSTAT, F_META, F_KEEPALIVE
from emulator.runtime.verify import StreamChecker
from emulator.config import CH_HR


def rec(patch_id=1, patient_id=2, hr=77, pseq=0):
    head = struct.pack("<IIIBBbB", patch_id, patient_id, pseq, 0, 90, -50, 1)
    return head + struct.pack("<BBH", CH_HR, 2, 1) + bytes([hr])


def good(seq, gw=7, ts=1000, meta=False, gwstat=False):
    flags = (F_META if meta else 0) | (F_GWSTAT if gwstat else 0)
    pre = (gwstat_block(10, 20, 30, -40, 2, 0, 100, 35) if gwstat else b"") + (meta_block({"gw": "x"}) if meta else b"")
    return frame(gw, seq, ts, 1, pre + rec(pseq=seq), flags)   # the patch numbers its packets too


def run(chunks):
    c = StreamChecker()
    for ch in chunks:
        c.feed(ch)
    c.close()
    return c.summary()


def test_clean_stream_has_no_anomalies():
    s = run([good(i, ts=1000 + i) for i in range(1, 50)] + [good(50, meta=True, gwstat=True, ts=2000)])
    assert s["frames"] == 50 and not any(s[k] for k in s if k not in ("frames", "gateways"))


def test_split_delivery_is_reassembled():
    data = b"".join(good(i, ts=i) for i in range(1, 20))
    s = run([data[i: i + 7] for i in range(0, len(data), 7)])
    assert s["frames"] == 19 and s["truncated"] == 0


def test_bad_magic_and_version_resync():
    f1, f2, f3 = good(1, ts=1), good(2, ts=2), good(3, ts=3)
    bad_magic = struct.pack("<H", 0x4D4D) + f2[2:]
    bad_ver = f2[:2] + b"\xff" + f2[3:]
    s = run([f1, bad_magic, f3])
    assert s["bad_magic"] == 1 and s["frames"] == 2
    s = run([f1, bad_ver, f3])
    assert s["bad_version"] == 1 and s["frames"] == 2


def test_seq_gap_dup_reorder_and_ts_backwards():
    s = run([good(1, ts=1), good(2, ts=2), good(5, ts=5), good(5, ts=5), good(4, ts=4), good(6, ts=3)])
    assert s["seq_gap"] == 1 and s["seq_dup"] == 1 and s["seq_reorder"] == 1 and s["ts_backwards"] >= 1


def test_truncated_and_garbage():
    f1, f2 = good(1, ts=1), good(2, ts=2)
    s = run([f1, bytes(np.random.default_rng(1).integers(0, 256, 100, dtype=np.uint8)), f2])
    assert s["resync"] >= 1 and s["frames"] >= 1
    s = run([f1, f2[: HEADER.size + 5]])
    assert s["truncated"] == 1 and s["frames"] == 1
    s = run([f1, f2[:10]])                                  # even a partial header counts as a cut-off frame
    assert s["truncated"] == 1


def test_oversize_length_and_bad_record():
    f1 = good(1, ts=1)
    magic, ver, flags, gw, seq, ts, n_rec, plen = HEADER.unpack(f1[:HEADER.size])
    over = HEADER.pack(magic, ver, flags, gw, seq, ts, n_rec, 0x7FFFFFF0) + f1[HEADER.size:]
    s = run([over, good(2, ts=2)])
    assert s["oversize"] == 1
    bogus = struct.pack("<IIIBBbB", 0xFFFFFFF0, 0, 0, 0, 100, -40, 1) + struct.pack("<BBH", 250, 9, 4) + b"\x00" * 8
    bad = HEADER.pack(magic, ver, flags, gw, seq, ts, n_rec + 1, plen + len(bogus)) + f1[HEADER.size:] + bogus
    s = run([bad])
    assert s["unknown_channel"] == 1


def test_patch_seq_gap_is_counted():
    """A patch's own packet counter skipping numbers = packets lost between patch and router (not a gateway frame gap)."""
    chk = StreamChecker()
    for i, pseq in enumerate([1, 2, 3, 6, 7]):                      # 4 and 5 never arrived
        chk.feed(frame(7, i + 1, 1000 + i, 1, rec(patch_id=42, pseq=pseq), 0))
    sm = chk.summary()
    assert sm["seq_gap"] == 0 and sm["patch_seq_gap"] == 1 and sm["patch_seq_missing"] == 2
    assert chk.per_patch[42]["missing"] == 2
