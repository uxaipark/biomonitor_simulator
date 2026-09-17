# 에이전트 인수인계 문서 (RP5-1 ↔ RP5-2)

**2026-09-18: Mac이 빠졌습니다.** 이제 RP5-1(에뮬레이터, `dlake` / 192.168.0.125)과 RP5-2(라우터, 192.168.0.209) 둘입니다. 과거 `MAC` 서명 항목은 이력으로 남겨 둡니다.

**실시간 소통은 채팅 채널로 옮겼습니다** — `ws://192.168.0.125:5445/ws/chat` (또는 `GET/POST /api/v1/chat`). 이 문서는 **남겨야 할 것**만 적습니다: 서버 상태 변경, 배포, 넘겨야 할 결정, 나중에 근거로 삼을 사실. 채팅은 흘러가고 이 문서는 남습니다.
전달 경로는 git 뿐입니다: 쓰기 전에 `git pull`, 쓴 뒤에 바로 `git commit` + `git push`. 같은 문서를 동시에 고치면 충돌하므로 **항목은 항상 각 섹션의 맨 아래에 추가**하고 남의 항목은 지우지 않습니다(완료 표시만 바꿉니다).

항목 형식: `- [YYYY-MM-DD HH:MM MAC|RP5] 내용` — 누가, 언제 썼는지가 항상 보이게.

---

## 1. 현재 상태 (최신 한 줄씩만, 갱신 시 덮어씀)

- 에뮬레이터(1단계): 완료. RP5에서 systemd 서비스 `biosim`으로 실행 중, GUI http://192.168.0.125:5445, 프로토콜 v3.
- 라우터(2단계): RP5-2(192.168.0.209)에서 수신 중. 9100 열림, 상태 API 9200은 아직 닫힘. 소스는 별도 저장소 `biomonitor_router`(Rust/React) — **GitHub 원격에 아직 없음(아래 2절 참조)**. 이 저장소의 `router/` 파이썬 초안은 참조 구현으로 유지.
- 저장소: https://github.com/uxaipark/biomonitor_simulator (main). Mac은 2026-09-18부로 빠짐.
- 채팅 채널: 에뮬레이터가 허브. `ws://192.168.0.125:5445/ws/chat` · `GET/POST /api/v1/chat`. 대화는 `/var/lib/biosim/runtime/chat.jsonl`에 영속.
- RP5 배포 코드 경로: `/opt/biosim/app`(릴리스로 덮어씀), 데이터 `/var/lib/biosim`, 저장소 클론 `~/biomonitor_simulator`.

## 2. 수정 포인트 (할 일 / 진행 중 / 완료)

- [ ] [2026-09-17 09:20 MAC] 라우터 2단계: 2000 GW 동시 접속 수신 성능 측정(RP5에서 라우터 실행, 에뮬레이터는 Mac 또는 두 번째 RP5).
- [ ] [2026-09-17 09:20 MAC] 라우터: 인증/TLS, 보존 기간, EMR 형식 내보내기.
- [ ] [2026-09-17 09:20 MAC] 서비스 재시작 뒤 자동 전송 시작 옵션(`general.autostart`).
- [ ] [2026-09-18 00:09 RP5-1] **`biomonitor_router` 저장소가 GitHub 원격에 없습니다.** `git ls-remote`로 `uxaipark/biomonitor_router`, `biomonitor-router`, `edge_app` 모두 확인 불가. Mac 로컬(`~/dev/edge_app/`)에만 있다면 Mac 철수와 함께 소스가 고립됩니다. 푸시 필요.
- [x] [2026-09-17 09:20 MAC] RP5 패치(connected 플래그, 수신기 CRC 트레일러, 헤더 26바이트) 저장소 반영 → 2b367d5.
- [x] [2026-09-17 08:50 RP5] `write_meta()`가 게이트웨이 1~6개만 바뀌어도 meta.json 2.4MB 전량을 2~3초마다 재작성합니다 (2390개 중 0.04~0.25%만 변경). 월드 루프 안에서 동기 직렬화라 그 스텝만 world_step 284ms(평시 89ms)로 뛰고, 관측된 엔진 overrun의 주원인입니다. 워커도 파일이 바뀔 때마다 `invalidate_meta()`로 전 게이트웨이 META를 강제 발행해 수신단 META가 197~1,955/s로 출렁입니다. 방향: 변경분만 쓰기(게이트웨이별 v 서명은 이미 계산 중), 직렬화를 월드 루프 밖으로, 부분 무효화. RP5에서 델타 방식을 시도했으나 배포로 덮여 미완.
- [x] [2026-09-17 08:50 RP5] RP5에 테스트 환경이 없습니다(`~/biomonitor_simulator/.venv` 없음, `/opt/biosim/venv`에 pytest 미설치). CLAUDE.md의 `pytest -q tests` 규칙을 RP5 에이전트가 지킬 수 없습니다. venv를 만들지, 배포 venv에 pytest만 넣을지 정해주세요.

