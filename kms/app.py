"""KMS HTTP API — /kms/v1.  조회(키 목록·공개키)는 인증 없이, 나머지는 Authorization: Bearer <토큰>."""
from __future__ import annotations

import json
import os
import urllib.request

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from .core import Kms, KmsError

API = "/kms/v1"


def chat_notifier(url: str | None):
    """키 변경을 라우터 에이전트 채널에 알린다 (기본 http://192.168.0.91:7300/api/v1/chat, BIOSIM_KMS_CHAT_URL='' 이면 끔)."""
    if not url:
        return None

    def post(text: str) -> None:
        body = json.dumps({"from": "rp5-1", "text": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=2).read()
    return post


def create_app(data_dir: str | None = None, notify=None) -> FastAPI:
    data_dir = data_dir or os.environ.get("BIOSIM_KMS_DIR", "/var/lib/biosim-kms")
    if notify is None:
        notify = chat_notifier(os.environ.get("BIOSIM_KMS_CHAT_URL", "http://192.168.0.91:7300/api/v1/chat"))
    kms = Kms(data_dir, notify=notify or None)
    app = FastAPI(title="biosim-kms", docs_url=f"{API}/docs", openapi_url=f"{API}/openapi.json")
    app.state.kms = kms

    @app.exception_handler(KmsError)
    async def _err(_req: Request, e: KmsError):
        return JSONResponse({"error": e.code, "message": e.msg}, status_code=e.status)

    def who(authorization: str | None):
        tok = (authorization or "").removeprefix("Bearer ").strip() if authorization else None
        return kms.auth(tok)

    # 공개 (인증 없음)
    @app.get(API)
    def status():
        return kms.status()

    @app.get(f"{API}/keys")
    def keys(since_rev: int = Query(0, ge=0), purpose: str | None = None, owner: str | None = None):
        return kms.keys(since_rev, purpose, owner)

    @app.get(f"{API}/keys/{{kid:path}}")
    def key(kid: str):
        return kms.key(kid)

    # 키 관리
    @app.post(f"{API}/keys")
    def create_key(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.create_key(who(authorization), str(body.get("kid") or ""), str(body.get("alg") or "aes256-gcm"),
                              body.get("acl"), str(body.get("note") or ""), body.get("not_after"))

    @app.post(f"{API}/keys/register-public")
    def register_public(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.register_public(who(authorization), str(body.get("kid") or ""), str(body.get("alg") or ""),
                                   str(body.get("public_key") or ""), str(body.get("note") or ""), body.get("acl"))

    @app.post(f"{API}/keys/{{kid:path}}/rotate")
    def rotate(kid: str, body: dict = Body(default={}), authorization: str | None = Header(None)):
        return kms.rotate(who(authorization), kid, body.get("new_kid"), body.get("public_key"))

    for st, name in (("retiring", "retire"), ("disabled", "disable"), ("active", "enable"), ("destroyed", "destroy")):
        def _mk(state):
            def h(kid: str, body: dict = Body(default={}), authorization: str | None = Header(None)):
                if state == "destroyed" and (body or {}).get("confirm") != kid:
                    raise KmsError(422, "CONFIRM_REQUIRED", "폐기는 되돌릴 수 없습니다 — 본문에 {\"confirm\": \"<kid>\"} 를 넣으세요")
                return kms.set_state(who(authorization), kid, state)
            return h
        app.post(f"{API}/keys/{{kid:path}}/{name}")(_mk(st))

    # 암복호화
    @app.post(f"{API}/datakey")
    def datakey(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.datakey(who(authorization), str(body.get("kid") or ""), str(body.get("aad") or ""), int(body.get("bytes") or 32))

    @app.post(f"{API}/unwrap")
    def unwrap(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.unwrap(who(authorization), body.get("wrapped") or body)

    @app.post(f"{API}/encrypt")
    def encrypt(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.encrypt(who(authorization), str(body.get("kid") or ""), str(body.get("plaintext") or ""), str(body.get("aad") or ""))

    @app.post(f"{API}/decrypt")
    def decrypt(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.decrypt(who(authorization), body)

    # 클라이언트·감사 (관리자)
    @app.get(f"{API}/clients")
    def clients(authorization: str | None = Header(None)):
        return {"items": kms.list_clients(who(authorization))}

    @app.post(f"{API}/clients")
    def add_client(body: dict = Body(...), authorization: str | None = Header(None)):
        return kms.create_client(who(authorization), str(body.get("name") or ""), str(body.get("role") or ""))

    @app.post(f"{API}/clients/{{name}}/disable")
    def disable_client(name: str, authorization: str | None = Header(None)):
        return kms.disable_client(who(authorization), name)

    @app.get(f"{API}/audit")
    def audit(since: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), authorization: str | None = Header(None)):
        return kms.audit(who(authorization), since, limit)

    @app.get(f"{API}/audit/verify")
    def audit_verify(authorization: str | None = Header(None)):
        kms.require(who(authorization), "admin")
        return kms.audit_verify()

    return app
