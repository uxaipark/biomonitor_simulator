"""Binary gateway->router stream protocol (v1) and its machine-readable description.

TCP stream of frames.  All integers little-endian.

Frame header (24 bytes):
  magic u16 = 0x4742 ('B','G')  ver u8 = 1  flags u8  gw_id u32  seq u32
  ts_ms u64  n_rec u16  payload_len u32   (payload_len = bytes following the header)
flags: bit0 META block present, bit1 GW_STATUS block present, bit2 KEEPALIVE (no records)
Payload order: [GW_STATUS 12 B] [META u32 len + JSON] [records...]
Record: patch_id u32, patient_id u32, seq u32 (per-patch packet counter: a gap = packets lost between patch and router), flags u8, battery u8, rssi i8, n_ch u8, then n_ch channel blocks
Channel block: ch_id u8, dtype u8, n u16, data (n * axes * itemsize)
"""
from __future__ import annotations

import json
import struct
from functools import lru_cache

import numpy as np

from ..config import CHANNELS, CH_ECG, CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE, CH_ACCEL, CH_PPG, CH_RESP_WAVE, CH_PACE

MAGIC = 0x4742
VERSION = 2                      # v2: record header carries the per-patch packet sequence (16 bytes, was 12)
F_META = 0x01
F_GWSTAT = 0x02
F_KEEPALIVE = 0x04
HEADER = struct.Struct("<HBBIIQHI")       # 24 bytes
GWSTAT = struct.Struct("<BBBbBBIB")       # cpu mem net wan_rssi n_conn status uptime temp_c  = 12 bytes
DTYPE_CODE = {"int16": 1, "uint8": 2, "uint16": 3, "int8": 4, "float32": 5}
NP_DTYPE = {"int16": np.int16, "uint8": np.uint8, "uint16": np.uint16, "int8": np.int8, "float32": np.float32}
WAVE_CH = [CH_ECG, CH_PPG, CH_RESP_WAVE, CH_ACCEL]
NUM_CH = [CH_HR, CH_TEMP, CH_RESP_RATE, CH_SPO2, CH_GLUCOSE]


def samples_per_frame(ch: int, bundle_ms: int, fs: dict) -> int:
    key = {CH_ECG: "ecg", CH_PPG: "ppg", CH_RESP_WAVE: "resp", CH_ACCEL: "accel"}[ch]
    return int(round(fs[key] * bundle_ms / 1000.0))


@lru_cache(maxsize=64)
def record_dtype(chan_mask: int, with_numerics: bool, bundle_ms: int, ecg_fs: int, ppg_fs: int, resp_fs: int, accel_fs: int) -> np.dtype:
    """Packed structured dtype for one patch record of a given channel set."""
    fs = {"ecg": ecg_fs, "ppg": ppg_fs, "resp": resp_fs, "accel": accel_fs}
    fields: list = [("patch_id", "<u4"), ("patient_id", "<u4"), ("seq", "<u4"), ("flags", "u1"), ("battery", "u1"), ("rssi", "i1"), ("n_ch", "u1")]
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


def pace_record(patch_id: int, patient_id: int, seq: int, flags: int, battery: int, rssi: int, marks: np.ndarray) -> bytes:
    """Standalone record carrying only pacemaker spike marks (sample offsets within the frame); same seq as the data record."""
    head = struct.pack("<IIIBBbB", patch_id, patient_id, seq, flags, battery, rssi, 1)
    return head + struct.pack("<BBH", CH_PACE, DTYPE_CODE["uint16"], len(marks)) + marks.astype("<u2").tobytes()


