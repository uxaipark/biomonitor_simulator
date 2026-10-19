"""Shared-memory state tables read by the fast path (worker processes) and
written by the world engine (main process).  Struct-of-arrays layout so the
per-tick sample gather is fully vectorised.
"""
from __future__ import annotations

from multiprocessing import shared_memory

import numpy as np

PATCH_FIELDS = [
    ("active", np.uint8),        # 0 idle, 1 streaming
    ("gw", np.int32),            # gateway row or -1 (no link)
    ("patch_id", np.uint32),
    ("patient_id", np.uint32),
    ("seq", np.uint32),          # per-patch packet sequence (protocol v2 record header), advanced by the worker that streams the patch
    ("variant", np.int32),       # ecg/ppg/resp/sec bank row
    ("variant_prev", np.int32),
    ("switch_tick", np.int64),   # tick at which variant changed (crossfade)
    ("offset_ms", np.int32),     # loop time offset
    ("gain", np.float32),
    ("noise", np.float32),       # 0..1 fraction of shared noise loop
    ("activity", np.int32),      # activity template row
    ("act_offset_ms", np.int32),
    ("art_gain", np.float32),    # motion artefact gain (0 = none)
    ("posture", np.int32),
    ("lead_off", np.uint8),
    ("spo2_off", np.uint8),
    ("battery", np.uint8),
    ("rssi", np.int8),
    ("flags", np.uint8),
    ("temp_var", np.int32),
    ("gluc_var", np.int32),
    ("temp_bias", np.float32),
    ("spo2_bias", np.int8),
    ("resp_src", np.uint8),      # 0 capacitive 1 edr 2 spo2
    ("spo2_src", np.uint8),      # 0 fingertip 1 ring 2 wrist_ptt
    ("paced", np.uint8),         # 1 = device present (pacemaker/ICD/CRT)
    ("hr_override", np.uint8),   # 0 = use loop
    ("pace_amp", np.float32),    # spike amplitude in 0.001 mV units (signed); 0 = no visible spike
    ("pace_detect", np.uint8),   # hardware pace-detection probability (%) for the marker channel
    ("chan_mask", np.uint16),    # channels provided by the patient's device set (ANDed with the global mask)
    ("hr_scale", np.float32),    # slow multi-day modulation (circadian + drift): HR factor, also time-stretches ECG/PPG
    ("rr_add", np.float32),
    ("spo2_add", np.float32),
    ("temp_add", np.float32),
    ("gl_add", np.float32),
    ("attach_tick", np.int64),   # tick when the patch was (re)attached -> electrode settling transient (0 = none)
    ("detach_tick", np.int64),   # tick when the electrodes came off -> brief violent artefact before the rail
    ("truth", np.uint8),         # 1 = 정답 전용 벤치마크 패치: 프레임마다 TRUTH 레코드(채널 11)를 같이 보낸다
    ("beat_tap", np.uint8),      # 1 = 일반 환자 패치의 박동 단위 정답을 링버퍼에 남긴다 (POST /api/v1/truth/watch)
    ("beats_n", np.uint32),      # 박동 정답 누적 카운터 (워커가 프레임마다 더함): 전체 · AAMI S · V · F · Q (N = 전체 - 나머지)
    ("beats_s", np.uint32), ("beats_v", np.uint32), ("beats_f", np.uint32), ("beats_q", np.uint32),
    # ---- 패킷 번호와 재전송 (2026-10-15).  번호 = seq0 + (절대 tick - tabs0): 패치가 켜진 뒤 200 ms 칸마다 1 — 끊긴 동안에도 저절로
    #      올라가고, 번호만으로 원래 시각을 거꾸로 알 수 있다 (프로토콜 v4 는 하위 16 비트만 보낸다).  절대 tick = 벽시계 ms // bundle_ms.
    ("seq0", np.uint32), ("tabs0", np.int64),
    # ECG 루프 위치(표본, 실수)를 워커끼리 공유한다: pos = 다음 tick 의 시작 위치, pos_start = pos_tabs tick 의 시작 위치.
    # (예전엔 워커마다 따로 누적해 워커가 다른 게이트웨이로 옮기면 파형이 튀었고, 끊긴 동안엔 멈춰 끼워 넣을 자리가 없었다)
    ("pos", np.float64), ("pos_start", np.float64), ("pos_tabs", np.int64), ("pos_off", np.int32),
    ("ls_var", np.int32), ("ls_gain", np.float32), ("ls_art", np.float32), ("ls_noise", np.float32),   # 마지막으로 송출한 tick 의 상태
    # 끊긴 구간 기록 (최근 GAP_K 개, 원형): [a, b) = 송출하지 못한 절대 tick, 양 끝의 ECG 위치·변형·이득 — 재전송 파형을 양 끝에 맞물려 만든다
    ("gp_a", np.int64, (4,)), ("gp_b", np.int64, (4,)), ("gp_apos", np.float64, (4,)), ("gp_bpos", np.float64, (4,)),
    ("gp_avar", np.int32, (4,)), ("gp_bvar", np.int32, (4,)), ("gp_ag", np.float32, (4,)), ("gp_bg", np.float32, (4,)),
    ("gp_aa", np.float32, (4,)), ("gp_ba", np.float32, (4,)), ("gp_an", np.float32, (4,)), ("gp_bn", np.float32, (4,)),
    ("gp_head", np.uint8),
    # 자세·활동 변경 기록 (최근 AH_K 개, 원형, 2026-10-20): ah_t = 그 상태가 시작된 절대 tick.  재전송 가속도(중력 방향·활동 패턴)를
    # 지금 상태가 아니라 그 tick 에 실제로 보냈던 상태로 만든다 — 지금 상태로 만들면 '정상 → 재전송' 경계에 ±0.5 g 계단이 생겼다.
    ("ls_post", np.int32), ("ls_act", np.int32), ("ls_aoff", np.int32),
    ("ah_t", np.int64, (8,)), ("ah_post", np.int32, (8,)), ("ah_act", np.int32, (8,)), ("ah_aoff", np.int32, (8,)),
    ("ah_art", np.float32, (8,)), ("ah_head", np.uint8),
]
GAP_K = 4
AH_K = 8
GW_FIELDS = [
    ("active", np.uint8),
    ("status", np.uint8),        # 0 ok, 1 degraded, 2 down (no frames, socket closed)
    ("loss", np.float32),        # emulated packet loss probability
    ("latency_ms", np.float32),
    ("jitter_ms", np.float32),
    ("cpu", np.uint8), ("mem", np.uint8), ("net", np.uint8),
    ("wan_rssi", np.int8),
    ("type", np.uint8),
    ("n_conn", np.uint8),
    ("gw_id", np.uint32),
    ("uptime_s", np.uint32),
    ("temp_c", np.uint8),
    ("battery", np.uint8),       # mobile gateways
    ("silent", np.uint8),        # 1 = half-open drill: TCP socket stays up but the gateway sends nothing (no keepalive)
]
STAT_FIELDS = [                 # per gateway, written by workers
    ("pkts", np.uint64), ("bytes", np.uint64), ("drop_emul", np.uint64), ("drop_backlog", np.uint64), ("drop_noconn", np.uint64),
    ("send_err", np.uint32), ("connected", np.uint8), ("last_tick", np.int64),
    ("saf_bytes", np.uint64),    # store-and-forward buffer currently held (bytes)
    ("saf_replayed", np.uint64), # frames replayed from the buffer after reconnect
    ("drop_saf", np.uint64),     # frames evicted from a full store-and-forward buffer
    ("fuzz", np.uint32),         # corrupt/duplicate/reordered frames injected (fuzz drill)
    ("nack_rx", np.uint32),      # NACK control frames received from the router
    ("resent", np.uint64),       # frames re-sent from the keep buffer on request
    ("resend_miss", np.uint32),  # requested frames no longer in the keep buffer
    ("bad_ctrl", np.uint32),     # control frames that failed CRC / parsing
    ("pn_req", np.uint32),       # 패치 재전송 요청 (라우터 → 게이트웨이 → 패치)
    ("pn_sent", np.uint64),      # 그 요청으로 다시 만들어 보낸 패치 프레임
    ("pn_fail", np.uint32),      # 처리하지 못한 요청 (패치가 이 게이트웨이에 없음 · 너무 오래됨 · 패치 생성 전)
]
WSTAT_FIELDS = [                # per worker
    ("ticks", np.uint64), ("overruns", np.uint64), ("build_us", np.float64), ("send_us", np.float64),
    ("last_tick", np.int64), ("alive", np.uint8), ("n_patches", np.uint32), ("n_gw", np.uint32), ("pkts", np.uint64), ("bytes", np.uint64),
    ("truth_w", np.int64),       # 정답 박동 링버퍼(truthlog) 쓰기 카운터 (단조 증가; 메인이 읽은 위치와의 차로 새 항목을 안다)
]
# 정답 전용 패치의 박동/스파이크 이벤트 링버퍼 (워커 → 메인): 워커마다 TRUTH_RING 칸.  QRS 검출 채점(/api/v1/truth/score)의 정답 원천.
TRUTH_RING = 16384
TRUTH_RING_FIELDS = [("ts_ms", np.int64), ("patch_id", np.uint32), ("seq", np.uint32), ("kind", np.uint8), ("rhythm", np.uint8), ("flags", np.uint8), ("is_pace", np.uint8)]
# control scalars (float64 array)
CTL = {"running": 0, "epoch_ns": 1, "bundle_ms": 2, "chan_mask": 3, "ecg_fs": 4, "ppg_fs": 5, "resp_fs": 6, "accel_fs": 7,
       "meta_every": 8, "gwstat_every": 9, "target_port": 10, "socket_mode": 11, "crossfade_ticks": 12, "cfg_version": 13,
       "loop_seconds": 14, "max_backlog": 15, "reconnect_s": 16, "generate_only": 17, "n_workers": 18, "stop_workers": 19,
       "saf_enabled": 20, "saf_max_bytes": 21, "saf_burst": 22, "fuzz_rate": 23, "fuzz_mask": 24, "storm_epoch": 25, "storm_until": 26,
       "tap": 27, "connect_budget": 28, "truth_all": 29,
       "proto": 30, "pn_per_tick": 31}      # proto: 송출 프로토콜 버전(3 = 레코드 seq u32, 4 = u16 + 패치 재전송) · pn_per_tick: 워커가 tick 마다 다시 만들 최대 패치 프레임 수      # truth_all: 모든 환자 패치 프레임에 TRUTH 레코드(채널 11)
