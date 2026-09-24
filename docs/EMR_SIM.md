# 가상 EMR 연동 서버 (상용 EMR 연동 시험용 20곳)

에뮬레이터 안에 **가상 의료기관 20곳**이 있다. 각 기관은 자기 나라·EMR 계열의 형식과 인증 방식으로 환자, 입·전·퇴원(ADT), 간호 바이탈을 내준다.
라우터(또는 연동 모듈)가 바이탈을 보내면 그 기관의 규칙대로 검증해 저장하거나 거부한다.
목적은 **상용 EMR 연동 코드를 실제 병원에 붙이기 전에 형식 차이, 인증, 오류 응답, 장애를 미리 겪어 보는 것**이다.

- 카탈로그: `GET /api/v1/emrsim` — 기관 목록, 형식, 인증 정보, 예시 환자, 엔드포인트
- 기관별 연동 경로: `/emrsim/{site_id}/...`
- HL7 v2 MLLP: **tcp/2575** (한 포트, MSH-6 수신 기관으로 기관을 고른다)
- GUI: 상단 메뉴 **EMR 연동** — 기관 표, 자체 시험, 예시 요청, 장애 주입, 요청 로그, 수신 바이탈

> 기관명은 모두 가공이다. "계열"은 그 제품이 외부에 내보이는 인터페이스 형식을 흉내냈다는 뜻이며 해당 회사의 서버·데이터와 무관하다.
> 식별번호(NHS·BSN·CPF·CNS·INS·IHI·NRIC·Emirates ID·NPI·OHIP·KVNR·주민번호)는 각국 **체크 디지트 규칙을 통과하는 합성값**이다. NHS 번호는 시험용 999 대역을 쓴다.
> 주민등록번호는 외부로 항상 마스킹(`YYMMDD-G******`)해서 내보낸다.

## 1. 기관 목록

