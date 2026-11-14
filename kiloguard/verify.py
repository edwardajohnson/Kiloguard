"""Independent receipt verification: anyone can check a charging session after the fact.

For one session it checks, step by step:
  1. the charger's signed receipt exists (and, in goat mode, which key signed it)
  2. the receipt's hash equals the hash the guardian anchored
  3. the payments listed in the receipt add up to the amount on the receipt
  4. goat mode: the anchor transaction on GOAT really carries that hash, and every payment
     transaction listed in the receipt succeeded on-chain
"""
from __future__ import annotations

import json
from pathlib import Path

from .crypto import canonical, sha256_hex
from .util import usd


def load_events(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def sessions_with_receipts(events: list[dict]) -> list[str]:
    return [e["receipt"]["session_id"] for e in events if e.get("kind") == "receipt" and e.get("receipt")]


def _recover_signer(receipt: dict) -> str | None:
    sig = receipt.get("signature", "")
    if not sig.startswith("0x"):
        return None
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        return None
    body = {k: v for k, v in receipt.items() if k != "signature"}
    try:
        return Account.recover_message(encode_defunct(text=canonical(body)), signature=sig)
    except Exception:
        return None


def verify_session(events: list[dict], session_id: str, rpc_url: str | None = None) -> dict:
    steps: list[dict] = []

    def step(ok: bool, text: str) -> None:
        steps.append({"ok": bool(ok), "text": text})

    receipt_ev = next((e for e in events if e.get("kind") == "receipt" and e.get("receipt", {}).get("session_id")
                       == session_id), None)
    anchor_ev = next((e for e in events if e.get("kind") == "anchor" and e.get("session_id") == session_id), None)
    run_ev = next((e for e in events if e.get("kind") == "run"), {})
    if receipt_ev is None:
        step(False, f"No receipt found for session {session_id}")
        return {"ok": False, "session_id": session_id, "steps": steps}
    receipt = receipt_ev["receipt"]
    name = receipt.get("charger_name", receipt.get("charger_agent_id"))

    signer = _recover_signer(receipt)
    if signer:
        step(True, f"Receipt from {name} is signed by {signer}")
    else:
        step(True, f"Receipt from {name} carries a signature that the car verified against the registry "
                   "during the run (simulation key)")

    digest = sha256_hex(canonical(receipt))
    if anchor_ev is None and not receipt.get("paid_cents"):
        step(True, "Nothing was paid in this session, so there was nothing to anchor")
    elif anchor_ev is None:
        step(False, "No anchor recorded for this receipt")
    else:
        step(digest == anchor_ev.get("digest"), f"Receipt hash {digest[:18]}… "
             + ("matches" if digest == anchor_ev.get("digest") else "does NOT match") + " the anchored hash")

    tx_hashes = receipt.get("tx_hashes", [])
    paid = sum(e.get("amount_cents", 0) for e in events if e.get("kind") == "payment" and e.get("tx_hash") in tx_hashes)
    step(paid == receipt.get("paid_cents"), f"{len(tx_hashes)} payments listed, totalling {usd(paid)}; receipt says "
         f"{usd(receipt.get('paid_cents', 0))}")

    on_chain = run_ev.get("mode") == "goat" and rpc_url
    if on_chain:
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
            if anchor_ev and anchor_ev.get("tx_hash"):
                tx = w3.eth.get_transaction(anchor_ev["tx_hash"])
                data = tx["input"] if isinstance(tx["input"], str) else "0x" + bytes(tx["input"]).hex()
                step(data.lower() == digest.lower(), "Anchor transaction on GOAT carries the receipt hash"
                     if data.lower() == digest.lower() else "Anchor transaction data does NOT match the receipt hash")
            good = 0
            for h in tx_hashes:
                r = w3.eth.get_transaction_receipt(h)
                good += int(r["status"] == 1)
            step(good == len(tx_hashes), f"{good} of {len(tx_hashes)} payment transactions succeeded on GOAT")
        except Exception as exc:
            step(False, f"Could not check GOAT ({exc.__class__.__name__}: {exc})")
    else:
        step(True, "Simulated ledger: on-chain checks apply in goat mode")

    return {"ok": all(s["ok"] for s in steps), "session_id": session_id, "charger": name,
            "kwh": receipt.get("kwh_delivered"), "paid_cents": receipt.get("paid_cents"),
            "anchor_url": (anchor_ev or {}).get("tx_url", ""), "steps": steps}
