# 에이전트 공통 지침 (RP5-1 에뮬레이터 · RP5-2 라우터)

## 역할

`hostname`과 IP로 자신이 어느 쪽인지 안다. **Mac은 2026-09-18부로 빠졌다.**

| 역할 | 장비 | 담당 |
|---|---|---|
| **RP5-1** | `dlake` / 192.168.0.125 | 에뮬레이터(`biosim`), 관측 스택 |
| **RP5-2** | 192.168.0.14 | 라우터(수신 9100, 상태 API 7300 `GET /api/stats`) |

`hostname`이 `dlake`가 아니면 RP5-2다. 어느 쪽도 아닌 장비에서는 **먼저 사람에게 확인하고**,
특히 `deploy/pi/update.sh`로 다른 장비에 배포하지 않는다.

## 소통

- **실시간 소통은 채팅 채널로 한다.** 에뮬레이터가 허브다.
  - WebSocket `ws://192.168.0.125:5445/ws/chat?sender=<rp5-1|rp5-2>&since=<마지막seq>`
  - REST `POST /api/v1/chat {"from","text"}` · `GET /api/v1/chat?since=<seq>`
  - 메시지 `{seq, t(epoch초), from, kind(msg|system), text}`. `seq`가 이어받기 기준이며
    재접속 시 마지막 `seq`를 `since`에 주면 끊긴 구간만 받는다. `runtime/chat.jsonl`에 영속된다.
  - 세션을 시작하면 채팅 백로그를 먼저 읽는다. 상대에게 물어볼 것이 있으면 여기에 쓴다.
- **`docs/HANDOFF.md`는 기록용으로 남긴다.** 서버 상태를 바꾸는 작업(재시작, 재구성, 설정 초기화,
  배포)과 넘겨야 할 결정은 여기에 적고 커밋·푸시한다. 형식 `- [YYYY-MM-DD HH:MM RP5-1|RP5-2] ...`,
  각 섹션 맨 아래에 추가하고 남의 항목은 지우지 않는다.
  채팅은 흘러가고 HANDOFF는 남는다 — 나중에 근거로 삼아야 할 것은 HANDOFF에 적는다.

## 코드

- RP5-1에서는 `/opt/biosim/app`(배포본)을 직접 고치지 말고 `~/biomonitor_simulator`(git 클론)에서
  고쳐 커밋한다. 배포는 클론에서 `deploy/pi/build_release.sh` 후
  `sudo BIOSIM_PORT=5445 <release>/install.sh --update`.
  **`BIOSIM_PORT=5445`를 빠뜨리면 저장소 기본값이 아니라 8080으로 뜰 수 있는지 확인할 것**
  (8080은 별도 키오스크가 상시 사용 중이라 절대 뺏으면 안 된다).
- 배포 뒤에는 전송이 멈춘 상태로 뜬다. `curl -XPOST localhost:5445/api/v1/control/start` 필요.
- 변경 후 `pytest -q tests`를 돌린다. RP5-1에는 아직 테스트 환경이 없다(HANDOFF 2절 참조).
- 프로토콜을 바꾸면 `emulator/runtime/protocol.py` · `verify.py` · `tools/receiver.py` · `router/` ·
  `tests` · README를 **함께** 맞춘다. 저장소 밖 수신 구현(`/opt/biosim-monitor/sink.py`)도 같이 고친다 —
  v3 전환 때 이걸 빠뜨려 40분간 수신이 끊긴 적이 있다.
- 커밋 메시지 끝: `Co-Authored-By: Claude <model> <noreply@anthropic.com>` 트레일러 유지.
