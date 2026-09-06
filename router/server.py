"""Router server: thousands of gateway TCP sockets in, per-patch files out.

Every connection feeds a StreamChecker (the emulator's own protocol validator: resync after corrupt bytes, seq gap/dup/
reorder, ts backwards, unknown channel/dtype).  Frames are attributed to gateways by the gw_id in the header, so the
per-gateway and the shared socket modes of the emulator both work.  Records are handed to the PatchStore; META blocks
are kept per gateway; GW_STATUS blocks update the gateway table.  A small HTTP API exposes status, gateways, patches and
anomalies, and the same status can be reported to the emulator (`POST /api/v1/router/status`).
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import struct
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from emulator.runtime.protocol import HEADER, GWSTAT, F_GWSTAT, F_META, F_KEEPALIVE   # noqa: E402
from emulator.runtime.verify import StreamChecker                                     # noqa: E402
from .store import PatchStore                                                          # noqa: E402


class RouterServer:
    def __init__(self, host: str, port: int, store: PatchStore, emulator_url: str | None = None, report_every: float = 5.0):
        self.host, self.port = host, port
        self.store = store
        self.emulator_url = emulator_url.rstrip("/") if emulator_url else None
        self.report_every = report_every
        self.started_at = time.time()
        self.conns: dict[int, dict] = {}                  # conn id -> {addr, gws:set, frames, bytes, checker}
        self.gws: dict[int, dict] = {}                    # gw_id -> table row
        self.events: collections.deque = collections.deque(maxlen=500)
        self.totals = collections.Counter()
        self._rate_prev = (time.time(), collections.Counter())
        self.rates: dict[str, float] = {}
        self._conn_seq = 0
        self._server: asyncio.AbstractServer | None = None
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- connections
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._conn_seq += 1
        cid = self._conn_seq
        addr = writer.get_extra_info("peername")
        conn = {"id": cid, "addr": f"{addr[0]}:{addr[1]}" if addr else "?", "gws": set(), "frames": 0, "bytes": 0, "opened": time.time()}
        chk = StreamChecker(lambda *a, _c=conn: self._on_frame(_c, *a))
        conn["checker"] = chk
        self.conns[cid] = conn
        self.totals["connections"] += 1
        try:
            while True:
                data = await reader.read(1 << 16)
                if not data:
                    break
                conn["bytes"] += len(data)
                self.totals["bytes"] += len(data)
                chk.feed(data)
        except (ConnectionResetError, asyncio.IncompleteReadError, OSError):
            pass
        finally:
            chk.close()
            for k, v in chk.counts.items():
                self.totals[f"anom_{k}"] += v
            self.conns.pop(cid, None)
            for g in conn["gws"]:
                row = self.gws.get(g)
                if row and row.get("conn") == cid:
                    row["conn"] = None
                    row["connected"] = False
                    self._event("link", f"gw {g} disconnected ({conn['addr']})")
            writer.close()

    def _on_frame(self, conn: dict, gw_id: int, seq: int, ts_ms: int, flags: int, n_rec: int, recs: list, payload) -> None:
        conn["frames"] += 1
        self.totals["frames"] += 1
        row = self.gws.get(gw_id)
        if row is None:
            row = self.gws[gw_id] = {"gw_id": gw_id, "conn": conn["id"], "addr": conn["addr"], "connected": True, "first_seen": time.time(),
                                     "frames": 0, "records": 0, "keepalive": 0, "last_seq": seq, "last_ts": ts_ms, "status": None, "dup_conn": 0}
            self._event("link", f"gw {gw_id} connected ({conn['addr']})")
        elif row.get("conn") not in (None, conn["id"]) and row["conn"] in self.conns:
            row["dup_conn"] += 1                          # the same gw_id is alive on another socket (emulator drill 'dup_id')
            self.totals["dup_gw"] += 1
            if row["dup_conn"] == 1:
                self._event("warn", f"gw {gw_id}: frames from a second connection {conn['addr']} (already on conn {row['conn']})")
        if row.get("conn") != conn["id"]:
            row["conn"] = conn["id"]; row["addr"] = conn["addr"]; row["connected"] = True
        conn["gws"].add(gw_id)
        row["frames"] += 1
        row["last_seq"] = seq
        row["last_ts"] = ts_ms
        row["last_seen"] = time.time()
        if flags & F_KEEPALIVE:
            row["keepalive"] += 1
        off = 0
        if flags & F_GWSTAT and len(payload) >= GWSTAT.size:
            cpu, mem, net, wan, n_conn, status, uptime, temp = GWSTAT.unpack_from(payload, 0)
            row["status"] = {"cpu": cpu, "mem": mem, "net": net, "wan_rssi": wan, "n_conn": n_conn, "status": status, "uptime_s": uptime, "temp_c": temp}
            off += GWSTAT.size
        if flags & F_META and off + 4 <= len(payload):
            (mlen,) = struct.unpack_from("<I", payload, off)
            try:
                meta = json.loads(bytes(payload[off + 4: off + 4 + mlen]).decode("utf-8"))
                row["meta_at"] = time.time()
                row["patches"] = len(meta.get("patches", [])) if isinstance(meta, dict) else None
                self.store.set_meta(gw_id, meta)
                self.totals["meta"] += 1
            except Exception:
                self.totals["anom_meta_json"] += 1
        for patch_id, patient_id, pseq, fl, batt, rssi, chans in recs:
            self.store.write(patch_id, ts_ms, gw_id, patient_id, pseq, fl, batt, rssi, chans)
        row["records"] += len(recs)
        self.totals["records"] += len(recs)

    def _event(self, kind: str, msg: str) -> None:
        self.events.append({"t": time.time(), "kind": kind, "msg": msg})

    # ---------------------------------------------------------------- periodic
    async def _housekeeping(self) -> None:
        last_report = 0.0
        while True:
            await asyncio.sleep(1.0)
            try:
                self.store.flush()
            except Exception as ex:
                self._event("error", f"store flush: {ex}")
            now = time.time()
            t0, prev = self._rate_prev
            dt = max(1e-3, now - t0)
            self.rates = {k: (self.totals[k] - prev.get(k, 0)) / dt for k in ("frames", "bytes", "records")}
            self._rate_prev = (now, collections.Counter(self.totals))
            for g in self.gws.values():                  # gateway silence: no frame for 10 s while its socket is up
                if g["connected"] and now - g.get("last_seen", now) > 10 and not g.get("silent"):
                    g["silent"] = True; self._event("warn", f"gw {g['gw_id']} silent for 10 s")
                elif g.get("silent") and now - g.get("last_seen", now) <= 10:
                    g["silent"] = False
            if self.emulator_url and now - last_report >= self.report_every:
                last_report = now
                threading.Thread(target=self._report, daemon=True).start()

    def _report(self) -> None:
        try:
            body = json.dumps(self.status()).encode("utf-8")
            req = urllib.request.Request(self.emulator_url + "/api/v1/router/status", data=body, headers={"content-type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3).read()
        except Exception as ex:
            self._event("error", f"report to emulator failed: {type(ex).__name__}")

    # ---------------------------------------------------------------- views
    def status(self) -> dict:
        anomalies = {k[5:]: v for k, v in self.totals.items() if k.startswith("anom_")}
        live = collections.Counter()
        for c in self.conns.values():
            for k, v in c["checker"].counts.items():
                live[k] += v
        for k, v in live.items():
            anomalies[k] = anomalies.get(k, 0) + v
        return {"router": "biosim-router", "uptime_s": round(time.time() - self.started_at, 1), "listen": f"{self.host}:{self.port}",
                "connections": len(self.conns), "gateways": len(self.gws), "gateways_connected": sum(1 for g in self.gws.values() if g["connected"]),
                "patches": len(self.store.index), "frames": self.totals["frames"], "records": self.totals["records"], "bytes": self.totals["bytes"],
                "meta_blocks": self.totals["meta"], "dup_gw_frames": self.totals["dup_gw"],
                "patch_packets_lost": sum(ix.get("lost", 0) for ix in self.store.index.values()),
                "rates": {k: round(v, 1) for k, v in self.rates.items()}, "anomalies": anomalies,
                "store": {"root": str(self.store.root), "bytes_written": self.store.bytes_written, "open_files": len(self.store.files)}}

    def gateways_view(self) -> list[dict]:
        return [{k: v for k, v in g.items() if k != "conn"} | {"conn": g.get("conn")} for g in sorted(self.gws.values(), key=lambda g: g["gw_id"])]

    # ---------------------------------------------------------------- run
    async def serve(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port, backlog=4096, limit=1 << 20)
        self._event("system", f"listening on {self.host}:{self.port}")
        hk = asyncio.create_task(self._housekeeping())
        try:
            async with self._server:
                await self._server.serve_forever()
        finally:
            hk.cancel()
            self.store.close()

    def stop(self) -> None:
        if self._server:
            self._server.close()
