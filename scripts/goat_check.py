"""Check the GOAT Testnet3 setup: RPC, chain id, wallet balances and the payment asset.

Usage: python scripts/goat_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings, env  # noqa: E402

ROLES = ["OWNER", "GUARDIAN", "VEHICLE", "CHARGER_A", "CHARGER_B", "ATTACKER"]


def main() -> None:
    try:
        from eth_account import Account
        from web3 import Web3
    except ImportError:
        sys.exit("Install requirements first: pip install -r requirements.txt")
    s = Settings.load(mode="goat")
    w3 = Web3(Web3.HTTPProvider(s.rpc_url, request_kwargs={"timeout": 20}))
    print(f"RPC        {s.rpc_url}")
    if not w3.is_connected():
        sys.exit("Not connected. Try GOAT_RPC_URL=https://rpc.ankr.com/goat_testnet")
    chain = w3.eth.chain_id
    print(f"Chain id   {chain} {'OK' if chain == s.chain_id else f'(expected {s.chain_id})'}")
    print(f"Block      {w3.eth.block_number}")
    print(f"Gas price  {w3.eth.gas_price} wei")
    print(f"Asset      {s.payment_asset}" + (f" at {s.stablecoin_address}" if s.payment_asset == "erc20" else
                                             f" ({s.wei_per_cent} wei per US cent)"))
    print()
    for role in ROLES:
        key = env(f"{role}_PRIVATE_KEY")
        if not key:
            print(f"{role:<10} no key in .env (run scripts/make_wallets.py)")
            continue
        addr = Account.from_key(key).address
        bal = w3.eth.get_balance(addr)
        print(f"{role:<10} {addr}  {w3.from_wei(bal, 'ether'):.8f} BTC")
    print(f"\nExplorer   {s.explorer_url}/address/<address>")
    guardian_key = env("GUARDIAN_PRIVATE_KEY")
    if guardian_key:
        bal = w3.eth.get_balance(Account.from_key(guardian_key).address)
        if bal == 0:
            print("\nGUARDIAN has no test BTC: fund it from the faucet before running goat mode.")


if __name__ == "__main__":
    main()
