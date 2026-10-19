"""패치 재전송 (프로토콜 v4): 2 바이트 패킷 번호, 끊긴 구간 기록, 번호 → 원래 시각 역산, 다시 만든 파형이 앞뒤 정상 프레임과 이어짐."""
import numpy as np

from emulator.config import CH_ECG, CH_ACCEL
from emulator.runtime import protocol as P
from emulator.runtime.fastpath import FrameBuilder
from emulator.runtime.state import SharedState, CTL
from emulator.runtime.verify import decode_records, StreamChecker


class FakeBank:
    """매끄러운 합성 은행: ECG = 1 Hz 사인 + 느린 성분 (불연속 없는 생성기 가정)."""
    def __init__(self, n=250 * 60):
        i = np.arange(n)
        base = 1000 * np.sin(2 * np.pi * i / 250) + 300 * np.sin(2 * np.pi * i / 2000)
        self.ecg = np.stack([base, base * 0.6]).astype(np.int16)
        self.ppg = np.zeros((2, 100 * 60), np.int16); self.resp = np.zeros((2, 25 * 60), np.int16)
        self.accel = np.zeros((1, 50 * 60, 3), np.int16); self.art = np.zeros((1, n), np.int16); self.noise = np.zeros(n, np.int16)
        self.sec = np.zeros((2, 7, 60), np.uint8); self.seconds = 60
        self.rpeaks = None; self.rkinds = None; self.pace = [np.zeros(0, np.int64)] * 2; self.pace_type = [np.zeros(0, np.uint8)] * 2
        self.index = {"variants": [{"rhythm": "nsr"}, {"rhythm": "afib"}]}


def _setup():
    st = SharedState(4, 2, 1)
    c = st.ctl
    for k, v in {"bundle_ms": 200, "ecg_fs": 250, "ppg_fs": 100, "resp_fs": 25, "accel_fs": 50, "chan_mask": 1 << CH_ECG, "crossfade_ticks": 5,
                 "meta_every": 100000, "gwstat_every": 100000, "proto": 4, "truth_all": 0, "pn_per_tick": 1000, "epoch_ns": 1_790_000_000_000 * 1_000_000}.items():
        c[CTL[k]] = v
    Pa = st.patch.arr
    Pa["active"][0] = 1; Pa["gw"][0] = 0; Pa["patch_id"][0] = 10000001; Pa["patient_id"][0] = 7; Pa["variant"][0] = 0; Pa["variant_prev"][0] = -1
    Pa["gain"][0] = 1.0; Pa["hr_scale"][0] = 1.0; Pa["switch_tick"][0] = -10 ** 9; Pa["battery"][0] = 100
    st.gw.arr["active"][:] = 1; st.gw.arr["gw_id"][:] = [101, 102]
    fb = FrameBuilder(FakeBank(), st, np.array([0, 1]), meta_provider=lambda g: None, shared_pos=True)
    Pa["tabs0"][0] = fb.g.tick_off()          # 패치 부착 = tick 0
    return st, fb


def _ecg(fr):
    magic, ver, flags, gw_id, seq, ts, n_rec, plen = P.HEADER.unpack_from(fr, 0)
    pay = memoryview(fr)[P.HEADER.size:P.HEADER.size + plen]
    off = P.GWSTAT.size if flags & P.F_GWSTAT else 0
    if flags & P.F_META:
        off += 4 + int.from_bytes(pay[off:off + 4], "little")
    recs = list(decode_records(pay, off, n_rec, ver))
    r = recs[0]
    return ver, flags, ts, r[2], np.frombuffer(r[6][CH_ECG][2], dtype="<i2").astype(np.int64)


