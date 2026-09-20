#!/usr/bin/env bash
# Bio-Signal Emulator 종료 스크립트.
#
#   sudo ./server-stop.sh               # 전송 정지 -> 서비스 종료 -> 잔여물 확인
#   sudo ./server-stop.sh --all         # 모니터링(수집·타이머)도 함께 종료
#   sudo ./server-stop.sh --quiet       # 라우터에 채팅 공지를 보내지 않음
#
# 순서가 중요하다: 먼저 /control/stop 으로 전송을 멈춰 라우터가 정상 종료로 인식하게 하고, 그 다음 서비스를
# 종료한다.  곧바로 SIGKILL 되면 resource_tracker 가 /dev/shm/psm_* 를 정리하지 못해 세그먼트가 남는다
# (2026-09-17·18 에 실제로 12개가 남았다).  종료 후 남은 세그먼트를 확인해 알려준다.
set -uo pipefail
PORT="${BIOSIM_PORT:-5445}"; API="http://127.0.0.1:$PORT/api/v1"
ALL=0; QUIET=0
for a in "$@"; do case "$a" in
  --all) ALL=1 ;; --quiet) QUIET=1 ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
  *) echo "unknown option: $a" >&2; exit 2 ;;
esac; done
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요: sudo $0 $*" >&2; exit 1; }

say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }

if ! systemctl is-active --quiet biosim; then
  say "biosim 이미 정지 상태"
else
  if curl -fsS --max-time 3 "$API/status" >/dev/null 2>&1; then
    [ "$QUIET" = 1 ] || curl -fsS --max-time 3 -X POST "$API/chat" -H 'Content-Type: application/json' \
      -d '{"from":"RP5-1","text":"[RP5-1] 에뮬레이터를 종료합니다. 전 회선이 끊깁니다."}' >/dev/null 2>&1
    say "전송 정지 (/control/stop)"
    curl -fsS --max-time 10 -X POST "$API/control/stop" >/dev/null 2>&1 || say "  stop 호출 실패 (그대로 진행)"
    sleep 2
  fi
  say "서비스 종료..."
  systemctl stop biosim
  say "종료됨 (exit: $(systemctl show biosim -p Result --value))"
fi

if [ "$ALL" = 1 ]; then
  for u in biosim-monitor.service biosim-capture-janitor.timer biosim-monitor-check.timer; do
    systemctl stop "$u" 2>/dev/null && say "$u 정지"
  done
fi

left=$(pgrep -f '/opt/biosim/app/run.py|/opt/biosim/venv/bin/python run.py' | wc -l)
[ "$left" -gt 0 ] && say "경고: 남은 프로세스 $left 개 -> $(pgrep -af '/opt/biosim' | head -3)"

orphan=0
for f in /dev/shm/psm_*; do
  [ -e "$f" ] || continue
  grep -qlF "$(basename "$f")" /proc/*/maps 2>/dev/null || orphan=$((orphan+1))
done
if [ "$orphan" -gt 0 ]; then
  say "고아 공유메모리 $orphan 개 남음 -> 다음 기동의 ExecStartPre(shm-sweep.sh)가 정리합니다"
  say "  지금 지우려면: /opt/biosim/app/deploy/pi/shm-sweep.sh"
else
  say "고아 공유메모리 없음"
fi
if [ -x /usr/local/bin/biosim-start ]; then say "완료.  다시 시작: sudo biosim-start"; else say "완료.  다시 시작: sudo $(dirname "$(readlink -f "$0")")/server-start.sh"; fi
