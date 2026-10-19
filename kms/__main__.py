import argparse

import uvicorn

from .app import create_app

ap = argparse.ArgumentParser(prog="python -m kms", description="biosim-kms 암호 키 관리 서비스")
ap.add_argument("--host", default="0.0.0.0")
ap.add_argument("--port", type=int, default=7400)
a = ap.parse_args()
if a.port in (8080, 5445, 7300):
    raise SystemExit(f"포트 {a.port} 는 다른 서비스가 씁니다 (8080 키오스크·5445 에뮬레이터·7300 라우터)")
uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")
