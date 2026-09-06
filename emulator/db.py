"""SQLite persistence for the emulator (stdlib sqlite3, WAL mode).

Stores what a real hospital system would keep and what the router may want
to query later: patient profiles, admissions/discharges, the patch registry
(every patch ever issued), exam schedules, and the event log.  The fast path
never touches the DB; the world thread writes in small batches once a second.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import BASE_DIR

DB_PATH = BASE_DIR / "emulator.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS patients (
  id INTEGER PRIMARY KEY, mrn TEXT UNIQUE, name TEXT, sex TEXT, age INTEGER, birth_date TEXT, nationality TEXT,
  disease TEXT, icd10 TEXT, disease_group TEXT, ward_specialty TEXT, rhythm TEXT, pacemaker INTEGER,
  pacemaker_json TEXT, devices_json TEXT, status TEXT, profile_json TEXT, updated_at REAL);
CREATE INDEX IF NOT EXISTS ix_patients_name ON patients(name);
CREATE INDEX IF NOT EXISTS ix_patients_status ON patients(status);
CREATE TABLE IF NOT EXISTS admissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER, mode TEXT, admit_time REAL, discharge_time REAL, discharge_reason TEXT,
  ward TEXT, ward_name TEXT, room TEXT, bed TEXT, doctor TEXT, nurse TEXT, first_patch TEXT);
CREATE INDEX IF NOT EXISTS ix_adm_patient ON admissions(patient_id);
CREATE TABLE IF NOT EXISTS patches (
  serial TEXT PRIMARY KEY, patch_id INTEGER UNIQUE, patient_id INTEGER, patient_name TEXT, status TEXT,
  issued_at REAL, issued_reason TEXT, retired_at REAL, retired_reason TEXT, battery_at_retire REAL);
CREATE INDEX IF NOT EXISTS ix_patches_patient ON patches(patient_id);
CREATE INDEX IF NOT EXISTS ix_patches_status ON patches(status);
CREATE TABLE IF NOT EXISTS patch_events (id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL, serial TEXT, patient_id INTEGER, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS exams (id INTEGER PRIMARY KEY AUTOINCREMENT, admission_id INTEGER, patient_id INTEGER, type TEXT, room TEXT,
  scheduled_at REAL, duration_min INTEGER, patch_policy TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, t REAL, kind TEXT, msg TEXT, patient_id INTEGER, gw INTEGER);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind);
CREATE TABLE IF NOT EXISTS gateways (
  gw_no INTEGER PRIMARY KEY, row INTEGER, gw_id TEXT, mac TEXT, type TEXT, building TEXT, floor INTEGER, room TEXT,
  status TEXT, installed_at REAL, installed_reason TEXT, retired_at REAL, retired_reason TEXT);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL, stopped_at REAL, target TEXT, workers INTEGER,
  patients INTEGER, gateways INTEGER, pkts INTEGER, bytes INTEGER, drops INTEGER);
"""


