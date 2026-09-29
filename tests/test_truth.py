"""정답 전용 벤치마크 패치: TRUTH 레코드(채널 11) 부호화·복호화, 검증기 통과, 등급 세트 구성."""
import numpy as np

from emulator.config import CH_ECG, CH_TRUTH, CHANNELS
from emulator.runtime import protocol as P
from emulator.runtime.verify import StreamChecker, decode_records
from emulator.runtime.truthbench import PLANS, GRADES, grade_sets, TRUTH_MAX
from emulator.signals.rhythms import RHYTHMS, BEAT_KINDS


def test_truth_block_roundtrip():
    b = P.truth_block(P.RHYTHM_CODE_ID["afib"], P.RHYTHM_CODE_ID["nsr"], P.T_SWITCH | P.T_ARTIFACT | P.T_PACED, 73, 120, 88,
                      [(12, 0), (140, 2), (300, 3)], [(11, 1), (139, 1)])
    d = P.parse_truth(b)
    assert d["ver"] == 1 and d["rhythm"] == "afib" and d["rhythm_prev"] == "nsr" and d["switching"] and d["artifact"] and d["paced"] and not d["lead_off"]
    assert d["hr"] == 88 and abs(d["art_level"] - 0.73) < 1e-9 and abs(d["noise_level"] - 0.12) < 1e-9
    assert [(x["offset"], x["kind"], x["aami"]) for x in d["beats"]] == [(12, "N", "N"), (140, "V", "V"), (300, "Vp", "Q")]
    assert [(x["offset"], x["chamber"]) for x in d["pace"]] == [(11, 1), (139, 1)]
    e = P.parse_truth(P.truth_block(P.RHYTHM_CODE_ID["vfib"], 255, P.T_LEAD_OFF | P.T_NO_BEATS, 0, 0, 0, [], []))
    assert e["rhythm"] == "vfib" and e["rhythm_prev"] is None and e["lead_off"] and e["beats_unavailable"] and e["beats"] == [] and e["pace"] == []


def test_truth_record_passes_the_stream_checker():
    ecg = np.zeros(50, dtype="<i2").tobytes()
    import struct
    rec_ecg = struct.pack("<IIIBBbB", 0xF0000, 900001, 7, 0, 100, -55, 1) + struct.pack("<BBH", CH_ECG, P.DTYPE_CODE["int16"], 50) + ecg
    blob = P.truth_block(P.RHYTHM_CODE_ID["pvc"], 255, 0, 0, 0, 72, [(10, 0), (40, 2)], [])
    rec_tr = P.truth_record(0xF0000, 900001, 7, 0, 100, -55, blob)
    recs = list(decode_records(memoryview(rec_ecg + rec_tr), 0, 2))
    assert len(recs) == 2 and recs[1][0] == 0xF0000 and recs[1][2] == 7 and CH_TRUTH in recs[1][6]
    dt, n, data = recs[1][6][CH_TRUTH]
    assert dt == P.DTYPE_CODE["uint8"] and n == len(blob) and P.parse_truth(bytes(data))["beats"][1]["kind"] == "V"
    fr = P.frame(1778, 1, 1_790_000_000_000, 2, rec_ecg + rec_tr, 0)
    c = StreamChecker()
    c.feed(fr)
    sm = c.summary()
    bad = {k: v for k, v in sm.items() if k not in ("frames", "gateways") and v}
    assert sm["frames"] == 1 and not bad, sm
    assert CHANNELS[CH_TRUTH]["kind"] == "truth"


def test_grade_sets_are_consistent():
    gs = grade_sets()
    assert [gs[g]["patches"] for g in ("basic", "standard", "precise")] == [16, 32, 64] and TRUTH_MAX == 64
    for g, (label, n) in GRADES.items():
        assert sum(PLANS[g].values()) == n == gs[g]["patches"] and gs[g]["label"] == label
        assert set(PLANS[g]) <= set(RHYTHMS) and all(k >= 1 for k in PLANS[g].values())
    assert set(PLANS["precise"]) == set(RHYTHMS)                                  # 정밀: 모든 리듬 종류
    assert set(PLANS["basic"]) <= set(PLANS["standard"]) <= set(PLANS["precise"])    # 등급이 오를수록 포함 관계
    assert len(P.RHYTHM_CODES) == len(RHYTHMS) and len(BEAT_KINDS) == 14 and set(P.AAMI.values()) == {"N", "S", "V", "F", "Q"} and set(P.AAMI) == set(BEAT_KINDS)


def test_score_beats_se_ppv_and_exclusions():
    from emulator.runtime.truthbench import score_beats
    from emulator.runtime.protocol import T_LEAD_OFF, T_ARTIFACT
    afib = P.RHYTHM_CODE_ID["afib"]
    truth = [(1000, 1, 0, afib, 0), (1800, 1, 0, afib, 0), (2600, 1, 2, afib, T_ARTIFACT), (3400, 1, 0, afib, T_LEAD_OFF), (4200, 1, 0, afib, 0),
             (1000, 2, 0, afib, 0), (1900, 2, 0, afib, 0)]
    det = {1: [1010, 1790, 3000, 3410, 4190, 5000], 2: [1000, 1900]}
    r = score_beats(truth, det, 150)
    o = r["overall"]
    assert (o["tp"], o["fp"], o["fn"], o["ignored_truth"], o["ignored_det"]) == (5, 2, 1, 1, 1)         # 3000 은 2600(V) 과 400 ms 차 → FN+FP; 3400 리드오프 제외
    assert o["se_pct"] == round(100 * 5 / 6, 2) and o["ppv_pct"] == round(100 * 5 / 7, 2)
    assert r["patches"][2]["se_pct"] == 100.0 and r["patches"][2]["ppv_pct"] == 100.0
    assert r["by_condition"]["artifact"]["fn"] == 1 and r["by_beat_kind"]["V"]["fn"] == 1 and r["by_rhythm"]["afib"]["tp"] == 5
    assert r["fn_examples"][0]["ts_ms"] == 2600 and {e["ts_ms"] for e in r["fp_examples"]} == {3000, 5000}
    r2 = score_beats(truth, det, 150, exclude=())                                # 제외 없음: 리드오프 박동도 채점
    assert r2["overall"]["tp"] == 6 and r2["overall"]["ignored_truth"] == 0
    assert score_beats([], {1: [5]}, 150)["overall"]["se_pct"] is None
