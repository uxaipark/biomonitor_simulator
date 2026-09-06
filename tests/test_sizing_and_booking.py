"""Hospital sized to the patient count; exam-room booking never over-books a room; RW lock semantics."""
import math
import threading
import time
import types

import pytest

from emulator.hospital import layout as L
from emulator.runtime.engine import RWLock
from emulator.runtime.world import World


@pytest.mark.parametrize("patients", [50, 100, 200, 500, 1000, 2000])
def test_hospital_size_follows_patients(patients):
    beds_target = max(20, math.ceil(patients / 0.8))
    lay = L.generate(beds_target, 20240905, "auto", None, 3)
    h = lay["hospital"]
    assert h["n_buildings"] <= 3
    assert abs(h["beds"] - beds_target) <= max(8, 0.06 * beds_target), (h["beds"], beds_target)      # within a room or two of the target
    per_b = {}
    for f in lay["floors"]:
        if f["kind"] == "ward":
            per_b[f["building_idx"]] = per_b.get(f["building_idx"], 0) + 1
    if h["n_buildings"] < 3:                                     # a new wing is only added once a building holds ~12 ward floors
        assert max(per_b.values()) <= 12
    assert 0.7 <= patients / h["beds"] <= 0.9                    # ~80 % occupancy, never over-full


def _stub_world(n_beds: int = 500):
    """A World shell with just what the booking code needs (no bank, no DB)."""
    w = World.__new__(World)
    rooms = [{"idx": 0, "name": "MRI실", "building_idx": 0, "floor": 1}, {"idx": 1, "name": "채혈실", "building_idx": 0, "floor": 1}]
    beds = [{"room_idx": 0} for _ in range(n_beds)]
    w.hospital = types.SimpleNamespace(rooms=rooms, beds=beds)
    w.exam_book = {}
    w.exam_load = __import__("collections").Counter()
    return w


def test_exam_booking_respects_capacity():
    w = _stub_world(500)
    key = "0:MRI실"
    cap = w.exam_capacity(key)
    assert cap == 1                                              # 500 beds -> 1x base stations
    t0 = time.mktime(time.strptime("2026-09-07 09:00", "%Y-%m-%d %H:%M"))
    starts = [w._book_exam(key, 40 * 60, t0) for _ in range(6)]
    # no two 40-minute MRI slots overlap when the room has one station
    for a in starts:
        assert sum(1 for b in starts if abs(a - b) < 40 * 60) == 1
    # every slot inside opening hours
    for s in starts:
        assert 8 <= time.localtime(s).tm_hour < 17
    # a bigger hospital gets more stations and can overlap
    w2 = _stub_world(2000)
    assert w2.exam_capacity(key) == 4
    s2 = [w2._book_exam(key, 40 * 60, t0) for _ in range(4)]
    assert len(set(s2)) == 1                                     # four scans start together on four stations


def test_rwlock_readers_share_writer_excludes():
    lock = RWLock()
    state = {"writing": False, "readers": 0, "bad": 0, "max_readers": 0}

    def reader():
        for _ in range(200):
            with lock.read():
                state["readers"] += 1
                state["max_readers"] = max(state["max_readers"], state["readers"])
                if state["writing"]:
                    state["bad"] += 1
                time.sleep(0.0005)
                state["readers"] -= 1

    def writer():
        for _ in range(20):
            with lock.write():
                if state["readers"]:
                    state["bad"] += 1
                state["writing"] = True
                time.sleep(0.002)
                state["writing"] = False

    th = [threading.Thread(target=reader) for _ in range(4)] + [threading.Thread(target=writer)]
    for t in th:
        t.start()
    for t in th:
        t.join(timeout=30)
    assert state["bad"] == 0
    assert state["max_readers"] >= 2                             # readers really ran concurrently