# frame-corruption drill kinds (bit i of CTL fuzz_mask enables FUZZ_KINDS[i])
FUZZ_KINDS = ["bad_magic", "bad_version", "bad_len", "truncated", "oversize", "garbage", "dup", "reorder", "seq_gap", "bad_record"]
CTL_SIZE = 32
TARGET_BUF = 64

FLAG_LEAD_OFF = 0x01
FLAG_MOTION = 0x02
BATT_OK, BATT_LOW = 100, 10     # 레코드 battery 바이트: 패치 연료계 2단계 (충분 100 / 부족 10) — scenario.patch.battery_report=percent 면 실제 %
FLAG_LOW_BATT = 0x04
FLAG_SPO2_OFF = 0x08
FLAG_PACED = 0x10
FLAG_CHARGING = 0x20
FLAG_NEW_PATCH = 0x40


class Table:
    """A struct-of-arrays table living in one shared memory block."""

    def __init__(self, fields, n: int, name: str | None = None, create: bool = True):
        self.fields = fields
        self.n = n
        self.dtype = np.dtype([tuple(x) for x in fields])      # (name, type) 또는 (name, type, shape)
        nbytes = self.dtype.itemsize * n
        if create:
            self.shm = shared_memory.SharedMemory(create=True, size=max(1, nbytes))
        else:
            self.shm = shared_memory.SharedMemory(name=name)
        self.arr = np.ndarray((n,), dtype=self.dtype, buffer=self.shm.buf)
        if create:
            self.arr[:] = 0
        self.name = self.shm.name

    def __getitem__(self, field: str) -> np.ndarray:
        return self.arr[field]

    def close(self) -> None:
        try:
            self.shm.close()
        except Exception:
            pass

    def unlink(self) -> None:
        try:
            self.shm.unlink()
        except Exception:
            pass


