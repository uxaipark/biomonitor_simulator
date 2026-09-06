"""python -m router --port 9100 --api-port 9200 --data data/router [--emulator-url http://<emulator>:5445]"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from router.server import RouterServer      # noqa: E402
from router.store import PatchStore         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Bio-Signal router server (part 2)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9100, help="gateway TCP port (emulator transport.target_port)")
    ap.add_argument("--api-host", default="0.0.0.0")
    ap.add_argument("--api-port", type=int, default=9200, help="HTTP status API port")
    ap.add_argument("--data", default="data/router", help="store root (per-patch files)")
    ap.add_argument("--emulator-url", default=None, help="report status to the emulator every 5 s (POST /api/v1/router/status)")
    ap.add_argument("--max-open-files", type=int, default=256)
    ap.add_argument("--flush-interval", type=float, default=1.0)
    args = ap.parse_args()
    store = PatchStore(args.data, flush_interval=args.flush_interval, max_open=args.max_open_files)
    rs = RouterServer(args.host, args.port, store, emulator_url=args.emulator_url)

    def api():
        import uvicorn
        from router.api import make_app
        uvicorn.run(make_app(rs), host=args.api_host, port=args.api_port, log_level="warning")
    threading.Thread(target=api, daemon=True, name="router-api").start()
    print(f"[router] gateways on {args.host}:{args.port} · API http://{args.api_host}:{args.api_port}/status · store {args.data}", flush=True)
    try:
        asyncio.run(rs.serve())
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
