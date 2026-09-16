# 에이전트 공통 지침 (Mac · RP5)

- 세션을 시작하면 먼저 `docs/HANDOFF.md`를 읽는다. 작업을 끝내면 그 문서의 해당 섹션 맨 아래에 항목을 추가하고(형식 `- [YYYY-MM-DD HH:MM MAC|RP5] ...`) 커밋·푸시한다. 남의 항목은 지우지 않는다.
- 자신이 어느 쪽인지는 `hostname`으로 안다: `dlake` = RP5, 그 외 = MAC.
- RP5에서는 `/opt/biosim/app`(배포본)을 직접 고치지 말고 `~/biomonitor_simulator`(git 클론)에서 고쳐 커밋한다. 배포는 Mac에서 `deploy/pi/update.sh master@192.168.0.125` 또는 RP5에서 릴리스 tarball로 `sudo install.sh --update`.
- 변경 후 `.venv/bin/python -m pytest -q tests`를 돌린다. 프로토콜 변경은 emulator/runtime/protocol.py · verify.py · tools/receiver.py · router/ · tests · README를 함께 맞춘다.
- 서버 상태를 바꾸는 작업(재시작, 재구성, 설정 초기화)은 HANDOFF.md에 남긴다.
- 커밋 메시지 끝: `Co-Authored-By: Claude <model> <noreply@anthropic.com>` 트레일러 유지.
