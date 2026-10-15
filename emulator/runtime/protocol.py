"""Binary gateway->router stream protocol (v1) and its machine-readable description.

TCP stream of frames.  All integers little-endian.

Frame header (26 bytes):
  magic u16 = 0x4742 ('B','G')  ver u8 = 1  flags u8  gw_id u32  seq u32
  ts_ms u64  n_rec u16  payload_len u32   (payload_len = bytes following the header)
flags: bit0 META block present, bit1 GW_STATUS block present, bit2 KEEPALIVE (no records)
Payload order: [GW_STATUS 12 B] [META u32 len + JSON] [records...]
Frame: header (26) + payload + CRC-32 trailer (4).  Control frames (router->gateway, F_CTRL): NACK seq ranges.
Record: patch_id u32, patient_id u32, seq u32 (per-patch packet counter: a gap = packets lost between patch and router), flags u8, battery u8, rssi i8, n_ch u8, then n_ch channel blocks
Channel block: ch_id u8, dtype u8, n u16, data (n * axes * itemsize)
"""
from __future__ import annotations

import json
import struct
import zlib
from functools import lru_cache

import numpy as np

from ..config import CHANNELS, CH_ECG, CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE, CH_ACCEL, CH_PPG, CH_RESP_WAVE, CH_PACE, CH_TRUTH
from ..signals.rhythms import RHYTHMS, BEAT_KINDS, AAMI, AAMI_CLASSES

MAGIC = 0x4742
VERSION = 3                      # v3: CRC-32 trailer on every frame + control frames (router -> gateway NACK); v2 added the per-patch seq
F_META = 0x01
F_GWSTAT = 0x02
F_KEEPALIVE = 0x04
F_CTRL = 0x08                    # control frame (router -> gateway): payload = <B kind><I from><I to>; kind 1 = NACK (resend frames from..to)
F_RESEND = 0x10                  # v4: 이 프레임의 레코드는 패치 재전송분 — ts_ms 는 원래 시각(과거), 레코드 seq 는 원래 패킷 번호
CTRL_NACK = 1
CTRL_PATCH_NACK = 2              # v4: 패치 재전송 요청 = <B kind=2><I patch_id><H seq_from><H seq_to> (양 끝 포함, 16 비트 감김 허용)
CTRL = struct.Struct("<BII")
CTRL_PN = struct.Struct("<BIHH")  # CTRL 과 같은 9 바이트
VERSION_V4 = 4                   # v4 = v3 + 레코드 패킷 번호 2 바이트(u16) + 패치 재전송(CTRL kind 2, F_RESEND)
REC_HEAD = {3: struct.Struct("<IIIBBbB"), 4: struct.Struct("<IIHBBbB")}   # patch_id patient_id seq flags battery rssi n_ch
SEQ_MASK = {3: 0xFFFFFFFF, 4: 0xFFFF}


def rec_head(ver: int) -> struct.Struct:
    return REC_HEAD[4] if ver >= 4 else REC_HEAD[3]


def seq_mask(ver: int) -> int:
    return SEQ_MASK[4] if ver >= 4 else SEQ_MASK[3]
CRC = struct.Struct("<I")         # frame trailer: CRC-32 (zlib) over header + payload
HEADER = struct.Struct("<HBBIIQHI")       # 26 bytes
GWSTAT = struct.Struct("<BBBbBBIB")       # cpu mem net wan_rssi n_conn status uptime temp_c  = 12 bytes
DTYPE_CODE = {"int16": 1, "uint8": 2, "uint16": 3, "int8": 4, "float32": 5}
NP_DTYPE = {"int16": np.int16, "uint8": np.uint8, "uint16": np.uint16, "int8": np.int8, "float32": np.float32}
WAVE_CH = [CH_ECG, CH_PPG, CH_RESP_WAVE, CH_ACCEL]
NUM_CH = [CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE]


def samples_per_frame(ch: int, bundle_ms: int, fs: dict) -> int:
    key = {CH_ECG: "ecg", CH_PPG: "ppg", CH_RESP_WAVE: "resp", CH_ACCEL: "accel"}[ch]
    return int(round(fs[key] * bundle_ms / 1000.0))