class DB:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._pending: list[tuple] = []
        self._pending_pe: list[tuple] = []

    # ------------------------------------------------------------- meta / lifecycle
    def get_meta(self, key: str, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, key: str, value) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def reset(self) -> None:
        with self.lock:
            for t in ("patients", "admissions", "patches", "patch_events", "exams", "events", "runs", "gateways", "meta"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.execute("VACUUM")

    def begin_session(self, signature: dict) -> bool:
        """Keep history when the world signature (seed/capacity/profiles) is unchanged; otherwise start fresh.
        Returns True when history was kept."""
        keep = self.get_meta("signature") == signature
        if not keep:
            self.reset()
            self.set_meta("signature", signature)
            self.set_meta("created_at", time.time())
        else:
            # runtime state is gone after a restart: every still-active patch is retired
            with self.lock:
                self.conn.execute("UPDATE patches SET status='retired', retired_at=?, retired_reason='에뮬레이터 재시작 (반납)' WHERE status='active'", (time.time(),))
                self.conn.execute("UPDATE admissions SET discharge_time=?, discharge_reason='에뮬레이터 재시작' WHERE discharge_time IS NULL", (time.time(),))
                self.conn.execute("UPDATE patients SET status='pool' WHERE status IN ('admitted','outpatient')")
        self.set_meta("last_start", time.time())
        return keep

    def max_gateway_no(self) -> int:
        with self.lock:
            r = self.conn.execute("SELECT MAX(gw_no) AS m FROM gateways").fetchone()
        return int(r["m"]) if r and r["m"] else 0

    def upsert_gateway(self, g: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO gateways VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (g["gw_no"], g["row"], g["gw_id"], g["mac"], g["type"], g["building"], g["floor"], g["room"], g["status"],
                               g["installed_at"], g["installed_reason"], g.get("retired_at"), g.get("retired_reason")))

    def upsert_gateways(self, rows: list[dict]) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            self.conn.executemany("INSERT OR REPLACE INTO gateways VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  [(g["gw_no"], g["row"], g["gw_id"], g["mac"], g["type"], g["building"], g["floor"], g["room"], g["status"],
                                    g["installed_at"], g["installed_reason"], g.get("retired_at"), g.get("retired_reason")) for g in rows])
            self.conn.execute("COMMIT")

    def max_patch_serial(self) -> int:
        with self.lock:
            r = self.conn.execute("SELECT MAX(patch_id) AS m FROM patches").fetchone()
        return int(r["m"] - 0x10000) if r and r["m"] else 0

    # ------------------------------------------------------------- patients
    def upsert_patients(self, profiles: list[dict]) -> None:
        rows = [(p["id"], p["mrn"], p["name"], p["sex"], p["age"], p["birth_date"], p["nationality"], p["disease"], p["icd10"],
                 p["disease_group"], p["ward_specialty"], p["rhythm"], int(bool(p["pacemaker"])), json.dumps(p.get("pacemaker_info"), ensure_ascii=False),
                 json.dumps(p.get("devices"), ensure_ascii=False), p.get("status", "pool"),
                 json.dumps({k: v for k, v in p.items() if k not in ("avatar", "admission", "history", "patch_history")}, ensure_ascii=False), time.time())
                for p in profiles]
        with self.lock:
            self.conn.execute("BEGIN")
            self.conn.executemany("INSERT OR REPLACE INTO patients VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.conn.execute("COMMIT")

    def update_patient(self, p: dict) -> None:
        with self.lock:
            self.conn.execute("UPDATE patients SET status=?, devices_json=?, updated_at=? WHERE id=?",
                              (p.get("status", "pool"), json.dumps(p.get("devices"), ensure_ascii=False), time.time(), p["id"]))

    # ------------------------------------------------------------- admissions / exams
    def add_admission(self, pid: int, adm: dict, t: float) -> int:
        with self.lock:
            cur = self.conn.execute("INSERT INTO admissions(patient_id, mode, admit_time, ward, ward_name, room, bed, doctor, nurse, first_patch) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    (pid, adm.get("mode"), t, adm.get("ward"), adm.get("ward_name"), adm.get("room"), adm.get("bed"), adm.get("doctor"), adm.get("nurse"), adm.get("patch")))
            aid = cur.lastrowid
            self.conn.executemany("INSERT INTO exams(admission_id, patient_id, type, room, scheduled_at, duration_min, patch_policy, done) VALUES (?,?,?,?,?,?,?,0)",
                                  [(aid, pid, e["type"], e["room"], e["t"], e["duration_min"], e["patch_policy"]) for e in adm.get("exams", [])])
            self.conn.execute("UPDATE patients SET status=?, updated_at=? WHERE id=?", ("outpatient" if adm.get("mode") == "mcot" else "admitted", t, pid))
        return int(aid)

    def close_admission(self, aid: int | None, pid: int, t: float, reason: str) -> None:
        with self.lock:
            if aid:
                self.conn.execute("UPDATE admissions SET discharge_time=?, discharge_reason=? WHERE id=?", (t, reason, aid))
            self.conn.execute("UPDATE patients SET status='discharged', updated_at=? WHERE id=?", (t, pid))

    def update_admission_bed(self, aid: int | None, ward: str, ward_name: str, room: str, bed: str, nurse: str) -> None:
        if aid:
            with self.lock:
                self.conn.execute("UPDATE admissions SET ward=?, ward_name=?, room=?, bed=?, nurse=? WHERE id=?", (ward, ward_name, room, bed, nurse, aid))

    def mark_exam_done(self, aid: int | None, exam_type: str, t: float) -> None:
        if aid:
            with self.lock:
                self.conn.execute("UPDATE exams SET done=1 WHERE admission_id=? AND type=? AND done=0 AND scheduled_at<=?", (aid, exam_type, t + 1))

    # ------------------------------------------------------------- patches
    def upsert_patch(self, e: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO patches VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (e["serial"], e["patch_id"], e["patient_id"], e["patient_name"], e["status"], e["issued_at"], e["issued_reason"],
                               e["retired_at"], e["retired_reason"], e["battery_at_retire"]))

    def add_patch_event(self, t: float, serial: str, pid: int, kind: str, detail: str) -> None:
        self._pending_pe.append((t, serial, pid, kind, detail))   # written with the next flush() (once a second), not one transaction each

    # ------------------------------------------------------------- events (batched)
    def queue_events(self, events: list[dict]) -> None:
        self._pending.extend((e["seq"], e["t"], e["kind"], e["msg"], e.get("patient_id"), e.get("gw")) for e in events)

    def flush(self) -> None:
        if self._pending_pe:
            rows, self._pending_pe = self._pending_pe, []
            with self.lock:
                self.conn.executemany("INSERT INTO patch_events(t, serial, patient_id, kind, detail) VALUES (?,?,?,?,?)", rows)
        if not self._pending:
            return
        rows, self._pending = self._pending, []
        with self.lock:
            self.conn.executemany("INSERT OR IGNORE INTO events(seq, t, kind, msg, patient_id, gw) VALUES (?,?,?,?,?,?)", rows)

    def prune(self, before: float) -> dict:
        """Retention: drop history older than `before` (retired patches, closed admissions and their exams, patch events, runs)."""
        with self.lock:
            c = self.conn
            n = {}
            n["patch_events"] = c.execute("DELETE FROM patch_events WHERE t < ?", (before,)).rowcount
            n["patches"] = c.execute("DELETE FROM patches WHERE status='retired' AND retired_at < ?", (before,)).rowcount
            n["exams"] = c.execute("DELETE FROM exams WHERE admission_id IN (SELECT id FROM admissions WHERE discharge_time IS NOT NULL AND discharge_time < ?)", (before,)).rowcount
            n["admissions"] = c.execute("DELETE FROM admissions WHERE discharge_time IS NOT NULL AND discharge_time < ?", (before,)).rowcount
            n["events"] = c.execute("DELETE FROM events WHERE t < ?", (before,)).rowcount
            n["runs"] = c.execute("DELETE FROM runs WHERE stopped_at IS NOT NULL AND stopped_at < ?", (before,)).rowcount
            if sum(n.values()):
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return n

    # ------------------------------------------------------------- runs
    def start_run(self, target: str, workers: int, patients: int, gateways: int) -> int:
        with self.lock:
            cur = self.conn.execute("INSERT INTO runs(started_at, target, workers, patients, gateways) VALUES (?,?,?,?,?)", (time.time(), target, workers, patients, gateways))
        return int(cur.lastrowid)

    def stop_run(self, rid: int | None, pkts: int, nbytes: int, drops: int) -> None:
        if rid:
            with self.lock:
                self.conn.execute("UPDATE runs SET stopped_at=?, pkts=?, bytes=?, drops=? WHERE id=?", (time.time(), pkts, nbytes, drops, rid))

    # ------------------------------------------------------------- queries
    def stats(self) -> dict:
        with self.lock:
            counts = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("patients", "admissions", "patches", "patch_events", "exams", "events", "gateways", "runs")}
        size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_name(self.path.name + "-wal")
        if wal.exists():
            size += wal.stat().st_size
        return {"path": str(self.path), "size_mb": round(size / 1e6, 2), "tables": counts, "created_at": self.get_meta("created_at"), "last_start": self.get_meta("last_start")}

    def query(self, sql: str, params: tuple = (), limit: int = 500) -> dict:
        s = sql.strip().rstrip(";")
        low = s.lower()
        if not (low.startswith("select") or low.startswith("with")) or any(k in low for k in (" insert ", " update ", " delete ", " drop ", " alter ", " attach ", " pragma ", "create ")):
            raise ValueError("read-only: SELECT only")
        with self.lock:
            cur = self.conn.execute(f"SELECT * FROM ({s}) LIMIT {int(limit)}", params)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows, "truncated": len(rows) >= limit}

    def close(self) -> None:
        with self.lock:
            self.flush()
            self.conn.close()