| site_id | 국가 | 기관 | 형식 · 버전 | 인증 | 특징 |
|---|---|---|---|---|---|
| `us-lakeshore` | 미국 | Lakeshore University Health | FHIR R4 · US Core (Epic 계열) | SMART Backend Services (private_key_jwt, RS384) | 불투명 id(`e…3`), EPI/MRN OID, **Patient는 검색 조건 필수, Observation은 category 또는 code 필수**, 재원 명단은 `Group/inpatient-census`, 체온 °F |
| `us-sierravista` | 미국 | Sierra Vista Regional Medical Center | FHIR R4 · US Core (Oracle Health/Cerner 계열) | client_credentials + HTTP Basic | 테넌트 경로 `r4/{tenant}`, 숫자 id, `-pageContext` 페이지 링크, 모르는 검색 파라미터는 400, Encounter는 patient 필수, 애리조나(서머타임 없음) |
| `us-pineridge` | 미국 | Pine Ridge Community Hospital | HL7 v2.5.1 · MLLP/HTTP (MEDITECH 계열) | MLLP=MSH-6, HTTP=Basic | 대문자 이름, PID-18 계정번호, NPI 주치의, ICD-10-CM(I10C), **IHE PCD-01(v2.6, MDC 코드)도 수신** |
| `us-bayside` | 미국 | Bayside Heart & Vascular Clinic | athenaOne 계열 REST | client_credentials + Basic | `/v1/{practiceid}/…`, MM/DD/YYYY, 단건 조회도 배열, form-encoded 쓰기, 체온 °F, 변경분 구독(`patients/changed`), 외래·원격 모니터링 |
| `uk-kingsmere` | 영국 | Kingsmere Hospitals NHS FT | FHIR R4 · UK Core | 서명 JWT (RS512, kid 필수) | **`X-Request-ID`(UUID) 헤더 필수**(없으면 400 INVALID_VALUE), NHS 번호 + 검증상태 확장, 민족 코드, GP(ODS) |
| `uk-wexcombe` | 영국 | Wexcombe University Hospitals NHS Trust | HL7 v2.4 · MLLP/HTTP (TrakCare/CareFlow 계열) | MLLP/Basic | PID-3 `NHS^NH` + 병원번호, PD1 GP 진료소, 병동^베이^병상, 구형 ERR 형식 |
| `jp-toto` | 일본 | 東都中央医療センター | HL7 v2.5 · SS-MIX2 | MLLP/Basic | **ISO-2022-JP** (`MSH-18 ~ISO IR87`), 한자/가나 이름 반복(I/P), 患者ID(PI), MEDIS 病名(MDCDX2), 로컬 바이탈 코드 `VS00x^…^99L01`, 시각에 오프셋 없음 |
| `jp-kitahama` | 일본 | 北浜市立総合病院 | FHIR R4 · JP Core | 고정 Bearer 토큰 | 환자 id 체계 `urn:oid:1.2.392.100495.20.3.51.1{医療機関コード}`, 이름 IDE/SYL 표현 확장 |
| `kr-hanbit` | 한국 | 한빛대학교병원 | FHIR R4 · KR Core | client_credentials (secret post) | 등록번호 OID, 주민번호 마스킹(NNKOR), KCD-8, 로컬 바이탈 코드 병기 |
| `kr-saesol` | 한국 | 새솔대학교병원 | REST JSON (국내 대학병원 EMR 계열) | `X-API-KEY` + `X-HOSP-CD` | 대문자 컬럼 키(`PT_NO`, `ADM_DTM`…), **업무 오류도 HTTP 200 + `RESULT_CD`**, 세로형 바이탈(`VS_CD`/`VS_VAL`) |
| `kr-donghae` | 한국 | 동해중앙병원 | XML over HTTP · **EUC-KR** (OCS 레거시) | 없음(송신 시스템 코드 필수) | URL 하나(`POST /if`)에 `IF_ID` 전문, 가로형 바이탈(`BT/PR/RR/BP_H/BP_L/SPO2`), 부분 성공 `P` |
| `kr-cheongram` | 한국 | 청람의료재단 청람병원 | HL7 CDA R2 (진료정보교류 표준 방식) | 고정 Bearer 토큰 | 문서 기반: 진료의뢰서·퇴원요약 조회, 활력징후 CDA 문서 등록 |
| `de-rheinaue` | 독일 | Klinikum Rheinaue | FHIR R4 · ISiK Stufe 3 | HTTP Basic | KVNR(`KVZ10`), ICD-10-GM + 진단확실도, Fachabteilungsschlüssel, 모르는 검색 파라미터 400 |
| `fr-belveze` | 프랑스 | CHU de Belvèze | HL7 v2.5 · IHE PAM FR | MLLP/Basic | **ISO 8859-1**, IPP + INS(NIR, OID 1.2.250.1.213.1.4.8), 출생성(L)/사용성(D), **ZBE 이동 세그먼트**, PID-32 `VALI`, RPPS |
| `nl-amstelwaard` | 네덜란드 | Ziekenhuis Amstelwaard | **FHIR STU3** · Nictiz zib | client_credentials (secret post) | BSN(11-proef), 성 접두사 확장(`van der`), STU3 문법(Observation.`context`, 코드형 status) |
| `au-brindabella` | 호주 | Brindabella Base Hospital | FHIR R4 · AU Core | client_credentials (secret post) | IHI(+상태 확장), Medicare, HPI-O 범위 MRN, 원주민 여부 확장 |
| `ca-stlucien` | 캐나다 | St. Lucien General Hospital | HL7 **v2.3** · MLLP/HTTP | MLLP/Basic | MSH-9 두 구성요소, OHIP 건강카드 + 버전코드(CX-2), 오프셋 없는 시각, ICD-10-CA |
| `sg-tanjongrhu` | 싱가포르 | Tanjong Rhu General Hospital | FHIR R4 | `x-api-key` + `x-client-id` | NRIC(체크 문자), 민족별 이름 관례(bin/binte, s/o) |
| `br-santaclara` | 브라질 | Hospital Santa Clara do Vale | FHIR R4 · RNDS 프로파일 | 인증서 토큰 + `X-Authorization-Server` + CPF | CNS/CPF, CID-10(`BRCID10`), 인종/피부색 확장, 토큰 `expires_in`은 ms |
| `ae-alwaha` | 아랍에미리트 | Al Waha Specialty Hospital | HL7 v2.5.1 · Malaffi 관례 | MLLP/Basic | UTF-8, 영문/아랍 이름 반복, Emirates ID(Luhn), PID-28 국적, 외국 국적 환자 다수 |

