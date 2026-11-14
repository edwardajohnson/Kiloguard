"""Claim test USDC on GOAT Testnet3 from the GOAT faucet, and move it between wallets.

The faucet (shared by the GOAT team) gives 20 USDC per claimToken() call, to the wallet that calls it.
Each wallet needs a little test BTC for gas.

  python scripts/claim_usdc.py                               # GUARDIAN claims 20 USDC
  python scripts/claim_usdc.py --roles GUARDIAN CHARGER_A    # several wallets claim
  python scripts/claim_usdc.py --balances                    # show USDC balances, claim nothing
  python scripts/claim_usdc.py --send 10 --from GUARDIAN --to 0xB06CC6965628872C7D63Fa29622Fa94856123A25
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings, env  # noqa: E402

FAUCET = "0x89e7dfd01a86e5393ce6d8A78c9aa6653Ee113A6"
USDC = "0x29D1Ee93e9ecf6E50F309f498e40a6b42D352Fa1"
ROLES = ["OWNER", "GUARDIAN", "VEHICLE", "CHARGER_A", "CHARGER_B", "ATTACKER"]

FAUCET_ABI = [{"name": "claimToken", "type": "function", "stateMutability": "nonpayable",
               "inputs": [{"name": "token", "type": "address"}], "outputs": []}]
ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--roles", nargs="+", default=["GUARDIAN"], help="wallets (from .env) that each claim 20 USDC")
    p.add_argument("--balances", action="store_true")
    p.add_argument("--send", type=float, help="amount of USDC to send")
    p.add_argument("--from", dest="sender", default="GUARDIAN")
    p.add_argument("--to")
    args = p.parse_args()

    try:
        from eth_account import Account
        from web3 import Web3
    except ImportError:
        sys.exit("Install requirements first: pip install -r requirements.txt")
    s = Settings.load(mode="goat")
    w3 = Web3(Web3.HTTPProvider(s.rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        sys.exit(f"Cannot reach {s.rpc_url}")
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=ERC20_ABI)
    faucet = w3.eth.contract(address=Web3.to_checksum_address(FAUCET), abi=FAUCET_ABI)
    decimals = int(usdc.functions.decimals().call())

    def account(role):
        key = env(f"{role}_PRIVATE_KEY")
        if not key:
            sys.exit(f"No {role}_PRIVATE_KEY in .env")
        return Account.from_key(key)

    def send_tx(acct, fn):
        tx = fn.build_transaction({"from": acct.address, "chainId": s.chain_id, "gasPrice": w3.eth.gas_price,
                                   "nonce": w3.eth.get_transaction_count(acct.address, "pending")})
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        h = w3.eth.send_raw_transaction(raw)
        r = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        h = "0x" + bytes(h).hex()
        if r["status"] != 1:
            sys.exit(f"Transaction failed: {s.explorer_url}/tx/{h}")
        return h

    def show(label, address):
        bal = usdc.functions.balanceOf(address).call() / 10 ** decimals
        gas = w3.from_wei(w3.eth.get_balance(address), "ether")
        print(f"  {label:<10} {address}  {bal:,.2f} USDC   {gas:.8f} test BTC")

    print(f"USDC {USDC} on chain {s.chain_id} ({decimals} decimals)\n")
    if args.send:
        acct = account(args.sender)
        if not args.to:
            sys.exit("--send needs --to <address>")
        to = Web3.to_checksum_address(args.to)
        amount = int(round(args.send * 10 ** decimals))
        have = usdc.functions.balanceOf(acct.address).call()
        if have < amount:
            sys.exit(f"{args.sender} has {have / 10 ** decimals:,.2f} USDC, not enough to send {args.send}. "
                     f"Claim first: python scripts/claim_usdc.py --roles {args.sender}")
        h = send_tx(acct, usdc.functions.transfer(to, amount))
        print(f"Sent {args.send} USDC from {args.sender} to {to}\n  {s.explorer_url}/tx/{h}")
        show(args.sender, acct.address)
        show("recipient", to)
        return
    if not args.balances:
        for role in args.roles:
            acct = account(role)
            if w3.eth.get_balance(acct.address) == 0:
                print(f"  {role}: no test BTC for gas; fund it from the faucet first")
                continue
            h = send_tx(acct, faucet.functions.claimToken(Web3.to_checksum_address(USDC)))
            print(f"  {role} claimed 20 USDC  {s.explorer_url}/tx/{h}")
        print()
    for role in ROLES:
        if env(f"{role}_PRIVATE_KEY"):
            show(role, account(role).address)
    print("\nFor .env:  GOATFLOW_CHAIN_ID=48816  GOATFLOW_TOKEN_SYMBOL=USDC")
    print(f"           GOATFLOW_TOKEN_CONTRACT={USDC}  GOATFLOW_TOKEN_DECIMALS={decimals}")


if __name__ == "__main__":
    main()
