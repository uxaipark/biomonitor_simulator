#!/usr/bin/env bash
# Quick health report of a running emulator (default port 5445): status, load, moving patients, gateway summary.
PORT="${1:-5445}"; H="http://127.0.0.1:$PORT/api/v1"
python3 - "$H" <<'PY'
import json, sys, urllib.request
h = sys.argv[1]
def get(p):
    with urllib.request.urlopen(h + p, timeout=5) as r: return json.load(r)
try:
    st = get("/status"); last = st.get("last") or {}
    print(f"running={st['running']} uptime={st['uptime_s']:.0f}s pkts/s={last.get('pkts_ps', 0):.0f} bytes/s={last.get('bytes_ps', 0):.0f}")
    tr = get("/emr/trips")["stats"]; print(f"admitted={tr['admitted']} moving={tr['moving']} kinds={tr['kinds']}")
    gw = get("/emr/gateways")["gateways"]; import collections; c = collections.Counter(g["status"] for g in gw)
    print(f"gateways={len(gw)} ok={c[0]} degraded={c[1]} down={c[2]} ble={sum(g['n_conn'] for g in gw)}")
except Exception as e:
    print("healthcheck failed:", e); sys.exit(1)
PY
