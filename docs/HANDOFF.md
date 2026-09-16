# 에이전트 인수인계 문서 (MAC ↔ RP5)

이 파일 하나로 Mac의 에이전트와 RP5(라즈베리파이, hostname `dlake`)의 에이전트가 소통합니다. 사람이 직접 적어도 됩니다.
전달 경로는 git 뿐입니다: 쓰기 전에 `git pull`, 쓴 뒤에 바로 `git commit` + `git push`. 같은 문서를 동시에 고치면 충돌하므로 **항목은 항상 각 섹션의 맨 아래에 추가**하고 남의 항목은 지우지 않습니다(완료 표시만 바꿉니다).

항목 형식: `- [YYYY-MM-DD HH:MM MAC|RP5] 내용` — 누가, 언제 썼는지가 항상 보이게.

---

## 1. 현재 상태 (최신 한 줄씩만, 갱신 시 덮어씀)

- 에뮬레이터(1단계): 완료. RP5에서 systemd 서비스 `biosim`으로 실행 중, GUI http://192.168.0.125:5445, 프로토콜 v3.
- 라우터(2단계): `router/` 초안(수신·CRC/순번 검증·NACK 재전송·패치별 저장·상태 API). 본격 개발 시작 전.
- 저장소: https://github.com/uxaipark/biomonitor_simulator (main). 로컬 Mac 서버는 꺼져 있음.
- RP5 배포 코드 경로: `/opt/biosim/app`(릴리스로 덮어씀), 데이터 `/var/lib/biosim`, 저장소 클론 `~/biomonitor_simulator`.

## 2. 수정 포인트 (할 일 / 진행 중 / 완료)

- [ ] [2026-09-17 09:20 MAC] 라우터 2단계: 2000 GW 동시 접속 수신 성능 측정(RP5에서 라우터 실행, 에뮬레이터는 Mac 또는 두 번째 RP5).
- [ ] [2026-09-17 09:20 MAC] 라우터: 인증/TLS, 보존 기간, EMR 형식 내보내기.
- [ ] [2026-09-17 09:20 MAC] 서비스 재시작 뒤 자동 전송 시작 옵션(`general.autostart`).
- [x] [2026-09-17 09:20 MAC] RP5 패치(connected 플래그, 수신기 CRC 트레일러, 헤더 26바이트) 저장소 반영 → 2b367d5.

## 3. MAC → RP5 전달 사항

- [2026-09-17 09:20 MAC] `/opt/biosim/app`은 릴리스로 통째로 덮어쓰입니다. 코드는 `~/biomonitor_simulator`(git 클론)에서 고치고 커밋·푸시하세요. 배포본을 직접 고쳐야 했다면 다음 업데이트 때 `/var/lib/biosim/local-changes-*.patch`로 자동 저장되니 그 패치를 저장소에 반영하면 됩니다.
- [2026-09-17 09:20 MAC] 서비스 재시작(`install.sh --update` 포함) 뒤에는 전송이 멈춘 상태로 뜹니다. `curl -XPOST localhost:5445/api/v1/control/start` 또는 GUI ▶ 시작이 필요합니다.
- [2026-09-17 09:20 MAC] RP5의 8080은 다른 http.server가 쓰고 있어 에뮬레이터는 5445입니다. 라우터 기본 포트는 9100(게이트웨이), 9200(API).
- [2026-09-17 09:20 MAC] 테스트는 `.venv/bin/python -m pytest -q tests`(23개). 프로토콜을 바꾸면 `protocol.py`, `verify.py`, `tools/receiver.py`, `router/`, 테스트, README를 함께 바꿔야 합니다.

## 4. RP5 → MAC 전달 사항

- (RP5 에이전트가 여기에 추가)

## 5. 공통 주의사항

- 저장소의 `data/emulator.db`(20000 프로필)와 `data/config.json`은 기준본입니다. 실행 중 바뀐 DB는 커밋하지 않습니다(19 MB씩 늘어남). 올려야 하면 `sqlite3 data/emulator.db "PRAGMA wal_checkpoint(TRUNCATE)"` 뒤에 커밋.
- 구조 설정(시드·병상 상한·프로필 수) 변경은 재구성이 필요하고 DB 이력이 새로 시작됩니다.
- 재구성·템플릿 재생성은 엔진 읽기/쓰기 잠금 아래에서만 실행됩니다. 공유메모리 배열을 잠금 없이 읽는 코드를 추가하지 마세요(과거 SIGSEGV 원인).
- 커밋 메시지 끝에 `Co-Authored-By: Claude ...` 트레일러를 유지합니다. Mac의 기본 git은 Xcode 라이선스 문제로 막힐 수 있어 `/Library/Developer/CommandLineTools/usr/bin/git`을 씁니다.

## 6. 변경 로그 (에이전트가 한 작업, 최신이 아래)

- [2026-09-17 09:20 MAC] 프로토콜 v3(CRC-32, NACK 재전송, 저장 CRC) 구현·검증·배포. RP5 패치 반영. 배포 시 장비 수정 자동 보존(MANIFEST).
