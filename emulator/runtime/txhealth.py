"""송출(전송) 장애 감시: 엔진 통계를 주기적으로 받아 상태 전이만 로그 문장으로 바꾼다.

카운터(send_err, drop_*, connected)는 올라가도 이벤트 로그에는 아무것도 남지 않아서, 라우터가 꺼졌을 때도
로그 탭이 조용했다.  여기서는 운영자가 나중에 근거로 삼을 사건만 남긴다:

  * 송출 대상 접속 불가 (연결 0이 DOWN_AFTER 초 지속)  /  송출 재개
  * 회선 대량 끊김 (한 번에 최고치의 10 % 이상)        /  회선 정상화 (최고치의 95 % 복귀)
  * 데이터 유실 (미연결·백로그·SAF 초과로 버린 프레임) — SUMMARY 초마다 한 줄 요약
  * 송신 오류, 워커 오버런                              — SUMMARY 초마다 한 줄 요약
  * 저장 후 전송(SAF) 시작 / 해소, 워커 중지 / 복귀

시나리오로 일부러 만든 손실(drop_emul)은 기록하지 않는다 — 장애가 아니라 설정이다.
"""
from __future__ import annotations

SUMMARY = 60.0          # 누적 카운터 요약 주기 (s)
DOWN_AFTER = 10.0       # 연결 0이 이만큼 지속되면 접속 불가로 본다 (s)


class TxHealth:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.prev: dict | None = None
        self.peak = 0
        self.gws = 0
        self.zero_since: float | None = None
        self.down = False
        self.degraded = False
        self.saf_on = False
        self.dead: set[int] = set()
        self.acc = {k: 0 for k in ("send_err", "drop_noconn", "drop_backlog", "drop_saf", "overruns")}
        self.acc_t = 0.0
        self.saf_replayed0 = 0

    def update(self, s: dict, now: float) -> list[tuple[str, str]]:
        """s = engine.stats(history=False).  Returns [(level, message)] with level in info|warn|error."""
        out: list[tuple[str, str]] = []
        if not s.get("running"):
            self.reset()
            return out
        last = s.get("last") or {}
        t = last.get("total") or {}
        tgt = s.get("target") or {}
        where = f'{tgt.get("ip")}:{tgt.get("port")}'
        conn = int(t.get("connected", 0) or 0)
        gws = int(s.get("gateways", 0) or 0)

        if self.prev is None or gws != self.gws:                     # 첫 표본이거나 병원이 재구축됨: 기준만 잡는다
            self.prev, self.gws, self.peak, self.acc_t = t, gws, conn, now
            self.saf_replayed0 = int(t.get("saf_replayed", 0) or 0)
            return out
        pconn = int(self.prev.get("connected", 0) or 0)

        # ---- 접속 불가 / 재개
        if conn == 0 and gws > 0:
            self.zero_since = self.zero_since or now
            if not self.down and now - self.zero_since >= DOWN_AFTER:
                self.down = True
                out.append(("error", f"송출 대상 {where} 접속 불가 — 연결된 게이트웨이 0/{gws}"))
        else:
            self.zero_since = None
            if self.down:
                self.down = False
                self.degraded = True                                  # 다 붙을 때까지 정상화 보고를 기다린다
                out.append(("info", f"송출 재개 — 연결 {conn}/{gws}"))

        # ---- 대량 끊김 / 정상화
        if not self.down and pconn - conn >= max(20, 0.10 * self.peak):
            self.degraded = True
            out.append(("warn", f"회선 대량 끊김 {pconn} → {conn} (게이트웨이 {gws})"))
        if self.degraded and self.peak and conn >= 0.95 * self.peak:
            self.degraded = False
            out.append(("info", f"회선 정상화 — 연결 {conn}/{gws}"))
        self.peak = max(self.peak, conn)

        # ---- 누적 카운터 (카운터가 줄면 재시작으로 보고 새 값부터)
        for k in self.acc:
            cur, old = int(t.get(k, 0) or 0), int(self.prev.get(k, 0) or 0)
            self.acc[k] += cur - old if cur >= old else cur

        # ---- 저장 후 전송
        saf_g = int(t.get("saf_gateways", 0) or 0)
        if saf_g and not self.saf_on:
            self.saf_on = True
            self.saf_replayed0 = int(t.get("saf_replayed", 0) or 0)
            out.append(("warn", f"저장 후 전송 시작 — 게이트웨이 {saf_g}개가 프레임을 버퍼링 중 ({int(t.get('saf_bytes', 0) or 0) / 1e6:.1f} MB)"))
        elif not saf_g and self.saf_on:
            self.saf_on = False
            rep = int(t.get("saf_replayed", 0) or 0) - self.saf_replayed0
            out.append(("info", f"저장 후 전송 해소 — 버퍼 프레임 {max(rep, 0):,}개 재전송 완료"))

        # ---- 워커
        for w in last.get("workers") or []:
            i = int(w.get("id", -1))
            if not w.get("alive") and i not in self.dead:
                self.dead.add(i)
                out.append(("error", f"워커 {i} 중지 — 담당 게이트웨이 {w.get('n_gw', '?')}개 송출 불가"))
            elif w.get("alive") and i in self.dead:
                self.dead.discard(i)
                out.append(("info", f"워커 {i} 복귀"))

        # ---- 요약
        if now - self.acc_t >= SUMMARY:
            a = self.acc
            span = int(round(now - self.acc_t))
            loss = [(n, a[k]) for k, n in (("drop_noconn", "미연결"), ("drop_backlog", "송신 적체"), ("drop_saf", "SAF 버퍼 초과")) if a[k]]
            if loss:
                out.append(("error", f"데이터 유실 (최근 {span}초): " + ", ".join(f"{n} {v:,}" for n, v in loss) + " 프레임"))
            if a["send_err"] and not self.down:                       # 접속 불가 중의 송신 오류는 위 한 줄이 이미 설명한다
                out.append(("warn", f"송신 오류 {a['send_err']:,}회 (최근 {span}초, 연결 {conn}/{gws})"))
            if a["overruns"]:
                out.append(("warn", f"워커 오버런 {a['overruns']:,}회 (최근 {span}초) — 프레임 주기를 못 맞춤"))
            self.acc = {k: 0 for k in self.acc}
            self.acc_t = now

        self.prev = t
        return out