@lru_cache(maxsize=64)
def record_dtype(chan_mask: int, with_numerics: bool, bundle_ms: int, ecg_fs: int, ppg_fs: int, resp_fs: int, accel_fs: int, ver: int = 3) -> np.dtype:
    """Packed structured dtype for one patch record of a given channel set (v4: seq u16)."""
    fs = {"ecg": ecg_fs, "ppg": ppg_fs, "resp": resp_fs, "accel": accel_fs}
    fields: list = [("patch_id", "<u4"), ("patient_id", "<u4"), ("seq", "<u2" if ver >= 4 else "<u4"), ("flags", "u1"), ("battery", "u1"), ("rssi", "i1"), ("n_ch", "u1")]
    for ch in WAVE_CH:
        if chan_mask & (1 << ch):
            n = samples_per_frame(ch, bundle_ms, fs)
            shape = (n, 3) if ch == CH_ACCEL else (n,)
            fields += [(f"h{ch}", "u1"), (f"d{ch}", "u1"), (f"n{ch}", "<u2"), (f"c{ch}", "<i2", shape)]
    if with_numerics:
        for ch in NUM_CH:
            if chan_mask & (1 << ch):
                dt = CHANNELS[ch]["dtype"]
                code = {"uint8": "u1", "int16": "<i2", "uint16": "<u2"}[dt]
                fields += [(f"h{ch}", "u1"), (f"d{ch}", "u1"), (f"n{ch}", "<u2"), (f"c{ch}", code)]
    return np.dtype(fields)


def fill_constants(rec: np.ndarray, chan_mask: int, with_numerics: bool, bundle_ms: int, fs: dict) -> None:
    n_ch = 0
    for ch in WAVE_CH:
        if chan_mask & (1 << ch):
            rec[f"h{ch}"] = ch
            rec[f"d{ch}"] = DTYPE_CODE["int16"]
            rec[f"n{ch}"] = samples_per_frame(ch, bundle_ms, fs)
            n_ch += 1
    if with_numerics:
        for ch in NUM_CH:
            if chan_mask & (1 << ch):
                rec[f"h{ch}"] = ch
                rec[f"d{ch}"] = DTYPE_CODE[CHANNELS[ch]["dtype"]]
                rec[f"n{ch}"] = 1
                n_ch += 1
    rec["n_ch"] = n_ch


# ---------------------------------------------------------------- TRUTH record (channel 11) — 정답 전용 벤치마크 패치만 보낸다
# 채널 블록: ch_id 11 | dtype 2(uint8) | n = 블록 길이 | 블록.  블록(v1, little-endian):
#   u8 ver=1 | u8 rhythm | u8 rhythm_prev(255=없음; 크로스페이드 중 이전 리듬) | u8 flags | u8 art_level(0..255, 동작 아티팩트 이득×100)
#   u8 noise_level(0..255, 잡음 비율×1000) | u16 hr_true(bpm, 리드오프면 0)
#   u8 n_beats | n × (u16 offset(이 프레임 ECG 블록 안 표본 인덱스), u8 kind)      kind = BEAT_KINDS 인덱스
#   u8 n_pace  | n × u16 (bits 0-13 offset, 14-15 chamber 0 A 1 V 2 LV)            하드웨어 검출 손실 없는 '모든' 스파이크
# rhythm 코드 = RHYTHM_CODES 인덱스 (append-only).  flags 비트는 아래 T_*.
RHYTHM_CODES = list(RHYTHMS.keys())
RHYTHM_CODE_ID = {k: i for i, k in enumerate(RHYTHM_CODES)}
TRUTH_VER = 1
T_LEAD_OFF, T_ARTIFACT, T_NOISE, T_PACED, T_SWITCH, T_SETTLING, T_NO_BEATS = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
TRUTH_HEAD = struct.Struct("<BBBBBBH")


