#!/usr/bin/env bash
# Bio-Signal Emulator 실행 스크립트.
#
#   sudo biosim-start              # 에뮬레이터 기동 후 전송이 시작될 때까지 대기
#   sudo biosim-start --all        # 모니터링(수집·정리·일일점검)도 함께 기동
#   sudo biosim-start --no-wait    # 기동만 하고 바로 반환
#
# 기동 순서: 고아 공유메모리 정리(유닛의 ExecStartPre) -> 서비스 -> 엔진 응답 -> 회선 접속 안정.
# general.autostart 가 참이면 엔진이 스스로 전송을 시작하고, 아니면 이 스크립트가 /control/start 를 호출한다.
set -uo pipefail
PORT="${BIOSIM_PORT:-5445}"; API="http://127.0.0.1:$PORT/api/v1"
ALL=0; WAIT=1; TIMEOUT="${BIOSIM_WAIT_S:-240}"
for a in "$@"; do case "$a" in
  --all) ALL=1 ;; --no-wait) WAIT=0 ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  *) echo "unknown option: $a" >&2; exit 2 ;;
esac; done
[ "$(id -u)" = 0 ] || { echo "sudo 로 실행하세요: sudo $0 $*" >&2; exit 1; }

say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
get() { curl -fsS --max-time 5 "$API/status" 2>/dev/null; }

if systemctl is-active --quiet biosim; then
  say "biosim 이미 실행 중 (since $(systemctl show biosim -p ActiveEnterTimestamp --value))"
else
  say "biosim 기동..."
  systemctl start biosim || { say "기동 실패"; journalctl -u biosim -n 20 --no-pager; exit 1; }
fi

if [ "$ALL" = 1 ]; then
  for u in biosim-monitor.service biosim-capture-janitor.timer biosim-monitor-check.timer; do
    systemctl start "$u" 2>/dev/null && say "$u 기동"
  done
fi

[ "$WAIT" = 1 ] || { say "대기 없이 반환"; exit 0; }

say "엔진 응답 대기 (최대 ${TIMEOUT}s)..."
end=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$end" ]; do
  get >/dev/null && break
  sleep 2
done
get >/dev/null || { say "엔진이 응답하지 않습니다"; journalctl -u biosim -n 20 --no-pager; exit 1; }

running=$(get | python3 -c 'import sys,json; print(json.load(sys.stdin)["running"])' 2>/dev/null)
if [ "$running" != "True" ]; then
  say "엔진 정지 상태 -> /control/start 호출"
  curl -fsS --max-time 10 -X POST "$API/control/start" >/dev/null || say "  start 호출 실패"
fi

say "회선 접속 대기..."
TARGET=$(get | python3 -c 'import sys,json; t=json.load(sys.stdin)["target"]; print(t["ip"], t["port"])' 2>/dev/null)
prev=-1; stable=0; zero=0
while [ "$(date +%s)" -lt "$end" ]; do
  now=$(get | python3 -c 'import sys,json; print(json.load(sys.stdin)["last"].get("total",{}).get("connected",0))' 2>/dev/null || echo 0)
  if [ "$now" -le 0 ]; then                       # 대상이 죽어 있으면 만료까지 기다릴 이유가 없다
    zero=$(( zero + 1 ))
    if [ "$zero" -ge 7 ]; then
      say "경고: 회선이 하나도 붙지 않습니다 (송신 대상 ${TARGET:-?})"
      # shellcheck disable=SC2086
      set -- $TARGET
      if [ -n "${1:-}" ] && ! timeout 3 bash -c "cat < /dev/null > /dev/tcp/$1/$2" 2>/dev/null; then
        say "  $1:$2 에 접속할 수 없습니다 - 라우터 쪽을 확인하세요 (에뮬레이터는 계속 생성·재시도합니다)"
      fi
      break
    fi
  else
    zero=0
  fi
  # 핸드오버 때문에 연결 수는 늘 조금씩 흔들린다.  증가가 멎었는지만 본다 (변동 3 이하가 두 번 연속).
  d=$(( now - prev )); [ "$d" -lt 0 ] && d=$(( -d ))
  if [ "$now" -gt 0 ] && [ "$prev" -ge 0 ] && [ "$d" -le 3 ]; then stable=$(( stable + 1 )); else stable=0; fi
  [ "$stable" -ge 2 ] && break
  prev="$now"; sleep 3
done

get | python3 -c '
import sys, json
d = json.load(sys.stdin); last = d["last"]; t = last.get("total", {})
run, up = d["running"], d["uptime_s"]
inp, outp, beds = d["inpatients"], d["outpatients"], d["bed_capacity"]
gw, conn = d["gateways"], t.get("connected", 0)
ip, port = d["target"]["ip"], d["target"]["port"]
pkts, byts = last.get("pkts_ps", 0), last.get("bytes_ps", 0)
err, drop, ovr = t.get("send_err", 0), t.get("drop_backlog", 0), t.get("overruns", 0)
print(f"  엔진       running={run}  uptime={up:.0f}s")
print(f"  환자       입원 {inp} · 원외 {outp} / 병상 {beds}")
print(f"  게이트웨이 {gw}  연결 {conn}")
print(f"  전송       {ip}:{port}  {pkts:.0f} pkt/s  {byts/1e6:.2f} MB/s")
print(f"  오류       send_err={err} drop={drop} overruns={ovr}")
if run and conn == 0:
    print(f"  ! 연결된 게이트웨이가 없습니다.  {ip}:{port} 의 수신 서버를 확인하세요.")
'
say "GUI: http://$(hostname -I | awk '{print $1}'):$PORT"
