"""Live dashboard: a map of Toronto, the guardian's decisions, payments and receipts.

Start it, open http://127.0.0.1:8000 and press "Start demo" (the run is started for you), or run the demo
yourself in another terminal; the dashboard tails runs/events.jsonl either way:
    python dashboard/server.py
    python scripts/run_demo.py --scenes 1,2,3,4,5 --pace 2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import FileResponse, JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from kiloguard.config import ROOT, Settings, load_json  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"

# The runs the dashboard can start. Fixed presets only: the page cannot pass arbitrary arguments.
PRESETS = {
    "sim": {"label": "Simulation (about 2:30)",
            "args": ["--mode", "sim", "--rail", "direct", "--pace", "2"]},
    "sim_backboard": {"label": "Simulation + Backboard AI",
                      "args": ["--mode", "sim", "--rail", "direct", "--reasoner", "backboard", "--pace", "1.5"]},
    "goat": {"label": "Live on GOAT (GOAT Flow, USDC) + Backboard",
             "args": ["--mode", "goat", "--rail", "goatflow", "--reasoner", "backboard", "--pace", "0.5"]},
    "goat_plain": {"label": "Live on GOAT (GOAT Flow, USDC), simulated AI",
                   "args": ["--mode", "goat", "--rail", "goatflow", "--reasoner", "naive", "--pace", "0.5"]},
}


class Runner:
    """Starts scripts/run_demo.py in the background, one run at a time."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.preset = ""
        self.started = 0.0
        self.user_stopped = False
        self.log_path = ROOT / "runs" / "run_console.log"

    def status(self) -> dict:
        running = self.proc is not None and self.proc.poll() is None
        code = None if self.proc is None or running else self.proc.returncode
        tail = ""
        if code not in (None, 0) and not self.user_stopped and self.log_path.exists():
            tail = "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-6:])
        return {"running": running, "preset": self.preset, "elapsed_s": round(time.time() - self.started) if self.started else 0,
                "returncode": code, "error_tail": tail,
                "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]}

    def start(self, preset: str, bonus: bool) -> dict:
        if preset not in PRESETS:
            return {"ok": False, "error": "unknown preset"}
        if self.proc is not None and self.proc.poll() is None:
            return {"ok": False, "error": "a run is already going; stop it first"}
        scenes = "1,2,3,4,5,6" if bonus else "1,2,3,4,5"
        cmd = [sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--scenes", scenes, *PRESETS[preset]["args"]]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
        self.preset, self.started, self.user_stopped = preset, time.time(), False
        print("Started:", " ".join(cmd[1:]), flush=True)
        return {"ok": True, "command": "python " + " ".join(c if c != str(ROOT / "scripts" / "run_demo.py")
                                                            else "scripts/run_demo.py" for c in cmd[1:])}

    def stop(self) -> dict:
        if self.proc is not None and self.proc.poll() is None:
            self.user_stopped = True
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return {"ok": True}


def make_app(events_path: Path, runner: Runner | None = None) -> Starlette:
    runner = runner or Runner()

    async def run_status(request: Request):
        return JSONResponse(runner.status())

    async def run_start(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return JSONResponse(runner.start(str(body.get("preset", "sim")), bool(body.get("bonus", False))))

    async def run_stop(request: Request):
        return JSONResponse(runner.stop())

    async def index(request: Request):
        return FileResponse(STATIC / "index.html")

    async def events(request: Request):
        since = int(request.query_params.get("since", 0))
        out = []
        if events_path.exists():
            for line in events_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("seq", 0) >= since:
                    out.append(event)
        # a new run restarts seq at 0: tell the page to reset
        reset = bool(out) and since > 0 and out[0].get("seq", 0) < since
        if not out and since > 0 and events_path.exists():
            lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
            reset = len(lines) < since
        return JSONResponse({"events": out[-2000:], "reset": reset})

    async def chargers(request: Request):
        cfg = load_json("chargers.json")
        return JSONResponse({"vehicle": cfg["vehicle"], "chargers": cfg["chargers"]})

    async def verify(request: Request):
        from starlette.concurrency import run_in_threadpool

        from kiloguard.verify import load_events, verify_session

        sid = request.query_params.get("session_id", "")
        result = await run_in_threadpool(verify_session, load_events(events_path), sid, Settings.load().rpc_url)
        return JSONResponse(result)

    return Starlette(routes=[
        Route("/", index),
        Route("/api/events", events),
        Route("/api/chargers", chargers),
        Route("/api/verify", verify),
        Route("/api/run", run_status, methods=["GET"]),
        Route("/api/run/start", run_start, methods=["POST"]),
        Route("/api/run/stop", run_stop, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    settings = Settings.load()
    print(f"KiloGuard dashboard on http://{args.host}:{args.port}  (reading {settings.events_path.relative_to(ROOT)})")
    uvicorn.run(make_app(settings.events_path), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