국가 4·2·2·4 + 기타 8. 프로토콜은 FHIR R4 9 · STU3 1 · HL7 v2 6(2.3/2.4/2.5/2.5.1) · 벤더 REST 1 · JSON 1 · EUC-KR XML 1 · CDA 1.

인증 정보(클라이언트 id, 비밀, 토큰, API 키)는 카탈로그 `auth` 에 그대로 나온다(시험용).

## 2. 공통 동작

**ADT 시뮬레이션**: 기관마다 병동·병실·병상(24~60개), 환자 마스터(420명, 외래 기관 300명), 의료진 12명을 가진다.
- 시작 시각은 UTC 자정 기준 3일 전이다. 그때 병상의 약 82%가 차 있다.
- 이후 입원(A01), 전동(A02), 퇴원(A03), 정보 변경(A08), 입원 취소(A11)가 결정적으로 생긴다.
- **같은 날 안에서는 재시작해도 같은 환자, 같은 내원번호, 같은 이벤트가 나온다.** 날짜가 바뀐 뒤 재시작하면 새 3일 이력이 생긴다.

**간호 바이탈**: 입원 15분 뒤 1회, 이후 현지 02·06·10·14·18·22시(몇 분씩 늦게)에 HR, RR, SpO₂, 체온, 혈압이 기록된다. 값은 진단(심방세동, 패혈증, COPD…)과 나이에 따라 치우친다.

**수신 바이탈**: 형식별로 검증을 통과하면 저장되고, 그 기관의 조회 API에도 함께 나온다(FHIR는 보낸 원문 그대로). 저장소는 메모리이므로 **재시작하면 비워진다.**

**측정 항목과 코드**

| 항목 | LOINC | IEEE 11073 MDC | 한국 로컬 | 일본 로컬 | athena |
|---|---|---|---|---|---|
| 심박 | 8867-4 | 147842 MDC_ECG_HEART_RATE (149530 PULS_OXIM_PULS_RATE) | PR | VS002 | VITALS.HEARTRATE |
| 호흡 | 9279-1 | 151562 MDC_RESP_RATE | RR | VS003 | VITALS.RESPIRATIONRATE |
| SpO₂ | 59408-5 (2708-6) | 150456 MDC_PULS_OXIM_SAT_O2 | SPO2 | VS006 | VITALS.O2SATURATION |
| 체온 | 8310-5 | 150364 MDC_TEMP_BODY | BT | VS001 | VITALS.TEMPERATURE (°F) |
| 수축기 | 8480-6 | 150021 | SBP / BP_H | VS004 | VITALS.BLOODPRESSURE.SYSTOLIC |
| 이완기 | 8462-4 | 150022 | DBP / BP_L | VS005 | VITALS.BLOODPRESSURE.DIASTOLIC |
| 혈압 패널 | 85354-9 | — | — | — | — |

단위는 UCUM(`/min`, `%`, `Cel`, `[degF]`, `mm[Hg]`)과 MDC 단위 코드를 받는다. 미국 기관은 °F를 돌려주고, °F로 받아도 °C로 환산해 저장한다.
허용 범위를 벗어나면 거부한다: HR 20–300, RR 2–80, SpO₂ 50–100, 체온 30–43.5 °C, 수축기 40–300, 이완기 20–200.

## 3. FHIR 기관 (10곳)

기본 경로(사이트 루트 뒤):