def truth_block(rhythm: int, rhythm_prev: int, flags: int, art_level: int, noise_level: int, hr: int, beats, pace) -> bytes:
    """beats: iterable of (offset, kind); pace: iterable of (offset, chamber).  offset 은 이 프레임 ECG 블록 안 표본 인덱스."""
    beats = list(beats)[:255]
    pace = list(pace)[:255]
    out = TRUTH_HEAD.pack(TRUTH_VER, rhythm & 0xFF, rhythm_prev & 0xFF, flags & 0xFF, min(255, max(0, int(art_level))), min(255, max(0, int(noise_level))), min(65535, max(0, int(hr))))
    out += struct.pack("<B", len(beats)) + b"".join(struct.pack("<HB", int(o) & 0xFFFF, int(k) & 0xFF) for o, k in beats)
    out += struct.pack("<B", len(pace)) + b"".join(struct.pack("<H", (int(o) & 0x3FFF) | ((int(c) & 3) << 14)) for o, c in pace)
    return out


def parse_truth(b: bytes) -> dict:
    ver, rh, rhp, fl, art, nz, hr = TRUTH_HEAD.unpack_from(b, 0)
    off = TRUTH_HEAD.size
    nb = b[off]; off += 1
    beats = [(struct.unpack_from("<H", b, off + 3 * i)[0], b[off + 3 * i + 2]) for i in range(nb)]
    off += 3 * nb
    npc = b[off]; off += 1
    pace = [((v & 0x3FFF), v >> 14) for v in struct.unpack_from(f"<{npc}H", b, off)]
    return {"ver": ver, "rhythm": RHYTHM_CODES[rh] if rh < len(RHYTHM_CODES) else None, "rhythm_code": rh,
            "rhythm_prev": RHYTHM_CODES[rhp] if rhp < len(RHYTHM_CODES) else None, "flags": fl,
            "lead_off": bool(fl & T_LEAD_OFF), "artifact": bool(fl & T_ARTIFACT), "noise": bool(fl & T_NOISE), "paced": bool(fl & T_PACED),
            "switching": bool(fl & T_SWITCH), "settling": bool(fl & T_SETTLING), "beats_unavailable": bool(fl & T_NO_BEATS),
            "art_level": art / 100.0, "noise_level": nz / 1000.0, "hr": hr,
            "beats": [{"offset": o, "kind": BEAT_KINDS[k] if k < len(BEAT_KINDS) else k, "aami": AAMI.get(BEAT_KINDS[k], "Q") if k < len(BEAT_KINDS) else "Q"} for o, k in beats],
            "pace": [{"offset": o, "chamber": c} for o, c in pace]}


def truth_record(patch_id: int, patient_id: int, seq: int, flags: int, battery: int, rssi: int, blob: bytes, ver: int = 3) -> bytes:
    """정답 레코드: 같은 프레임의 ECG 레코드와 같은 seq 를 가진 별도 레코드 (n_ch=1, 채널 11)."""
    head = rec_head(ver).pack(patch_id, patient_id, seq & seq_mask(ver), flags, battery, rssi, 1)
    return head + struct.pack("<BBH", CH_TRUTH, DTYPE_CODE["uint8"], len(blob)) + blob


def pace_record(patch_id: int, patient_id: int, seq: int, flags: int, battery: int, rssi: int, marks: np.ndarray, ver: int = 3) -> bytes:
    """Standalone record carrying only pacemaker spike marks (sample offsets within the frame); same seq as the data record."""
    head = rec_head(ver).pack(patch_id, patient_id, seq & seq_mask(ver), flags, battery, rssi, 1)
    return head + struct.pack("<BBH", CH_PACE, DTYPE_CODE["uint16"], len(marks)) + marks.astype("<u2").tobytes()


def frame(gw_id: int, seq: int, ts_ms: int, n_rec: int, payload: bytes, flags: int, ver: int = VERSION) -> bytes:
    body = HEADER.pack(MAGIC, ver, flags, gw_id, seq, ts_ms, n_rec, len(payload)) + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def frame_crc_ok(body: bytes, trailer: bytes) -> bool:
    return (zlib.crc32(body) & 0xFFFFFFFF) == CRC.unpack(trailer)[0]