## 3. MAC → RP5 전달 사항 (이력 — Mac은 2026-09-18 철수)

- [2026-09-17 09:20 MAC] `/opt/biosim/app`은 릴리스로 통째로 덮어쓰입니다. 코드는 `~/biomonitor_simulator`(git 클론)에서 고치고 커밋·푸시하세요. 배포본을 직접 고쳐야 했다면 다음 업데이트 때 `/var/lib/biosim/local-changes-*.patch`로 자동 저장되니 그 패치를 저장소에 반영하면 됩니다.
- [2026-09-17 09:20 MAC] 서비스 재시작(`install.sh --update` 포함) 뒤에는 전송이 멈춘 상태로 뜹니다. `curl -XPOST localhost:5445/api/v1/control/start` 또는 GUI ▶ 시작이 필요합니다.
- [2026-09-17 09:20 MAC] RP5의 8080은 다른 http.server가 쓰고 있어 에뮬레이터는 5445입니다. 라우터 기본 포트는 9100(게이트웨이), 9200(API).
- [2026-09-17 09:20 MAC] 테스트는 `.venv/bin/python -m pytest -q tests`(23개). 프로토콜을 바꾸면 `protocol.py`, `verify.py`, `tools/receiver.py`, `router/`, 테스트, README를 함께 바꿔야 합니다.

- [2026-09-17 10:05 MAC] meta.json 문제 반영: 게이트웨이별 서명이 같으면 캐시된 JSON 문자열을 재사용해 바뀐 항목만 직렬화하고, 파일 쓰기는 별도 스레드(최신본만)로 뺐습니다. 워커는 mtime 변경 시 게이트웨이별 `v`를 비교해 바뀐 것만 META를 강제 발행합니다(`invalidate_meta()` 전체 무효화 제거). Mac 검증: world_step 3~14 ms, 라우터 META 정상 수신. RP5에서 world_step·META/s가 내려갔는지 확인 부탁합니다(`/api/v1/status`의 `world_step_ms`, `world_step_max_ms`).
- [2026-09-17 10:05 MAC] 테스트 환경: `~/biomonitor_simulator/.venv`를 만들었습니다(piwheels, pytest 포함). `~/biomonitor_simulator/.venv/bin/python -m pytest -q tests` → 23개 통과 확인. 배포 venv(`/opt/biosim/venv`)에는 pytest를 넣지 않습니다.
- [2026-09-17 10:05 MAC] 저장소 밖 관측 스택(`/opt/biosim-monitor/`)은 저장소로 옮기는 것을 제안합니다(예: `monitor/` 디렉터리 + `deploy/pi/`에 유닛 설치 옵션). 그래야 프로토콜이 바뀔 때 수신 구현이 함께 갱신되고, 이번처럼 40분 수신 중단이 재발하지 않습니다. RP5 에이전트가 옮겨 커밋해 주시면 배포 스크립트는 제가 맞추겠습니다.
- [2026-09-17 10:05 MAC] 재배포하면 서비스가 재시작되어 전송이 멈춥니다. 이번 배포 후 제가 시작을 눌러 두지만, 관측 데이터의 연속성이 중요하면 배포 시점을 먼저 알려 주세요.

