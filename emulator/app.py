"""FastAPI application: discovery / EMR / control / stats APIs, live WebSocket and the web GUI."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config, CHANNELS, RESP_SOURCES, SPO2_SOURCES, DEFAULT_CONFIG
from .hospital.avatars import render_svg
from .hospital.devices import describe as describe_devices, DEVICES
from .hospital import layout as hospital_layout
from .config import BASE_DIR
from .runtime.engine import Engine
from .runtime.protocol import describe as describe_protocol
from .signals.rhythms import RHYTHMS
from .signals import trend as trend_model
from .signals.accel import ACTIVITIES
from .runtime.state import CTL

STATIC = Path(__file__).parent / "web" / "static"
cfg = Config()
engine: Engine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = Engine(cfg)
    yield
    engine.shutdown()


app = FastAPI(title="Bio-Signal Emulator", version=__version__, lifespan=lifespan)
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=2048)       # gateway/patient lists shrink ~10x on the wire (Pi over Wi-Fi)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


WRITE_PATHS = ("/api/v1/control/rebuild", "/api/v1/emr/layout/reset", "/api/v1/emr/layout/import", "/api/v1/config/reset", "/api/v1/control/generate")


@app.middleware("http")
async def _read_guard(request, call_next):
    """Every API request runs under the engine's read lock (rebuilds take the write lock), so a request thread can never
    read a shared-memory array that a concurrent template regeneration is unmapping (SIGSEGV seen 2026-09-06)."""
    path = request.url.path
    if engine is None or not path.startswith("/api/v1/") or any(path.startswith(w) for w in WRITE_PATHS):
        return await call_next(request)
    cm = engine.rw.read()
    await asyncio.to_thread(cm.__enter__)                    # may wait for a rebuild in progress; never blocks the event loop
    try:
        return await call_next(request)
    finally:
        cm.__exit__(None, None, None)


def E() -> Engine:
    if engine is None:
        raise HTTPException(503, "engine not ready")
    return engine


# ------------------------------------------------------------------ discovery (entry point)
@app.get("/api/v1")
@app.get("/api/v1/discovery")
def discovery():
    c = cfg.snapshot()
    s, t = c["signals"], c["transport"]
    fs = {"ecg": s["ecg_fs"], "ppg": s["ppg_fs"], "resp": s["resp_fs"], "accel": s["accel_fs"]}
    e = E()
    return {
        "name": "biosignal-emulator", "version": __version__, "protocol_version": 1,
        "description": "병원/원외 생체신호 에뮬레이터. 라우터 서버는 이 문서를 읽고 EMR API로 정적 정보를 받은 뒤 소켓 수신을 시작한다.",
        "how_to_start": ["1. GET /api/v1 (이 문서) 로 프로토콜/엔드포인트 확인", "2. GET /api/v1/emr/* 로 병원 맵·게이트웨이·환자·의료진 수신 및 BioSignal MAP 구성",
                         "3. transport.target_ip/port 에 TCP 리스너 오픈 (에뮬레이터가 게이트웨이별로 접속)", "4. 프레임 수신: META 블록으로 채널 구성 갱신, 레코드를 패치 번호 파일로 저장",
                         "5. POST /api/v1/router/status 로 라우터 상태를 주기적으로 보고 (선택)"],
        "endpoints": {
            "discovery": "/api/v1", "config": "/api/v1/config [GET, PATCH]", "status": "/api/v1/status", "stats": "/api/v1/stats", "events": "/api/v1/events?since=<seq>",
            "control": {"start": "POST /api/v1/control/start", "stop": "POST /api/v1/control/stop", "rebuild": "POST /api/v1/control/rebuild",
                        "generate_loops": "POST /api/v1/control/generate", "trigger": "POST /api/v1/control/trigger {what: gateway_fault|gateway_replace|network_event|lead_off|episode|vfib|exam|replace_patch|discharge|admit, target}", "autotune": "POST /api/v1/control/autotune {start|stop}",
                        "apply_devices": "POST /api/v1/control/devices/apply {policy: auto|all|minimal}"},
            "emr": {"hospital": "/api/v1/emr/hospital", "floors": "/api/v1/emr/floors", "floor_map": "/api/v1/emr/floors/{building_idx}/{floor}", "wards": "/api/v1/emr/wards",
                    "rooms": "/api/v1/emr/rooms", "beds": "/api/v1/emr/beds", "gateways": "/api/v1/emr/gateways", "staff": "/api/v1/emr/staff",
                    "patients": "/api/v1/emr/patients?status=admitted|pool|discharged|outpatient|all&q=&offset=&limit=", "patient": "/api/v1/emr/patients/{id}",
                    "avatar": "/api/v1/emr/patients/{id}/avatar.svg", "admissions": "/api/v1/emr/admissions (현재 입원+MCOT 환자와 패치/게이트웨이 매핑)",
                    "layout": "/api/v1/emr/layout (건축 도면 JSON v1: 건물·층·실 폴리곤·침대·복도·설비(전광판/간호사 카운터)·게이트웨이·병동·점유; 라우터는 이 JSON으로 동일 화면 구성)",
                    "layout_templates": "/api/v1/emr/layout/templates", "layout_import": "POST /api/v1/emr/layout/import {layout}", "layout_reset": "POST /api/v1/emr/layout/reset",
                    "schedules": "/api/v1/emr/schedules", "patches": "/api/v1/emr/patches?status=active|retired|all&q= (패치 레지스트리: 발급·교체·반납 이력, 번호 재사용 없음)", "devices": "/api/v1/emr/devices (기기 카탈로그·정책·장착 통계)",
                    "set_devices": "POST /api/v1/emr/patients/{id}/devices {devices:[...]}"},
            "signals": {"catalog": "/api/v1/signals/catalog", "preview": "/api/v1/signals/preview/{row}", "trend": "/api/v1/signals/trend/{patient_id}?days=1|2|3 (합성 다일 추세)", "live_ws": "ws://<host>/ws/live?row=<patch_row>"},
            "router": {"status_report": "POST /api/v1/router/status (라우터 → 에뮬레이터 상태 보고)", "status_get": "GET /api/v1/router/status"},
            "db": {"stats": "GET /api/v1/db/stats", "query": "POST /api/v1/db/query {sql, params?, limit?} (SQLite, SELECT 전용; 테이블: patients, admissions, patches, patch_events, exams, events, runs)"},
        },
        "transport": {"target_ip": t["target_ip"] or None, "target_port": t["target_port"], "socket_mode": t["socket_mode"], "bundle_ms": t["bundle_ms"],
                      "meta_every_n_frames": t["meta_every_n_frames"], "gw_status_every_n_frames": t["gw_status_every_n_frames"],
                      "gateway_capacity": c["scenario"]["gateway"]["capacity"], "max_gateways": len(e.world.hospital.gateways), "max_patches": e.world.hospital.bed_capacity + c["general"]["outpatient_count"]},
        "stream_protocol": describe_protocol(t["bundle_ms"], fs, t["meta_every_n_frames"], t["gw_status_every_n_frames"]),
        "signals": {"enabled": s["enabled"], "sampling": fs, "resp_source": s["resp_source"],
                    "resp_sources": RESP_SOURCES, "spo2_sources": SPO2_SOURCES, "loop_seconds": s["loop_seconds"],
                    "per_patient_channels": "each patch sends enabled ∩ device-set channels; see META patches[].devices/channels and /emr/devices"},
        "devices": describe_devices(),
        "scenario": c["scenario"],
        "ids": {"gw_id": "frame gw_id = gateway hardware number (gw_no): unique, changes when a failed unit is replaced (META gw_no/gw/mac; /emr/gateways; DB table gateways)",
                "patch_id": "u32 unique per physical patch, never reused (DB table patches); changes on replacement (META + NEW_PATCH flag)",
                "patient_id": "환자번호: unique per admission, never reused (= DB admissions.id); META patches[].profile_id/mrn map it to the person (/emr/patients/{profile_id}); 0 when unassigned"},
    }


# ------------------------------------------------------------------ config & control
@app.get("/api/v1/config")
def get_config():
    return {"config": cfg.snapshot(), "defaults": DEFAULT_CONFIG, "channels": {k: v for k, v in CHANNELS.items()},
            "rhythms": {k: {"label": v["label"], "cls": v["cls"]} for k, v in RHYTHMS.items()}, "activities": ACTIVITIES,
            "resp_sources": RESP_SOURCES, "spo2_sources": SPO2_SOURCES}


STRUCTURAL = {("general", "seed"), ("general", "bed_capacity"), ("general", "profile_count"), ("general", "heart_disease_ratio"), ("hospital", "template"), ("hospital", "layout_file"),
              ("general", "korean_ratio"), ("signals", "pacemaker_ratio"), ("scenario", "gateway", "capacity"), ("scenario", "gateway", "corridor_gateways")}
BANK_KEYS = {("signals", "ecg_fs"), ("signals", "ppg_fs"), ("signals", "resp_fs"), ("signals", "accel_fs"), ("signals", "variants_per_rhythm"), ("signals", "loop_seconds"), ("general", "seed")}


def _changed_paths(old: dict, new: dict, prefix=()):
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(old.get(k), dict):
            yield from _changed_paths(old[k], v, prefix + (k,))
        elif old.get(k) != v:
            yield prefix + (k,)


def _apply_config(patch: dict, source: str) -> dict:
    e = E()
    old = cfg.snapshot()
    new = cfg.update(patch, source=source)
    changed = set(_changed_paths(old, new))
    needs_rebuild = bool(changed & STRUCTURAL) or (("general", "outpatient_count") in changed and new["general"]["outpatient_count"] > e.world.mobile_pool)
    if ("general", "active_patients") in changed and (new.get("hospital") or {}).get("size_by_patients", True) and not (new.get("hospital") or {}).get("layout_file"):
        planned = e.world.planned_beds(); have = e.world.hospital.bed_capacity
        if planned > have or planned < 0.6 * have:                    # the scenario outgrew (or shrank far below) the generated hospital
            needs_rebuild = True
    needs_bank = bool(changed & BANK_KEYS)
    if not needs_rebuild:
        with e.world.lock:
            e.world.apply_config()
    return {"config": new, "changed": [".".join(c) for c in changed], "needs_rebuild": needs_rebuild, "needs_generate": needs_bank,
            "note": "구조 설정(병상수/시드/게이트웨이 용량)은 POST /control/rebuild, 샘플링/변형 수는 /control/generate 로 반영"}


@app.patch("/api/v1/config")
async def patch_config(patch: dict, request: Request):
    return _apply_config(patch, request.headers.get("x-source", "api"))


@app.get("/api/v1/config/history")
def config_history(limit: int = Query(200, le=2000), offset: int = 0, path: str = ""):
    """Every setting change with time, source (gui/api/autotune/reset/restore), run id, path and old -> new value."""
    return E().world.db.config_history(limit, offset, path)


@app.get("/api/v1/runs")
def runs(limit: int = Query(50, le=500), offset: int = 0):
    """Transmission runs with the full scenario snapshot taken at start (GET /runs/{id}/config)."""
    return {"runs": E().world.db.runs(limit, offset), "current": getattr(E(), "run_id", None) if E().running else None}


@app.get("/api/v1/runs/{rid}/config")
def run_config(rid: int):
    c = E().world.db.run_config(rid)
    if c is None:
        raise HTTPException(404, "no snapshot for this run")
    return {"run_id": rid, "config": c}


@app.post("/api/v1/config/restore")
async def config_restore(body: dict):
    """{run_id}: put the whole scenario back to that run's snapshot.  {history_id}: undo one change (path back to its old value).
    Applied like a PATCH (needs_rebuild / needs_generate flags), recorded in the history with source 'restore'."""
    db = E().world.db
    if "run_id" in body:
        snap = db.run_config(int(body["run_id"]))
        if snap is None:
            raise HTTPException(404, "no snapshot for this run")
        r = _apply_config(snap, f"restore:run{int(body['run_id'])}")
        r["restored"] = {"run_id": int(body["run_id"])}
        return r
    if "history_id" in body:
        h = db.config_change(int(body["history_id"]))
        if h is None:
            raise HTTPException(404, "unknown history entry")
        patch: dict = {}
        cur = patch
        parts = h["path"].split(".")
        for k in parts[:-1]:
            cur = cur.setdefault(k, {})
        cur[parts[-1]] = h["old"]
        r = _apply_config(patch, f"restore:h{h['id']}")
        r["restored"] = {"history_id": h["id"], "path": h["path"], "value": h["old"]}
        return r
    raise HTTPException(400, "run_id or history_id required")


@app.post("/api/v1/config/reset")
async def reset_config(body: dict | None = None):
    """scope=all: factory defaults (structure too -> rebuild needed).  scope=runtime (default from the GUI): every parameter back
    to default except the structural / loop-bank keys, so the hospital and the loop files stay as they are."""
    scope = (body or {}).get("scope", "all")
    e = E()
    if scope != "runtime":
        return {"config": cfg.reset(source="reset:all"), "needs_rebuild": True}
    old = cfg.snapshot()
    keep = STRUCTURAL | BANK_KEYS | {("hospital", "template"), ("hospital", "layout_file")}
    cfg.reset(source="reset:runtime")
    patch: dict = {}
    for path in keep:
        cur = old
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            continue
        d = patch
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = cur
    new = cfg.update(patch, source="reset:runtime")
    with e.world.lock:
        e.world.apply_config()
    return {"config": new, "needs_rebuild": False, "kept": [".".join(p) for p in keep]}


@app.post("/api/v1/control/start")
async def control_start():
    return {"result": await asyncio.to_thread(E().start)}


@app.post("/api/v1/control/stop")
async def control_stop():
    return {"result": await asyncio.to_thread(E().stop)}


@app.post("/api/v1/control/rebuild")
async def control_rebuild():
    await asyncio.to_thread(E().rebuild)
    return {"result": "rebuilt"}


@app.post("/api/v1/control/generate")
def control_generate():
    return {"result": "started" if E().start_generation() else "already running", "progress": E().bank.progress}


@app.post("/api/v1/control/trigger")
def control_trigger(body: dict):
    what = body.get("what", "")
    target = body.get("target")
    params = {k: v for k, v in body.items() if k not in ("what", "target")}
    return {"result": E().world.trigger(what, int(target) if target is not None else None, params)}


# ------------------------------------------------------------------ scenario scripts (repeatable drill sequences)
SCENARIO_DIR = next((d for d in (BASE_DIR / "scenarios", Path(__file__).resolve().parent.parent / "scenarios") if d.exists()), Path(__file__).resolve().parent.parent / "scenarios")


@app.get("/api/v1/control/script")
def script_status():
    files = sorted(p.name for p in SCENARIO_DIR.glob("*.json")) if SCENARIO_DIR.exists() else []
    return {"status": E().world.script_status(), "files": files, "dir": str(SCENARIO_DIR)}


@app.post("/api/v1/control/script")
def script_start(body: dict):
    """{"file": "name.json"} loads scenarios/<file>; or {"name": "...", "items": [{"at": s, "what": trigger, "target": ..., "params": {...}}]}."""
    if "file" in body:
        path = SCENARIO_DIR / str(body["file"]).replace("/", "").replace("..", "")
        if not path.exists():
            raise HTTPException(404, "script not found")
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        name, items = doc.get("name", path.stem), doc.get("items", [])
    else:
        name, items = str(body.get("name", "adhoc")), body.get("items", [])
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items required")
    return E().world.load_script(name, items)


@app.delete("/api/v1/control/script")
def script_cancel():
    return E().world.cancel_script()


@app.get("/api/v1/capture")
def capture_list():
    """Ground-truth capture files (exact bytes handed to the sockets, before fuzz): data/capture/w<worker>.bin."""
    from .runtime.engine import CAPTURE_DIR
    files = []
    if CAPTURE_DIR.exists():
        for p in sorted(CAPTURE_DIR.glob("w*.bin")):
            files.append({"name": p.name, "bytes": p.stat().st_size})
    return {"enabled": bool(E().world.st.ctl[CTL["tap"]] > 0), "dir": str(CAPTURE_DIR), "files": files,
            "format": "[gw_row u32][len u32][frame bytes] repeated; decode with tools/verify_capture.py"}


@app.post("/api/v1/capture")
def capture_control(body: dict):
    from .runtime.engine import CAPTURE_DIR
    e = E()
    if body.get("clear"):
        if CAPTURE_DIR.exists():
            for p in CAPTURE_DIR.glob("w*.bin"):
                try:
                    p.unlink()
                except OSError:
                    pass
    if "enabled" in body:
        e.world.trigger("capture", None, {"enabled": bool(body["enabled"])})
    return capture_list()


@app.post("/api/v1/control/autotune")
def control_autotune(body: dict):
    e = E()
    if body.get("action", "start") == "start":
        if not e.running:
            return {"result": "start the emulator first"}
        e.world.start_autotune(int(body.get("step", 100)), float(body.get("window_s", 12)))
        return {"result": "autotune started"}
    e.world.stop_autotune()
    return {"result": "autotune stopped"}


@app.get("/api/v1/status")
def status():
    """Live status without the 300-entry rate history (the GUI polls this at 1 Hz); /stats carries the history."""
    return E().stats(history=False)


@app.get("/api/v1/stats")
def stats():
    return E().stats()


@app.get("/api/v1/events")
def events(since: int = 0, limit: int = 200):
    return {"events": E().log.recent(since, limit)}


# ------------------------------------------------------------------ SQLite DB
@app.get("/api/v1/db/stats")
def db_stats():
    return E().world.db.stats()


@app.post("/api/v1/db/query")
def db_query(body: dict):
    """Read-only SQL (SELECT/WITH only, max 500 rows) for the router or the GUI console."""
    try:
        return E().world.db.query(str(body.get("sql", "")), tuple(body.get("params", []) or ()), int(body.get("limit", 200)))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:
        raise HTTPException(400, f"{type(ex).__name__}: {ex}")


# ------------------------------------------------------------------ router status (part 2 hook)
_router_status: dict = {"reported_at": None, "status": None}


@app.post("/api/v1/router/status")
def router_status_post(body: dict):
    _router_status["reported_at"] = time.time()
    _router_status["status"] = body
    return {"ok": True}


@app.get("/api/v1/router/status")
def router_status_get():
    return _router_status


# ------------------------------------------------------------------ EMR
@app.get("/api/v1/emr/hospital")
def emr_hospital():
    w = E().world; h = w.hospital; hp = cfg.get("hospital", default={}) or {}
    return {**h.describe(), "floors": h.floors, "exam_rooms": {k: h.rooms[v]["id"] for k, v in h.exam_rooms.items()}, "layout_schema": hospital_layout.__doc__,
            "sizing": {"size_by_patients": bool(hp.get("size_by_patients", True)), "headroom_pct": hp.get("headroom_pct", 20), "max_buildings": hp.get("max_buildings", 3),
                       "active_patients": cfg.get("general", "active_patients"), "planned_beds": w.planned_beds(), "beds": h.bed_capacity,
                       "stale": bool(hp.get("size_by_patients", True)) and not hp.get("layout_file") and abs(w.planned_beds() - h.bed_capacity) > max(8, 0.15 * h.bed_capacity)}}


@app.get("/api/v1/emr/layout")
def emr_layout():
    """Full architectural plan (JSON) + derived gateways/wards + occupancy — the router renders exactly this."""
    return E().world.layout_export()


@app.get("/api/v1/emr/layout/templates")
def emr_layout_templates():
    return {"templates": [{"key": k, **{kk: vv for kk, vv in v.items() if kk in ("name", "scale", "max_beds", "topology", "floors_max")}} for k, v in hospital_layout.TEMPLATES.items()],
            "current": E().world.hospital.template, "selected_by_capacity": hospital_layout.select_template(E().world.planned_beds(), "auto")}


@app.post("/api/v1/emr/layout/import")
async def emr_layout_import(body: dict):
    """Import a layout JSON (schema v1). Saved to data/layout_import.json, then the world is rebuilt on it."""
    data = body.get("layout", body)
    errs = hospital_layout.validate(data)
    if errs:
        raise HTTPException(400, {"errors": errs[:20]})
    path = BASE_DIR / "layout_import.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    cfg.update({"hospital": {"layout_file": str(path)}})
    await asyncio.to_thread(E().rebuild)
    h = E().world.hospital
    return {"result": "imported", "beds": h.bed_capacity, "gateways": len(h.gateways), "floors": len(h.floors)}


@app.post("/api/v1/emr/layout/reset")
async def emr_layout_reset():
    cfg.update({"hospital": {"layout_file": ""}})
    await asyncio.to_thread(E().rebuild)
    return {"result": "generated", "template": E().world.hospital.template}


@app.get("/api/v1/emr/floors")
def emr_floors():
    return {"floors": E().world.hospital.floors}


@app.get("/api/v1/emr/floors/{building_idx}/{floor}")
def emr_floor_map(building_idx: int, floor: int):
    w = E().world
    h = w.hospital
    fm = h.floor_map(building_idx, floor)
    # patient positions & link state
    G = w.st.gw.arr
    S = w.st.stat.arr
    pats = []
    now = w.sim_time
    for pid, rec in w.admitted.items():
        loc = rec["location"]
        if loc < 0:
            continue
        moving = bool(rec["trip"]) or rec["trip_step_until"] > now
        loc = w.display_location(rec)                             # lift / MRI bore while in the shadow zone
        r = h.rooms[loc]
        if r["building_idx"] != building_idx or r["floor"] != floor:
            continue
        p = w.by_id[pid]
        at_bed = loc == rec["room_idx"] and rec["bed_idx"] >= 0 and not moving
        bx, by = (h.beds[rec["bed_idx"]]["x"], h.beds[rec["bed_idx"]]["y"]) if at_bed else (r["x"], r["y"])
        pats.append({"id": pid, "name": p["name"], "room_idx": loc, "x": bx, "y": by, "at_bed": at_bed, "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else None,
                     "moving": moving, "note": rec.get("note") or "", "stage": rec.get("stage_label", "") if moving else "",
                     "stage_remaining": max(0.0, rec["trip_step_until"] - now) if moving else 0.0, "mri": bool(rec.get("mri_pending")),
                     "gw": rec["gw"], "activity": rec["activity"], "shadow": rec["shadow"], "lead_off": bool(w.st.patch["lead_off"][rec["row"]]), "row": rec["row"],
                     "patient_no": rec.get("patient_no"), "rssi": int(w.st.patch["rssi"][rec["row"]]), "battery": int(w.st.patch["battery"][rec["row"]]),
                     "patch": w.patches[rec["row"]].serial if rec["row"] in w.patches else None})
    for g in fm["gateways"]:
        i = g["idx"]
        g.update(status=int(G["status"][i]), n_conn=int(G["n_conn"][i]), cpu=int(G["cpu"][i]), connected=bool(S["connected"][i]))
    fm["patients"] = pats
    return fm


@app.get("/api/v1/emr/wards")
def emr_wards():
    h = E().world.hospital
    return {"wards": [{**w, "n_beds": len(w["bed_idxs"]), "occupied": sum(1 for b in w["bed_idxs"] if h.beds[b]["patient_id"])} for w in h.wards]}


@app.get("/api/v1/emr/rooms")
def emr_rooms(kind: str | None = None):
    h = E().world.hospital
    return {"rooms": [r for r in h.rooms if kind is None or r["kind"] == kind]}


@app.get("/api/v1/emr/beds")
def emr_beds():
    return {"beds": E().world.hospital.beds}


@app.get("/api/v1/emr/trips")
def emr_trips(upcoming_h: float = 1.0):
    """Patients on the move right now (with step timetable + upcoming steps) and exams scheduled within `upcoming_h` hours."""
    return E().world.list_trips(upcoming_h)


@app.get("/api/v1/emr/gateways")
def emr_gateways():
    return {"gateways": E().world.gateway_view()}


@app.get("/api/v1/emr/gateways/{idx}/patients")
def emr_gateway_patients(idx: int):
    """Patients whose patch is currently linked to gateway row `idx` (central-monitor modal in the hospital tab)."""
    w = E().world
    h = w.hospital
    P = w.st.patch.arr
    G = w.st.gw.arr
    S = w.st.stat.arr
    if not (0 <= idx < len(h.gateways)):
        raise HTTPException(404, "gateway not found")
    g = h.gateways[idx]
    out = []
    with w.lock:
        for pid, rec in w.admitted.items():
            if rec["gw"] != idx:
                continue
            prof = w.by_id[pid]
            row = rec["row"]
            gm = int(w.st.ctl[CTL["chan_mask"]])
            pm = int(P["chan_mask"][row])
            mask = gm if pm == 0 else (gm & pm)
            out.append({"row": row, "id": pid, "patient_no": rec.get("patient_no"), "name": prof["name"], "sex": prof["sex"], "age": prof["age"],
                        "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else "MCOT", "ward": prof["admission"]["ward_name"], "room": h.rooms[rec["room_idx"]]["name"] if rec["room_idx"] >= 0 else "",
                        "disease": prof["disease"], "rhythm": prof["rhythm"], "rhythm_label": w._variant_rhythm(int(P["variant"][row])),
                        "paced": bool(prof["pacemaker"]), "pacemaker_mode": prof["pacemaker_info"]["mode"] if prof.get("pacemaker_info") else None,
                        "lead_off": bool(P["lead_off"][row]), "rssi": int(P["rssi"][row]), "battery": int(P["battery"][row]), "activity": rec["activity"], "shadow": bool(rec["shadow"]),
                        "patch": w.patches[row].serial if row in w.patches else None, "channels": [CHANNELS[c]["key"] for c in CHANNELS if mask & (1 << c)],
                        "nibp": trend_model.nibp(pid, prof, w.sim_time)})
    out.sort(key=lambda x: x["bed"])
    return {"gateway": {"idx": idx, "id": g["id"], "gw_no": g["gw_no"], "type": g["type"], "building": g["building"], "floor": g["floor"],
                        "room": h.rooms[g["room_idx"]]["name"] if g["room_idx"] >= 0 else "", "capacity": g["capacity"], "status": int(G["status"][idx]),
                        "n_conn": int(G["n_conn"][idx]), "cpu": int(G["cpu"][idx]), "connected": bool(S["connected"][idx])}, "patients": out}


@app.get("/api/v1/emr/staff")
def emr_staff(role: str | None = None):
    return {"staff": [s for s in E().world.hospital.staff if role is None or s["role"] == role]}


@app.get("/api/v1/emr/patients")
def emr_patients(status: str = "admitted", q: str = "", offset: int = 0, limit: int = Query(100, le=20000), row: int = -1):
    w = E().world
    if row >= 0:                                              # single live row (plan marker / moving list click): no 20 000-row scan
        rec = next((r for r in w.list_admitted_rows([row])), None)
        return {"total": 1 if rec else 0, "patients": [rec] if rec else []}
    ql = q.lower()
    live = {r["id"]: r for r in w.list_admitted()}          # runtime rows (patch serial/id, gateway, battery, rssi, activity...)

    def match(p: dict, r: dict | None) -> bool:
        if not ql:
            return True
        hay = [p["name"], p["mrn"], p["disease"], str(p["id"]), p.get("rhythm", ""), RHYTHMS.get(p.get("rhythm", ""), {}).get("label", ""), p.get("ward_specialty", "")]
        if r:
            hay += [r.get("patch") or "", str(r.get("patch_id") or ""), r.get("bed") or "", r.get("gateway") or "", r.get("rhythm_label") or "", str(r.get("patient_no") or ""),
                    r.get("ward") or "", r.get("ward_id") or "", r.get("specialty") or ""]
        elif p.get("admission"):
            hay += [p["admission"].get("ward_name") or "", p["admission"].get("ward") or ""]
        return any(ql in str(h).lower() for h in hay)

    if status == "admitted":
        rows = [r for r in live.values() if match(w.by_id[r["id"]], r)]
        return {"total": len(rows), "patients": rows[offset: offset + limit]}
    items = [p for p in w.profiles if (status == "all" or p["status"] == status)]
    items = [p for p in items if match(p, live.get(p["id"]))]
    out = []
    for p in items[offset: offset + limit]:
        d = {k: v for k, v in p.items() if k != "avatar"}
        r = live.get(p["id"])
        if r:                                                 # merge live fields for monitored patients
            d.update({k: r[k] for k in ("patch", "patch_id", "bed", "ward", "gateway", "activity", "note", "battery", "rssi", "row", "rhythm_label", "devices", "pacemaker_mode", "pacemaker_lead", "pacemaker_type", "lead_off") if k in r})
        elif p.get("admission"):
            d["patch"] = p["admission"].get("patch")
            d["patch_id"] = p["admission"].get("patch_id")
        out.append(d)
    return {"total": len(items), "patients": out}


@app.get("/api/v1/emr/patients/{pid}")
def emr_patient(pid: int):
    v = E().world.patient_view(pid)
    if v is None:
        raise HTTPException(404, "patient not found")
    return v


@app.get("/api/v1/emr/patients/{pid}/avatar.svg")
def emr_avatar(pid: int):
    p = E().world.by_id.get(pid)
    if p is None:
        raise HTTPException(404)
    svg = render_svg(p["avatar"], p["name"])
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/v1/emr/admissions")
def emr_admissions():
    w = E().world
    h = w.hospital
    out = []
    for pid, rec in w.admitted.items():
        p = w.by_id[pid]
        patch = w.patches.get(rec["row"])
        out.append({"patient_id": rec.get("patient_no"), "profile_id": pid, "mrn": p["mrn"], "name": p["name"], "patch_id": patch.patch_id if patch else None, "patch_serial": patch.serial if patch else None,
                    "gateway_idx": rec["gw"], "gateway": h.gateways[rec["gw"]]["id"] if rec["gw"] >= 0 else None, "gw_id_in_frame": h.gateways[rec["gw"]]["gw_no"] if rec["gw"] >= 0 else None,
                    "bed": h.beds[rec["bed_idx"]]["id"] if rec["bed_idx"] >= 0 else None, "room": h.rooms[rec["room_idx"]]["id"] if rec["room_idx"] >= 0 else None,
                    "ward": p["admission"]["ward"], "mode": p["admission"]["mode"], "doctor": rec["doctor"], "nurse": rec["nurse"], "admit_time": p["admission"]["time"]})
    return {"admissions": out}


@app.get("/api/v1/emr/devices")
def emr_devices():
    return {**describe_devices(), "stats": E().world.device_stats(), "policy": cfg.get("scenario", "devices", "policy")}


@app.post("/api/v1/emr/patients/{pid}/devices")
def emr_set_devices(pid: int, body: dict):
    w = E().world
    if pid not in w.by_id:
        raise HTTPException(404, "patient not found")
    devs = w.set_devices(pid, list(body.get("devices", [])))
    return {"patient_id": pid, "devices": devs}


@app.post("/api/v1/control/devices/apply")
def control_devices_apply(body: dict | None = None):
    body = body or {}
    policy = body.get("policy")
    if policy:
        cfg.update({"scenario": {"devices": {"policy": policy}}})
    return E().world.apply_device_policy(policy)


@app.get("/api/v1/emr/schedules")
def emr_schedules():
    w = E().world
    return {"schedules": [{"patient_id": pid, "name": w.by_id[pid]["name"], "exams": rec["exams"]} for pid, rec in w.admitted.items() if rec["exams"]]}


@app.get("/api/v1/emr/patches")
def emr_patches(status: str = "all", q: str = "", offset: int = 0, limit: int = Query(100, le=1000), sort: str = "patch_id", dir: str = "desc"):
    """Patch registry: every patch ever issued (active + retired), unique serial/patch_id, lifecycle reasons.
    sort = serial|patch_id|status|patient_name|issued_at|issued_reason|retired_at|retired_reason|battery, dir = asc|desc."""
    return E().world.patch_registry_view(status, q, offset, limit, sort, dir)


# ------------------------------------------------------------------ signals
@app.get("/api/v1/signals/catalog")
def signals_catalog():
    e = E()
    b = e.bank
    return {"rhythms": RHYTHMS, "activities": ACTIVITIES, "variants": b.index.get("variants", []) if b.loaded else [], "bank": {"loaded": b.loaded, "dir": str(b.dir), "files": b.paths()}}


@app.get("/api/v1/signals/preview/{row}")
def signals_preview(row: int, n: int = 1):
    e = E()
    out = []
    tick = e.world._tick_now()
    for k in range(max(1, min(n, 50))):
        p = e.preview(row, tick - n + 1 + k)
        if p is None:
            raise HTTPException(404, "no active patch at this row")
        out.append(p)
    return {"row": row, "frames": out}


@app.get("/api/v1/signals/trend/{pid}")
def signals_trend(pid: int, days: int = 1):
    e = E()
    days = max(1, min(3, days))
    rec = e.world.admitted.get(pid)
    loop_now = None
    if rec is not None and e.gather is not None and e.gather.last_tick[rec["row"]] >= 0:
        loop_now = float(e.gather.pos[rec["row"]]) / (int(e.world.st.ctl[CTL["ecg_fs"]]) or 250)
    out = e.world.trend(pid, days, loop_now=loop_now)
    if out is None:
        raise HTTPException(404, "patient not monitored")
    return out


def _guarded(e: Engine, fn, *a):
    with e.rw.read():
        return fn(*a)


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket, row: int = -1):
    """Live samples.  Single-patch mode: ?row= / {"row": n}.  Multi-patch mode (central monitor): send {"rows": [n, ...]}
    and every tick returns {"tick", "multi": {row: preview | {"error": ...}}}."""
    await ws.accept()
    e = E()
    last_tick = -1
    rows: list[int] = []
    numeric = False                       # numeric-only board: no waveforms, one update per second
    last_stop_msg = 0.0
    try:
        while True:
            msg = None
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=0.02)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            if msg:
                try:
                    d = json.loads(msg)
                    if "rows" in d:
                        rows = [int(x) for x in d["rows"]][:160]
                        numeric = bool(d.get("numeric", False))
                        last_tick = -1
                    elif "row" in d:
                        row = int(d["row"])
                        rows = []
                        last_tick = -1
                except Exception:
                    pass
            bundle = int(e.world.st.ctl[CTL["bundle_ms"]]) or 200
            if not e.running:                                  # stopped: nothing is generated, the live views freeze
                if time.time() - last_stop_msg > 1.0:
                    last_stop_msg = time.time()
                    if rows:
                        await ws.send_text(json.dumps({"tick": -1, "multi": {str(r): {"error": "stopped"} for r in rows}}))
                    elif row >= 0:
                        await ws.send_text(json.dumps({"row": row, "tick": -1, "error": "stopped"}))
                await asyncio.sleep(0.1)
                continue
            tick = e.world._tick_now()
            if rows and tick != last_tick and (not numeric or tick % max(1, 1000 // bundle) == 0):
                last_tick = tick
                try:
                    many = await asyncio.to_thread(lambda: _guarded(e, e.preview_many, rows, tick, numeric))
                except Exception as ex:
                    many = {r: None for r in rows}
                await ws.send_text(json.dumps({"tick": tick, "multi": {str(r): (v if v is not None else {"error": "inactive"}) for r, v in many.items()}}))
            elif tick != last_tick and row >= 0:
                last_tick = tick
                try:
                    data = await asyncio.to_thread(lambda: _guarded(e, e.preview, row, tick))
                except Exception as ex:                       # e.g. world being rebuilt: report, keep the stream alive
                    data = None
                    err = f"{type(ex).__name__}"
                else:
                    err = "inactive"
                if data is not None:
                    data["tick"] = tick
                    data["row"] = row
                    await ws.send_text(json.dumps(data))
                else:
                    await ws.send_text(json.dumps({"row": row, "tick": tick, "error": err}))
            elif not rows and row < 0 and tick != last_tick:
                last_tick = tick
                await ws.send_text(json.dumps({"tick": tick, "row": row, "error": "no-row"}))
            await asyncio.sleep(0.03)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the GUI with cache-busting query strings so edited CSS/JS are never served stale."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        try:
            v = int((STATIC / name).stat().st_mtime)
        except OSError:
            v = 0
        html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
    return html