def ctrl_frame(gw_id: int, kind: int, seq_from: int, seq_to: int, ts_ms: int = 0) -> bytes:
    """Router -> gateway control frame (same framing + CRC, F_CTRL flag, n_rec = 0)."""
    return frame(gw_id, 0, ts_ms, 0, CTRL.pack(kind, seq_from & 0xFFFFFFFF, seq_to & 0xFFFFFFFF), F_CTRL)


def parse_ctrl(payload: bytes) -> tuple[int, int, int] | None:
    """kind 1 (NACK) → (1, seq_from, seq_to).  kind 2 (PATCH_NACK) → (2, patch_id, (seq_from << 16) | seq_to) — parse_patch_nack 로 푸는 편이 낫다."""
    if len(payload) < CTRL.size:
        return None
    return CTRL.unpack_from(payload, 0)


def patch_nack_frame(gw_id: int, patch_id: int, seq_from: int, seq_to: int, ts_ms: int = 0) -> bytes:
    """v4 router -> gateway: 패치 patch_id 의 패킷 seq_from..seq_to(u16, 포함) 를 다시 보내 달라.  게이트웨이는 그 패치에 전달하고,
    패치가 그 게이트웨이에 붙어 있으면 다시 만든 프레임(F_RESEND, 원래 ts_ms·seq)이 같은 소켓으로 온다."""
    return frame(gw_id, 0, ts_ms, 0, CTRL_PN.pack(CTRL_PATCH_NACK, patch_id & 0xFFFFFFFF, seq_from & 0xFFFF, seq_to & 0xFFFF), F_CTRL, VERSION_V4)


def parse_patch_nack(payload: bytes) -> tuple[int, int, int] | None:
    if len(payload) < CTRL_PN.size or payload[0] != CTRL_PATCH_NACK:
        return None
    _, pid, a, b = CTRL_PN.unpack_from(payload, 0)
    return pid, a, b


def gwstat_block(cpu: int, mem: int, net: int, wan_rssi: int, n_conn: int, status: int, uptime: int, temp_c: int) -> bytes:
    return GWSTAT.pack(cpu, mem, net, wan_rssi, n_conn, status, uptime, temp_c)


def meta_block(meta: dict) -> bytes:
    b = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(b)) + b


