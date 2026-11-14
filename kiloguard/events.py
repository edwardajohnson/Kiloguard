"""Event log. Every step of the payment loop becomes one JSON line.

The console shows a coloured, human-readable line; the dashboard tails the JSONL file.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

COLOURS = {
    "ok": "\033[32m", "deny": "\033[31m", "warn": "\033[33m", "money": "\033[36m",
    "scene": "\033[1;35m", "info": "\033[0m", "ai": "\033[34m",
}
ICONS = {"ok": "✔", "deny": "✖", "warn": "!", "money": "$", "scene": "▶", "info": "·", "ai": "◆"}
RESET = "\033[0m"
PACED = {"ok", "deny", "money", "scene", "warn", "ai"}


class EventBus:
    def __init__(self, path: Path | None = None, echo: bool = True, clock=None, pace: float = 0.0, fresh: bool = True):
        self.events: list[dict] = []
        self.path = path
        self.echo = echo
        self.clock = clock
        self.pace = pace
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if fresh:
                path.write_text("")

    def emit(self, kind: str, msg: str = "", level: str = "info", **data) -> dict:
        with self._lock:
            ts = self.clock.now().isoformat() if self.clock else time.strftime("%Y-%m-%dT%H:%M:%S")
            event = {"seq": len(self.events), "ts": ts, "kind": kind, "level": level, "msg": msg, **data}
            self.events.append(event)
            if self.path:
                with self.path.open("a") as fh:
                    fh.write(json.dumps(event, default=str) + "\n")
        if self.echo and msg:
            colour = COLOURS.get(level, "") if sys.stdout.isatty() else ""
            reset = RESET if colour else ""
            clock_txt = ts[11:16] if len(ts) >= 16 else ts
            print(f"{colour}[{clock_txt}] {ICONS.get(level, '·')} {msg}{reset}", flush=True)
        if self.pace and level in PACED:
            time.sleep(self.pace * (2.5 if level == "scene" else 1.0))
        return event

    def of(self, kind: str | None = None, **match) -> list[dict]:
        out = []
        for e in self.events:
            if kind and e["kind"] != kind:
                continue
            if all(e.get(k) == v for k, v in match.items()):
                out.append(e)
        return out