| site | base | 토큰 |
|---|---|---|
| us-lakeshore | `api/FHIR/R4` | `POST oauth2/token` (client_assertion JWT RS384/ES384) |
| us-sierravista | `r4/{tenant}` | `POST tenants/{tenant}/protocols/oauth2/profiles/smart-v1/token` (Basic) |
| uk-kingsmere | `FHIR/R4` | `POST oauth2/token` (JWT RS512 + kid) |
| jp-kitahama | `fhir` | 없음 (고정 Bearer) |
| kr-hanbit | `fhir/r4` | `POST oauth2/token` (client_id/secret 폼) |
| de-rheinaue | `fhir` | 없음 (Basic) |
| nl-amstelwaard | `fhir/stu3` | `POST oauth2/token` (폼) |
| au-brindabella | `fhir/r4` | `POST oauth2/token` (폼) |
| sg-tanjongrhu | `fhir/r4` | 없음 (API 키) |
| br-santaclara | `api/fhir/r4` | `GET api/token` + `X-Client-Cert-CN` (mTLS 흉내) |

- JWT는 형식과 클레임만 검사한다: `iss = sub = client_id`, `aud = 토큰 URL`, `jti`, `exp`가 현재~5분 안. **서명은 검증하지 않는다.**
- 토큰 수명 기본값은 3600초이고 GUI에서 바꿀 수 있다. 만료되면 401과 `WWW-Authenticate: Bearer error="invalid_token"` 을 돌려준다.

**리소스**: Patient, Encounter, Observation, Condition, AllergyIntolerance, Practitioner, Location(병동/병실/병상, 병상은 `operationalStatus` O/U), Organization, Group(재원 명단).

**읽기와 공개 경로**
- `GET {type}/{id}`
- `GET metadata`: CapabilityStatement, 인증 불필요
- `GET .well-known/smart-configuration`

**검색**
- `_id`, `identifier=system|value`, `family`, `given`, `name`, `birthdate`, `gender`
- `patient`/`subject`, `status`, `date`(ge/le/gt/lt), `location`, `_lastUpdated`
- `category`, `code`, `encounter`, `clinical-status`, `_count`, `_sort`
- `_include=Encounter:patient|Encounter:location`

페이지는 `link[relation=next]` 를 따라간다.

**계열별 검색 제약**

| 계열 | 제약 |
|---|---|
| Epic | Patient는 조건 없이 검색하면 400. Encounter는 patient 필수. Observation은 patient + (category 또는 code). `total`이 생략된다(`_total=accurate` 를 주면 포함). |
| Oracle | Patient, Encounter, Observation 모두 patient 계열 조건이 필수다. 모르는 파라미터는 400. `-pageContext` 링크, `_count` 최대 100. |
| UK Core·ISiK | 모르는 파라미터는 400. |
| 그 외 | `Encounter?status=in-progress&_include=Encounter:patient` 로 재원 명단을 받는다. Epic·Oracle은 `Group/inpatient-census` 를 쓴다. |

**쓰기**
- `POST Observation`: 성공하면 201과 `Location: …/Observation/{id}/_history/1`, `ETag`, `Last-Modified` 를 준다. `Prefer: return=minimal|OperationOutcome` 을 지원한다.
- `POST {base}`: Bundle.
  - `transaction`: 전부 검증한 뒤 저장하고, 하나라도 실패하면 400이며 아무것도 저장하지 않는다.
  - `batch`: 항목별 결과를 돌려준다.
- 검증 실패는 `OperationOutcome` 으로 돌려준다.
  - 400: 문법 오류. 예를 들어 **시간대 없는 `effectiveDateTime`**, STU3에 `encounter`.
  - 422: 규칙 위반. 예를 들어 category에 vital-signs 없음, LOINC 없음, 모르는 환자, 다른 환자의 Encounter, 단위 불일치, 범위 초과, 미래 시각.
- 환자 참조는 `Patient/{id}` 외에 `subject.identifier {system, value}`(논리 참조)도 받는다.

## 4. HL7 v2 기관 (6곳)