def describe(bundle_ms: int, fs: dict, meta_every: int, gwstat_every: int) -> dict:
    """Machine-readable protocol description served by the discovery API."""
    chans = []
    for cid, c in CHANNELS.items():
        d = {"id": cid, "key": c["key"], "name": c["name"], "unit": c["unit"], "dtype": c["dtype"],
             "dtype_code": DTYPE_CODE[c["dtype"]], "scale": c["scale"], "kind": c["kind"], "axes": c.get("axes", 1)}
        if cid in WAVE_CH:
            d["fs"] = fs[{CH_ECG: "ecg", CH_PPG: "ppg", CH_RESP_WAVE: "resp", CH_ACCEL: "accel"}[cid]]
            d["samples_per_frame"] = samples_per_frame(cid, bundle_ms, fs)
        elif cid in NUM_CH:
            d["period_ms"] = 1000
            d["note"] = "1 value/s, staggered per patch: sent on frames where (tick + patch_row) % (1000/bundle_ms) == 0. 0 = invalid/no reading."
        elif cid == CH_TRUTH:
            d["note"] = ("Ground-truth block sent only by the benchmark patches (gateway type 'truth', META patches[].benchmark=true), one record per "
                         "frame with the same seq as the ECG record: u8 ver=1 | u8 rhythm(RHYTHM_CODES idx) | u8 rhythm_prev(255=none, set during a "
                         "crossfade) | u8 flags | u8 art_level(x0.01) | u8 noise_level(x0.001) | u16 hr_true(bpm) | u8 n_beats + n x (u16 offset, u8 "
                         "beat_kind) | u8 n_pace + n x u16 (bits 0-13 offset, 14-15 chamber). Offsets index this frame's ECG block. flags: 0x01 lead_off "
                         "0x02 motion_artifact 0x04 noise 0x08 paced_device 0x10 rhythm_switching 0x20 electrode_settling 0x40 beats_unavailable "
                         "(loop bank without annotations). All pacing spikes are listed (no detection loss), unlike channel 10.")
            d["rhythm_codes"] = RHYTHM_CODES
            d["beat_kinds"] = BEAT_KINDS
            d["beat_kind_labels"] = {"N": "normal", "A": "atrial premature (PAC)", "a": "aberrated PAC (RBBB-like)", "V": "ventricular premature (PVC)", "FV": "ventricular fusion",
                                     "J": "junctional escape", "E": "ventricular escape", "S": "supraventricular tachy beat", "Vp": "ventricular paced", "AVp": "AV sequential paced",
                                     "Ap": "atrial paced", "AsVp": "atrial sensed, ventricular paced", "CRT": "biventricular paced", "F": "paced fusion / pseudofusion"}
            d["aami"] = {"classes": AAMI_CLASSES, "map": AAMI, "note": "ANSI/AAMI EC57 classes: N normal (incl. BBB, junctional escape), S supraventricular ectopic, V ventricular ectopic (incl. escape), F fusion of V and N, Q paced/unclassifiable"}
        else:
            d["note"] = ("uint16 per detected pacing spike: bits 0-13 = sample offset within this frame's ECG block, bits 14-15 = chamber "
                         "(0 atrial, 1 ventricular/RV, 2 LV for CRT). Emulates hardware pace detection: bipolar/leadless spikes are tiny in the "
                         "waveform and detection may miss some (see META patches[].pacemaker.detect_pct). Sent as a separate record (n_ch=1) only when spikes occurred.")
        chans.append(d)
    return {"version": VERSION, "version_v4": {
        "enable": "transport.protocol_version = 4 (기본 3).  헤더 ver = 4, 나머지 프레이밍·CRC 는 v3 와 같다.",
        "record_header": "patch_id u32 | patient_id u32 | seq u16 | flags u8 | battery u8 | rssi i8 | n_ch u8  (14 바이트; v3 은 seq u32 · 16 바이트)",
        "patch_seq": "패치 패킷 번호 = 패치가 켜진 뒤 bundle(200 ms) 칸 수 (16 비트에서 감김 = 약 3.6 시간).  송출하지 못한 동안(음영·연결 끊김)에도 시간과 함께 올라가므로 공백 = 유실 구간",
        "patch_nack": "라우터 → 게이트웨이 소켓 CTRL(flags 0x08) payload <B kind=2><I patch_id><H seq_from><H seq_to> (양 끝 포함, 감김 허용).  패치가 그 게이트웨이에 붙어 있으면 그 번호들의 프레임을 다시 만들어 같은 소켓으로 보낸다",
        "resend_frame": "flags 0x10 F_RESEND: 레코드가 재전송분 — ts_ms 는 원래(과거) 시각, 레코드 seq 는 원래 번호.  한 프레임 = 한 패치의 한 칸.  파형은 앞뒤 정상 프레임과 표본 단위로 이어진다",
        "rest": "GET /api/v1/patches/{patch_id}/gaps · POST /api/v1/patches/{patch_id}/resend {seq_from, seq_to} · GET /api/v1/patches/resend/stats"},
        
        "transport": {"type": "tcp", "byte_order": "little", "framing": "length-prefixed frames, back-to-back on the stream",
                      "socket_modes": {"per_gateway": "one TCP connection per gateway (default, realistic)",
                                       "shared": "all gateways multiplexed on one TCP connection per worker process"},
                      "bundle_ms": bundle_ms, "keepalive": "frame with flag KEEPALIVE and n_rec=0 when a gateway has no patches"},
        "frame_header": {"size": HEADER.size, "struct": "<HBBIIQHI",
                         "fields": ["magic u16 = 0x4742", "version u8 = 1", "flags u8", "gw_id u32", "seq u32 (per gateway, wraps)",
                                    "ts_ms u64 (unix epoch ms, gateway clock)", "n_rec u16", "payload_len u32 (bytes after header)"],
                         "flags": {"0x01": "META block present", "0x02": "GW_STATUS block present", "0x04": "KEEPALIVE"}},
        "payload_order": ["GW_STATUS (if flag)", "META (if flag)", "records x n_rec"],
        "trailer": "crc32 u32 (zlib CRC-32 over header + payload; a mismatch = corrupt frame, the receiver resyncs and may NACK it)",
        "control_frames": {"direction": "router -> gateway on the same socket", "flag": "0x08 F_CTRL", "payload": "<B kind><I seq_from><I seq_to>",
                           "kinds": {"1": "NACK: resend gateway frames seq_from..seq_to (the gateway keeps the last transport.resend_keep_s seconds of sent frames)"}},
        "gw_status_block": {"size": GWSTAT.size, "struct": "<BBBbBBIB",
                            "fields": ["cpu_pct u8", "mem_pct u8", "net_load_pct u8", "wan_rssi_dbm i8", "ble_connections u8",
                                       "status u8 (0 ok,1 degraded,2 down)", "uptime_s u32", "temp_c u8"],
                            "every_n_frames": gwstat_every},
        "meta_block": {"struct": "<I len + UTF-8 JSON", "every_n_frames": meta_every,
                       "note": "Sent on the first frame after (re)connect and then periodically (frames are staggered across gateways).",
                       "json_schema": {"gw": "gateway id", "gw_idx": "row", "type": "gateway type", "location": {"building": "", "floor": 0, "x": 0, "y": 0, "room": ""},
                                       "net": {"uplink": "wired|wifi", "switch": "SW-<building>-<floor>", "ap": "AP-... | null", "power": "ups|emergency|normal",
                                               "_note": "병원 게이트웨이만. 같은 switch/ap 를 공유하는 GW 는 함께 끊긴다"},
                                       "uplink": "LTE|5G (MCOT 모바일 게이트웨이만)",
                                       "bundle_ms": bundle_ms, "tick": "frame tick counter", "channels_enabled": ["keys"],
                                       "patches": [{"patch_id": "u32", "serial": "8-digit number (= patch_id), e.g. 10132809", "patient_id": "u32", "mrn": "", "fw": "",
                                                    "home": {"sido": "서울", "sigungu": "강남구", "dong": "역삼동", "label": "서울 강남구 역삼동"},
                                                    "resp_source": "capacitive|edr|spo2", "spo2_source": "fingertip|ring|wrist_ptt",
                                                    "channels": [{"id": 1, "key": "ecg", "fs": 250, "dtype": "int16", "scale": 0.001, "unit": "mV"}]}]}},
        "record": {"header": ["patch_id u32", "patient_id u32 (0 = unassigned)", "seq u32 (per-patch packet counter, +1 per record the patch produced; a gap at the router = packets lost anywhere between patch and router, a gateway frame seq gap = lost between gateway and router)", "flags u8", "battery u8 (patch fuel gauge, 2 levels like the real patch: 100 = sufficient, 10 = low -> may switch off within a day; flag LOW_BATT 0x04 set when low; scenario.patch.battery_report=percent sends the true %)", "rssi_dbm i8", "n_ch u8"],
                   "flags": {"0x01": "LEAD_OFF (ECG electrodes detached; ECG rails, HR/RESP invalid)", "0x02": "MOTION", "0x04": "LOW_BATTERY",
                             "0x08": "SPO2_SENSOR_OFF", "0x10": "PACEMAKER_PATIENT", "0x20": "CHARGING", "0x40": "NEW_PATCH (first frames after replacement)"},
                   "channel_block": ["ch_id u8", "dtype_code u8 (1 int16, 2 uint8, 3 uint16, 4 int8, 5 float32)", "n u16 (samples)", "data n*axes*itemsize"],
                   "note": "Channel blocks appear in ascending ch_id order: waveforms first, then numerics, then pace marks. Physical value = raw * scale."},
        "channels": chans,
        "sample_alignment": "Each frame's waveform block covers exactly bundle_ms; consecutive frames of a patch are contiguous unless a gap (seq jump) occurred.",
    }
