"""Register KiloGuard's agents on GOAT Testnet3's ERC-8004 identity registry (no AgentKit needed).

  Unit 07 (vehicle)                registered by OWNER      (the fleet owns the car's identity)
  Dufferin & King                  registered by CHARGER_A
  King West, Queen West,
  Fort York, Spadina               registered by CHARGER_B
  Front St (the clone)             never registered: it borrows Dufferin's identity

Each registering wallet needs a little test BTC for gas. The agent ids are written back into
config/chargers.json, then set IDENTITY=erc8004 in .env.

Usage:
  python scripts/register_agents.py --dry-run     # show what would be registered
  python scripts/register_agents.py               # register (skips agents that already have an id)
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import CONFIG_DIR, Settings  # noqa: E402
from kiloguard.crypto import make_signer  # noqa: E402

REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"
KEY_ROLE = {"A": "CHARGER_A", "B": "CHARGER_B"}


def registration_uri(name: str, description: str, endpoint: str | None, x402: bool) -> str:
    doc = {"type": REGISTRATION_TYPE, "name": name, "description": description, "active": True,
           "x402Support": x402, "supportedTrust": ["reputation"],
           "services": [{"name": "kiloguard-charger", "endpoint": endpoint, "version": "0.1"}] if endpoint else []}
    return "data:application/json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()


def write_config(cfg: dict) -> None:
    """Rewrites chargers.json keeping one charger per line, so diffs stay readable."""
    lines = ["{", f'  "_note": {json.dumps(cfg["_note"])},', f'  "vehicle": {json.dumps(cfg["vehicle"])},',
             '  "chargers": [']
    lines += [f"    {json.dumps(c)}" + ("," if i < len(cfg["chargers"]) - 1 else "")
              for i, c in enumerate(cfg["chargers"])]
    lines += ["  ]", "}"]
    (CONFIG_DIR / "chargers.json").write_text("\n".join(lines) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    settings = Settings.load(mode="goat")
    cfg = json.loads((CONFIG_DIR / "chargers.json").read_text())
    plan = []
    v = cfg["vehicle"]
    plan.append((v, "OWNER", registration_uri(f"KiloGuard {v['name']}", "Autonomous robotaxi that buys its own "
                                              "charging; payments signed only by its guardian.", None, True)))
    for c in cfg["chargers"]:
        if c.get("key") in KEY_ROLE and c.get("behaviour") != "clone":
            plan.append((c, KEY_ROLE[c["key"]], registration_uri(
                c["name"], "KiloGuard demo charger: x402-style metered charging, 2 kWh per payment.",
                f"http://127.0.0.1:{c['port']}", True)))

    print(f"Identity registry {settings.erc8004_registry} on chain {settings.chain_id}\n")
    for entry, role, _uri in plan:
        done = entry.get("erc8004_agent_id")
        print(f"  {entry['name']:<26} by {role:<10} {'already agent #' + str(done) if done is not None else 'to register'}")
    if args.dry_run:
        return

    from kiloguard.identity import Erc8004Registry
    from kiloguard.ledger import GoatLedger

    ledger = GoatLedger(settings)
    registry = Erc8004Registry(ledger, settings.erc8004_registry, settings.erc8004_reputation)
    for entry, role, uri in plan:
        if entry.get("erc8004_agent_id") is not None:
            continue
        key = settings.private_key(role)
        if not key:
            print(f"  skip {entry['name']}: no {role}_PRIVATE_KEY in .env")
            continue
        signer = make_signer(role.lower(), key)
        try:
            agent_id, tx = registry.register_agent(signer, uri)
        except Exception as exc:
            print(f"  FAILED {entry['name']}: {exc}")
            continue
        entry["erc8004_agent_id"] = agent_id
        write_config(cfg)   # save after each success, so a later failure loses nothing
        print(f"  {entry['name']} is ERC-8004 agent #{agent_id}  {ledger.tx_url(tx)}")
    print("\nDone. Set IDENTITY=erc8004 in .env, then run: python scripts/run_demo.py --mode goat --scenes 1,2")


if __name__ == "__main__":
    main()
