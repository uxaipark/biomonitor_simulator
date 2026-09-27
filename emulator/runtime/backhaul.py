"""보안 백홀: 라우터가 같은 LAN 에 없을 때 송출 경로에 SSH 터널을 끼워 넣고 그 상태를 감시한다.

세 가지 모드 (transport.backhaul.mode):

  direct   지금까지와 같음 — 워커가 target_ip:target_port 로 직접 접속.
  ssh_in   회사(원격) 쪽에서 이 장비로 SSH 로 들어와 역방향 포워딩을 걸어 둔 경우.
             회사 PC:  ssh -N -R 127.0.0.1:<local_port>:<회사라우터IP>:9100 <계정>@<이 장비 공인주소>
           sshd 가 127.0.0.1:<local_port> 에 리스너를 열고, 워커는 거기로 보낸다.  에뮬레이터는 프로세스를 띄우지 않고
           리스너 유무(/proc/net/tcp)만 본다 — 리스너가 사라지면 터널이 끊긴 것이다.
  ssh_out  에뮬레이터가 회사 점프호스트로 ssh -N -L 을 열고(아웃바운드) 감독한다.  끊기면 지수 백오프로 다시 띄운다.
             ssh -N -L 127.0.0.1:<local_port>:<remote_target> <user>@<host>

터널 모드에서는 워커의 실제 접속 대상이 127.0.0.1:<local_port> 가 된다(effective_target).  target_ip 는 '진짜' 라우터 주소로
남아 ssh_out 의 remote_target 기본값과 화면 표시에 쓰인다.  터널이 끊긴 동안은 기존 저장 후 전송(SAF)이 프레임을 보관하고
복구 뒤 재전송하므로, 여기서는 단절 횟수·누적 시간·복구 뒤 버퍼 소진 시간(recover_s)을 재서 실험 지표로 남긴다.

터널로 갈 때는 socket_mode=shared(워커당 연결 1개)를 권한다 — 게이트웨이마다 SSH 채널을 여는 것(2,000개)보다 훨씬 가볍다.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time

MODES = ("direct", "ssh_in", "ssh_out")
LOCALHOST = "127.0.0.1"


def effective_target(t: dict) -> tuple[str, int, bool]:
    """워커가 실제로 접속할 (ip, port, generate_only).  터널 모드면 로컬 진입점, 아니면 target_ip 그대로."""
    bh = t.get("backhaul") or {}
    mode = bh.get("mode", "direct")
    if mode in ("ssh_in", "ssh_out"):
        return LOCALHOST, int(bh.get("local_port") or t.get("target_port") or 9100), False
    ip = (t.get("target_ip") or "").strip()
    return ip, int(t.get("target_port") or 9100), not ip


def listener_present(port: int, proc_net: str = "/proc/net") -> bool:
    """127.0.0.1(또는 0.0.0.0/::)의 port 에 LISTEN 소켓이 있는가 — sshd 의 -R 리스너, 또는 우리가 띄운 ssh -L 의 리스너."""
    want = f":{port:04X}"
    for fn in ("tcp", "tcp6"):
        try:
            with open(os.path.join(proc_net, fn)) as f:
                next(f)
                for line in f:
                    parts = line.split()
                    if len(parts) > 3 and parts[3] == "0A" and parts[1].endswith(want):
                        return True
        except (OSError, StopIteration):
            continue
    return False


def ssh_command(bh: dict, t: dict, known_hosts: str = "") -> list[str]:
    """ssh_out 모드의 ssh 명령줄.  BatchMode(비밀번호 프롬프트 금지)·keepalive·ExitOnForwardFailure 로 감독 가능하게."""
    s = bh.get("ssh") or {}
    remote = (s.get("remote_target") or "").strip() or f'{t.get("target_ip") or ""}:{t.get("target_port") or 9100}'
    keep = int(bh.get("keepalive_s") or 10)
    cmd = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", f"ServerAliveInterval={keep}", "-o", "ServerAliveCountMax=3",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "TCPKeepAlive=yes"]
    if known_hosts:
        cmd += ["-o", f"UserKnownHostsFile={known_hosts}"]
    if s.get("key_path"):
        cmd += ["-i", str(s["key_path"])]
    if s.get("port"):
        cmd += ["-p", str(int(s["port"]))]
    if s.get("extra_args"):
        cmd += shlex.split(str(s["extra_args"]))
    cmd += ["-L", f'{LOCALHOST}:{int(bh.get("local_port") or 9100)}:{remote}']
    host = (s.get("host") or "").strip()
    user = (s.get("user") or "").strip()
    cmd.append(f"{user}@{host}" if user else host)
    return cmd


def inbound_hint(bh: dict, t: dict, login: str = "<계정>", public: str = "<이 장비의 공인주소>") -> str:
    """ssh_in 모드에서 회사 쪽이 실행할 명령 (화면·API 안내용)."""
    port = int(bh.get("local_port") or 9100)
    router = (t.get("target_ip") or "<회사라우터IP>")
    return f"ssh -N -o ServerAliveInterval={int(bh.get('keepalive_s') or 10)} -o ExitOnForwardFailure=yes -R {LOCALHOST}:{port}:{router}:{int(t.get('target_port') or 9100)} {login}@{public}"


class Backhaul:
    """터널 감시·감독 스레드.  engine 이 하나 갖고, stats()['backhaul'] 로 상태를 내보낸다."""

    POLL_S = 2.0

    def __init__(self, cfg, log, state_dir: str = "", stats_fn=None, on_event=None):
        self.cfg, self.log = cfg, log
        self.state_dir = state_dir
        self.stats_fn = stats_fn                   # engine.stats(history=False) — 복구 뒤 SAF 소진 시간 측정용
        self.on_event = on_event                   # (level, msg) → 채팅 등 바깥 알림
        self.mode = "direct"
        self.local_port = 9100
        self.up = False
        self.since = 0.0                           # 현재 상태(up/down)가 시작된 시각
        self.downs = 0                             # 단절 횟수 (up→down 전이)
        self.down_total_s = 0.0
        self.last_down_s = 0.0
        self.recover_s: float | None = None        # 마지막 복구 뒤 SAF 버퍼가 다 비는 데 걸린 시간
        self._recover_t0: float | None = None
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.last_error = ""
        self._cmd: list[str] = []
        self._backoff = 3.0
        self._next_spawn = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="backhaul")
        self._thread.start()

    # ---------------------------------------------------------------- public
    def status(self) -> dict:
        now = time.time()
        t = self.cfg.snapshot()["transport"]
        bh = t.get("backhaul") or {}
        out = {"mode": self.mode, "local_port": self.local_port, "up": self.up if self.mode != "direct" else None,
               "since_s": round(now - self.since, 1) if self.since else 0.0, "downs": self.downs, "down_total_s": round(self.down_total_s, 1),
               "last_down_s": round(self.last_down_s, 1), "recover_s": None if self.recover_s is None else round(self.recover_s, 1),
               "restarts": self.restarts, "last_error": self.last_error, "ssh_pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
               "effective_target": "%s:%d" % effective_target(t)[:2] if effective_target(t)[0] else "",
               "inbound_hint": inbound_hint(bh, t) if self.mode == "ssh_in" else "",
               "command": " ".join(shlex.quote(c) for c in self._cmd) if self.mode == "ssh_out" and self._cmd else ""}
        return out

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    # ---------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick(time.time())
            except Exception as e:                                  # 감시 스레드는 죽지 않는다
                self.last_error = f"{type(e).__name__}: {e}"
            self._stop.wait(self.POLL_S)

    def _tick(self, now: float) -> None:
        t = self.cfg.snapshot()["transport"]
        bh = t.get("backhaul") or {}
        mode = bh.get("mode", "direct") if bh.get("mode") in MODES else "direct"
        port = int(bh.get("local_port") or 9100)
        if mode != self.mode or port != self.local_port:
            self._switch(mode, port, now)
        if mode == "ssh_out":
            self._supervise(bh, t, now)
        if mode == "direct":
            return
        up = listener_present(port) and (mode != "ssh_out" or (self.proc is not None and self.proc.poll() is None))
        if up != self.up:
            self._transition(up, now)
        if self.up and self._recover_t0 is not None and self.stats_fn is not None:
            try:
                tot = ((self.stats_fn().get("last") or {}).get("total") or {})
                if not int(tot.get("saf_gateways", 0) or 0):
                    self.recover_s = now - self._recover_t0
                    self._recover_t0 = None
                    self._event("info", f"백홀 복구 완료 — 저장분 재전송 소진까지 {self.recover_s:.0f}초")
            except Exception:
                pass

    def _switch(self, mode: str, port: int, now: float) -> None:
        if self.mode == "ssh_out":
            self._kill()
        self.mode, self.local_port = mode, port
        self.up, self.since, self._recover_t0 = False, now, None
        self._backoff, self._next_spawn, self.last_error, self._cmd = 3.0, 0.0, "", []
        label = {"direct": "직접 접속", "ssh_in": f"SSH 역방향 터널 (127.0.0.1:{port} ← 회사)", "ssh_out": f"SSH 아웃바운드 터널 (127.0.0.1:{port} → 점프호스트)"}[mode]
        self._event("info", f"백홀 모드: {label}")

    def _transition(self, up: bool, now: float) -> None:
        if up:
            gap = now - self.since if self.since else 0.0
            if self.downs:                                            # 첫 연결이 아니라 단절 뒤 복구일 때만 단절 시간을 적립
                self.last_down_s = gap
                self.down_total_s += gap
            self.up, self.since = True, now
            self._recover_t0 = now
            self._event("info", f"백홀 연결 — 127.0.0.1:{self.local_port} 리스너 확인" + (f" (단절 {gap:.0f}초)" if self.downs else ""))
        else:
            self.up, self.since = False, now
            self.downs += 1
            self._recover_t0 = None
            self._event("error", f"백홀 단절 — 127.0.0.1:{self.local_port} 리스너 없음 (터널 끊김); 프레임은 저장 후 전송 버퍼에 보관")

    # ---------------------------------------------------------------- ssh_out supervision
    def _supervise(self, bh: dict, t: dict, now: float) -> None:
        s = bh.get("ssh") or {}
        if not (s.get("host") or "").strip():
            if self.last_error != "ssh.host 미설정":
                self.last_error = "ssh.host 미설정"
            return
        known = os.path.join(self.state_dir, "known_hosts") if self.state_dir else ""
        cmd = ssh_command(bh, t, known)
        if self.proc is not None and self.proc.poll() is None:
            if cmd != self._cmd:                                      # 설정이 바뀜 → 새 명령으로 재시작
                self._kill()
            else:
                return
        if self.proc is not None and self.proc.poll() is not None:    # 죽었다
            rc = self.proc.returncode
            err = self._read_stderr()
            self.last_error = f"ssh 종료 rc={rc}" + (f": {err}" if err else "")
            self._event("warn", f"백홀 ssh 종료 (rc={rc}){' — ' + err if err else ''}; {self._backoff:.0f}초 뒤 재시도")
            self.proc = None
            self._next_spawn = now + self._backoff
            self._backoff = min(60.0, self._backoff * 2)
        if now < self._next_spawn:
            return
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
            self._cmd = cmd
            self.restarts += 1
            self._event("info", f"백홀 ssh 시작 (#{self.restarts}): {' '.join(cmd[-2:])}")
        except OSError as e:
            self.last_error = f"ssh 실행 실패: {e}"
            self._next_spawn = now + self._backoff
            self._backoff = min(60.0, self._backoff * 2)

    def _read_stderr(self) -> str:
        try:
            if self.proc and self.proc.stderr:
                data = self.proc.stderr.read(2000) or b""
                return data.decode("utf-8", "replace").strip().splitlines()[-1] if data.strip() else ""
        except Exception:
            pass
        return ""

    def _kill(self) -> None:
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            pass

    def _event(self, level: str, msg: str) -> None:
        try:
            self.log.add("backhaul", msg, level=level)
        except Exception:
            pass
        print(f"[backhaul] {level.upper()} {msg}", flush=True)
        if self.on_event:
            try:
                self.on_event(level, msg)
            except Exception:
                pass
