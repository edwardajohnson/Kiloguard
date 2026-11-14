"""Serves a charger agent over real HTTP (Starlette + Uvicorn).

Run every charger as its own server (useful in goat mode, where the ledger is the shared chain):
    python -m kiloguard.charger_server --all
"""
from __future__ import annotations

import argparse
import json
import threading

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def make_app(agent) -> Starlette:
    async def endpoint(request: Request) -> JSONResponse:
        body = None
        if request.method == "POST":
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        status, headers, payload = agent.handle(request.method, request.url.path, dict(request.query_params),
                                                dict(request.headers), body)
        return JSONResponse(payload, status_code=status, headers=headers)

    return Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST"])])


def main() -> None:
    import uvicorn

    from .config import Settings
    from .events import EventBus
    from .world import build_world

    parser = argparse.ArgumentParser(description="Serve KiloGuard chargers over HTTP")
    parser.add_argument("--all", action="store_true", help="serve every charger on its configured port")
    parser.add_argument("--charger", help="serve one charger by id")
    args = parser.parse_args()

    settings = Settings.load()
    if settings.mode != "goat":
        raise SystemExit("Separate charger processes need a shared ledger: set KILOGUARD_MODE=goat. "
                         "In sim mode use: python scripts/run_demo.py --transport http")
    world = build_world(settings, EventBus(None), transport="local")
    keys = list(world.chargers) if args.all else [args.charger]
    servers = []
    for key in keys:
        agent = world.chargers[key]
        port = agent.cfg["port"]
        server = uvicorn.Server(uvicorn.Config(make_app(agent), host="0.0.0.0", port=port, log_level="info"))
        servers.append(threading.Thread(target=server.run))
        print(f"{agent.name} ({agent.behaviour}) on http://127.0.0.1:{port}")
    for t in servers:
        t.start()
    for t in servers:
        t.join()


if __name__ == "__main__":
    main()
