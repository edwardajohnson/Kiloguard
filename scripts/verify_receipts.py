"""Verify every charging receipt from the last demo run (or one session).

Usage:
  python scripts/verify_receipts.py
  python scripts/verify_receipts.py --session s-1a2b3c4d5e6f
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings  # noqa: E402
from kiloguard.verify import load_events, sessions_with_receipts, verify_session  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session")
    args = p.parse_args()
    settings = Settings.load()
    events = load_events(settings.events_path)
    sessions = [args.session] if args.session else sessions_with_receipts(events)
    if not sessions:
        sys.exit(f"No receipts in {settings.events_path}. Run the demo first.")
    all_ok = True
    for sid in sessions:
        result = verify_session(events, sid, settings.rpc_url)
        all_ok &= result["ok"]
        print(f"\n{'VERIFIED' if result['ok'] else 'FAILED'}  {sid}  {result.get('charger', '')}")
        for s in result["steps"]:
            print(f"   {'✔' if s['ok'] else '✖'} {s['text']}")
        if result.get("anchor_url"):
            print(f"   {result['anchor_url']}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
