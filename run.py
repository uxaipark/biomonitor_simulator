#!/usr/bin/env python3
"""Bio-Signal Emulator entry point.

  python run.py                      # web GUI + APIs on http://0.0.0.0:5445
  python run.py --pregen             # only pre-generate the 1-hour loop bank and exit
  python run.py --port 8090 --workers 3
"""
import argparse
import os
import sys


def _check_ws_support() -> None:
    """The GUI's live waveform runs over a WebSocket; uvicorn needs 'websockets' or 'wsproto' for that.  A bare
    system python (no uvicorn[standard]) still serves the pages, so fail loudly instead of silently showing flat traces."""
    for mod in ("websockets", "wsproto"):
        try:
            __import__(mod)
            return
        except ImportError:
            continue
    sys.exit(f"[run.py] WebSocket 라이브러리가 없습니다 (python: {sys.executable}).\n"
             "  .venv/bin/python run.py 로 실행하거나  pip install 'uvicorn[standard]'  를 설치하세요. (실시간 파형 스트림 /ws/live 에 필요)")


def main():
    _check_ws_support()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5445)
    ap.add_argument("--pregen", action="store_true", help="generate loop bank and exit")
    ap.add_argument("--gen-workers", type=int, default=0)
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()
    if args.data_dir:
        os.environ["BIOSIM_DATA_DIR"] = args.data_dir
    if args.pregen:
        from emulator.config import Config
        from emulator.runtime.engine import Engine
        cfg = Config()
        s, g = cfg.get("signals"), cfg.get("general")
        from emulator.signals.loops import LoopBank
        bank = LoopBank(s["ecg_fs"], s["ppg_fs"], s["resp_fs"], s["accel_fs"], s["loop_seconds"], s["variants_per_rhythm"], g["seed"])
        if bank.is_ready():
            print("loop bank already up to date:", bank.dir)
            return
        import threading, time
        th = threading.Thread(target=bank.generate, kwargs={"workers": args.gen_workers or None})
        th.start()
        while th.is_alive():
            p = bank.progress
            print(f"\r{p['done']}/{p['total']} {p['message']:<40s} ETA {p['eta_s']:.0f}s", end="", flush=True)
            time.sleep(1)
        print("\ndone:", bank.dir)
        return
    import uvicorn
    uvicorn.run("emulator.app:app", host=args.host, port=args.port, log_level="warning", ws_ping_interval=20)


if __name__ == "__main__":
    sys.exit(main())
