"""KMS 1단계: 최상위 키로 감싼 저장, 권한, 교체(retiring 은 복호화만), 폐기 = 암호학적 삭제, 감사 해시 체인, HTTP API."""
import base64
import json
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from kms.core import Kms, KmsError  # noqa: E402

b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


@pytest.fixture
def kms(tmp_path):
    said = []
    k = Kms(str(tmp_path / "kms"), notify=said.append)
    k.said = said
    admin = k.auth(open(k.admin_token_file).read().strip())
    k.admin = admin
    k.router = k.auth(k.create_client(admin, "router-fitlet3", "router")["token"])
    k.vc = k.auth(k.create_client(admin, "vitalcenter-kr-seoul", "vitalcenter")["token"])
    return k


def test_files_private_and_secret_sealed(kms):
    assert oct(os.stat(os.path.join(kms.dir, "master.key")).st_mode & 0o777) == "0o600"
    assert oct(os.stat(kms.admin_token_file).st_mode & 0o777) == "0o600"
    kms.create_key(kms.admin, "router-backup:fitlet3#1")
    raw = sqlite3.connect(os.path.join(kms.dir, "kms.db")).execute("SELECT secret FROM keys").fetchone()[0]
    assert len(raw) == 12 + 32 + 16                  # nonce + 감싼 비밀값 + 태그 (평문 저장 아님)
    assert any("router-backup:fitlet3#1" in s for s in kms.said)


def test_datakey_roundtrip_and_acl(kms):
    kms.create_key(kms.admin, "router-backup:fitlet3#1")
    d = kms.datakey(kms.router, "router-backup:fitlet3#1", aad=b64(b"gen-7"))
    assert kms.unwrap(kms.router, d["wrapped"])["dek"] == d["dek"]
    with pytest.raises(KmsError) as e:               # Vital-Center 는 라우터 백업 키를 풀 수 없다
        kms.unwrap(kms.vc, d["wrapped"])
    assert e.value.status == 403
    bad = dict(d["wrapped"], aad=b64(b"gen-8"))      # aad 가 다르면 실패
    with pytest.raises(KmsError) as e:
        kms.unwrap(kms.router, bad)
    assert e.value.code == "DECRYPT_FAILED"
    with pytest.raises(KmsError) as e:
        kms.datakey(None, "router-backup:fitlet3#1")
    assert e.value.status == 401


def test_router_encrypts_only_vitalcenter_decrypts(kms):
    kms.create_key(kms.admin, "pii-s1:KR-SEOUL#1")
    kms.create_key(kms.admin, "vc-ingest:KR-SEOUL#1", alg="x25519")
    for kid in ("pii-s1:KR-SEOUL#1", "vc-ingest:KR-SEOUL#1"):
        env = kms.encrypt(kms.router, kid, b64(b"PSN 10154366"), aad=b64(b"REQ-1"))
        with pytest.raises(KmsError):
            kms.decrypt(kms.router, env)
        assert base64.b64decode(kms.decrypt(kms.vc, env)["plaintext"]) == b"PSN 10154366"
    assert kms.key("vc-ingest:KR-SEOUL#1")["public_key"]


def test_rotate_retiring_decrypts_only(kms):
    kms.create_key(kms.admin, "pii-s2:KR-SEOUL#1")
    env = kms.encrypt(kms.vc, "pii-s2:KR-SEOUL#1", b64(b"x"))
    out = kms.rotate(kms.admin, "pii-s2:KR-SEOUL#1")
    assert out["new"]["kid"] == "pii-s2:KR-SEOUL#2" and out["old"]["state"] == "retiring"
    assert out["old"]["replaced_by"] == "pii-s2:KR-SEOUL#2"
    assert kms.decrypt(kms.vc, env)["plaintext"] == b64(b"x")
    with pytest.raises(KmsError) as e:
        kms.encrypt(kms.vc, "pii-s2:KR-SEOUL#1", b64(b"y"))
    assert e.value.code == "NOT_ACTIVE"


def test_destroy_is_crypto_erase(kms):
    kms.create_key(kms.admin, "router-backup:fitlet3#1")
    d = kms.datakey(kms.router, "router-backup:fitlet3#1")
    kms.set_state(kms.admin, "router-backup:fitlet3#1", "destroyed")
    with pytest.raises(KmsError) as e:
        kms.unwrap(kms.router, d["wrapped"])
    assert e.value.status == 410
    with pytest.raises(KmsError):
        kms.set_state(kms.admin, "router-backup:fitlet3#1", "active")
    assert sqlite3.connect(os.path.join(kms.dir, "kms.db")).execute("SELECT secret FROM keys").fetchone()[0] is None