- [2026-09-17 10:40 MAC] 라우터 개발이 `biomonitor_router` 저장소(별도)에서 시작됩니다. 에뮬레이터 프로토콜/EMR API를 바꿀 일이 생기면 두 저장소를 같이 맞춰야 하니 먼저 여기에 적어 주세요. 라우터가 쓰는 계약 스냅샷은 `biomonitor_router/docs/contract/`(discovery.json, layout-sample.json, trips-sample.json)에 있습니다.

- [2026-09-17 22:50 MAC] 라우터 P1 완료(`biomonitor_router` 커밋 48a3480, Rust `router-server`). 에뮬레이터 v3 프레임을 그대로 받아 CRC·시퀀스 검사, NACK 재전송 요청, 패치별 저장(`router/store.py` 와 바이트 호환), 상태 API 를 제공합니다. 로컬 에뮬레이터 2000 환자로 검증했고(드롭 0, NACK/복구 동작), RP5 에뮬레이터 설정은 건드리지 않았습니다.
- [2026-09-17 22:50 MAC] 발견: `fastpath.py` 가 페이스마크(ch 10)를 **같은 패치·같은 seq 의 두 번째 레코드**(`pace_record`)로 보냅니다. 계약 문서는 "채널 블록 오름차순으로 한 레코드"라고 적혀 있어 처음엔 `patch_seq_dup` 로 잡혔습니다. 라우터는 이제 ECG 없는 같은 seq 레코드를 연속 레코드로 허용하니 **에뮬레이터를 고칠 필요는 없지만**, 언젠가 레코드 하나로 합치면 `describe()` 문구와 일치하게 됩니다. 합치기로 하면 여기에 먼저 적어 주세요(라우터 파서는 그대로 동작).
- [2026-09-17 22:50 MAC] 라우터가 `POST /api/v1/router/status` 로 5 s 마다 보고하고 `GET /api/v1/emr/admissions` 를 30 s 마다 읽습니다(환자 이름·병동·의료진). RP5 에뮬레이터가 맥 라우터로 보내려면 맥 방화벽에서 `router-server` 수신 허용이 필요합니다(사용자가 직접 설정). RP5 #2 배포(P4) 전까지는 RP5 에뮬레이터의 `transport.target_ip` 를 바꾸지 마세요.

## 4. RP5-1 ↔ RP5-2 전달 사항 (남겨야 할 것만; 실시간 대화는 채팅으로)

- (RP5 에이전트가 여기에 추가)
- [2026-09-17 08:50 RP5] RP5에 저장소 밖 관측 스택이 상주합니다: `/opt/biosim-monitor/`(collect·sink·read_capture·report·janitor, README 포함), systemd 유닛 `biosim-monitor`(지표 1초/60초 2단 수집), `biosim-receiver`(게이트웨이 TCP 싱크 127.0.0.1:9100, 시간별 캡처), `biosim-capture-janitor.timer`(200GB 상한), `biosim-monitor-check.timer`(일일 누수 판정). 로그 `/var/log/biosim-monitor/`, 캡처 `/var/lib/biosim-capture/`. **저장소 밖이라 배포로 갱신되지 않습니다** — 프로토콜을 바꾸면 이쪽도 같이 고쳐야 합니다(아래 v3 사고 참조).
- [2026-09-17 08:50 RP5] v3 배포(09-17 07:59) 직후 약 40분간 수신이 전부 끊겼습니다. 원인은 CRC-32 트레일러 4바이트를 소비하지 않아 매 프레임 스트림이 밀린 것: `bad` 135,237건, 재접속 141,818회, SAF에 1,526개 게이트웨이 적체, 캡처 정지. 같은 버그가 저장소 `tools/receiver.py`에도 있었고 2b367d5로 함께 고쳐졌습니다. 교훈: 프로토콜 변경 시 `verify.py`뿐 아니라 **모든 수신 구현**(저장소 밖 포함)을 같이 올려야 합니다.
- [2026-09-17 08:50 RP5] 누수 판정은 RSS 합계로 하면 안 됩니다. 워커 3개가 같은 로프 뱅크(~590MB)를 mmap해 RSS를 더하면 공유 페이지가 중복 계상됩니다(실측 RSS 1,206MB vs PSS 756MB, 차이 450MB). `smaps_rollup`의 PSS를 쓰고 MemAvailable로 교차 검증해야 합니다. 초기 버전이 이 때문에 LEAK을 오탐했습니다.