def test_resend_fills_the_gap_without_seams():
    st, fb = _setup()
    Pa = st.patch.arr
    got = {}                                                   # seq -> samples
    for t in range(0, 40):
        if t == 10:
            Pa["gw"][0] = -1                                   # 음영 진입: 송출 없음
        if t == 30:
            Pa["gw"][0] = 0; Pa["hr_scale"][0] = 1.05          # 재연결 (그 사이 심박 속도도 바뀜)
        frames = fb.build(t, 1_790_000_000_000 + t * 200)
        if 0 in frames and not P.HEADER.unpack_from(frames[0], 0)[2] & P.F_KEEPALIVE:     # 끊긴 동안 게이트웨이는 keepalive 만
            ver, flags, ts, seq, x = _ecg(frames[0])
            assert ver == 4 and not flags & P.F_RESEND
            got[seq] = x
    assert sorted(got) == list(range(0, 10)) + list(range(30, 40))       # 번호 = 부착 뒤 경과 칸 수: 끊긴 동안에도 올라갔다
    k = (int(Pa["gp_head"][0]) - 1) % 4
    assert int(Pa["gp_b"][0][k]) - int(Pa["gp_a"][0][k]) == 20               # 끊김 기록 [10, 30)
    # 라우터 → 게이트웨이 → 패치: 10..29 재전송
    assert fb.queue_patch_nack(0, 10000001, 10, 29)

    class Snd:
        frames = []
        def send_resend(self, gw, data, now, mb):
            assert gw == 0
            self.frames.append(data)
    snd = Snd()
    fb.pump_patch_nacks(40, 1_790_000_000.0, snd, 0.0)
    assert len(snd.frames) == 20
    for fr in snd.frames:
        ver, flags, ts, seq, x = _ecg(fr)
        assert flags & P.F_RESEND and ver == 4 and 10 <= seq <= 29
        assert ts == 1_790_000_000_000 + seq * 200                   # 원래 시각
        got[seq] = x
    full = np.concatenate([got[s] for s in range(40)])
    d = np.abs(np.diff(full))
    normal = np.abs(np.diff(np.concatenate([got[s] for s in range(10)])))
    assert d.max() <= normal.max() * 1.15, (d.max(), normal.max(), int(np.argmax(d)))   # 이음매 없음 (경계에서 튀지 않음)
    # 검증기: 재전송 프레임은 시간 역행·순서 뒤바뀜이 아니다
    chk = StreamChecker()
    for fr in snd.frames:
        chk.feed(fr)
    sm = chk.summary()
    assert sm["patch_resend"] == 20 and sm["ts_backwards"] == 0 and sm["patch_seq_reorder"] == 0, sm
    st.close(); st.unlink()


def test_resend_rejected_when_patch_is_elsewhere_and_seq_wraps_16bit():
    st, fb = _setup()
    Pa = st.patch.arr
    Pa["tabs0"][0] = fb.g.tick_off() - 70000                   # 부착 뒤 70,000 칸: 16 비트 번호가 한 바퀴 돌았다
    for t in range(3):
        fr = fb.build(t, 0)[0]
    ver, flags, ts, seq, x = _ecg(fr)
    assert seq == (70000 + 2) & 0xFFFF
    assert fb._seq_to_tabs(0, seq, False, fb.g.tick_off() + 3) == fb.g.tick_off() + 2   # 번호 → 가장 최근의 그 tick
    Pa["gw"][0] = 1
    assert not fb.queue_patch_nack(0, 10000001, 0, 5)            # 패치가 다른 게이트웨이에 있다 → 거절
    assert int(st.stat.arr["pn_fail"][0]) == 1
    st.close(); st.unlink()


def test_v4_patch_nack_ctrl_roundtrip_and_record_head():
    fr = P.patch_nack_frame(101, 10000001, 65530, 4)
    magic, ver, flags, gw_id, seq, ts, n_rec, plen = P.HEADER.unpack_from(fr, 0)
    assert ver == 4 and flags & P.F_CTRL and P.parse_patch_nack(fr[P.HEADER.size:P.HEADER.size + plen]) == (10000001, 65530, 4)
    assert P.rec_head(4).size == 14 and P.rec_head(3).size == 16


def _acc(fr):
    magic, ver, flags, gw_id, seq, ts, n_rec, plen = P.HEADER.unpack_from(fr, 0)
    pay = memoryview(fr)[P.HEADER.size:P.HEADER.size + plen]
    off = P.GWSTAT.size if flags & P.F_GWSTAT else 0
    if flags & P.F_META:
        off += 4 + int.from_bytes(pay[off:off + 4], "little")
    r = list(decode_records(pay, off, n_rec, ver))[0]
    return r[2], np.frombuffer(r[6][CH_ACCEL][2], dtype="<i2").reshape(-1, 3).astype(np.int64)


