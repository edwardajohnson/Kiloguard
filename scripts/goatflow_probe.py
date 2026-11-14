"""Create ONE small test order on GOAT Flow (Testnet3) and print exactly what GOAT returns.

This tells us what the payer must do (pay-to address, amount, any data to sign) before we wire
GOAT Flow into the guardian. It moves no money: it only creates an order and reads its status.

Needs in .env: GOATFLOW_API_KEY, GOATFLOW_API_SECRET, GOATFLOW_MERCHANT_ID,
               GOATFLOW_TOKEN_SYMBOL, GOATFLOW_TOKEN_CONTRACT, GOATFLOW_TOKEN_DECIMALS, GUARDIAN_PRIVATE_KEY

Usage: python scripts/goatflow_probe.py            (orders $0.64, one 2 kWh chunk)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings, env  # noqa: E402
from kiloguard.goatflow import TESTNET3_API, GoatFlowClient  # noqa: E402


def main() -> None:
    settings = Settings.load(mode="goat")
    need = ["GOATFLOW_API_KEY", "GOATFLOW_API_SECRET", "GOATFLOW_MERCHANT_ID", "GOATFLOW_TOKEN_SYMBOL",
            "GOATFLOW_TOKEN_CONTRACT", "GUARDIAN_PRIVATE_KEY"]
    missing = [n for n in need if not env(n)]
    if missing:
        sys.exit("Missing in .env: " + ", ".join(missing))
    from eth_account import Account

    payer = Account.from_key(env("GUARDIAN_PRIVATE_KEY")).address
    decimals = int(env("GOATFLOW_TOKEN_DECIMALS", "6"))
    amount_wei = 64 * 10 ** (decimals - 2)   # $0.64 in the token's smallest unit
    client = GoatFlowClient(env("GOATFLOW_API_KEY"), env("GOATFLOW_API_SECRET"), env("GOATFLOW_MERCHANT_ID"),
                            env("GOATFLOW_API_URL", TESTNET3_API))
    order_ref = f"kiloguard-probe-{int(time.time())}"
    # The chain the merchant accepts this token on (Crypto Payments page): 48816 GOAT Testnet3, 11155111 Sepolia
    chain_id = int(env("GOATFLOW_CHAIN_ID", str(settings.chain_id)))
    print(f"Creating order {order_ref}: {amount_wei} units of {env('GOATFLOW_TOKEN_SYMBOL')} "
          f"from {payer} on chain {chain_id}\n")
    status, body = client.create_order(order_ref, chain_id, env("GOATFLOW_TOKEN_SYMBOL"),
                                       env("GOATFLOW_TOKEN_CONTRACT"), payer, amount_wei)
    print(f"HTTP {status} (402 is expected: it carries the payment terms)")
    print(json.dumps(body, indent=2))
    order_id = body.get("order_id") or body.get("data", {}).get("order_id") if isinstance(body, dict) else None
    if order_id:
        s2, b2 = client.get_order(order_id)
        print(f"\nStatus check: HTTP {s2}")
        print(json.dumps(b2, indent=2))
    print("\nPaste this whole output to Claude.")


if __name__ == "__main__":
    main()
