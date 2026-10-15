"""패치 재전송 (프로토콜 v4): 2 바이트 패킷 번호, 끊긴 구간 기록, 번호 → 원래 시각 역산, 다시 만든 파형이 앞뒤 정상 프레임과 이어짐."""
import numpy as np

from emulator.config import CH_ECG
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
