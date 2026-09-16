"""Router end-to-end: frames over a TCP socket -> per-patch files, gateway table, status, dup-gw detection."""
import asyncio
import json
import socket
import struct
import threading
import time

import numpy as np

from emulator.runtime.protocol import frame, gwstat_block, meta_block, F_GWSTAT, F_META, HEADER, CRC, parse_ctrl, CTRL_NACK
from router.server import RouterServer
from router.store import PatchStore, iter_entries, verify_file


_PSEQ = {}


def _record(patch_id, patient_id, n_ecg=50, pseq=None):
    if pseq is None:
        _PSEQ[patch_id] = _PSEQ.get(patch_id, 0) + 1
        pseq = _PSEQ[patch_id]
    ecg = (np.arange(n_ecg, dtype=np.int16) * 3).tobytes()
    hr = struct.pack("<H", 72)
    body = struct.pack("<IIIBBbB", patch_id, patient_id, pseq, 0, 97, -55, 2)
    body += struct.pack("<BBH", 1, 1, n_ecg) + ecg          # ch1 ECG int16
    body += struct.pack("<BBH", 2, 3, 1) + hr               # ch2 HR uint16
    return body


def _start(tmp_path):
    store = PatchStore(tmp_path / "router", flush_interval=0.2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    rs = RouterServer("127.0.0.1", port, store)
    loop = asyncio.new_event_loop()
    th = threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_until_complete(rs.serve())), daemon=True)
    th.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.05)
    return rs, store, port


def test_router_stores_records_and_tracks_gateways(tmp_path):
    rs, store, port = _start(tmp_path)
    c = socket.create_connection(("127.0.0.1", port))
    payload = gwstat_block(10, 20, 30, -40, 2, 0, 1000, 45) + meta_block({"gw": 7, "patches": [{"patch_id": 1001}, {"patch_id": 1002}]}) + _record(1001, 55) + _record(1002, 56)
    c.sendall(frame(7, 1, 1000, 2, payload, F_GWSTAT | F_META))
    for seq in range(2, 6):
        c.sendall(frame(7, seq, 1000 + seq * 200, 2, _record(1001, 55) + _record(1002, 56), 0))
    c.sendall(frame(8, 1, 5000, 1, _record(2001, 77), 0))
    time.sleep(1.2)
    st = rs.status()
    assert st["gateways"] == 2 and st["frames"] == 6 and st["records"] == 11 and st["meta_blocks"] == 1
    assert st["anomalies"] == {}
    g7 = next(g for g in rs.gateways_view() if g["gw_id"] == 7)
    assert g7["status"]["cpu"] == 10 and g7["last_seq"] == 5 and g7["records"] == 10
    store.flush(force=True)
    p = store.patch(1001)
    assert p and p["records"] == 5 and p["patient_id"] == 55 and p["gw_id"] == 7
    files = list((tmp_path / "router" / "patches" / "00001001").glob("*.rec"))
    assert len(files) == 1
    entries = list(iter_entries(files[0]))
    assert len(entries) == 5 and entries[0][1] == 7 and entries[0][2] == 55 and 1 in entries[0][7] and 2 in entries[0][7]
    assert [e[3] for e in entries] == [1, 2, 3, 4, 5]               # the patch's own packet numbers, in order
    assert json.load(open(tmp_path / "router" / "meta" / "gw_7.json"))["gw"] == 7
    # sequence gap and a second socket claiming the same gw_id are flagged
    c.sendall(frame(7, 9, 9000, 1, _record(1001, 55, pseq=9), 0))   # patch packets 6..8 lost somewhere upstream
    c2 = socket.create_connection(("127.0.0.1", port))
    c2.sendall(frame(7, 10, 9200, 1, _record(1001, 55), 0))
    time.sleep(0.5)
    st = rs.status()
    assert st["anomalies"].get("seq_gap", 0) >= 1
    assert st["anomalies"].get("patch_seq_gap", 0) >= 1
    assert st["dup_gw_frames"] >= 1
    store.flush(force=True)
    assert store.patch(1001)["lost"] >= 3
    c.close(); c2.close(); rs.stop()


def _read_frame(sock, timeout=3.0):
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < HEADER.size:
        buf += sock.recv(4096)
    plen = HEADER.unpack_from(buf, 0)[7]
    while len(buf) < HEADER.size + plen + CRC.size:
        buf += sock.recv(4096)
    return buf


def test_router_nacks_gaps_and_counts_recovery(tmp_path):
    """v3: a seq gap makes the router send a NACK control frame back on the gateway's socket; the resent frames are counted
    as recovered (not as reorders), and a corrupt frame is NACKed by its seq.  Store entries carry a CRC that verifies."""
    rs, store, port = _start(tmp_path)
    c = socket.create_connection(("127.0.0.1", port))
    c.sendall(frame(9, 1, 1000, 1, _record(3001, 88), 0))
    c.sendall(frame(9, 2, 1200, 1, _record(3001, 88), 0))
    c.sendall(frame(9, 5, 1800, 1, _record(3001, 88), 0))          # 3 and 4 lost on the way
    nack = _read_frame(c)
    hdr = HEADER.unpack_from(nack, 0)
    assert hdr[2] & 0x08 and hdr[3] == 9
    assert parse_ctrl(nack[HEADER.size: -CRC.size]) == (CTRL_NACK, 3, 4)
    c.sendall(frame(9, 3, 1400, 1, _record(3001, 88, pseq=3), 0))  # the gateway answers from its keep buffer
    c.sendall(frame(9, 4, 1600, 1, _record(3001, 88, pseq=4), 0))
    time.sleep(0.6)
    st = rs.status()
    assert st["nack_tx"] == 1 and st["recovered"] == 2 and st["resend_pending"] == 0
    assert st["anomalies"].get("seq_reorder", 0) == 0 and st["anomalies"].get("resend_ok", 0) == 2
    # corrupt frame -> bad_crc + NACK for that seq
    bad = bytearray(frame(9, 6, 2000, 1, _record(3001, 88), 0)); bad[HEADER.size + 2] ^= 0xFF
    time.sleep(0.6)                                                 # NACK rate limit
    c.sendall(bytes(bad)); c.sendall(frame(9, 7, 2200, 1, _record(3001, 88), 0))
    nack2 = _read_frame(c)
    assert parse_ctrl(nack2[HEADER.size: -CRC.size])[1:] == (6, 6)
    time.sleep(0.3)
    assert rs.status()["anomalies"].get("bad_crc", 0) == 1
    store.flush(force=True)
    f = next((tmp_path / "router" / "patches" / "00003001").glob("*.rec"))
    v = verify_file(f)
    assert v["ok"] and v["entries"] == 6 and v["bad"] == 0
    raw = bytearray(f.read_bytes()); raw[40] ^= 0x01; f.write_bytes(bytes(raw))   # flip a bit on disk
    v2 = verify_file(f)
    assert v2["bad"] >= 1 and not v2["ok"]
    assert store.verify_patch(3001)["bad"] >= 1
    c.close(); rs.stop()
