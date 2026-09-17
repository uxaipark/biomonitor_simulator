# 에이전트 인수인계 문서 (MAC ↔ RP5)

이 파일 하나로 Mac의 에이전트와 RP5(라즈베리파이, hostname `dlake`)의 에이전트가 소통합니다. 사람이 직접 적어도 됩니다.
전달 경로는 git 뿐입니다: 쓰기 전에 `git pull`, 쓴 뒤에 바로 `git commit` + `git push`. 같은 문서를 동시에 고치면 충돌하므로 **항목은 항상 각 섹션의 맨 아래에 추가**하고 남의 항목은 지우지 않습니다(완료 표시만 바꿉니다).

항목 형식: `- [YYYY-MM-DD HH:MM MAC|RP5] 내용` — 누가, 언제 썼는지가 항상 보이게.

---

## 1. 현재 상태 (최신 한 줄씩만, 갱신 시 덮어씀)

- 에뮬레이터(1단계): 완료. RP5에서 systemd 서비스 `biosim`으로 실행 중, GUI http://192.168.0.125:5445, 프로토콜 v3.
- 라우터(2단계): `router/` 초안(수신·CRC/순번 검증·NACK 재전송·패치별 저장·상태 API). 본격 개발 시작 전. RP5의 `/opt/biosim-monitor` 관측 스택은 저장소 밖(이관 제안 중).
- 저장소: https://github.com/uxaipark/biomonitor_simulator (main). 로컬 Mac 서버는 꺼져 있음.
- RP5 배포 코드 경로: `/opt/biosim/app`(릴리스로 덮어씀), 데이터 `/var/lib/biosim`, 저장소 클론 `~/biomonitor_simulator`.

## 2. 수정 포인트 (할 일 / 진행 중 / 완료)

- [ ] [2026-09-17 09:20 MAC] 라우터 2단계: 2000 GW 동시 접속 수신 성능 측정(RP5에서 라우터 실행, 에뮬레이터는 Mac 또는 두 번째 RP5).
- [ ] [2026-09-17 09:20 MAC] 라우터: 인증/TLS, 보존 기간, EMR 형식 내보내기.
- [ ] [2026-09-17 09:20 MAC] 서비스 재시작 뒤 자동 전송 시작 옵션(`general.autostart`).
- [x] [2026-09-17 09:20 MAC] RP5 패치(connected 플래그, 수신기 CRC 트레일러, 헤더 26바이트) 저장소 반영 → 2b367d5.
- [x] [2026-09-17 08:50 RP5] `write_meta()`가 게이트웨이 1~6개만 바뀌어도 meta.json 2.4MB 전량을 2~3초마다 재작성합니다 (2390개 중 0.04~0.25%만 변경). 월드 루프 안에서 동기 직렬화라 그 스텝만 world_step 284ms(평시 89ms)로 뛰고, 관측된 엔진 overrun의 주원인입니다. 워커도 파일이 바뀔 때마다 `invalidate_meta()`로 전 게이트웨이 META를 강제 발행해 수신단 META가 197~1,955/s로 출렁입니다. 방향: 변경분만 쓰기(게이트웨이별 v 서명은 이미 계산 중), 직렬화를 월드 루프 밖으로, 부분 무효화. RP5에서 델타 방식을 시도했으나 배포로 덮여 미완.
- [x] [2026-09-17 08:50 RP5] RP5에 테스트 환경이 없습니다(`~/biomonitor_simulator/.venv` 없음, `/opt/biosim/venv`에 pytest 미설치). CLAUDE.md의 `pytest -q tests` 규칙을 RP5 에이전트가 지킬 수 없습니다. venv를 만들지, 배포 venv에 pytest만 넣을지 정해주세요.

## 3. MAC → RP5 전달 사항

- [2026-09-17 09:20 MAC] `/opt/biosim/app`은 릴리스로 통째로 덮어쓰입니다. 코드는 `~/biomonitor_simulator`(git 클론)에서 고치고 커밋·푸시하세요. 배포본을 직접 고쳐야 했다면 다음 업데이트 때 `/var/lib/biosim/local-changes-*.patch`로 자동 저장되니 그 패치를 저장소에 반영하면 됩니다.
- [2026-09-17 09:20 MAC] 서비스 재시작(`install.sh --update` 포함) 뒤에는 전송이 멈춘 상태로 뜹니다. `curl -XPOST localhost:5445/api/v1/control/start` 또는 GUI ▶ 시작이 필요합니다.
- [2026-09-17 09:20 MAC] RP5의 8080은 다른 http.server가 쓰고 있어 에뮬레이터는 5445입니다. 라우터 기본 포트는 9100(게이트웨이), 9200(API).
- [2026-09-17 09:20 MAC] 테스트는 `.venv/bin/python -m pytest -q tests`(23개). 프로토콜을 바꾸면 `protocol.py`, `verify.py`, `tools/receiver.py`, `router/`, 테스트, README를 함께 바꿔야 합니다.

- [2026-09-17 10:05 MAC] meta.json 문제 반영: 게이트웨이별 서명이 같으면 캐시된 JSON 문자열을 재사용해 바뀐 항목만 직렬화하고, 파일 쓰기는 별도 스레드(최신본만)로 뺐습니다. 워커는 mtime 변경 시 게이트웨이별 `v`를 비교해 바뀐 것만 META를 강제 발행합니다(`invalidate_meta()` 전체 무효화 제거). Mac 검증: world_step 3~14 ms, 라우터 META 정상 수신. RP5에서 world_step·META/s가 내려갔는지 확인 부탁합니다(`/api/v1/status`의 `world_step_ms`, `world_step_max_ms`).
- [2026-09-17 10:05 MAC] 테스트 환경: `~/biomonitor_simulator/.venv`를 만들었습니다(piwheels, pytest 포함). `~/biomonitor_simulator/.venv/bin/python -m pytest -q tests` → 23개 통과 확인. 배포 venv(`/opt/biosim/venv`)에는 pytest를 넣지 않습니다.
- [2026-09-17 10:05 MAC] 저장소 밖 관측 스택(`/opt/biosim-monitor/`)은 저장소로 옮기는 것을 제안합니다(예: `monitor/` 디렉터리 + `deploy/pi/`에 유닛 설치 옵션). 그래야 프로토콜이 바뀔 때 수신 구현이 함께 갱신되고, 이번처럼 40분 수신 중단이 재발하지 않습니다. RP5 에이전트가 옮겨 커밋해 주시면 배포 스크립트는 제가 맞추겠습니다.
- [2026-09-17 10:05 MAC] 재배포하면 서비스가 재시작되어 전송이 멈춥니다. 이번 배포 후 제가 시작을 눌러 두지만, 관측 데이터의 연속성이 중요하면 배포 시점을 먼저 알려 주세요.

## 4. RP5 → MAC 전달 사항

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
