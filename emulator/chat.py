"""Chat channel between the emulator, the router and whoever is watching the GUI.

One room, everybody sees everything.  Messages are kept in memory for fast fan-out and
appended to <data>/runtime/chat.jsonl so a restart does not lose the conversation -- the
emulator restarts on every deploy and a channel that forgets on restart is not much of a
channel.

Delivery is pull-based: `since(seq)` returns everything newer than a caller's sequence
number, and the WebSocket endpoint polls it.  That keeps a REST POST (which runs in the
thread pool) and the WebSocket (which runs on the event loop) from having to hand objects
across threads; chat traffic is a few messages a minute, so the poll costs nothing.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

KEEP = 500                     # messages held in memory / replayed to a new client
MAX_TEXT = 4000                # per message; longer is truncated, not rejected
MAX_FILE = 4 * 1024 * 1024     # chat.jsonl is rotated to .1 past this


class ChatHub:
    def __init__(self, path: Path, keep: int = KEEP):
        self.path = Path(path)
        self.keep = keep
        self.msgs: deque[dict] = deque(maxlen=keep)
        self.seq = 0
        self.lock = threading.RLock()
        self._load()

    # ---------------------------------------------------------------- storage
    def _load(self) -> None:
        """Replay the tail of the log so restarts keep the conversation."""
        try:
            if not self.path.exists():
                return
            with open(self.path, "r", encoding="utf-8") as f:
                tail = deque(f, maxlen=self.keep)
            for line in tail:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if isinstance(m, dict) and "seq" in m:
                    self.msgs.append(m)
                    self.seq = max(self.seq, int(m["seq"]))
        except OSError:
            pass

    def _append(self, m: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_FILE:
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        except OSError:
            pass                                   # a full disk must not break the channel

    # ---------------------------------------------------------------- api
    def post(self, sender: str, text: str, kind: str = "msg") -> dict:
        sender = (str(sender or "anon").strip() or "anon")[:40]
        text = str(text or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError("빈 메시지는 보낼 수 없습니다")
        with self.lock:
            self.seq += 1
            m = {"seq": self.seq, "t": round(time.time(), 3), "from": sender, "kind": kind, "text": text}
            self.msgs.append(m)
        self._append(m)
        return m

    def since(self, seq: int = 0, limit: int = KEEP) -> list[dict]:
        with self.lock:
            return [m for m in self.msgs if m["seq"] > seq][-limit:]

    def state(self) -> dict:
        with self.lock:
            return {"seq": self.seq, "held": len(self.msgs), "file": str(self.path)}