| site | 버전 (허용) | MSH-6 수신기관 | 문자셋 | 환자 식별 |
|---|---|---|---|---|
| us-pineridge | 2.5.1 (2.5.1, 2.6) | `PRCH` | UTF-8 | `M000xxxxxx^^^PRCH^MR` |
| uk-wexcombe | 2.4 | `RW8` | UTF-8 | `999xxxxxxx^^^NHS^NH` 또는 `Wxxxxxxx^^^RW8^MR` |
| jp-toto | 2.5 (2.5, 2.5.1) | `1311234567` | ISO-2022-JP | `0000xxxxxx^^^^PI` |
| fr-belveze | 2.5 (2.5, 2.5.1) | `CHUBEL` | ISO 8859-1 | IPP `^PI` 또는 INS `^INS` |
| ca-stlucien | 2.3 (2.3, 2.3.1) | `SLGH` | UTF-8 | `Mxxxxxxx^^^SLGH^MR` 또는 OHIP `^HC` |
| ae-alwaha | 2.5.1 (2.5.1, 2.6) | `MF1234` | UTF-8 | `AWxxxxxxx^^^MF1234^MR` 또는 Emirates ID |

**수신(ORU^R01)**
- MLLP `tcp/2575` 로 보낸다. 프레임은 `0x0B … 0x1C 0x0D` 이고, 한 연결에서 여러 메시지를 연달아 보낼 수 있다.
- 또는 `POST /emrsim/{site}/hl7` (Basic 인증) 로 보낸다.
- 세그먼트 구분자는 `\r` 이다.

- OBX-3은 LOINC(`LN`), MDC(`MDC`), 일본 로컬(`99L01`)을 받는다. 1차 코드와 대체 코드(4~6번째 구성요소)를 모두 본다.
- OBX-2는 NM/SN/ST, OBX-11은 F/C/P/R. 측정 시각은 OBX-14, 없으면 OBR-7. 장비 id는 OBX-18.
- ACK의 MSA 코드
  - **AA**: 저장. 경고는 ERR 심각도 `W` 로 붙는다.
  - **AE**: 모르는 환자(204), 숫자 아님(102), 단위·범위 오류.
  - **AR**: 지원하지 않는 버전(203), 메시지 타입(200/201), 파싱 불가.
- ERR 형식은 버전에 따라 다르다.
  - 2.3/2.4: `ERR|PID^1^3^204&설명`
  - 2.5 이상: `ERR||PID^1^3|204^Unknown key identifier^HL70357|E`
- MSH-6이 어느 기관과도 맞지 않으면 MLLP는 AR로 답한다.

**ADT 내보내기**
- `GET /emrsim/{site}/hl7/adt?since={seq}&limit=`: JSON으로 `messages[].er7` 과 `next_since` 를 준다. `&format=batch` 를 주면 FHS/BHS 배치 텍스트(그 기관 문자셋)가 된다.
- `GET /emrsim/{site}/hl7/census`: 현재 재원 환자 전체를 A01로 준다(초기 동기화용).
- `POST /api/v1/emrsim/{site}/push {"host","port","since"}`: 에뮬레이터가 ADT를 MLLP로 **순서대로** 밀어 준다. ACK가 AA/CA가 아니면 10초 뒤 같은 메시지를 다시 보낸다. 중지는 `DELETE /api/v1/emrsim/{site}/push`.
- 이벤트 종류는 A01, A02(PV1-6 이전 병상), A03(PV1-45·PV1-36), A08, A11이다. A01에는 AL1(알레르기)과 DG1(진단)이 붙는다. 프랑스 기관은 모든 이벤트에 ZBE가 붙는다.

## 5. athenaOne 계열 (`us-bayside`)

토큰은 `POST oauth2/v1/token` (Basic `client_id:secret`, `grant_type=client_credentials`). 경로 앞부분은 `v1/1959418`.

- `GET departments`, `GET providers`
- `GET patients?departmentid=1`: 현재 모니터링 중인 환자. `lastname`/`firstname`/`dob(MM/DD/YYYY)` 로도 찾는다. 조건이 없으면 400 `missingfields`.
- `GET patients/{patientid}`: **배열**로 돌려준다.
- `GET chart/{patientid}/encounters|vitals|problems|allergies?departmentid=1`
- `POST chart/encounter/{encounterid}/vitals`: form-encoded `departmentid=1&source=DEVICE&vitals=[[{"clinicalelementid":"VITALS.HEARTRATE","value":"82"}], …]`.
  - 체온은 °F다. `"unit":"C"` 를 주면 °C로 받는다.
  - 닫힌 encounter에는 쓸 수 없다(400 `The encounter is not open.`).
