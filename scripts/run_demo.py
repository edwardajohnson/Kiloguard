"""Run the KiloGuard demo night.

Examples:
  python scripts/run_demo.py                            # all scenes, simulated ledger, in-process
  python scripts/run_demo.py --transport http           # same, but every charger is a real HTTP 402 server
  python scripts/run_demo.py --mode goat --scenes 1     # happy path with real payments on GOAT Testnet3
  python scripts/run_demo.py --pace 0.8                 # slow it down for the live dashboard / video
  python scripts/run_demo.py --fleet                    # then simulate 20 cars for a day
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings  # noqa: E402
from kiloguard.events import EventBus  # noqa: E402
from kiloguard.scenarios import run_night  # noqa: E402
from kiloguard.simulator import run_fleet  # noqa: E402
from kiloguard.util import SimClock  # noqa: E402
from kiloguard.world import build_world  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["sim", "goat"], help="override KILOGUARD_MODE")
    p.add_argument("--transport", choices=["local", "http"], default="local")
    p.add_argument("--external", action="store_true", help="use chargers already running (charger_server --all)")
    p.add_argument("--scenes", default="1,2,3,4,5", help="comma list; 6 is the bonus bait-and-switch")
    p.add_argument("--reasoner", choices=["naive", "backboard"], help="override REASONER")
    p.add_argument("--pace", type=float, default=0.0, help="seconds to pause after key events")
    p.add_argument("--fleet", action="store_true", help="run the 20-car fleet day after the scenes")
    p.add_argument("--fleet-only", action="store_true")
    p.add_argument("--cars", type=int, default=20)
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--no-anchor", action="store_true", help="skip writing receipt hashes on-chain")
    args = p.parse_args()

    settings = Settings.load(mode=args.mode, reasoner=args.reasoner,
                             anchor_receipts=False if args.no_anchor else None)
    clock = SimClock()
    events = EventBus(settings.events_path, clock=clock, pace=args.pace)
    events.emit("run", f"KiloGuard demo: mode={settings.mode}, transport={args.transport}, "
                f"asset={settings.asset_label}, reasoner={settings.reasoner}", level="info",
                mode=settings.mode, transport=args.transport, explorer_url=settings.explorer_url
                if settings.mode == "goat" else "")
    world = build_world(settings, events, transport=args.transport, external=args.external)
    try:
        if not args.fleet_only:
            run_night(world, [int(s) for s in args.scenes.split(",") if s.strip()])
        if args.fleet or args.fleet_only:
            if settings.mode != "sim":
                print("Skipping the fleet day: it runs on the simulated ledger only.")
            else:
                run_fleet(world, cars=args.cars, hours=args.hours)
    finally:
        world.stop()
    print(f"\nEvents written to {settings.events_path}")


if __name__ == "__main__":
    main()