def test_register_public_existing_names(kms):
    pk = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    v = kms.register_public(kms.router, "router:fitlet3#2", "es256", b64(pk))
    assert v["origin"] == "external" and not v["has_secret"]
    kms.register_public(kms.router, "router:fitlet3", "x25519", b64(os.urandom(32)))           # 기존 이름 그대로
    kms.register_public(kms.vc, "dev-vitalcenter-ingest", "x25519", b64(os.urandom(32)))
    with pytest.raises(KmsError) as e:                                                          # 남의 용도 이름
        kms.register_public(kms.router, "vc-ingest:KR-SEOUL#9", "x25519", b64(os.urandom(32)))
    assert e.value.status == 403
    with pytest.raises(KmsError) as e:
        kms.register_public(kms.router, "router:fitlet3#3", "es256", b64(b"\x04" + os.urandom(64)))
    assert e.value.code == "BAD_PUBLIC_KEY"
    with pytest.raises(KmsError) as e:                                                          # 공개키만 있는 키로는 연산 불가
        kms.encrypt(kms.admin, "router:fitlet3", b64(b"x"))
    assert e.value.code == "PUBLIC_ONLY"
    assert {i["kid"] for i in kms.keys(since_rev=0)["items"]} >= {"router:fitlet3#2", "router:fitlet3", "dev-vitalcenter-ingest"}


def test_audit_chain_and_since_rev(kms):
    kms.create_key(kms.admin, "pii-s3:KR-SEOUL#1")
    r0 = kms.rev()
    kms.rotate(kms.admin, "pii-s3:KR-SEOUL#1")
    changed = {i["kid"] for i in kms.keys(since_rev=r0)["items"]}
    assert changed == {"pii-s3:KR-SEOUL#1", "pii-s3:KR-SEOUL#2"}
    try:
        kms.encrypt(kms.router, "pii-s3:KR-SEOUL#2", b64(b"x"))
    except KmsError:
        pass
    v = kms.audit_verify()
    assert v["ok"] and v["checked"] >= 5
    kms.db.execute("UPDATE audit SET client='someone' WHERE seq=2")                               # 변조하면 드러난다
    assert kms.audit_verify() == {"ok": False, "broken_at": 2, "checked": 1}


def test_reopen_keeps_keys(tmp_path):
    k1 = Kms(str(tmp_path / "k"))
    admin = k1.auth(open(k1.admin_token_file).read().strip())
    k1.create_key(admin, "router-backup:fitlet3#1")
    d = k1.datakey(admin, "router-backup:fitlet3#1")
    k1.db.close()
    k2 = Kms(str(tmp_path / "k"))
    assert k2.unwrap(admin, d["wrapped"])["dek"] == d["dek"]
    assert len(k2.list_clients(admin)) == 1          # 관리자 토큰을 다시 만들지 않는다


def test_http_api(tmp_path):
    import uvicorn
    from kms.app import create_app
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = create_app(str(tmp_path / "h"), notify=False)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True); th.start()
    base = f"http://127.0.0.1:{port}/kms/v1"
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    tok = open(app.state.kms.admin_token_file).read().strip()

    def call(method, path, body=None, token=tok):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None, h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert call("GET", "", token=None)[1]["service"] == "biosim-kms"
        assert call("POST", "/keys", {"kid": "router-backup:fitlet3#1"}, token=None)[0] == 401
        assert call("POST", "/keys", {"kid": "router-backup:fitlet3#1"})[0] == 200
        st, k = call("GET", "/keys/router-backup:fitlet3%231", token=None)
        assert st == 200 and k["state"] == "active" and "secret" not in k
        st, d = call("POST", "/datakey", {"kid": "router-backup:fitlet3#1"})
        assert call("POST", "/unwrap", {"wrapped": d["wrapped"]})[1]["dek"] == d["dek"]
        assert call("POST", "/keys/router-backup:fitlet3%231/destroy", {})[1]["error"] == "CONFIRM_REQUIRED"
        assert call("POST", "/keys/router-backup:fitlet3%231/destroy", {"confirm": "router-backup:fitlet3#1"})[1]["state"] == "destroyed"
        assert call("POST", "/unwrap", {"wrapped": d["wrapped"]})[0] == 410
        assert call("GET", "/audit/verify")[1]["ok"]
        assert call("GET", "/audit", token=None)[0] == 401
    finally:
        srv.should_exit = True
        th.join(5)


def test_router_manages_own_backup_generations(kms):
    """라우터는 가동 세대마다 router-backup 키를 만들고 초기화 때 폐기한다 — 다른 용도 키는 관리 못 한다."""
    kms.create_key(kms.router, "router-backup:fitlet3#1")
    d = kms.datakey(kms.router, "router-backup:fitlet3#1")
    out = kms.rotate(kms.router, "router-backup:fitlet3#1")                      # 초기화 → 새 세대
    assert out["new"]["kid"] == "router-backup:fitlet3#2"
    kms.set_state(kms.router, "router-backup:fitlet3#1", "destroyed")             # 이전 세대 백업은 이제 못 읽는다
    with pytest.raises(KmsError):
        kms.unwrap(kms.router, d["wrapped"])
    for bad in (lambda: kms.create_key(kms.router, "pii-s1:KR-SEOUL#1"),
                lambda: kms.create_key(kms.router, "router-backup:fitlet3#9", acl={"decrypt": ["vitalcenter"]}),
                lambda: kms.create_key(kms.vc, "router-backup:fitlet3#9")):
        with pytest.raises(KmsError) as e:
            bad()
        assert e.value.status == 403
    kms.create_key(kms.admin, "pii-s1:KR-SEOUL#1")
    with pytest.raises(KmsError):
        kms.set_state(kms.router, "pii-s1:KR-SEOUL#1", "destroyed")
