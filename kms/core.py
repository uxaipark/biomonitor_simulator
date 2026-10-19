"""KMS 핵심: 키 저장(최상위 키로 감싸서)·상태 전이·암복호화·접근 권한·해시 체인 감사 로그.

키 계층
    최상위 키(master.key, 32 B, 파일 0600) ─ AES-256-GCM 으로 ─▶ 하위 키 비밀값(keys.secret, aad = "kms-key|<kid>")
    하위 키:  aes256-gcm  대칭 KEK (router-backup, pii-s1~s3) — 데이터 키 감싸기·풀기, 작은 값 암복호화
              x25519      KMS 가 만든 키쌍 (vc-ingest 를 KMS 로 옮길 때) — 공개키 공개, 풀기는 KMS 안에서만
              x25519/es256 공개키만 등록 (개인키를 밖으로 꺼낼 수 없는 TPM 키 등) — 조회만, 비밀값 없음

상태: active → retiring(복호화·풀기만) → disabled(모든 연산 거부, 되살릴 수 있음) → destroyed(비밀값 삭제, 되돌릴 수 없음 = 암호학적 삭제)
kid: <용도>:<소유자>#<세대>  (예 router-backup:fitlet3#1).  기존 이름(router:fitlet3, dev-vitalcenter-ingest)도 그대로 등록할 수 있다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGS = ("aes256-gcm", "x25519", "es256")
STATES = ("active", "retiring", "disabled", "destroyed")
OPS = ("encrypt", "decrypt")                       # encrypt = datakey·encrypt·wrap,  decrypt = unwrap·decrypt
ROLES = ("admin", "router", "vitalcenter", "emulator", "reader")
KID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(:[A-Za-z0-9._\-]+)?(#[0-9]+)?$")
X25519_INFO = b"biosim-kms x25519-hkdf-sha256-aes256gcm v1"

# 용도별 기본 권한 (역할 이름).  키를 만들 때 acl 을 주면 그것이 우선한다.
DEFAULT_ACL = {
    "router-backup": {"encrypt": ["router"], "decrypt": ["router"]},
    "vc-ingest": {"encrypt": ["router"], "decrypt": ["vitalcenter"]},
    "pii-s1": {"encrypt": ["router", "vitalcenter"], "decrypt": ["vitalcenter"]},
    "pii-s2": {"encrypt": ["router", "vitalcenter"], "decrypt": ["vitalcenter"]},
    "pii-s3": {"encrypt": ["router", "vitalcenter"], "decrypt": ["vitalcenter"]},
}


# 관리자 말고도 스스로 관리할 수 있는 용도: 라우터는 가동 세대마다 백업 키를 만들고 초기화 때 폐기한다 (암호학적 삭제)
SELF_MANAGED = {"router-backup": "router"}


class KmsError(Exception):
    def __init__(self, status: int, code: str, msg: str):
        super().__init__(msg)
        self.status, self.code, self.msg = status, code, msg


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64d(s: str, what: str = "값") -> bytes:
    try:
        return base64.b64decode(str(s), validate=True)
    except Exception:
        raise KmsError(422, "BAD_BASE64", f"{what} 가 올바른 base64 가 아닙니다")


def split_kid(kid: str) -> tuple[str, str, int | None]:
    """'router-backup:fitlet3#2' → ('router-backup', 'fitlet3', 2).  세대가 없으면 None."""
    base, _, gen = kid.partition("#")
    purpose, _, owner = base.partition(":")
    return purpose, owner, (int(gen) if gen else None)


@dataclass
class Client:
    name: str
    role: str


class Kms:
    def __init__(self, data_dir: str, notify=None):
        self.dir = data_dir
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        self._lock = threading.RLock()
        self._notify = notify                      # callable(text) — 키 변경을 채팅으로 알린다 (실패해도 무시)
        self._master = self._load_master()
        self.db = sqlite3.connect(os.path.join(data_dir, "kms.db"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # 폐기한 비밀값이 빈 페이지·저널에 남지 않게: 지운 내용을 0 으로 덮고, WAL 대신 롤백 저널
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS keys(
            kid TEXT PRIMARY KEY, purpose TEXT, owner TEXT, gen INTEGER, alg TEXT, state TEXT,
            public_key TEXT, secret BLOB, origin TEXT, acl TEXT, note TEXT,
            created REAL, not_before REAL, not_after REAL, updated REAL, rev INTEGER, replaced_by TEXT);
        CREATE TABLE IF NOT EXISTS clients(name TEXT PRIMARY KEY, role TEXT, token_hash TEXT, created REAL, disabled INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, t REAL, client TEXT, op TEXT, kid TEXT, ok INTEGER, detail TEXT,
            prev TEXT, hash TEXT);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        """)
        for p in (os.path.join(data_dir, "kms.db"), os.path.join(data_dir, "master.key")):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        self.admin_token_file = os.path.join(data_dir, "admin.token")
        if not self.db.execute("SELECT 1 FROM clients WHERE role='admin' AND disabled=0").fetchone():
            tok = self._add_client("admin", "admin")
            fd = os.open(self.admin_token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(tok + "\n")
            self._audit("system", "client.create", None, True, {"name": "admin", "role": "admin", "token_file": self.admin_token_file})

    # ── 최상위 키 ──────────────────────────────────────────────────────────────────────────────────
    def _load_master(self) -> bytes:
        p = os.path.join(self.dir, "master.key")
        if not os.path.exists(p):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
        with open(p, "rb") as f:
            k = f.read()
        if len(k) != 32:
            raise RuntimeError(f"{p}: 최상위 키 길이가 32 바이트가 아닙니다 ({len(k)})")
        return k

    def _seal(self, kid: str, secret: bytes) -> bytes:
        n = secrets.token_bytes(12)
        return n + AESGCM(self._master).encrypt(n, secret, b"kms-key|" + kid.encode())

    def _open(self, kid: str, blob: bytes) -> bytes:
        return AESGCM(self._master).decrypt(blob[:12], blob[12:], b"kms-key|" + kid.encode())

    # ── 클라이언트·인증 ─────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _th(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _add_client(self, name: str, role: str) -> str:
        tok = "kms_" + secrets.token_urlsafe(32)
        self.db.execute("INSERT OR REPLACE INTO clients(name,role,token_hash,created,disabled) VALUES(?,?,?,?,0)",
                        (name, role, self._th(tok), time.time()))
        return tok

    def auth(self, token: str | None) -> Client | None:
        if not token:
            return None
        r = self.db.execute("SELECT name,role FROM clients WHERE token_hash=? AND disabled=0", (self._th(token),)).fetchone()
        return Client(r["name"], r["role"]) if r else None

    def require(self, c: Client | None, *roles: str) -> Client:
        if c is None:
            raise KmsError(401, "UNAUTHENTICATED", "토큰이 없거나 맞지 않습니다 (Authorization: Bearer <토큰>)")
        if roles and c.role not in roles:
            raise KmsError(403, "FORBIDDEN", f"{c.name}({c.role}) 은 이 작업 권한이 없습니다")
        return c

    def create_client(self, c: Client | None, name: str, role: str) -> dict:
        self.require(c, "admin")
        if role not in ROLES:
            raise KmsError(422, "BAD_ROLE", f"역할은 {', '.join(ROLES)} 중 하나")
        if not re.match(r"^[A-Za-z0-9._\-]{1,64}$", name or ""):
            raise KmsError(422, "BAD_NAME", "클라이언트 이름은 영문·숫자·._- 1~64자")
        with self._lock:
            tok = self._add_client(name, role)
            self._audit(c.name, "client.create", None, True, {"name": name, "role": role})
        return {"name": name, "role": role, "token": tok, "note": "토큰은 지금 한 번만 보여 줍니다. 채팅으로 보내지 말고 운영자가 직접 전달하세요."}

    def disable_client(self, c: Client | None, name: str) -> dict:
        self.require(c, "admin")
        with self._lock:
            n = self.db.execute("UPDATE clients SET disabled=1 WHERE name=?", (name,)).rowcount
            self._audit(c.name, "client.disable", None, bool(n), {"name": name})
        if not n:
            raise KmsError(404, "NO_CLIENT", f"클라이언트 {name} 없음")
        return {"name": name, "disabled": True}

    def list_clients(self, c: Client | None) -> list[dict]:
        self.require(c, "admin")
        return [{"name": r["name"], "role": r["role"], "created": r["created"], "disabled": bool(r["disabled"])}
                for r in self.db.execute("SELECT * FROM clients ORDER BY created")]

    # ── 감사 로그 (해시 체인) ───────────────────────────────────────────────────────────────────────
    def _audit(self, client: str, op: str, kid: str | None, ok: bool, detail: dict | None = None) -> None:
        with self._lock:
            last = self.db.execute("SELECT seq,hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else "0" * 64
            t = round(time.time(), 3)
            d = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
            h = hashlib.sha256(json.dumps([seq, t, client, op, kid, int(ok), d, prev], ensure_ascii=False).encode()).hexdigest()
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?)", (seq, t, client, op, kid, int(ok), d, prev, h))

    def audit(self, c: Client | None, since: int = 0, limit: int = 200) -> dict:
        self.require(c, "admin")
        limit = max(1, min(int(limit), 1000))
        rows = self.db.execute("SELECT * FROM audit WHERE seq>? ORDER BY seq LIMIT ?", (int(since), limit)).fetchall()
        return {"items": [{"seq": r["seq"], "t": r["t"], "client": r["client"], "op": r["op"], "kid": r["kid"], "ok": bool(r["ok"]),
                           "detail": json.loads(r["detail"] or "{}"), "prev": r["prev"], "hash": r["hash"]} for r in rows]}

    def audit_verify(self) -> dict:
        prev, n = "0" * 64, 0
        for r in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            h = hashlib.sha256(json.dumps([r["seq"], r["t"], r["client"], r["op"], r["kid"], r["ok"], r["detail"], prev],
                                          ensure_ascii=False).encode()).hexdigest()
            if r["prev"] != prev or r["hash"] != h:
                return {"ok": False, "broken_at": r["seq"], "checked": n}
            prev, n = h, n + 1
        return {"ok": True, "checked": n, "head": prev}

    # ── 키 목록 ────────────────────────────────────────────────────────────────────────────────────
    def _next_rev(self) -> int:
        r = self.db.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
        rev = (int(r["v"]) if r else 0) + 1
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('rev',?)", (str(rev),))
        return rev

    def rev(self) -> int:
        r = self.db.execute("SELECT v FROM meta WHERE k='rev'").fetchone()
        return int(r["v"]) if r else 0

    def _row(self, kid: str) -> sqlite3.Row:
        r = self.db.execute("SELECT * FROM keys WHERE kid=?", (kid,)).fetchone()
        if not r:
            raise KmsError(404, "NO_KEY", f"키 {kid} 없음")
        return r

    @staticmethod
    def view(r: sqlite3.Row) -> dict:
        return {"kid": r["kid"], "purpose": r["purpose"], "owner": r["owner"], "gen": r["gen"], "alg": r["alg"], "state": r["state"],
                "origin": r["origin"], "public_key": r["public_key"], "has_secret": r["secret"] is not None,
                "acl": json.loads(r["acl"] or "{}"), "note": r["note"], "created": r["created"], "not_before": r["not_before"],
                "not_after": r["not_after"], "updated": r["updated"], "rev": r["rev"], "replaced_by": r["replaced_by"]}

    def keys(self, since_rev: int = 0, purpose: str | None = None, owner: str | None = None) -> dict:
        q, a = "SELECT * FROM keys WHERE rev>?", [int(since_rev)]
        if purpose:
            q += " AND purpose=?"; a.append(purpose)
        if owner:
            q += " AND owner=?"; a.append(owner)
        return {"rev": self.rev(), "items": [self.view(r) for r in self.db.execute(q + " ORDER BY purpose,owner,gen,kid", a)]}

    def key(self, kid: str) -> dict:
        return self.view(self._row(kid))

    # ── 키 만들기·등록 ──────────────────────────────────────────────────────────────────────────────
    def _insert(self, kid, alg, origin, public_key, secret, acl, note, not_after, client) -> dict:
        if not KID_RE.match(kid or "") or len(kid) > 128:
            raise KmsError(422, "BAD_KID", "kid 형식: <용도>:<소유자>#<세대> (영문·숫자·._-)")
        if alg not in ALGS:
            raise KmsError(422, "BAD_ALG", f"alg 는 {', '.join(ALGS)} 중 하나")
        purpose, owner, gen = split_kid(kid)
        acl = acl if acl is not None else DEFAULT_ACL.get(purpose, {"encrypt": [], "decrypt": []})
        bad = [r for op in acl for r in acl[op] if op not in OPS or r not in ROLES]
        if bad:
            raise KmsError(422, "BAD_ACL", f"acl 은 {{encrypt|decrypt: [역할]}}, 역할은 {', '.join(ROLES)}")
        now = time.time()
        with self._lock:
            if self.db.execute("SELECT 1 FROM keys WHERE kid=?", (kid,)).fetchone():
                raise KmsError(409, "KID_EXISTS", f"키 {kid} 가 이미 있습니다 (교체는 /rotate)")
            rev = self._next_rev()
            self.db.execute("INSERT INTO keys VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (kid, purpose, owner, gen, alg, "active", public_key, self._seal(kid, secret) if secret else None, origin,
                             json.dumps(acl), note, now, now, not_after, now, rev, None))
            self._audit(client, "key.create" if origin == "kms" else "key.register_public", kid, True, {"alg": alg, "acl": acl})
        self._say(f"[KMS] 키 {'생성' if origin == 'kms' else '공개키 등록'}: {kid} ({alg}) rev={rev}")
        return self.key(kid)

    def _manage(self, c: Client | None, kid: str) -> Client:
        """관리자, 또는 SELF_MANAGED 용도의 소유 역할 (라우터 → router-backup)."""
        c = self.require(c)
        if c.role != "admin" and SELF_MANAGED.get(split_kid(kid)[0]) != c.role:
            raise KmsError(403, "FORBIDDEN", f"{c.name}({c.role}) 은 {kid} 를 관리할 권한이 없습니다")
        return c

    def create_key(self, c: Client | None, kid: str, alg: str = "aes256-gcm", acl: dict | None = None, note: str = "",
                   not_after: float | None = None) -> dict:
        c = self._manage(c, kid)
        if c.role != "admin" and acl is not None:
            raise KmsError(403, "FORBIDDEN", "acl 지정은 관리자만")
        if alg == "es256":
            raise KmsError(422, "BAD_ALG", "ES256 서명 키는 소유자(TPM)가 갖고 공개키만 등록합니다 (/keys/register-public)")
        if alg == "aes256-gcm":
            secret, pub = secrets.token_bytes(32), None
        else:
            sk = x25519.X25519PrivateKey.generate()
            secret = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            pub = b64e(sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        return self._insert(kid, alg, "kms", pub, secret, acl, note, not_after, c.name)

    def register_public(self, c: Client | None, kid: str, alg: str, public_key: str, note: str = "",
                        acl: dict | None = None) -> dict:
        c = self.require(c, "admin", "router", "vitalcenter")
        raw = b64d(public_key, "public_key")
        if alg == "x25519":
            if len(raw) != 32:
                raise KmsError(422, "BAD_PUBLIC_KEY", "X25519 공개키는 32 바이트")
        elif alg == "es256":
            try:
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)       # SEC1 비압축 65 B / 압축 33 B
            except Exception:
                raise KmsError(422, "BAD_PUBLIC_KEY", "ES256 공개키는 P-256 SEC1 (04‖x‖y 65 B 또는 압축 33 B)")
        else:
            raise KmsError(422, "BAD_ALG", "공개키 등록은 x25519 또는 es256")
        if c.role != "admin":                       # 라우터·Vital-Center 는 자기 용도 이름의 키만 (router* / vc*·*vitalcenter*)
            purpose = split_kid(kid)[0]
            mine = purpose.startswith("router") if c.role == "router" else (purpose.startswith("vc") or "vitalcenter" in purpose)
            if not mine:
                raise KmsError(403, "FORBIDDEN", f"{c.role} 는 {purpose} 용도 키를 등록할 수 없습니다")
            if acl is not None:
                raise KmsError(403, "FORBIDDEN", "acl 지정은 관리자만")
        return self._insert(kid, alg, "external", b64e(raw), None, acl if acl is not None else {"encrypt": [], "decrypt": []},
                            note, None, c.name)

    def rotate(self, c: Client | None, kid: str, new_kid: str | None = None, public_key: str | None = None) -> dict:
        """세대 +1 새 키를 만들고 이전 키는 retiring(복호화만).  외부 키는 새 공개키를 같이 받는다."""
        c = self._manage(c, kid)
        with self._lock:
            r = self._row(kid)
            if r["state"] not in ("active", "retiring"):
                raise KmsError(409, "BAD_STATE", f"{kid} 는 {r['state']} 상태라 교체할 수 없습니다")
            purpose, owner, gen = split_kid(kid)
            if not new_kid:
                base = f"{purpose}:{owner}" if owner else purpose
                g = (gen or 0) + 1
                while self.db.execute("SELECT 1 FROM keys WHERE kid=?", (f"{base}#{g}",)).fetchone():
                    g += 1
                new_kid = f"{base}#{g}"
            acl = json.loads(r["acl"] or "{}")
            if split_kid(new_kid)[0] != purpose:
                raise KmsError(422, "BAD_KID", f"교체 키는 같은 용도({purpose})여야 합니다")
            if r["origin"] == "kms":
                out = self.create_key(c, new_kid, r["alg"], acl if c.role == "admin" else None, note=f"{kid} 교체")
            else:
                if not public_key:
                    raise KmsError(422, "NEED_PUBLIC_KEY", "외부 키 교체에는 새 공개키(public_key)가 필요합니다")
                out = self.register_public(c, new_kid, r["alg"], public_key, note=f"{kid} 교체", acl=acl)
            self._set_state(c, kid, "retiring", replaced_by=new_kid)
        return {"new": out, "old": self.key(kid)}

    def _set_state(self, c: Client, kid: str, state: str, replaced_by: str | None = None) -> dict:
        with self._lock:
            r = self._row(kid)
            if r["state"] == "destroyed":
                raise KmsError(410, "DESTROYED", f"{kid} 는 폐기되었습니다 (되돌릴 수 없음)")
            rev = self._next_rev()
            if state == "destroyed":
                # 비밀값을 지운다 (secure_delete 로 페이지도 0 으로 덮음) — 이 키로 감싼 데이터는 이제 아무도 풀 수 없다
                self.db.execute("UPDATE keys SET state=?, secret=NULL, updated=?, rev=? WHERE kid=?", (state, time.time(), rev, kid))
            else:
                self.db.execute("UPDATE keys SET state=?, updated=?, rev=?, replaced_by=COALESCE(?,replaced_by) WHERE kid=?",
                                (state, time.time(), rev, replaced_by, kid))
            self._audit(c.name, f"key.{state}", kid, True, {"from": r["state"], "replaced_by": replaced_by})
        self._say(f"[KMS] 키 상태 변경: {kid} {r['state']} → {state} rev={rev}" + (f" (새 키 {replaced_by})" if replaced_by else ""))
        return self.key(kid)

    def set_state(self, c: Client | None, kid: str, state: str) -> dict:
        c = self._manage(c, kid)
        if state not in ("active", "retiring", "disabled", "destroyed"):
            raise KmsError(422, "BAD_STATE", "상태는 active, retiring, disabled, destroyed")
        return self._set_state(c, kid, state)

    # ── 암복호화 ───────────────────────────────────────────────────────────────────────────────────
    def _use(self, c: Client | None, kid: str, op: str) -> tuple[sqlite3.Row, bytes]:
        c = self.require(c)
        r = self.db.execute("SELECT * FROM keys WHERE kid=?", (kid,)).fetchone()
        why = None
        if not r:
            why = (404, "NO_KEY", f"키 {kid} 없음")
        elif r["state"] == "destroyed":
            why = (410, "DESTROYED", f"{kid} 는 폐기되었습니다 — 이 키로 감싼 데이터는 풀 수 없습니다")
        elif r["state"] == "disabled":
            why = (409, "DISABLED", f"{kid} 는 사용 중지 상태입니다")
        elif op == "encrypt" and r["state"] != "active":
            why = (409, "NOT_ACTIVE", f"{kid} 는 {r['state']} 상태라 새로 암호화할 수 없습니다 (새 키 {r['replaced_by'] or '-'})")
        elif r["secret"] is None:
            why = (409, "PUBLIC_ONLY", f"{kid} 는 공개키만 등록된 키라 KMS 가 연산할 수 없습니다")
        elif c.role != "admin" and c.role not in json.loads(r["acl"] or "{}").get(op, []):
            why = (403, "FORBIDDEN", f"{c.name}({c.role}) 은 {kid} 로 {'암호화' if op == 'encrypt' else '복호화'}할 권한이 없습니다")
        elif r["not_after"] and time.time() > r["not_after"] and op == "encrypt":
            why = (409, "EXPIRED", f"{kid} 유효기간이 지났습니다")
        if why:
            self._audit(c.name, op, kid, False, {"error": why[1]})
            raise KmsError(*why)
        return r, self._open(kid, r["secret"])

    @staticmethod
    def _aad(kid: str, aad: bytes) -> bytes:
        return b"kms|" + kid.encode() + b"|" + aad

    def _wrap(self, r, secret: bytes, plaintext: bytes, aad: bytes) -> dict:
        kid = r["kid"]
        if r["alg"] == "aes256-gcm":
            n = secrets.token_bytes(12)
            return {"v": 1, "kid": kid, "alg": "A256GCM", "nonce": b64e(n), "ct": b64e(AESGCM(secret).encrypt(n, plaintext, self._aad(kid, aad))),
                    "aad": b64e(aad)}
        # x25519: 일회용 키쌍(epk)과 수신 공개키로 공유 비밀 → HKDF-SHA256(salt = epk‖pk) → AES-256-GCM
        pk = b64d(r["public_key"])
        esk = x25519.X25519PrivateKey.generate()
        epk = esk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        k = HKDF(hashes.SHA256(), 32, epk + pk, X25519_INFO).derive(esk.exchange(x25519.X25519PublicKey.from_public_bytes(pk)))
        n = secrets.token_bytes(12)
        return {"v": 1, "kid": kid, "alg": "X25519-HKDF-SHA256-A256GCM", "epk": b64e(epk), "nonce": b64e(n),
                "ct": b64e(AESGCM(k).encrypt(n, plaintext, self._aad(kid, aad))), "aad": b64e(aad)}

    def _unwrap(self, r, secret: bytes, env: dict) -> bytes:
        kid = r["kid"]
        aad = b64d(env.get("aad", ""), "aad") if env.get("aad") else b""
        n, ct = b64d(env.get("nonce", ""), "nonce"), b64d(env.get("ct", ""), "ct")
        try:
            if r["alg"] == "aes256-gcm":
                return AESGCM(secret).decrypt(n, ct, self._aad(kid, aad))
            sk = x25519.X25519PrivateKey.from_private_bytes(secret)
            epk = b64d(env.get("epk", ""), "epk")
            pk = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            k = HKDF(hashes.SHA256(), 32, epk + pk, X25519_INFO).derive(sk.exchange(x25519.X25519PublicKey.from_public_bytes(epk)))
            return AESGCM(k).decrypt(n, ct, self._aad(kid, aad))
        except Exception:
            raise KmsError(422, "DECRYPT_FAILED", "복호화 실패 — 키·aad·암호문이 맞지 않거나 변조되었습니다")

    def datakey(self, c: Client | None, kid: str, aad: str = "", nbytes: int = 32) -> dict:
        """새 데이터 키(DEK)를 원문과 감싼 형태로 준다.  호출자는 원문으로 데이터를 암호화하고, 원문은 버리고 감싼 형태만 저장한다."""
        r, secret = self._use(c, kid, "encrypt")
        if nbytes not in (16, 32):
            raise KmsError(422, "BAD_SIZE", "데이터 키 길이는 16 또는 32 바이트")
        dek = secrets.token_bytes(nbytes)
        out = {"kid": kid, "dek": b64e(dek), "wrapped": self._wrap(r, secret, dek, b64d(aad, "aad") if aad else b"")}
        self._audit(c.name, "datakey", kid, True, {})
        return out

    def encrypt(self, c: Client | None, kid: str, plaintext: str, aad: str = "") -> dict:
        r, secret = self._use(c, kid, "encrypt")
        pt = b64d(plaintext, "plaintext")
        if len(pt) > 64 * 1024:
            raise KmsError(413, "TOO_LARGE", "KMS 직접 암호화는 64 KiB 까지 — 큰 데이터는 /datakey 로 받은 데이터 키로 암호화하세요")
        out = self._wrap(r, secret, pt, b64d(aad, "aad") if aad else b"")
        self._audit(c.name, "encrypt", kid, True, {"bytes": len(pt)})
        return out

    def decrypt(self, c: Client | None, env: dict, op_name: str = "decrypt") -> dict:
        kid = str((env or {}).get("kid") or "")
        r, secret = self._use(c, kid, "decrypt")
        try:
            pt = self._unwrap(r, secret, env)
        except KmsError as e:
            self._audit(c.name, op_name, kid, False, {"error": e.code})
            raise
        self._audit(c.name, op_name, kid, True, {"bytes": len(pt)})
        return {"kid": kid, "plaintext": b64e(pt)}

    def unwrap(self, c: Client | None, env: dict) -> dict:
        out = self.decrypt(c, env, "unwrap")
        return {"kid": out["kid"], "dek": out["plaintext"]}

    # ── 기타 ──────────────────────────────────────────────────────────────────────────────────────
    def _say(self, text: str) -> None:
        if self._notify:
            try:
                self._notify(text)
            except Exception:
                pass

    def status(self) -> dict:
        n = {s: 0 for s in STATES}
        for r in self.db.execute("SELECT state, COUNT(*) n FROM keys GROUP BY state"):
            n[r["state"]] = r["n"]
        a = self.db.execute("SELECT COUNT(*) n FROM audit").fetchone()["n"]
        return {"service": "biosim-kms", "api": "/kms/v1", "rev": self.rev(), "keys": n, "audit_entries": a,
                "master_key": "file (0600)", "algorithms": list(ALGS), "roles": list(ROLES)}