- 변경분 구독: `POST patients/changed/subscription` 후 `GET patients/changed` 를 호출하면 마지막 호출 이후 바뀐 환자를 돌려준다. `leaveunprocessed=true` 를 주면 커서를 유지한다.

## 6. 국내 REST JSON (`kr-saesol`)

헤더: `X-API-KEY: SS-EMR-7F3A-91C2`, `X-HOSP-CD: 31200017`. 응답은 `{"RESULT_CD","RESULT_MSG","TRX_DTM","TOTAL_CNT","DATA"}`.

**조회**
- `GET api/v1/adm/inpatients?WARD_CD=&MEDDEPT_CD=`: 재원 명단
- `GET api/v1/adm/events?FROM_SEQ=`: ADT(`ADM/TRF/DSC/UPD/CNL`, 전동은 `BF_*`)
- `GET api/v1/pat/{PT_NO}`: 주민번호는 마스킹된다.
- `GET api/v1/vs?PT_NO=&FROM_DTM=&TO_DTM=`: 세로형이다. `MSR_TP_CD` 가 N이면 간호 기록, D이면 장비 값.
- `GET api/v1/code/wards`, `GET api/v1/code/vs`

**등록**
- `POST api/v1/vs {"PT_NO","ADM_NO","MSR_DTM":"YYYYMMDDHH24MISS","DEVICE_ID","VS_LIST":[{"VS_CD":"PR","VS_VAL":"82"}]}`

**오류 코드** (인증 실패만 HTTP 401이고, 나머지 업무 오류는 **HTTP 200**)

| 코드 | 뜻 |
|---|---|
| E001 | 인증 실패 |
| E100 | 필수 누락 |
| E101 | JSON 오류 |
| E102 | 일시 형식 오류 |
| E200 | 환자 없음 |
| E201 | 입원 불일치 또는 재원 아님 |
| E300 | 코드·범위 오류 |

## 7. EUC-KR XML 전문 (`kr-donghae`)

`POST /emrsim/kr-donghae/if`, `Content-Type: text/xml; charset=EUC-KR`. 본문은 EUC-KR(CP949)로 인코딩한다.

```xml
<?xml version="1.0" encoding="EUC-KR"?>
<IF_MSG><HEADER><IF_ID>EMR_VS_0002</IF_ID><SND_SYS_CD>BIOMON</SND_SYS_CD><RCV_SYS_CD>OCS</RCV_SYS_CD><TRX_ID>…</TRX_ID><TRX_DTM>…</TRX_DTM></HEADER>
<BODY><DATA_LIST><DATA><PTNT_NO>0303038</PTNT_NO><VS_DT>20260924</VS_DT><VS_TM>1015</VS_TM><BT>36.8</BT><PR>82</PR><RR>18</RR><BP_H/><BP_L/><SPO2>97</SPO2><EQUIP_ID>…</EQUIP_ID></DATA></DATA_LIST></BODY></IF_MSG>
```

| IF_ID | 내용 |
|---|---|
| EMR_ADT_0001 | 재원 조회 (`REQ/WD_CD`) |
| EMR_ADT_0002 | ADT 이벤트 (`REQ/FROM_SEQ`, `EVT_GB` I/T/O/U/C) |
| EMR_PAT_0001 | 환자 조회 (`REQ/PTNT_NO`) |
| EMR_VS_0001 | 바이탈 조회 (가로형, `INPUT_GB` N=간호, M=장비) |
| EMR_VS_0002 | 바이탈 등록 (가로형, 행별 `PROC_CD`) |

- 응답 `HEADER/RSLT_CD`: S(정상), E(오류), P(일부 오류).
- `SND_SYS_CD` 가 빠지면 E. 전문 오류도 HTTP 200으로 돌려준다.

