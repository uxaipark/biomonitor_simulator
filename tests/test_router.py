"""Router end-to-end: frames over a TCP socket -> per-patch files, gateway table, status, dup-gw detection."""
import asyncio
import json
import socket
import struct
import threading
import time

import numpy as np

from emulator.runtime.protocol import frame, gwstat_block, meta_block, F_GWSTAT, F_META
from router.server import RouterServer
from router.store import PatchStore, iter_entries


def _record(patch_id, patient_id, n_ecg=50):
    ecg = (np.arange(n_ecg, dtype=np.int16) * 3).tobytes()
    hr = struct.pack("<H", 72)
    body = struct.pack("<IIBBbB", patch_id, patient_id, 0, 97, -55, 2)
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
    assert len(entries) == 5 and entries[0][1] == 7 and entries[0][2] == 55 and 1 in entries[0][6] and 2 in entries[0][6]
    assert json.load(open(tmp_path / "router" / "meta" / "gw_7.json"))["gw"] == 7
    # sequence gap and a second socket claiming the same gw_id are flagged
    c.sendall(frame(7, 9, 9000, 1, _record(1001, 55), 0))
    c2 = socket.create_connection(("127.0.0.1", port))
    c2.sendall(frame(7, 10, 9200, 1, _record(1001, 55), 0))
    time.sleep(0.5)
    st = rs.status()
    assert st["anomalies"].get("seq_gap", 0) >= 1
    assert st["dup_gw_frames"] >= 1
    c.close(); c2.close(); rs.stop()
