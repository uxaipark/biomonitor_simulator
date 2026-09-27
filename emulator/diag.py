"""메모리 진단: 어디가 자라는지 — 프로세스 RSS, 오래 사는 자료구조 크기, gc 타입별 객체 수, tracemalloc 증분.

GET /api/v1/debug/memory            구조 크기·RSS·타입별 객체 수
GET /api/v1/debug/memory?trace=start  tracemalloc 시작 (그때부터의 할당만 추적; 운영 중 켜도 됨, 10~20 % 느려짐)
GET /api/v1/debug/memory?trace=top    시작 시점 대비 증가분 상위 30 (파일:줄)
GET /api/v1/debug/memory?trace=stop
"""
from __future__ import annotations

import collections
import gc
import os
import sys
import time
import tracemalloc

_base_snap = None
_started_at = 0.0


def rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _n(x) -> int:
    try:
        return len(x)
    except TypeError:
        return -1


def structures(engine, chat=None) -> dict:
    w = engine.world
    out: dict = {}
    out["world"] = {"admitted": _n(w.admitted), "patches": _n(w.patches), "patch_registry": _n(w.patch_registry), "profiles": _n(w.profiles),
                    "patch_history_total": sum(len(p.get("patch_history") or []) for p in w.profiles), "exams_total": sum(len(r.get("exams") or []) for r in w.admitted.values()),
                    "log_buf": _n(w.log.buf), "log_pending": _n(w.log.pending), "exam_book_intervals": sum(len(v) for v in w.exam_book.values()),
                    "exam_load": _n(w.exam_load), "net_events": _n(w.net_events), "gw_state": _n(w.gw_state), "meta_cache": _n(w._meta_cache),
                    "meta_delta_log": _n(w._meta_delta_log), "meta_delta": _n(w._meta_delta), "meta_versions": _n(w.meta_versions), "counters": _n(w.counters),
                    "db_pending": _n(getattr(w.db, "_pending", [])), "db_pending_pe": _n(getattr(w.db, "_pending_pe", [])), "db_pending_lb": _n(getattr(w.db, "_pending_lb", [])),
                    "rec_keys_avg": (sum(len(r) for r in w.admitted.values()) / max(1, len(w.admitted)))}
    real = getattr(w, "real", None)
    if real:
        out["realism"] = {"labels_open": _n(real.labels_open), "label_seen": _n(real.label_seen), "gw_seen": _n(real.gw_seen), "win": sum(len(v) for v in real.win.values()),
                          "tagged": _n(real.tagged), "dev": _n(real.dev), "recording_items": _n(real.recording["items"]) if real.recording else 0, "v2r": _n(real._v2r)}
    ft = getattr(w, "ft", None)
    if ft:
        out["fieldtest"] = {"active": _n(ft.active), "history": _n(ft.history)}
    rs = getattr(w, "rsig", None)
    if rs:
        out["realsig"] = {k: _n(v) for k, v in vars(rs).items() if isinstance(v, (list, dict, set))}
    try:
        from .emrsim import sim as emsim, link as emlink
        sims = dict(emsim._SIMS)
        ls = emlink.linked_sim()
        if ls is not None:
            sims["(linked)" + ls.id] = ls
        out["emrsim"] = {sid: {"people": _n(s.people), "person_by_key": _n(s.person_by_key), "encounters": _n(s.encounters), "events": _n(s.events), "heap": _n(s.heap),
                               "inbound": _n(s.inbound), "fhir_store": _n(s.fhir_store), "fhir_obs_cache": _n(s.fhir_obs_cache), "tokens": _n(s.tokens), "log": _n(s.log),
                               "documents": _n(s.documents), "athena_cursors": _n(s.athena_changed_cursor), "loc_index": _n((s.__dict__.get("_loc_index") or (0, {}))[1])}
                         for sid, s in sims.items()}
    except Exception as e:
        out["emrsim"] = {"error": str(e)}
    if chat is not None:
        out["chat"] = {"msgs": _n(getattr(chat, "msgs", [])), "clients": _n(getattr(chat, "clients", []))}
    return out


def malloc_info() -> dict:
    """glibc 힙 통계(mallinfo2): 아레나 수·시스템 바이트·사용 중·놀고 있는 바이트.  tracemalloc 이 못 보는 C 힙 조각화를 여기서 본다.
    2026-09-27 조사: 운영에서 70 시간 동안 RSS 259→585 MB 로 늘었는데 파이썬 객체 증가는 수 MB 뿐이었고, 스레드별 malloc 아레나
    (uvicorn 스레드풀·메타 기록·EMR 동기화 스레드)가 큰 JSON 문자열을 할당·해제하며 조각난 것이 원인 — MALLOC_ARENA_MAX 로 막는다."""
    import ctypes

    class MI(ctypes.Structure):
        _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.mallinfo2.restype = MI
        m = libc.mallinfo2()
        return {"arena_kb": m.arena // 1024, "mmap_kb": m.hblkhd // 1024, "mmap_chunks": m.hblks, "in_use_kb": m.uordblks // 1024, "free_kb": m.fordblks // 1024,
                "trimmable_kb": m.keepcost // 1024, "arena_max_env": os.environ.get("MALLOC_ARENA_MAX")}
    except (OSError, AttributeError) as e:
        return {"error": str(e)}


def malloc_trim() -> dict:
    """놀고 있는 힙 페이지를 OS 에 돌려준다(malloc_trim(0), 모든 아레나).  전후 RSS 를 돌려준다."""
    import ctypes
    before = rss_mb()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError) as e:
        return {"error": str(e)}
    return {"rss_before_mb": round(before, 1), "rss_after_mb": round(rss_mb(), 1)}


def type_counts(top: int = 30) -> list:
    c = collections.Counter(type(o).__name__ for o in gc.get_objects())
    return c.most_common(top)


def report(engine, chat=None, trace: str | None = None, top: int = 30, trim: bool = False) -> dict:
    global _base_snap, _started_at
    out = {"rss_mb": round(rss_mb(), 1), "uptime_s": round(time.time() - engine.started_at, 0) if getattr(engine, "started_at", None) else None,
           "threads": _n(sys._current_frames()), "malloc": malloc_info(),
           "gc_objects": len(gc.get_objects()), "gc_counts": gc.get_count(), "structures": structures(engine, chat), "types_top": type_counts(top)}
    if trim:
        out["trim"] = malloc_trim()
    if trace == "start":
        tracemalloc.start(8)
        gc.collect()
        _base_snap = tracemalloc.take_snapshot()
        _started_at = time.time()
        out["trace"] = "started"
    elif trace == "stop":
        tracemalloc.stop(); _base_snap = None
        out["trace"] = "stopped"
    elif trace == "top":
        if not tracemalloc.is_tracing() or _base_snap is None:
            out["trace"] = "not tracing (use ?trace=start first)"
        else:
            gc.collect()
            snap = tracemalloc.take_snapshot()
            cur, peak = tracemalloc.get_traced_memory()
            rows = []
            for st in snap.compare_to(_base_snap, "lineno")[:top]:
                fr = st.traceback[0]
                rows.append({"file": fr.filename.replace(os.path.dirname(os.path.dirname(__file__)) + "/", ""), "line": fr.lineno,
                             "size_kb": round(st.size / 1024, 1), "size_diff_kb": round(st.size_diff / 1024, 1), "count": st.count, "count_diff": st.count_diff})
            out["trace"] = {"since_s": round(time.time() - _started_at), "traced_mb": round(cur / 1048576, 1), "peak_mb": round(peak / 1048576, 1), "top": rows}
    return out
