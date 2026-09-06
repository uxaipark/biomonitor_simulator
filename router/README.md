# 라우터 서버 (2단계, 초안)

에뮬레이터의 게이트웨이 TCP 스트림(프로토콜 v1)을 받아 검증하고, 패치별 파일로 저장하는 서버입니다. 에뮬레이터와 같은 `StreamChecker`를 써서 손상 바이트 재동기화, seq 누락·중복·역전, 타임스탬프 역행, 알 수 없는 채널/자료형을 계수합니다.

```bash
.venv/bin/python -m router --port 9100 --api-port 9200 --data data/router --emulator-url http://localhost:5445
```

* 게이트웨이 소켓: `--port`(기본 9100, 에뮬레이터 `transport.target_port`). 게이트웨이당 소켓 모드와 공유 소켓 모드 모두 헤더의 gw_id로 구분합니다. 백로그 4096, 파일 한도는 `ulimit -n 65535`와 `deploy/pi/99-biosim.conf` sysctl을 같이 적용하세요.
* 저장: `data/router/patches/<patch_id 8자리>/<YYYYMMDD-HH>.rec`(시간별 append), `index.json`(첫/마지막 시각, 레코드·바이트 수, 파일 목록), `data/router/meta/gw_<gw_id>.json`(게이트웨이의 마지막 META). 레코드 파일 형식은 `router/store.py` 상단에, 읽기는 `iter_entries()`로 합니다. 쓰기는 패치별로 1초 버퍼링 후 한 번에 기록하고 열린 파일은 LRU 256개로 제한합니다.
* API(`--api-port`): `/status`(연결·게이트웨이·패치 수, 초당 프레임/레코드/바이트, 이상 카운터), `/gateways`, `/patches`, `/patches/{id}`, `/events`, `/anomalies`(연결별 카운터).
* 에뮬레이터 보고: `--emulator-url`을 주면 5초마다 `POST /api/v1/router/status`로 같은 상태를 보냅니다. 에뮬레이터 GUI 전송 탭에서 보입니다.
* 탐지: 같은 gw_id가 다른 소켓에서 동시에 오면 `dup_gw_frames`와 이벤트, 소켓은 살아 있는데 10초간 프레임이 없으면 `silent` 이벤트. 저장 후 전송 재전송은 seq가 이어지므로 정상 프레임으로 받고, 오래된 seq는 `seq_reorder`로 계수합니다.

검증: `tests/test_router.py`(소켓으로 프레임 전송 → 패치 파일·게이트웨이 표·META·seq 갭·중복 gw 탐지). 에뮬레이터를 `target_ip=127.0.0.1, target_port=9100`으로 두고 시작하면 실제 부하로 확인할 수 있습니다.

아직 없는 것: 인증/TLS, 디스크 보존 기간과 압축, 패치별 파일을 EMR 형식으로 내보내기, 다중 프로세스 수신(현재 asyncio 단일 프로세스, Pi 4에서 게이트웨이 2000대 기준 수신 부하는 테스트 필요).