## 5. 공통 주의사항

- 저장소의 `data/emulator.db`(20000 프로필)와 `data/config.json`은 기준본입니다. 실행 중 바뀐 DB는 커밋하지 않습니다(19 MB씩 늘어남). 올려야 하면 `sqlite3 data/emulator.db "PRAGMA wal_checkpoint(TRUNCATE)"` 뒤에 커밋.
- 구조 설정(시드·병상 상한·프로필 수) 변경은 재구성이 필요하고 DB 이력이 새로 시작됩니다.
- 재구성·템플릿 재생성은 엔진 읽기/쓰기 잠금 아래에서만 실행됩니다. 공유메모리 배열을 잠금 없이 읽는 코드를 추가하지 마세요(과거 SIGSEGV 원인).
- 프로토콜(프레임·레코드·트레일러)을 바꾸면 저장소 안팎의 **모든 수신 구현**을 같은 배포로 올립니다: `verify.py`, `tools/receiver.py`, `router/`, RP5의 `/opt/biosim-monitor` 싱크. 그러지 않으면 수신이 통째로 끊깁니다(09-17 v3 사고).
- 메모리 누수 판정은 RSS 합계가 아니라 PSS(`smaps_rollup`)로 합니다. 워커들이 같은 루프 뱅크를 mmap해 RSS가 중복 계상됩니다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude ...` 트레일러를 유지합니다. Mac의 기본 git은 Xcode 라이선스 문제로 막힐 수 있어 `/Library/Developer/CommandLineTools/usr/bin/git`을 씁니다.

## 6. 변경 로그 (에이전트가 한 작업, 최신이 아래)

- [2026-09-17 09:20 MAC] 프로토콜 v3(CRC-32, NACK 재전송, 저장 CRC) 구현·검증·배포. RP5 패치 반영. 배포 시 장비 수정 자동 보존(MANIFEST).
- [2026-09-17 08:50 RP5] 09-06~09-14 상시 관측(8일). 재시작 0회, HTTP 100%, drop·send_err 0, 누적 2.25G 패킷 / 2.08TB. 메모리 누수 없음(PSS 757→837MB, 기울기 +0.1MB/h로 수렴, swap 0). 게이트웨이 2,190→2,390 증가.
- [2026-09-17 08:50 RP5] **서버 상태 변경**: 09-17 08:2x에 관측 데이터를 전량 리셋했습니다 — 캡처 198GB(20파일, v3 사고의 손상 구간 포함) 및 지표 CSV 447MB 삭제, 엔진 카운터 초기화 후 재기동. 보존: `emulator.db`(환자 2만 로스터·이력), 루프 뱅크, 지난주 최종 리포트 2건(`/var/log/biosim-monitor/archive/`). 이후 새 관측 진행 중.
- [2026-09-17 08:50 RP5] 배포본 20260917-0846에서 connected 수정 라이브 검증: 게이트웨이 행 2,390 / connected 2,190 / 9100 ESTAB 2,190 일치 (대기 중인 MCOT 게이트웨이 200개가 올바르게 제외됨). 수신단 `bad=0 crc_bad=0 ver=v3`, 4,906 frames/s 정상.
- [2026-09-17 10:05 MAC] meta.json 델타 직렬화 + 비동기 쓰기 + 워커 선택적 META 재발행. RP5 클론에 테스트 venv 생성. HANDOFF 답신.
- [2026-09-17 22:02 RP5] GUI '이동 중 환자' 카드의 가로 스크롤 제거(a14b495). 여덟 열이 전역 `white-space:nowrap`을 물려받고 타임테이블 열이 `min-width:320px`를 요구해 표가 카드 밖으로 밀려나던 문제. `table-layout:fixed` + 열 폭 13/11/13/12/10/10/20/11%로 카드 폭을 나눠 갖게 하고 셀 안에서 줄바꿈(`keep-all`)하도록 변경. **서버 상태 변경**: 릴리스 20260917-2201 빌드 후 `install.sh --update`로 배포, 서비스 재시작 → 전송 재시작 (connected 2,190 / ESTAB 2,190 / bad·crc_bad 0 / 4,929 frames/s 정상 확인).
- [2026-09-17 22:02 RP5] 위 변경은 **테스트를 돌리지 못했습니다**(RP5에 pytest 환경 없음, 2절 참조). CSS 전용이라 `tests`가 다루는 범위는 아니지만 규칙상 남깁니다. 헤드리스 크로미움이 키오스크 인스턴스와 충돌해 스크린샷 검증도 못 했으니, Mac에서 화면을 한 번 봐주시면 좋겠습니다.
- [2026-09-17 22:50 MAC] 라우터 P1(v3 ingest·게이트웨이 표·NACK·패치 저장소·상태 API·에뮬레이터 링크) 구현·검증. 에뮬레이터 코드 변경 없음. 로컬 테스트용 에뮬레이터 복제본은 별도 데이터 디렉터리(`--data-dir`)로 띄웠다가 종료.
- [2026-09-18 00:09 RP5-1] 에뮬레이터·라우터 채팅 채널 추가(1e631b7): `/ws/chat` 양방향 + `GET/POST /api/v1/chat`, GUI '채팅' 탭, `runtime/chat.jsonl` 영속. 릴리스 20260917-2343 배포(서버 재시작 포함).
- [2026-09-18 00:09 RP5-1] 송신 대상 변경: 192.168.0.56 → **192.168.0.209**:9100. `config.json`에 기록. 전환 시 SAF 72.7MB(게이트웨이 118개분)가 `drop_saf=0`으로 전량 재생 완료 — v3 재전송 경로 실부하 검증됨.
- [2026-09-18 00:09 RP5-1] Mac 철수에 맞춰 `CLAUDE.md` 재작성: 역할을 RP5-1/RP5-2로 정의(기존 규칙은 `dlake`가 아닌 장비를 전부 MAC으로 판별해, 2호기가 1호기에 배포를 시도할 수 있었음), 실시간 소통을 채팅 채널로 지정.
- [2026-09-18 00:09 RP5-1] 프로파일링(py-spy): 워커 CPU의 **36.9%가 `_read_ctrl`**(v3 NACK 드레인). 게이트웨이 2,390개 × 초당 5회 = **초당 10,950회 `recv()`**이며 대부분 EAGAIN 예외. 비용이 환자 수가 아니라 게이트웨이 수에 비례해, 환자를 2,000→500으로 줄여도 그대로입니다. selectors 로 읽기 가능한 소켓만 드레인하면 전체 CPU의 약 1/5 회수. 2위는 `flush` 21.3%, 3위 `reload_meta` 전량 JSON 파싱.
- [2026-09-18 00:41 RP5-1] **서버 상태 변경**: 환자 500→1000 을 위해 `control/rebuild` 실행. 병원이 `size_by_patients` 로 500명 기준(628병상)으로 지어져 있어 설정만 올려서는 628에서 막혔음(빈 병상 0 → `admit()` None). 재구축 후 병상 1,250 / 게이트웨이 1,365 / 재원 1,000. 재구축은 lifespan autostart 를 타지 않아 수동 start 필요했음 — rebuild 후에도 autostart 적용하는 것이 다음 할 일.
- [2026-09-18 00:48 RP5-1] **서버 상태 변경**: 병원 규모 방식을 `hospital.size_by_patients=false` + `general.bed_capacity=2000` 고정으로 전환하고 재구축. 병상 2,003 / 게이트웨이 1,972(고정 1,772 + mobile 200) / 재원 1,000. 이제 `active_patients` 는 재구축 없이 즉시 반영됨(2,000 이하). 대가: 게이트웨이가 항상 최대치라 CPU 가 환자 수와 무관하게 높음(`_read_ctrl` 비용은 게이트웨이 수 비례). config.json 에 영속.
- [2026-09-18 07:15 RP5-1] **서버 상태 변경(배포 2회)**: (a) 0e5ac47 시나리오 탭에 병상 수(bed_capacity) 입력과 도면 버튼(템플릿 재생성·내보내기·가져오기) 배치, 데이터 탭 병상 수 제거(id 중복 방지), `control/rebuild`·`emr/layout/reset` 뒤 autostart 적용 — 이제 재구축·재시작 모두 수 초 내 자동 전송. (b) 4eee7c5 링크 감시 수정: 이상 카운터를 바뀔 때마다 쓰던 것을 5분 창 증가분 요약으로, 보고 끊김은 45초 지속 시에만. 첫 버전이 system 120줄로 채팅을 도배했음(patch_seq_reorder 가 초당 수 회 증가). 3daf9fc(ws/chat 비객체 JSON) 도 함께 배포됨.
- [2026-09-18 07:15 RP5-1] 관측: `patch_seq_reorder`/`seq_restart`/`seq_missing` 대부분은 양쪽 재시작·재구축 churn(라우터도 이 시간대에 여러 번 재배포). rp5-2 설명(#146)으로는 patch_seq_reorder 는 엘리베이터/복도 게이트웨이 핸드오버에서 나는 정상 현상. 정상 운전 구간에서 재측정 필요.
- [2026-09-18 07:53 RP5-1] **서버 상태 변경(배포)**: 9f0aa75 trend 계수 캐시. CPU 피크 원인 = `_step_modulation` 이 10초마다 전 재원 환자의 `trend.state()` 를 한 스텝에서 호출(환자당 default_rng 7회 생성 ≈ 286 µs → 2,000명 ≈ 520 ms). 250 ms 표본에서 정확히 10.0 s 간격 ~725 ms 스파이크 확인. 시드→계수를 lru_cache(원본 대비 3,000명×8시각 전수 대조 불일치 0). 배포 후: world_step 최대 725→214 ms, 1초 CPU 최대 138→87%, p95 116→85%.
- [2026-09-18 07:53 RP5-1] 남은 최적화 후보(2,000명/1,972 GW 프로파일): ① 워커 `reload_meta` 전량 JSON 파싱 13.5% — meta.json 2.25 MB 가 2.7 s 마다 바뀌고 갱신당 4~8개 GW 만 변경인데 워커 3개가 매번 전량 파싱(30초당 74 MB). 델타 파일 소비로 전환 권장. ② 메인 `candidate_gateways` 8.8% — `_relink` 가 초당 400회 호출하며 전 GW 배열 연산; 결과는 room_idx 의 순수 함수라 방별 캐시 가능(재구축 시 무효화). ③ 워커 `flush` 13.6%/`build` 11% 는 실제 송신·프레임 조립이라 구조적.
- [2026-09-18 08:08 RP5-1] **서버 상태 변경(배포)**: f994dd5 (1) meta.json 델타 소비 — 쓰기는 마지막 전량 스냅샷(`_gen`) 이후 변경 항목만 `meta.delta.json`(`_base`,`_gen`, 누적)으로, 항목이 전체 1/4 넘거나 force/재구축이면 전량 복귀; 워커는 `_base` 일치 시 병합하고 v 바뀐 GW 만 META 재공지. 실측 델타 297 KB vs 전량 2.3 MB, 워커 `raw_decode` 13.5%→3.0%, 수신단 META 183/s 로 선택 재공지 유지. (2) `Hospital.candidate_gateways` 방별 캐시(인스턴스 수명 = 재구축 시 자동 무효화) — 메인 8.8%→0%. 종합(2,000명/1,972 GW): 1초 CPU 평균 70→54%, p95 116→65%, 최대 138→69%. 메인 프로세스 py-spy 표본 1,025→526. drop/send_err 0 유지.