def test_resend_accel_uses_posture_sent_at_that_time():
    """2026-10-20 라우터 보고: 재전송 가속도 z 가 '정상 → 재전송' 경계에서 ±0.5 g 계단 — 지금 자세로 다시 만들었기 때문.
    전송 중 유실(보냈던 tick)은 그때 자세로, 끊김 구간은 끊기기 전 자세 → 끊김 뒤 자세로 표본마다 섞여 양 끝이 이어져야 한다."""
    st, fb = _setup()
    st.ctl[CTL["chan_mask"]] = (1 << CH_ECG) | (1 << CH_ACCEL)
    Pa = st.patch.arr
    got = {}
    for t in range(0, 50):
        if t == 5:
            Pa["posture"][0] = 3                               # 앉음 (보냈던 구간 안에서 바뀜)
        if t == 10:
            Pa["gw"][0] = -1                                   # 끊김 [10, 30)
        if t == 20:
            Pa["posture"][0] = 4                               # 끊긴 동안 섬
        if t == 30:
            Pa["gw"][0] = 0
        if t == 40:
            Pa["posture"][0] = 1                               # 재전송 요청 전에 또 바뀜 (지금 자세 ≠ 그때 자세)
        frames = fb.build(t, 1_790_000_000_000 + t * 200)
        if 0 in frames and not P.HEADER.unpack_from(frames[0], 0)[2] & P.F_KEEPALIVE:
            seq, x = _acc(frames[0])
            got[seq] = x
    orig = {s: got[s] for s in range(2, 9)}                    # 이 구간은 '전송 중 유실' 로 다시 요청한다

    class Snd:
        frames = []
        def send_resend(self, gw, data, now, mb):
            self.frames.append(data)
    snd = Snd()
    assert fb.queue_patch_nack(0, 10000001, 2, 29)
    fb.pump_patch_nacks(50, 1_790_000_000.0, snd, 0.0)
    re = dict(_acc(f) for f in snd.frames)
    assert sorted(re) == list(range(2, 30))
    for s in range(2, 9):                                      # 보냈던 tick: 원래와 똑같이
        assert np.array_equal(re[s], orig[s]), s
    for s in range(10, 30):
        got[s] = re[s]
    full = np.concatenate([got[s] for s in range(50)])
    jump = np.abs(np.diff(full, axis=0)).max(axis=1)
    spt = full.shape[0] // 50
    for b in (10, 30):                                         # 끊김 양 끝: 계단 없음 (원래 자세 변경 0.5 g 이상보다 훨씬 작게)
        assert jump[b * spt - 1] < 120, (b, jump[b * spt - 1])
    assert jump[5 * spt - 1] > 300                             # 실제로 보낸 자세 변경(t=5)은 그대로 남는다
    st.close(); st.unlink()


def test_resend_keeps_electrode_settling_of_new_patch():
    """새 패치 부착 직후(안정화 20 초) 구간을 재전송해도 원래 프레임과 같은 기준선이어야 한다 (#415 ECG +3.9 mV 계단)."""
    st, fb = _setup()
    Pa = st.patch.arr
    Pa["attach_tick"][0] = 3                                   # 번호 0 보다 조금 늦게 붙음
    got = {}
    for t in range(0, 140):
        if t == 50:
            Pa["gw"][0] = -1
        if t == 60:
            Pa["gw"][0] = 0
        frames = fb.build(t, 1_790_000_000_000 + t * 200)
        if 0 in frames and not P.HEADER.unpack_from(frames[0], 0)[2] & P.F_KEEPALIVE:
            ver, flags, ts, seq, x = _ecg(frames[0])
            got[seq] = x
    orig = {s: got[s] for s in range(20, 40)}

    class Snd:
        frames = []
        def send_resend(self, gw, data, now, mb):
            self.frames.append(data)
    snd = Snd()
    assert fb.queue_patch_nack(0, 10000001, 20, 59)
    fb.pump_patch_nacks(140, 1_790_000_000.0, snd, 0.0)
    re = {}
    for f in snd.frames:
        ver, flags, ts, seq, x = _ecg(f)
        re[seq] = x
    for s in range(20, 40):                                    # 안정화 중에 보냈던 tick: 기준선 그대로 (잡음 성분만 다를 수 있음)
        assert abs(int(np.median(re[s])) - int(np.median(orig[s]))) < 300, (s, np.median(re[s]), np.median(orig[s]))
    for s in range(50, 60):
        got[s] = re[s]
    full = np.concatenate([got[s] for s in range(40, 140)])
    spt = got[40].size
    for b in (50, 60):
        i = (b - 40) * spt - 1
        assert abs(int(full[i + 1]) - int(full[i])) < 1500, (b, full[i], full[i + 1])
    st.close(); st.unlink()
