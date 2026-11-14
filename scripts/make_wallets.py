"""Create the six hackathon wallets and write their keys to .env (never commit .env).

  OWNER      fleet owner: signs the car's authorisation; funds the guardian
  GUARDIAN   the car's wallet: the only key that pays chargers. Needs test BTC for payments and gas.
  VEHICLE    the car's identity key: signs handshakes. Needs no funds.
  CHARGER_A  operator of Dufferin & King (honest). Receives payments.
  CHARGER_B  operator of the four attack-scene chargers. Receives payments.
  ATTACKER   the cloned charger's key and the redirect target.

Usage: python scripts/make_wallets.py            (refuses to overwrite existing keys)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROLES = ["OWNER", "GUARDIAN", "VEHICLE", "CHARGER_A", "CHARGER_B", "ATTACKER"]


def main() -> None:
    try:
        from eth_account import Account
    except ImportError:
        sys.exit("Install requirements first: pip install -r requirements.txt")
    env_path = ROOT / ".env"
    existing = env_path.read_text() if env_path.exists() else (ROOT / ".env.example").read_text()
    lines = existing.splitlines()
    have = {ln.split("=", 1)[0] for ln in lines if "=" in ln and ln.split("=", 1)[1].strip()}
    created = []
    for role in ROLES:
        key_name = f"{role}_PRIVATE_KEY"
        if key_name in have:
            continue
        acct = Account.create()
        key = "0x" + bytes(acct.key).hex()
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith(key_name + "="):
                lines[i] = f"{key_name}={key}"
                replaced = True
        if not replaced:
            lines.append(f"{key_name}={key}")
        created.append((role, acct.address))
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)
    if not created:
        print("All six keys already exist in .env; nothing changed.")
    for role, address in created:
        print(f"{role:<10} {address}")
    print("\nKeys saved to .env (permissions 600). Store the same keys in your shared Bitwarden note.")
    print("Fund GUARDIAN (and OWNER) from https://bridge.testnet3.goat.network/faucet, then run:")
    print("  python scripts/goat_check.py")


if __name__ == "__main__":
    main()
