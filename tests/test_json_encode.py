"""큰 API 응답 직렬화 (2026-10-26): 항목별로 나눠 써도 json.dumps 와 같은 바이트, 다른 스레드(월드 1 Hz)를 오래 막지 않음."""
import json
import threading
import time

from emulator.app import _json_text

ROW = {"id": 1, "name": "홍길동", "admission": {"ward": "W1", "bed": "301-1", "devices": [1, 2, {"k": None}]}, "battery": 87.5,
       "history": [{"t": 1.5, "e": "입원"}] * 5, "flag": True, "none": None}


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str)


def test_same_bytes_as_json_dumps():
    for obj in ({"total": 3, "patients": [ROW] * 3, "next_offset": None},
                {"total": 2000, "patients": [dict(ROW, id=i) for i in range(2000)], "next_offset": 2000, "q": [1] * 300},
                [ROW] * 1000, {}, [], {"a": []}, "x", 5):
        assert _json_text(obj) == _dumps(obj)


def test_big_list_does_not_starve_other_threads():
    obj = {"total": 20000, "patients": [dict(ROW, id=i) for i in range(20000)], "next_offset": None}
    worst, stop = [0.0], [False]

    def tick():
        t = time.perf_counter()
        while not stop[0]:
            time.sleep(0.005)
            n = time.perf_counter()
            worst[0] = max(worst[0], n - t - 0.005)
            t = n
    th = threading.Thread(target=tick)
    th.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    _json_text(obj)
    whole = time.perf_counter() - t0
    stop[0] = True
    th.join()
    assert worst[0] < max(0.1, whole / 3), (worst[0], whole)       # 한 번에 다 막지 않는다