## 8. CDA R2 문서 (`kr-cheongram`)

헤더: `Authorization: Bearer crh-cda-4d2b7e61f0`.

- `GET cda/documents?ADMITTED=Y&PT_NO=&TYPE=REFERRAL|DISCHARGE|VITALS&FROM=`: 문서 목록(XML)
- `GET cda/documents/{DocId}`: `ClinicalDocument` 를 돌려준다. 섹션은 진단(KCD-8), 알레르기, 활력징후이고, 활력징후에는 표와 LOINC 관찰 엔트리가 들어 있다.
- `POST cda/documents`: 활력징후 문서를 등록한다.

**등록 검증 규칙**
- `typeId 2.16.840.1.113883.1.3 / POCD_HD000040`
- 허용 `templateId`
  - `1.2.410.100110.40.2.1.9`: 활력징후
  - `.1.1`: 진료의뢰서
  - `.1.2`: 진료회송서
  - `.1.3`: 퇴원요약
- 환자 id root는 `1.2.410.100110.10.34100089.100`
- 문서 id 중복은 409
- 관찰값은 `code@codeSystem=2.16.840.1.113883.6.1`(LOINC) + `value xsi:type="PQ"`

templateId OID는 이 에뮬레이터용 합성값이다(대한민국 arc 1.2.410 아래).

## 9. 관리 API

| 요청 | 내용 |
|---|---|
| `GET /api/v1/emrsim` | 카탈로그(20곳, 통계 포함) |
| `GET /api/v1/emrsim/{site}` | 상세(병동별 병상·점유) |
| `GET /api/v1/emrsim/{site}/samples` | 이 기관에 바이탈을 쓰는 **예시 요청**(토큰 발급 단계 포함, 헤더·본문 그대로) |
| `POST /api/v1/emrsim/{site}/selftest` | 예시 요청을 실제 처리 경로(인증·검증)로 흘려 통과 여부를 돌려준다 |
| `GET /api/v1/emrsim/{site}/log?since=` | 요청 로그(방향, 채널, 상태, 요약, 본문 앞 1500자) |
| `GET /api/v1/emrsim/{site}/received` | 수신·저장한 바이탈(표준 단위 + 원래 값·단위) |
| `POST /api/v1/emrsim/{site}/faults` | `{"latency_ms","error_rate","down","token_ttl_s","auth"}` 장애 주입 |
| `POST /api/v1/emrsim/{site}/tokens/revoke` | 발급 토큰 전부 폐기 (재발급 로직 시험) |
| `POST/DELETE /api/v1/emrsim/{site}/push` | HL7 ADT MLLP 푸시 시작·중지 |

장애 주입
- `down`: HTTP 503 + `Retry-After`, MLLP 연결 끊기
- `error_rate`: 비율만큼 5xx
- `latency_ms`: ±40% 지연
- `auth:false`: 인증 검사 끄기

## 10. 라우터 연동 체크리스트

1. `GET /api/v1/emrsim` 에서 기관과 인증 정보를 읽는다.
2. 기관마다 토큰을 발급·갱신한다. 만료되면 401을 받게 되고, `tokens/revoke` 로 시험할 수 있다.
3. 재원 명단과 ADT를 받는다. FHIR는 Group/Encounter, HL7은 census + adt 또는 push, JSON/XML은 inpatients + events, athena는 patients + changed.
4. 우리 환자를 그 기관 식별자로 매칭한다. MRN, NHS, IHI, BSN, INS, 등록번호…
5. 바이탈을 그 기관 형식으로 보낸다. 시간대, 단위(°F), 코드 체계, 문자셋(ISO-2022-JP, 8859-1, EUC-KR)에 주의한다.
6. 응답을 해석한다. 201/ACK AA/`0000`/`S` 이면 성공이고, 오류는 재시도하거나 폐기한다. **KR JSON·XML은 HTTP 200이어도 실패일 수 있다.**
7. `faults` 로 지연·5xx·다운을 걸어 재시도와 백오프를 확인한다.
