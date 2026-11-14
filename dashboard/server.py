"""Live dashboard: a map of Toronto, the guardian's decisions, payments and receipts.

It tails runs/events.jsonl, so start it first, then run the demo in another terminal:
    python dashboard/server.py            # http://127.0.0.1:8000
    python scripts/run_demo.py --pace 0.8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import FileResponse, JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

from kiloguard.config import ROOT, Settings, load_json  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"


def make_app(events_path: Path) -> Starlette:
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