def frame(gw_id: int, seq: int, ts_ms: int, n_rec: int, payload: bytes, flags: int) -> bytes:
    return HEADER.pack(MAGIC, VERSION, flags, gw_id, seq, ts_ms, n_rec, len(payload)) + payload


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
        else:
            d["note"] = ("uint16 per detected pacing spike: bits 0-13 = sample offset within this frame's ECG block, bits 14-15 = chamber "
                         "(0 atrial, 1 ventricular/RV, 2 LV for CRT). Emulates hardware pace detection: bipolar/leadless spikes are tiny in the "
                         "waveform and detection may miss some (see META patches[].pacemaker.detect_pct). Sent as a separate record (n_ch=1) only when spikes occurred.")
        chans.append(d)
    return {"version": VERSION, 
        "transport": {"type": "tcp", "byte_order": "little", "framing": "length-prefixed frames, back-to-back on the stream",
                      "socket_modes": {"per_gateway": "one TCP connection per gateway (default, realistic)",
                                       "shared": "all gateways multiplexed on one TCP connection per worker process"},
                      "bundle_ms": bundle_ms, "keepalive": "frame with flag KEEPALIVE and n_rec=0 when a gateway has no patches"},
        "frame_header": {"size": HEADER.size, "struct": "<HBBIIQHI",
                         "fields": ["magic u16 = 0x4742", "version u8 = 1", "flags u8", "gw_id u32", "seq u32 (per gateway, wraps)",
                                    "ts_ms u64 (unix epoch ms, gateway clock)", "n_rec u16", "payload_len u32 (bytes after header)"],
                         "flags": {"0x01": "META block present", "0x02": "GW_STATUS block present", "0x04": "KEEPALIVE"}},
        "payload_order": ["GW_STATUS (if flag)", "META (if flag)", "records x n_rec"],
        "gw_status_block": {"size": GWSTAT.size, "struct": "<BBBbBBIB",
                            "fields": ["cpu_pct u8", "mem_pct u8", "net_load_pct u8", "wan_rssi_dbm i8", "ble_connections u8",
                                       "status u8 (0 ok,1 degraded,2 down)", "uptime_s u32", "temp_c u8"],
                            "every_n_frames": gwstat_every},
        "meta_block": {"struct": "<I len + UTF-8 JSON", "every_n_frames": meta_every,
                       "note": "Sent on the first frame after (re)connect and then periodically (frames are staggered across gateways).",
                       "json_schema": {"gw": "gateway id", "gw_idx": "row", "type": "gateway type", "location": {"building": "", "floor": 0, "x": 0, "y": 0, "room": ""},
                                       "bundle_ms": bundle_ms, "tick": "frame tick counter", "channels_enabled": ["keys"],
                                       "patches": [{"patch_id": "u32", "serial": "BP-xxxxxx", "patient_id": "u32", "mrn": "", "fw": "",
                                                    "resp_source": "capacitive|edr|spo2", "spo2_source": "fingertip|ring|wrist_ptt",
                                                    "channels": [{"id": 1, "key": "ecg", "fs": 250, "dtype": "int16", "scale": 0.001, "unit": "mV"}]}]}},
        "record": {"header": ["patch_id u32", "patient_id u32 (0 = unassigned)", "seq u32 (per-patch packet counter, +1 per record the patch produced; a gap at the router = packets lost anywhere between patch and router, a gateway frame seq gap = lost between gateway and router)", "flags u8", "battery_pct u8", "rssi_dbm i8", "n_ch u8"],
                   "flags": {"0x01": "LEAD_OFF (ECG electrodes detached; ECG rails, HR/RESP invalid)", "0x02": "MOTION", "0x04": "LOW_BATTERY",
                             "0x08": "SPO2_SENSOR_OFF", "0x10": "PACEMAKER_PATIENT", "0x20": "CHARGING", "0x40": "NEW_PATCH (first frames after replacement)"},
                   "channel_block": ["ch_id u8", "dtype_code u8 (1 int16, 2 uint8, 3 uint16, 4 int8, 5 float32)", "n u16 (samples)", "data n*axes*itemsize"],
                   "note": "Channel blocks appear in ascending ch_id order: waveforms first, then numerics, then pace marks. Physical value = raw * scale."},
        "channels": chans,
        "sample_alignment": "Each frame's waveform block covers exactly bundle_ms; consecutive frames of a patch are contiguous unless a gap (seq jump) occurred.",
    }
