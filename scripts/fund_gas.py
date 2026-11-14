"""Send a little test BTC (gas) on GOAT Testnet3 from one wallet to others.

ERC-8004 registration needs gas in the OWNER, CHARGER_A and CHARGER_B wallets.

  python scripts/fund_gas.py                                   # 0.00005 BTC from GUARDIAN to OWNER, CHARGER_A, CHARGER_B
  python scripts/fund_gas.py --amount 0.0001 --to OWNER
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings, env  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="sender", default="GUARDIAN")
    p.add_argument("--to", nargs="+", default=["OWNER", "CHARGER_A", "CHARGER_B"])
    p.add_argument("--amount", type=float, default=0.00005, help="test BTC per wallet")
    args = p.parse_args()

    from eth_account import Account
    from web3 import Web3

    s = Settings.load(mode="goat")
    w3 = Web3(Web3.HTTPProvider(s.rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        sys.exit(f"Cannot reach {s.rpc_url}")
    key = env(f"{args.sender}_PRIVATE_KEY")
    if not key:
        sys.exit(f"No {args.sender}_PRIVATE_KEY in .env")
    sender = Account.from_key(key)
    value = w3.to_wei(args.amount, "ether")
    need = value * len(args.to)
    have = w3.eth.get_balance(sender.address)
    if have < need:
        sys.exit(f"{args.sender} has {w3.from_wei(have, 'ether')} test BTC, needs {w3.from_wei(need, 'ether')} plus gas")
    for role in args.to:
        rkey = env(f"{role}_PRIVATE_KEY")
        if not rkey:
            print(f"  skip {role}: no key in .env")
            continue
        to = Account.from_key(rkey).address
        tx = {"to": to, "value": value, "gas": 21000, "gasPrice": w3.eth.gas_price, "chainId": s.chain_id,
              "nonce": w3.eth.get_transaction_count(sender.address, "pending")}
        signed = sender.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        h = w3.eth.send_raw_transaction(raw)
        w3.eth.wait_for_transaction_receipt(h, timeout=180)
        print(f"  {role:<10} {to}  +{args.amount} test BTC  {s.explorer_url}/tx/0x{bytes(h).hex().removeprefix('0x')}")
    print(f"\n{args.sender} now has {w3.from_wei(w3.eth.get_balance(sender.address), 'ether')} test BTC")


if __name__ == "__main__":
    main()
