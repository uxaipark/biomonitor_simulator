"""HTTP status API of the router (FastAPI): /status, /gateways, /patches, /patches/{id}, /events, /anomalies."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .server import RouterServer


def make_app(rs: RouterServer) -> FastAPI:
    app = FastAPI(title="biosim-router", version="0.1.0")

    @app.get("/")
    @app.get("/status")
    def status():
        return rs.status()

    @app.get("/gateways")
    def gateways(connected: bool | None = None):
        rows = rs.gateways_view()
        if connected is not None:
            rows = [g for g in rows if g["connected"] == connected]
        return {"gateways": rows}

    @app.get("/patches")
    def patches(offset: int = 0, limit: int = 200):
        rows = rs.store.patches()
        return {"total": len(rows), "patches": rows[offset: offset + limit]}

    @app.get("/patches/{pid}")
    def patch(pid: int):
        p = rs.store.patch(pid)
        if not p:
            raise HTTPException(404, "unknown patch")
        return p

    @app.get("/events")
    def events(limit: int = 200):
        return {"events": list(rs.events)[-limit:]}

    @app.get("/anomalies")
    def anomalies():
        return {"anomalies": rs.status()["anomalies"], "per_connection": [{"conn": c["id"], "addr": c["addr"], "gws": len(c["gws"]), "counts": dict(c["checker"].counts)} for c in rs.conns.values()]}

    return app
