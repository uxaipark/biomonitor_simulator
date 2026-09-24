"""가상 EMR 20곳의 HTTP 진입점과 관리 API.

  /emrsim/{site_id}/...           각 기관의 실제 연동 엔드포인트 (FHIR / HL7 v2 over HTTP / 벤더 REST / XML / CDA)
  /api/v1/emrsim                  카탈로그 (기관·형식·인증 정보·예시 요청)
  /api/v1/emrsim/{site_id}/...    로그, 수신 기록, 장애 주입, ADT 푸시, 예시 요청, 자체 시험
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import random
import re
import time
import uuid
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import fhir as F
from . import fhir_api, hl7v2, vendor
from . import ids as idmod
from .clinical import VITALS, c_to_f
from .sim import get as get_sim, all_sims
from .sites import SITES, SITE_BY_ID, COUNTRY_KO, PROTOCOL_KO

router = APIRouter()
MLLP_PORT = 2575

# FHIR base 경로 (사이트 루트 뒤)
FHIR_BASE = {"epic": "api/FHIR/R4", "oracle": "r4/{tenant}", "uk-core": "FHIR/R4", "jp-core": "fhir", "kr-core": "fhir/r4", "isik": "fhir", "nl-zib": "fhir/stu3",
             "au-core": "fhir/r4", "sg": "fhir/r4", "br-rnds": "api/fhir/r4"}
TOKEN_PATH = {"smart-backend-jwt": "oauth2/token", "signed-jwt": "oauth2/token", "client-credentials-post": "oauth2/token",
              "rnds-token": "api/token"}


def fhir_base_path(sim) -> str:
    return FHIR_BASE[sim.site["flavor"]].format(tenant=sim.site.get("tenant", ""))


def token_path(sim) -> str | None:
    a = sim.site["auth"]["type"]
    if sim.site["flavor"] == "oracle":
        return f"tenants/{sim.site['tenant']}/protocols/oauth2/profiles/smart-v1/token"
    if sim.site["flavor"] == "athena":
        return "oauth2/v1/token"
    return TOKEN_PATH.get(a)


class Ctx:
    def __init__(self, method, path, qm, headers, body, root, client):
        self.method, self.path, self.qm, self.headers, self.body, self.root, self.client = method, path, qm, headers, body, root, client
        self.q = dict(qm)


class Reply:
    def __init__(self, status: int, body: bytes | str | dict | list | None = None, ctype: str = "application/json", headers: dict | None = None, summary: str = ""):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=None).encode("utf-8")
            if ctype == "application/json":
                ctype = "application/json; charset=utf-8"
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.status, self.body, self.ctype, self.headers, self.summary = status, body or b"", ctype, headers or {}, summary


FHIR_CT = "application/fhir+json; charset=utf-8"


def fhir_err(e: F.FhirError, sim=None) -> Reply:
    return Reply(e.status, e.outcome(), FHIR_CT, summary=f"{e.status} {e.diagnostics[:120]}")


# ------------------------------------------------------------------ 인증
def _b64url_json(seg: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))


def _jwt_check(sim, assertion: str, token_url: str, algs: tuple[str, ...], need_kid: bool) -> str | None:
    parts = assertion.split(".")
    if len(parts) != 3:
        return "client_assertion is not a JWS compact serialization (header.payload.signature)"
    try:
        hdr, pl = _b64url_json(parts[0]), _b64url_json(parts[1])
    except Exception:
        return "client_assertion header/payload is not base64url JSON"
    if hdr.get("alg") not in algs:
        return f"JWT alg must be one of {algs}, got {hdr.get('alg')}"
    if need_kid and not hdr.get("kid"):
        return "JWT header must contain kid"
    cid = sim.site["auth"]["client_id"]
    if pl.get("iss") != cid or pl.get("sub") != cid:
        return f"iss and sub must both equal the registered client_id ({cid})"
    if not pl.get("aud") or not str(pl["aud"]).rstrip("/").endswith(token_url.rstrip("/").split("/emrsim/", 1)[-1]):
        return f"aud must be the token endpoint URL ({token_url})"
    if not pl.get("jti"):
        return "jti is required"
    now = time.time()
    exp = pl.get("exp")
    if not isinstance(exp, (int, float)) or exp < now:
        return "JWT expired or exp missing"
    if exp > now + 300:
        return "exp must be no more than 5 minutes in the future"
    return None


def token_endpoint(sim, ctx: Ctx) -> Reply:
    a = sim.site["auth"]
    t = a["type"]
    form = dict(parse_qsl(ctx.body.decode("utf-8", "replace"))) if ctx.method == "POST" else ctx.q
    token_url = f"{ctx.root}/{token_path(sim)}"

    def oauth_err(err, desc, status=400):
        return Reply(status, {"error": err, "error_description": desc}, summary=f"토큰 거부: {desc}")
    if t == "rnds-token":
        cn = ctx.headers.get("x-client-cert-cn", "")
        if cn != a["cert_cn"]:
            return Reply(401, {"message": "Certificado digital ausente ou inválido", "detalhe": f"X-Client-Cert-CN must be '{a['cert_cn']}' (emulates the ICP-Brasil e-CNPJ mTLS client certificate)"},
                         summary="토큰 거부: 인증서 CN")
        tok, ttl = sim.issue_token()
        return Reply(200, {"access_token": tok, "expires_in": ttl * 1000}, summary="토큰 발급")      # RNDS 는 ms 단위
    if form.get("grant_type") != "client_credentials":
        return oauth_err("unsupported_grant_type", "grant_type must be client_credentials")
    if t in ("smart-backend-jwt", "signed-jwt"):
        if form.get("client_assertion_type") != "urn:ietf:params:oauth:client-assertion-type:jwt-bearer":
            return oauth_err("invalid_request", "client_assertion_type must be urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        err = _jwt_check(sim, form.get("client_assertion", ""), token_url, ("RS384", "ES384") if t == "smart-backend-jwt" else ("RS512",), t == "signed-jwt")
        if err:
            return oauth_err("invalid_client", err)
    elif t == "client-credentials-basic":
        auth = ctx.headers.get("authorization", "")
        want = "Basic " + base64.b64encode(f"{a['client_id']}:{a['client_secret']}".encode()).decode()
        if auth != want:
            return oauth_err("invalid_client", "HTTP Basic client authentication failed", 401)
    elif t == "client-credentials-post":
        if form.get("client_id") != a["client_id"] or form.get("client_secret") != a["client_secret"]:
            return oauth_err("invalid_client", "client_id/client_secret mismatch", 401)
    tok, ttl = sim.issue_token()
    body = {"access_token": tok, "token_type": "Bearer", "expires_in": ttl, "scope": form.get("scope") or a.get("scope", "system/*.read system/Observation.write")}
    if sim.site["flavor"] == "athena":
        body = {"access_token": tok, "token_type": "Bearer", "expires_in": str(ttl), "scope": a["scope"]}
    if t == "signed-jwt":
        body = {"access_token": tok, "expires_in": str(ttl), "token_type": "Bearer", "issued_at": str(int(time.time() * 1000))}
    return Reply(200, body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}, summary="토큰 발급")


def check_auth(sim, ctx: Ctx) -> str | None:
    """None 이면 통과, 아니면 거부 사유."""
    if not sim.faults.get("auth", True):
        return None
    a = sim.site["auth"]
    t = a["type"]
    h = ctx.headers
    if t in ("smart-backend-jwt", "signed-jwt", "client-credentials-basic", "client-credentials-post"):
        auth = h.get("authorization", "")
        if not auth.startswith("Bearer "):
            return "Authorization: Bearer <access_token> required"
        if not sim.token_ok(auth[7:].strip()):
            return "access token is invalid or expired"
        return None
    if t == "basic" or (t == "mllp-facility" and a.get("http") == "basic"):
        want = "Basic " + base64.b64encode(f"{a['username']}:{a['password']}".encode()).decode()
        return None if h.get("authorization", "") == want else "HTTP Basic authentication required"
    if t == "bearer-static":
        return None if h.get("authorization", "") == f"Bearer {a['token']}" else "Authorization: Bearer <static token> required"
    if t == "api-key":
        if h.get(a["header"].lower()) != a["key"]:
            return f"{a['header']} header missing or wrong"
        if a.get("hosp_header") and h.get(a["hosp_header"].lower()) != a["hosp_cd"]:
            return f"{a['hosp_header']} header must be {a['hosp_cd']}"
        if a.get("extra_header") and h.get(a["extra_header"].lower()) != a["extra_value"]:
            return f"{a['extra_header']} header must be {a['extra_value']}"
        return None
    if t == "rnds-token":
        xs = h.get("x-authorization-server", "")
        if not xs.startswith("Bearer ") or not sim.token_ok(xs[7:].strip()):
            return "X-Authorization-Server: Bearer <token> invalid or expired"
        cpf = re.sub(r"\D", "", h.get("authorization", ""))
        if not idmod.cpf_ok(cpf) or cpf != re.sub(r"\D", "", a["requester_cpf"]):
            return "Authorization header must carry the CPF of the requesting professional"
        return None
    return None


def auth_reply(sim, why: str) -> Reply:
    p = sim.site["protocol"]
    if p == "fhir":
        body = F.outcome([("error", "login" if "token" in why.lower() or "authorization" in why.lower() else "security", why, None)])
        return Reply(401, body, FHIR_CT, {"WWW-Authenticate": 'Bearer realm="' + sim.id + '", error="invalid_token"'}, summary=f"401 {why}")
    if p == "kr-json":
        return Reply(401, vendor._kr_err("E001", f"인증 실패: {why}"), summary=f"401 {why}")
    if p == "athena":
        return Reply(401, {"error": "Unauthorized", "detailedmessage": why}, summary=f"401 {why}")
    if p == "cda":
        return Reply(401, f'<?xml version="1.0" encoding="UTF-8"?>\n<Result><Code>E001</Code><Message>{why}</Message></Result>', "application/xml; charset=utf-8", summary=f"401 {why}")
    return Reply(401, why, "text/plain; charset=utf-8", {"WWW-Authenticate": f'Basic realm="{sim.id}"'}, summary=f"401 {why}")


# ------------------------------------------------------------------ 디스패치
def dispatch(sim, ctx: Ctx) -> Reply:
    parts = [p for p in ctx.path.split("/") if p]
    proto = sim.site["protocol"]
    tp = token_path(sim)
    if tp and ctx.path.strip("/") == tp and ctx.method in ("POST",) + (("GET",) if sim.site["auth"]["type"] == "rnds-token" else ()):
        return token_endpoint(sim, ctx)
    if proto == "fhir":
        bp = fhir_base_path(sim).split("/")
        if parts[:len(bp)] != bp:
            return Reply(404, F.outcome([("error", "not-found", f"Unknown path /{ctx.path}. FHIR base is /{fhir_base_path(sim)}", None)]), FHIR_CT, summary="404 경로")
        rp = parts[len(bp):]
        base = f"{ctx.root}/{fhir_base_path(sim)}"
        if rp == [".well-known", "smart-configuration"]:
            return Reply(200, smart_config(sim, ctx.root), summary="SMART 구성")
        if rp != ["metadata"]:
            why = check_auth(sim, ctx)
            if why:
                return auth_reply(sim, why)
            if sim.site["flavor"] == "uk-core":
                rid = ctx.headers.get("x-request-id", "")
                try:
                    uuid.UUID(rid)
                except ValueError:
                    return Reply(400, {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "value",
                                                                                       "details": {"coding": [{"system": "https://fhir.nhs.uk/R4/CodeSystem/Spine-ErrorOrWarningCode", "code": "INVALID_VALUE", "display": "Provided value is invalid"}]},
                                                                                       "diagnostics": "Invalid value - '" + rid + "' in header 'X-Request-ID'"}]}, FHIR_CT, summary="400 X-Request-ID")
        if ctx.q.get("_format") and "xml" in ctx.q["_format"]:
            return Reply(406, F.outcome([("error", "not-supported", "Only application/fhir+json is supported", None)]), FHIR_CT, summary="406 xml")
        try:
            st, obj, hdr = fhir_api.handle(sim, ctx.method, rp, ctx.qm, ctx.body, base, ctx.headers)
        except F.FhirError as e:
            return fhir_err(e)
        if sim.site["flavor"] == "uk-core":
            hdr.update({"X-Request-ID": ctx.headers.get("x-request-id", ""), **({"X-Correlation-ID": ctx.headers["x-correlation-id"]} if ctx.headers.get("x-correlation-id") else {})})
        summ = f"{ctx.method} {'/'.join(rp)[:60]} → {st}"
        if obj and obj.get("resourceType") == "Bundle":
            summ += f" ({len(obj.get('entry', []))}건)"
        return Reply(st, obj, FHIR_CT if obj is not None else "text/plain", hdr, summary=summ)
    why = check_auth(sim, ctx) if not (proto == "kr-xml") else None
    if why:
        return auth_reply(sim, why)
    if proto == "hl7v2":
        if parts == ["hl7"] and ctx.method == "POST":
            ackmsg, code, summ, n = hl7v2.process_inbound(sim, ctx.body, "http")
            return Reply(200, ackmsg.encode(hl7v2.charset(sim), errors="replace"), f"x-application/hl7-v2+er7; charset={hl7v2.charset(sim)}", {"X-HL7-ACK": code}, summary=summ)
        if parts == ["hl7", "adt"] and ctx.method == "GET":
            since = int(ctx.q.get("since", 0) or 0)
            limit = min(int(ctx.q.get("limit", 200) or 200), 1000)
            evs = sim.events_since(since, limit)
            msgs = [{"seq": ev["seq"], "event": ev["code"], "time": sim.iso(ev["t"]), "er7": hl7v2.adt(sim, ev)} for ev in evs]
            nxt = evs[-1]["seq"] if evs else since
            if ctx.q.get("format") == "batch":
                now = hl7v2.ts(sim, time.time())
                txt = f"FHS|^~\\&|{sim.site['app']}|{sim.site['fac']}|||{now}\rBHS|^~\\&|{sim.site['app']}|{sim.site['fac']}|||{now}\r" + "".join(m["er7"] for m in msgs) + \
                      f"BTS|{len(msgs)}\rFTS|1\r"
                return Reply(200, txt.encode(hl7v2.charset(sim), errors="replace"), f"x-application/hl7-v2+er7; charset={hl7v2.charset(sim)}", {"X-Next-Since": str(nxt)}, summary=f"ADT 배치 {len(msgs)}건")
            return Reply(200, {"since": since, "next_since": nxt, "count": len(msgs), "charset": hl7v2.charset(sim), "messages": msgs}, summary=f"ADT {len(msgs)}건")
        if parts == ["hl7", "census"] and ctx.method == "GET":
            msgs = hl7v2.census_messages(sim)
            return Reply(200, {"count": len(msgs), "note": "현재 재원 환자 전체 A01 (초기 동기화용)", "messages": msgs}, summary=f"재원 {len(msgs)}명")
        return Reply(404, f"Unknown HL7 endpoint /{ctx.path}. Use POST hl7 (ORU^R01), GET hl7/adt?since=, GET hl7/census, or MLLP port {MLLP_PORT}", "text/plain; charset=utf-8", summary="404")
    if proto == "athena":
        form = dict(parse_qsl(ctx.body.decode("utf-8", "replace"))) if ctx.method in ("POST", "PUT") else {}
        client = ctx.headers.get("authorization", "")[-12:]
        try:
            st, obj = vendor.athena_handle(sim, ctx.method, parts, ctx.q, form, client)
        except vendor.VendorError as e:
            return Reply(e.status, e.body, summary=f"{e.status} {str(e.body)[:100]}")
        return Reply(st, obj, summary=f"{ctx.method} /{ctx.path[:60]} → {st}")
    if proto == "kr-json":
        obj = vendor.kr_json_handle(sim, ctx.method, parts, ctx.q, ctx.body)
        return Reply(200, obj, summary=f"{ctx.method} /{ctx.path[:50]} → {obj['RESULT_CD']} {obj['RESULT_MSG'][:60]}")
    if proto == "kr-xml":
        if parts != ["if"]:
            return Reply(404, vendor.kr_xml_wrap(sim, "", "", "E", "전문 수신 URL 은 POST /if 하나입니다.", ""), "text/xml; charset=EUC-KR", summary="404")
        if ctx.method != "POST":
            return Reply(405, vendor.kr_xml_wrap(sim, "", "", "E", "POST 만 허용", ""), "text/xml; charset=EUC-KR", summary="405")
        st, body, rs, summ = vendor.kr_xml_handle(sim, ctx.body, ctx.client)
        return Reply(st, body, "text/xml; charset=EUC-KR", {"X-RSLT-CD": rs}, summary=summ)
    if proto == "cda":
        if parts == ["cda", "documents"] and ctx.method == "GET":
            docs = vendor.cda_doc_list(sim)
            if ctx.q.get("PT_NO"):
                docs = [d for d in docs if sim.people[d["person"]]["ids"]["mrn"] == ctx.q["PT_NO"]]
            if ctx.q.get("TYPE"):
                docs = [d for d in docs if d["type"] == ctx.q["TYPE"].upper()]
            if ctx.q.get("ADMITTED", "").upper() == "Y":
                docs = [d for d in docs if d["enc"] is not None and sim.encounters[d["enc"]]["status"] == "in-progress"]
            if ctx.q.get("FROM"):
                t0 = vendor._parse_local(sim, ctx.q["FROM"], ("%Y%m%d%H%M%S", "%Y%m%d")) or 0
                docs = [d for d in docs if d["t"] >= t0]
            return Reply(200, vendor.cda_list_xml(sim, docs, ctx.root), "application/xml; charset=utf-8", summary=f"문서 목록 {len(docs)}건")
        if len(parts) == 3 and parts[:2] == ["cda", "documents"] and ctx.method == "GET":
            d = next((x for x in vendor.cda_doc_list(sim) if x["doc_id"] == parts[2]), None)
            if not d:
                return Reply(404, f'<?xml version="1.0" encoding="UTF-8"?>\n<Result><Code>E404</Code><Message>문서 없음: {parts[2]}</Message></Result>', "application/xml; charset=utf-8", summary="404 문서")
            return Reply(200, vendor.cda_document(sim, d), "application/hl7-cda+xml; charset=utf-8", summary=f"CDA {d['type']} {d['doc_id']}")
        if parts == ["cda", "documents"] and ctx.method == "POST":
            st, xml, summ = vendor.cda_receive(sim, ctx.body)
            return Reply(st, xml, "application/xml; charset=utf-8", summary=summ)
        return Reply(404, '<?xml version="1.0" encoding="UTF-8"?>\n<Result><Code>E404</Code><Message>GET/POST cda/documents, GET cda/documents/{id}</Message></Result>',
                     "application/xml; charset=utf-8", summary="404")
    return Reply(404, "unknown", "text/plain")


def smart_config(sim, root: str) -> dict:
    t = sim.site["auth"]["type"]
    return {"issuer": root, "token_endpoint": f"{root}/{token_path(sim)}" if token_path(sim) else None,
            "token_endpoint_auth_methods_supported": {"smart-backend-jwt": ["private_key_jwt"], "signed-jwt": ["private_key_jwt"], "client-credentials-basic": ["client_secret_basic"],
                                                      "client-credentials-post": ["client_secret_post"]}.get(t, []),
            "token_endpoint_auth_signing_alg_values_supported": {"smart-backend-jwt": ["RS384", "ES384"], "signed-jwt": ["RS512"]}.get(t, []),
            "grant_types_supported": ["client_credentials"], "scopes_supported": ["system/*.read", "system/Observation.write"],
            "capabilities": ["client-confidential-asymmetric" if "jwt" in t else "client-confidential-symmetric"]}


# ------------------------------------------------------------------ 진입점
def _root(request: Request, site_id: str) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{scheme}://{host}/emrsim/{site_id}"


def _fault_reply(sim) -> Reply | None:
    f = sim.faults
    if f.get("down"):
        return Reply(503, "Service Unavailable (emulated outage)", "text/plain", {"Retry-After": "30"}, summary="503 장애 주입(down)")
    if f.get("error_rate") and random.random() < float(f["error_rate"]):
        if sim.site["protocol"] == "fhir":
            return Reply(500, F.outcome([("fatal", "exception", "Internal server error (emulated fault)", None)]), FHIR_CT, summary="500 장애 주입")
        return Reply(random.choice([500, 502, 503, 504]), "emulated upstream error", "text/plain", summary="5xx 장애 주입")
    return None


@router.api_route("/emrsim/{site_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
async def emr_entry(site_id: str, path: str, request: Request):
    sim = get_sim(site_id)
    if sim is None:
        return JSONResponse({"error": f"unknown site {site_id}", "sites": [s["id"] for s in SITES]}, 404)
    body = await request.body()
    t0 = time.time()
    lat = int(sim.faults.get("latency_ms") or 0)
    if lat:
        await asyncio.sleep(lat * random.uniform(0.6, 1.4) / 1000)
    ctx = Ctx(request.method, path, list(request.query_params.multi_items()), {k.lower(): v for k, v in request.headers.items()}, body, _root(request, site_id),
              request.client.host if request.client else "")
    rep = _fault_reply(sim)
    if rep is None:
        try:
            rep = await asyncio.to_thread(dispatch, sim, ctx)
        except Exception as e:                                           # 에뮬레이터 버그도 연동 대상에게는 500 으로 보인다
            import traceback
            traceback.print_exc()
            rep = Reply(500, {"error": "internal", "detail": str(e)}, summary=f"500 내부 오류 {e}")
    detail = None
    if request.method in ("POST", "PUT") and body:
        detail = body[:1500].decode(hl7v2.charset(sim) if sim.site["protocol"] == "hl7v2" else ("cp949" if sim.site["protocol"] == "kr-xml" else "utf-8"), errors="replace")
    sim.add_log("in", "http", rep.summary or f"{request.method} /{path}", rep.status, request.method, "/" + path + (("?" + request.url.query) if request.url.query else ""), ctx.client, detail)
    headers = {**rep.headers, "X-Emulator-Site": site_id, "X-Response-Time-ms": str(int((time.time() - t0) * 1000))}
    return Response(rep.body, rep.status, headers=headers, media_type=rep.ctype)


# ------------------------------------------------------------------ 카탈로그·관리
def _example_person(sim):
    c = sorted(sim.census(), key=lambda e: e["admit"])       # 가장 오래 입원한 환자 = 바이탈 기록이 쌓여 있다
    return (sim.people[c[0]["person"]], c[0]) if c else (sim.people[0], None)


def site_info(sim, root: str, detail: bool = False) -> dict:
    s = sim.site
    base = f"{root}/emrsim/{s['id']}"
    p, e = _example_person(sim)
    a = dict(s["auth"])
    info = {"id": s["id"], "country": s["country"], "country_ko": COUNTRY_KO[s["country"]], "name": s["name"], "name_local": s.get("name_local"), "city": s["city"], "tz": s["tz"],
            "protocol": s["protocol"], "protocol_ko": PROTOCOL_KO[s["protocol"]], "flavor": s["flavor"],
            "version": s.get("fhir_version") or s.get("hl7_version") or {"athena": "athenaOne REST v1", "kr-json": "JSON/UTF-8", "kr-xml": "XML/EUC-KR", "cda": "CDA R2"}[s["protocol"]],
            "style": s["style"], "base_url": base, "auth": a, "stats": sim.summary()}
    ex = {}
    if s["protocol"] == "fhir":
        fb = f"{base}/{fhir_base_path(sim)}"
        info["fhir_base"] = fb
        pid = p["fhir_id"]
        ex = {"metadata": f"GET {fb}/metadata", "smart": f"GET {fb}/.well-known/smart-configuration",
              "token": f"POST {base}/{token_path(sim)}" if token_path(sim) else None,
              "census": (f"GET {fb}/Group/inpatient-census  (Encounter 검색은 patient 필수)" if s["flavor"] in ("epic", "oracle") else f"GET {fb}/Encounter?status=in-progress&_include=Encounter:patient&_count=100"),
              "patient_by_identifier": f"GET {fb}/Patient?identifier={F.patient(sim, p)['identifier'][0].get('system', '')}|{F.patient(sim, p)['identifier'][0]['value']}",
              "encounters": f"GET {fb}/Encounter?patient={pid}", "vitals": f"GET {fb}/Observation?patient={pid}&category=vital-signs&_sort=-date",
              "write_vitals": f"POST {fb}/Observation  (Content-Type: application/fhir+json)", "write_bundle": f"POST {fb}  (Bundle type=transaction|batch)"}
    elif s["protocol"] == "hl7v2":
        ex = {"mllp": f"tcp://<emulator>:{MLLP_PORT}  (MSH-6 수신기관 = {s['fac']}, MLLP 0x0B … 0x1C 0x0D)", "http_inbound": f"POST {base}/hl7  (ORU^R01, Basic auth)",
              "adt_feed": f"GET {base}/hl7/adt?since=0  (&format=batch)", "census": f"GET {base}/hl7/census",
              "adt_push": f"POST /api/v1/emrsim/{s['id']}/push {{host, port}}  (에뮬레이터가 ADT 를 MLLP 로 밀어줌)"}
        info["mllp"] = {"port": MLLP_PORT, "receiving_facility": s["fac"], "receiving_app": s["app"], "charset": hl7v2.charset(sim), "accepts_versions": sorted(hl7v2.ACCEPT_VERSIONS[s["hl7_version"]])}
    elif s["protocol"] == "athena":
        v = f"{base}/v1/{s['practiceid']}"
        ex = {"token": f"POST {base}/oauth2/v1/token (Basic client_id:secret, grant_type=client_credentials)", "patients": f"GET {v}/patients?departmentid=1",
              "patient": f"GET {v}/patients/{p['ids'].get('patientid')}", "encounters": f"GET {v}/chart/{p['ids'].get('patientid')}/encounters?departmentid=1",
              "vitals": f"GET {v}/chart/{p['ids'].get('patientid')}/vitals?departmentid=1",
              "write_vitals": f"POST {v}/chart/encounter/{e['visit'] if e else '<encounterid>'}/vitals  (form: departmentid=1&source=DEVICE&vitals=[[...]])",
              "changed": f"POST {v}/patients/changed/subscription → GET {v}/patients/changed"}
    elif s["protocol"] == "kr-json":
        ex = {"inpatients": f"GET {base}/api/v1/adm/inpatients", "events": f"GET {base}/api/v1/adm/events?FROM_SEQ=0", "patient": f"GET {base}/api/v1/pat/{p['ids']['mrn']}",
              "vitals": f"GET {base}/api/v1/vs?PT_NO={p['ids']['mrn']}", "write_vitals": f"POST {base}/api/v1/vs", "codes": f"GET {base}/api/v1/code/wards , /api/v1/code/vs"}
    elif s["protocol"] == "kr-xml":
        ex = {"endpoint": f"POST {base}/if  (Content-Type: text/xml; charset=EUC-KR)",
              "if_ids": "EMR_ADT_0001 재원조회 · EMR_ADT_0002 ADT이벤트(FROM_SEQ) · EMR_PAT_0001 환자조회 · EMR_VS_0001 바이탈조회 · EMR_VS_0002 바이탈등록(가로형)"}
    elif s["protocol"] == "cda":
        ex = {"list": f"GET {base}/cda/documents?ADMITTED=Y", "document": f"GET {base}/cda/documents/<DocId>", "submit": f"POST {base}/cda/documents  (application/xml, 활력징후 templateId {vendor.KR_TEMPLATES['VITALS'][0]})"}
    info["endpoints"] = {k: v for k, v in ex.items() if v}
    info["example_patient"] = {"name": p["text"], "ids": {k: (idmod.rrn_masked(v) if k == "rrn" else v) for k, v in p["ids"].items()}, "fhir_id": p.get("fhir_id"),
                               "visit": e["visit"] if e else None}
    if detail:
        info["wards"] = [{"code": w["code"], "name": w["name"], "floor": w["floor"], "beds": sum(1 for b in sim.beds if b["ward"] == w["idx"]),
                          "occupied": sum(1 for b in sim.beds if b["ward"] == w["idx"] and sim.bed_occ[b["idx"]] is not None)} for w in sim.wards]
    return info


def _req_root(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    return f"{request.headers.get('x-forwarded-proto') or request.url.scheme}://{host}"


def _sim_or_404(site_id: str):
    sim = get_sim(site_id)
    if sim is None:
        raise HTTPException(404, f"unknown site {site_id}")
    return sim


@router.get("/api/v1/emrsim")
def catalog(request: Request):
    root = _req_root(request)
    sites = [site_info(s, root) for s in all_sims()]
    by_c = {}
    for s in sites:
        by_c[s["country_ko"]] = by_c.get(s["country_ko"], 0) + 1
    by_p = {}
    for s in sites:
        by_p[s["protocol_ko"]] = by_p.get(s["protocol_ko"], 0) + 1
    return {"description": "상용 EMR 연동 시험용 가상 의료기관 20곳. 기관명은 가공, 형식·코드 체계·식별자 검증 규칙은 각국 표준/벤더 관례를 따름. 서명·인증서 검증은 형식만 검사.",
            "count": len(sites), "by_country": by_c, "by_protocol": by_p, "mllp_port": MLLP_PORT, "doc": "docs/EMR_SIM.md", "sites": sites}


@router.get("/api/v1/emrsim/{site_id}")
def site_detail(site_id: str, request: Request):
    return site_info(_sim_or_404(site_id), _req_root(request), detail=True)


@router.get("/api/v1/emrsim/{site_id}/log")
def site_log(site_id: str, since: int = 0, limit: int = 100):
    sim = _sim_or_404(site_id)
    rows = [x for x in list(sim.log) if x["seq"] > since][-limit:]
    return {"site": site_id, "last_seq": sim.log_seq, "log": rows}


@router.get("/api/v1/emrsim/{site_id}/received")
def site_received(site_id: str, limit: int = 200):
    sim = _sim_or_404(site_id)
    rows = []
    for x in sim.inbound[-limit:][::-1]:
        p = sim.people[x["person"]]
        rows.append({"seq": x["seq"], "id": x["id"], "received": dt.datetime.fromtimestamp(x["received"], sim.tz).isoformat(timespec="seconds"),
                     "measured": sim.iso(x["t"]), "patient": p["text"], "patient_ids": {k: v for k, v in p["ids"].items() if k != "rrn"},
                     "visit": sim.encounters[x["enc"]]["visit"] if x["enc"] else None, "kind": x["kind"], "label": VITALS[x["kind"]]["display"],
                     "value": x["value"], "unit": VITALS[x["kind"]]["ucum"], "value_in": x["value_in"], "unit_in": x["unit_in"], "source": x["source"], "device": x["device"], "ref": x["ref"]})
    return {"site": site_id, "total": sim.counters["inbound_values"], "rows": rows}


@router.post("/api/v1/emrsim/{site_id}/faults")
def site_faults(site_id: str, body: dict):
    sim = _sim_or_404(site_id)
    allowed = {"latency_ms": int, "error_rate": float, "down": bool, "token_ttl_s": int, "auth": bool}
    for k, v in body.items():
        if k not in allowed:
            raise HTTPException(400, f"unknown fault key {k} (allowed: {sorted(allowed)})")
        sim.faults[k] = allowed[k](v)
    sim.faults["error_rate"] = min(1.0, max(0.0, sim.faults["error_rate"]))
    sim.add_log("sys", "admin", f"장애 설정 변경 {body}")
    return {"site": site_id, "faults": sim.faults}


@router.post("/api/v1/emrsim/{site_id}/tokens/revoke")
def site_revoke(site_id: str):
    sim = _sim_or_404(site_id)
    n = len(sim.tokens)
    sim.tokens.clear()
    sim.add_log("sys", "admin", f"발급 토큰 {n}개 폐기")
    return {"revoked": n}


@router.post("/api/v1/emrsim/{site_id}/push")
def site_push(site_id: str, body: dict):
    from . import mllp
    sim = _sim_or_404(site_id)
    if sim.site["protocol"] != "hl7v2":
        raise HTTPException(400, "ADT push (MLLP) is only for HL7 v2 sites")
    host, port = body.get("host"), int(body.get("port") or 0)
    if not host or not port:
        raise HTTPException(400, "host and port required")
    return mllp.start_push(sim, host, port, int(body.get("since", len(sim.events))))


@router.delete("/api/v1/emrsim/{site_id}/push")
def site_push_stop(site_id: str):
    from . import mllp
    return mllp.stop_push(_sim_or_404(site_id))


def _jwt(sim, aud: str, alg: str) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
    cid = sim.site["auth"]["client_id"]
    hdr = {"alg": alg, "typ": "JWT", **({"kid": sim.site["auth"]["kid"]} if sim.site["auth"].get("kid") else {})}
    pl = {"iss": cid, "sub": cid, "aud": aud, "jti": uuid.uuid4().hex, "exp": int(time.time()) + 240, "iat": int(time.time())}
    return f"{enc(hdr)}.{enc(pl)}.{base64.urlsafe_b64encode(random.randbytes(64)).decode().rstrip('=')}"


def samples(sim, root: str) -> list[dict]:
    """이 기관에 바이탈을 써 넣는 예시 요청(인증 포함) — 라우터 구현의 출발점."""
    s = sim.site
    base = f"{root}/emrsim/{s['id']}"
    p, e = _example_person(sim)
    now = time.time()
    out = []
    a = s["auth"]
    auth_steps = []
    if token_path(sim):
        tu = f"{base}/{token_path(sim)}"
        if a["type"] in ("smart-backend-jwt", "signed-jwt"):
            auth_steps.append({"title": "액세스 토큰 (private_key_jwt)", "method": "POST", "url": tu, "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                               "body": f"grant_type=client_credentials&client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&client_assertion={_jwt(sim, tu, 'RS384' if a['type'] == 'smart-backend-jwt' else 'RS512')}"
                                       + (f"&scope={a.get('scope', '').replace(' ', '%20')}" if a.get("scope") else ""),
                               "note": "JWT 는 형식·클레임(iss=sub=client_id, aud=토큰 URL, jti, exp≤5분)만 검사하고 서명은 검증하지 않는다. 예시 JWT 는 4분 뒤 만료."})
        elif a["type"] == "client-credentials-basic":
            auth_steps.append({"title": "액세스 토큰 (client_secret_basic)", "method": "POST", "url": tu,
                               "headers": {"Authorization": "Basic " + base64.b64encode(f"{a['client_id']}:{a['client_secret']}".encode()).decode(), "Content-Type": "application/x-www-form-urlencoded"},
                               "body": "grant_type=client_credentials" + (f"&scope={a['scope'].replace(' ', '%20')}" if a.get("scope") else "")})
        elif a["type"] == "client-credentials-post":
            auth_steps.append({"title": "액세스 토큰 (client_secret_post)", "method": "POST", "url": tu, "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                               "body": f"grant_type=client_credentials&client_id={a['client_id']}&client_secret={a['client_secret']}"})
        elif a["type"] == "rnds-token":
            auth_steps.append({"title": "액세스 토큰 (mTLS 인증서 흉내)", "method": "GET", "url": tu, "headers": {"X-Client-Cert-CN": a["cert_cn"]}, "body": None})
    auth_hdr = {"smart-backend-jwt": {"Authorization": "Bearer <access_token>"}, "signed-jwt": {"Authorization": "Bearer <access_token>", "X-Request-ID": str(uuid.uuid4())},
                "client-credentials-basic": {"Authorization": "Bearer <access_token>"}, "client-credentials-post": {"Authorization": "Bearer <access_token>"},
                "basic": {"Authorization": "Basic " + base64.b64encode(f"{a.get('username')}:{a.get('password')}".encode()).decode()},
                "bearer-static": {"Authorization": f"Bearer {a.get('token')}"},
                "api-key": {a.get("header", ""): a.get("key", ""), **({a["hosp_header"]: a["hosp_cd"]} if a.get("hosp_header") else {}), **({a["extra_header"]: a["extra_value"]} if a.get("extra_header") else {})},
                "rnds-token": {"X-Authorization-Server": "Bearer <access_token>", "Authorization": re.sub(r"\D", "", a.get("requester_cpf", ""))},
                "mllp-facility": {"Authorization": "Basic " + base64.b64encode(f"{a.get('username')}:{a.get('password')}".encode()).decode()},
                "ip-allow": {}}[a["type"]]
    if s["protocol"] == "fhir":
        fb = f"{base}/{fhir_base_path(sim)}"
        us = s.get("units") == "us"
        subj = {"reference": f"Patient/{p['fhir_id']}"}
        enc_ref = {"reference": f"Encounter/{e['fhir_id']}"} if e else None
        cat = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category" if not F.stu3(sim) else "http://hl7.org/fhir/observation-category", "code": "vital-signs", "display": "Vital Signs"}]}]
        eff = sim.iso(now - 60)
        hr = {"resourceType": "Observation", "status": "final", "category": cat, "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}], "text": "Heart rate"},
              "subject": subj, **({("context" if F.stu3(sim) else "encounter"): enc_ref} if enc_ref else {}), "effectiveDateTime": eff,
              "valueQuantity": {"value": 82, "unit": "beats/minute", "system": "http://unitsofmeasure.org", "code": "/min"},
              "device": {"display": "BIOSIM-PATCH BP-000123"}}
        hr["meta"] = {"profile": [F.profiles(sim)["hr"]]}
        temp_q = {"value": c_to_f(36.9), "unit": "degF", "system": "http://unitsofmeasure.org", "code": "[degF]"} if us else {"value": 36.9, "unit": "Cel", "system": "http://unitsofmeasure.org", "code": "Cel"}
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": [
            {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": {**hr, "valueQuantity": {**hr["valueQuantity"], "value": 80}}, "request": {"method": "POST", "url": "Observation"}},
            {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": {"resourceType": "Observation", "status": "final", "category": cat,
                                                                  "code": {"coding": [{"system": "http://loinc.org", "code": "59408-5", "display": "Oxygen saturation in Arterial blood by Pulse oximetry"}]},
                                                                  "subject": subj, "effectiveDateTime": eff, "valueQuantity": {"value": 96, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"}},
             "request": {"method": "POST", "url": "Observation"}},
            {"fullUrl": f"urn:uuid:{uuid.uuid4()}", "resource": {"resourceType": "Observation", "status": "final", "category": cat,
                                                                  "code": {"coding": [{"system": "http://loinc.org", "code": "8310-5", "display": "Body temperature"}]},
                                                                  "subject": subj, "effectiveDateTime": eff, "valueQuantity": temp_q}, "request": {"method": "POST", "url": "Observation"}}]}
        out.append({"title": "심박수 1건 쓰기 (Observation create)", "method": "POST", "url": f"{fb}/Observation", "headers": {**auth_hdr, "Content-Type": "application/fhir+json"},
                    "body": json.dumps(hr, ensure_ascii=False, indent=2)})
        out.append({"title": "여러 바이탈 한 번에 (transaction Bundle)", "method": "POST", "url": fb, "headers": {**auth_hdr, "Content-Type": "application/fhir+json"},
                    "body": json.dumps(bundle, ensure_ascii=False, indent=2)})
    elif s["protocol"] == "hl7v2":
        out.append({"title": f"ORU^R01 (HL7 {s['hl7_version']}) — HTTP 로 보내기", "method": "POST", "url": f"{base}/hl7",
                    "headers": {**auth_hdr, "Content-Type": f"x-application/hl7-v2+er7; charset={hl7v2.charset(sim)}"}, "body": hl7v2.sample_oru(sim, e["person"] if e else None),
                    "note": f"같은 메시지를 MLLP tcp/{MLLP_PORT} 로 보내면 MSH-6={s['fac']} 로 이 기관에 라우팅된다. 세그먼트 구분자는 \\r."})
        if s["flavor"] == "meditech":
            out.append({"title": "IHE PCD-01 (HL7 2.6, MDC 코드) — 장비 게이트웨이 형식", "method": "POST", "url": f"{base}/hl7", "headers": {**auth_hdr, "Content-Type": "x-application/hl7-v2+er7"},
                        "body": hl7v2.sample_oru(sim, e["person"] if e else None, pcd=True)})
    elif s["protocol"] == "athena":
        vit = [[{"clinicalelementid": "VITALS.HEARTRATE", "value": "82"}], [{"clinicalelementid": "VITALS.BLOODPRESSURE.SYSTOLIC", "value": "128"}, {"clinicalelementid": "VITALS.BLOODPRESSURE.DIASTOLIC", "value": "76"}],
               [{"clinicalelementid": "VITALS.O2SATURATION", "value": "97"}], [{"clinicalelementid": "VITALS.TEMPERATURE", "value": "98.4"}]]
        from urllib.parse import urlencode
        out.append({"title": "바이탈 쓰기 (form-encoded, 체온은 °F)", "method": "POST", "url": f"{base}/v1/{s['practiceid']}/chart/encounter/{e['visit'] if e else '<encounterid>'}/vitals",
                    "headers": {**auth_hdr, "Content-Type": "application/x-www-form-urlencoded"},
                    "body": urlencode({"departmentid": "1", "source": "DEVICE", "vitals": json.dumps(vit)})})
    elif s["protocol"] == "kr-json":
        body = {"HOSP_CD": s["hosp_code"], "PT_NO": p["ids"]["mrn"], "ADM_NO": e["visit"] if e else "", "MSR_DTM": sim.local(now - 60).strftime("%Y%m%d%H%M%S"), "DEVICE_ID": "BP-000123",
                "VS_LIST": [{"VS_CD": "PR", "VS_VAL": "82"}, {"VS_CD": "RR", "VS_VAL": "18"}, {"VS_CD": "SPO2", "VS_VAL": "97"}, {"VS_CD": "BT", "VS_VAL": "36.8"}]}
        out.append({"title": "바이탈 등록 (세로형 VS_LIST)", "method": "POST", "url": f"{base}/api/v1/vs", "headers": {**auth_hdr, "Content-Type": "application/json; charset=UTF-8"},
                    "body": json.dumps(body, ensure_ascii=False, indent=2)})
    elif s["protocol"] == "kr-xml":
        lt = sim.local(now - 60)
        xml = (f'<?xml version="1.0" encoding="EUC-KR"?>\n<IF_MSG><HEADER><IF_ID>EMR_VS_0002</IF_ID><SND_SYS_CD>{a["snd_sys"]}</SND_SYS_CD><RCV_SYS_CD>OCS</RCV_SYS_CD>'
               f'<TRX_ID>BM{int(now)}</TRX_ID><TRX_DTM>{lt.strftime("%Y%m%d%H%M%S")}</TRX_DTM></HEADER><BODY><DATA_LIST><DATA><PTNT_NO>{p["ids"]["mrn"]}</PTNT_NO>'
               f'<VS_DT>{lt.strftime("%Y%m%d")}</VS_DT><VS_TM>{lt.strftime("%H%M")}</VS_TM><BT>36.8</BT><PR>82</PR><RR>18</RR><BP_H></BP_H><BP_L></BP_L><SPO2>97</SPO2>'
               f'<EQUIP_ID>BP-000123</EQUIP_ID></DATA></DATA_LIST></BODY></IF_MSG>')
        out.append({"title": "바이탈 등록 전문 EMR_VS_0002 (가로형, EUC-KR 로 인코딩해서 전송)", "method": "POST", "url": f"{base}/if",
                    "headers": {"Content-Type": "text/xml; charset=EUC-KR"}, "body": xml, "encoding": "cp949"})
    elif s["protocol"] == "cda":
        out.append({"title": "활력징후 CDA 문서 등록", "method": "POST", "url": f"{base}/cda/documents", "headers": {**auth_hdr, "Content-Type": "application/xml; charset=utf-8"},
                    "body": vendor.cda_sample(sim)})
    return auth_steps + out


@router.get("/api/v1/emrsim/{site_id}/samples")
def site_samples(site_id: str, request: Request):
    sim = _sim_or_404(site_id)
    return {"site": site_id, "requests": samples(sim, _req_root(request))}


@router.post("/api/v1/emrsim/{site_id}/selftest")
async def site_selftest(site_id: str, request: Request):
    """예시 요청을 실제 엔드포인트 처리 경로(인증·검증 포함)로 흘려 결과를 돌려준다 — '이 형식이 통과하는지' 확인용."""
    sim = _sim_or_404(site_id)
    root = _req_root(request)
    reqs = samples(sim, root)
    token = None
    results = []
    for r in reqs:
        headers = {k.lower(): (v.replace("<access_token>", token) if token and isinstance(v, str) else v) for k, v in r["headers"].items()}
        body = r["body"]
        raw = body.encode(r.get("encoding", hl7v2.charset(sim) if sim.site["protocol"] == "hl7v2" else "utf-8"), errors="replace") if body else b""
        path = r["url"].split(f"/emrsim/{site_id}/", 1)[1] if f"/emrsim/{site_id}/" in r["url"] else fhir_base_path(sim)
        path, _, qs = path.partition("?")
        ctx = Ctx(r["method"], path, parse_qsl(qs), headers, raw, f"{root}/emrsim/{site_id}", "selftest")
        rep = await asyncio.to_thread(dispatch, sim, ctx)
        sim.add_log("in", "selftest", "[자체시험] " + (rep.summary or ""), rep.status, r["method"], "/" + path, "selftest", body[:1500] if body else None)
        text = rep.body.decode("cp949" if sim.site["protocol"] == "kr-xml" else hl7v2.charset(sim) if sim.site["protocol"] == "hl7v2" else "utf-8", errors="replace")
        if "token" in r["title"].lower() or "토큰" in r["title"]:
            try:
                token = json.loads(text).get("access_token")
            except ValueError:
                pass
        ok = rep.status < 300 and not re.search(r"MSA\|A[ER]|RESULT_CD\":\s*\"E|<RSLT_CD>E|\"error\"", text)
        results.append({"title": r["title"], "status": rep.status, "ok": ok, "summary": rep.summary, "response": text[:3000]})
    return {"site": site_id, "ok": all(x["ok"] for x in results), "results": results}
