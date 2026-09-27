"""보안 백홀: 실효 접속 대상 · 리스너 감지 · ssh 명령줄 · 상태 전이."""
import socket
import time

from emulator.runtime import backhaul as B


def test_effective_target_direct_and_tunnel():
    t = {"target_ip": "192.168.0.14", "target_port": 9100, "backhaul": {"mode": "direct"}}
    assert B.effective_target(t) == ("192.168.0.14", 9100, False)
    assert B.effective_target({"target_ip": "", "target_port": 9100, "backhaul": {"mode": "direct"}}) == ("", 9100, True)     # 생성만
    t["backhaul"] = {"mode": "ssh_in", "local_port": 9200}
    assert B.effective_target(t) == ("127.0.0.1", 9200, False)                                                               # target_ip 는 표시용으로 남는다
    t["backhaul"] = {"mode": "ssh_out", "local_port": 0}
    assert B.effective_target(t) == ("127.0.0.1", 9100, False)                                                               # local_port 비면 target_port


def test_listener_present_sees_a_real_listening_socket():
    s = socket.socket()
    s.bind(("127.0.0.1", 0)); s.listen(1)
    port = s.getsockname()[1]
    try:
        assert B.listener_present(port)
    finally:
        s.close()
    assert not B.listener_present(port)


def test_ssh_command_and_hint():
    bh = {"mode": "ssh_out", "local_port": 9100, "keepalive_s": 15, "ssh": {"host": "jump.example", "port": 2222, "user": "biosig", "key_path": "/k/id", "remote_target": "", "extra_args": "-o ProxyJump=bastion"}}
    t = {"target_ip": "10.0.0.5", "target_port": 9100, "backhaul": bh}
    cmd = B.ssh_command(bh, t, "/var/lib/biosim/known_hosts")
    assert cmd[0] == "ssh" and "-N" in cmd and "BatchMode=yes" in cmd and "ServerAliveInterval=15" in cmd
    assert cmd[cmd.index("-L") + 1] == "127.0.0.1:9100:10.0.0.5:9100"                                                        # remote_target 비면 라우터 주소
    assert cmd[-1] == "biosig@jump.example" and "-p" in cmd and cmd[cmd.index("-p") + 1] == "2222"
    assert "ProxyJump=bastion" in cmd and "UserKnownHostsFile=/var/lib/biosim/known_hosts" in cmd
    hint = B.inbound_hint({"local_port": 9100, "keepalive_s": 10}, t)
    assert "-R 127.0.0.1:9100:10.0.0.5:9100" in hint and hint.startswith("ssh -N")


class _Cfg:
    def __init__(self, t):
        self.t = t

    def snapshot(self):
        return {"transport": self.t}


class _Log:
    def __init__(self):
        self.items = []

    def add(self, kind, msg, **kw):
        self.items.append((kind, msg, kw.get("level")))


def test_ssh_in_transitions_count_downs_and_recovery(monkeypatch):
    t = {"target_ip": "10.0.0.5", "target_port": 9100, "socket_mode": "shared", "backhaul": {"mode": "ssh_in", "local_port": 9100}}
    log = _Log()
    saf = {"n": 3}
    present = {"v": False}
    monkeypatch.setattr(B, "listener_present", lambda port, proc_net="/proc/net": present["v"])
    monkeypatch.setattr(B.Backhaul, "_loop", lambda self: None)                      # 스레드 대신 손으로 tick
    bh = B.Backhaul(_Cfg(t), log, stats_fn=lambda: {"last": {"total": {"saf_gateways": saf["n"]}}})
    now = 1000.0
    bh._tick(now); assert bh.mode == "ssh_in" and bh.up is False
    present["v"] = True; bh._tick(now + 2); assert bh.up and bh.downs == 0
    saf["n"] = 0; bh._tick(now + 5); assert bh.recover_s is not None and bh.recover_s <= 3.5
    present["v"] = False; bh._tick(now + 10); assert not bh.up and bh.downs == 1
    present["v"] = True; saf["n"] = 4; bh._tick(now + 40); assert bh.up and abs(bh.last_down_s - 30) < 0.01 and abs(bh.down_total_s - 30) < 0.01
    st = bh.status()
    assert st["mode"] == "ssh_in" and st["up"] and st["downs"] == 1 and st["effective_target"] == "127.0.0.1:9100" and "-R 127.0.0.1:9100:10.0.0.5:9100" in st["inbound_hint"]
    kinds = [(k, lv) for k, _, lv in log.items]
    assert ("backhaul", "error") in kinds and ("backhaul", "info") in kinds
    t["backhaul"] = {"mode": "direct"}
    bh._tick(now + 50); assert bh.mode == "direct" and bh.status()["up"] is None