class SharedState:
    """All shared tables.  Created by the main process; attached by workers."""

    def __init__(self, n_patches: int, n_gateways: int, n_workers: int, names: dict | None = None):
        create = names is None
        names = names or {}
        self.n_patches, self.n_gateways, self.n_workers = n_patches, n_gateways, n_workers
        self.patch = Table(PATCH_FIELDS, n_patches, names.get("patch"), create)
        self.gw = Table(GW_FIELDS, n_gateways, names.get("gw"), create)
        self.stat = Table(STAT_FIELDS, n_gateways, names.get("stat"), create)
        self.wstat = Table(WSTAT_FIELDS, max(1, n_workers), names.get("wstat"), create)
        self.truthlog = Table(TRUTH_RING_FIELDS, max(1, n_workers) * TRUTH_RING, names.get("truthlog"), create)
        if create:
            self.ctl_shm = shared_memory.SharedMemory(create=True, size=CTL_SIZE * 8)
            self.target_shm = shared_memory.SharedMemory(create=True, size=TARGET_BUF)
        else:
            self.ctl_shm = shared_memory.SharedMemory(name=names["ctl"])
            self.target_shm = shared_memory.SharedMemory(name=names["target"])
        self.ctl = np.ndarray((CTL_SIZE,), dtype=np.float64, buffer=self.ctl_shm.buf)
        self.target = np.ndarray((TARGET_BUF,), dtype=np.uint8, buffer=self.target_shm.buf)
        if create:
            self.ctl[:] = 0
            self.target[:] = 0

    def names(self) -> dict:
        return {"patch": self.patch.name, "gw": self.gw.name, "stat": self.stat.name, "wstat": self.wstat.name, "truthlog": self.truthlog.name,
                "ctl": self.ctl_shm.name, "target": self.target_shm.name,
                "n_patches": self.n_patches, "n_gateways": self.n_gateways, "n_workers": self.n_workers}

    @staticmethod
    def attach(names: dict) -> "SharedState":
        return SharedState(names["n_patches"], names["n_gateways"], names["n_workers"], names)

    def set_target(self, ip: str) -> None:
        b = ip.encode("ascii", "ignore")[: TARGET_BUF - 1]
        self.target[:] = 0
        self.target[: len(b)] = np.frombuffer(b, dtype=np.uint8)
        self.ctl[CTL["cfg_version"]] += 1

    def get_target(self) -> str:
        raw = bytes(self.target.tobytes())
        return raw.split(b"\x00", 1)[0].decode("ascii", "ignore")

    def close(self) -> None:
        for t in (self.patch, self.gw, self.stat, self.wstat, self.truthlog):
            t.close()
        self.ctl_shm.close()
        self.target_shm.close()

    def unlink(self) -> None:
        for t in (self.patch, self.gw, self.stat, self.wstat, self.truthlog):
            t.unlink()
        for s in (self.ctl_shm, self.target_shm):
            try:
                s.unlink()
            except Exception:
                pass
