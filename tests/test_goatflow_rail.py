"""GOAT Flow rail, tested against a stand-in for GOAT's merchant API (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.charger import GoatFlowCharger  # noqa: E402
from kiloguard.config import Settings  # noqa: E402
from kiloguard.events import EventBus  # noqa: E402
from kiloguard.goatflow import extract_terms, sign  # noqa: E402
from kiloguard.scenarios import run_scene  # noqa: E402
from kiloguard.transport import LocalChargerClient  # noqa: E402
from kiloguard.world import build_world  # noqa: E402

SETTLEMENT = "0x" + "5e" * 20


class FakeGoatFlow:
    def __init__(self, pay_to=None, final_status="PAYMENT_CONFIRMED"):
        self.pay_to, self.final_status, self.orders = pay_to, final_status, []

    def create_order(self, ref, chain_id, symbol, contract, from_address, amount_wei):
        order_id = f"ord-{len(self.orders) + 1}"
        self.orders.append({"ref": ref, "from": from_address, "amount_wei": amount_wei, "chain_id": chain_id})
        accepts = {"payTo": self.pay_to, "maxAmountRequired": str(amount_wei), "asset": contract}
        return 402, {"order_id": order_id, "accepts": [{k: v for k, v in accepts.items() if v}],
                     "extensions": {"goatx402": {"expiresAt": 9999999999}}}

    def wait_confirmed(self, order_id, timeout_s=120, interval_s=3.0):
        return self.final_status, {"status": self.final_status}


def world_with_goatflow(tmp_path, fake, trusted=()):
    settings = Settings.load(mode="sim", reasoner="naive", identity="local", events_path=tmp_path / "e.jsonl")
    w = build_world(settings, EventBus(settings.events_path, echo=False))
    old = w.chargers["dufferin"]
    gf_settings = Settings(goatflow_chain_id=11155111, goatflow_token_contract="0xtoken", goatflow_token_symbol="USDC",
                           goatflow_token_decimals=6, goatflow_confirm_timeout_s=1)
    new = GoatFlowCharger(old.cfg, old.signer, old.payout_address, w.registry, w.ledger, w.clock, w.events, w.policy,
                          "eip155:11155111", "USDC", goatflow=fake, settings=gf_settings)
    w.chargers["dufferin"], w.clients["dufferin"] = new, LocalChargerClient(new)
    w.guardian.trusted_settlement = {a.lower() for a in trusted}
    return w


def test_happy_path_through_goatflow_orders(tmp_path):
    fake = FakeGoatFlow()
    w = world_with_goatflow(tmp_path, fake)
    r = run_scene(w, 1)[0]
    assert r.ok and r.paid_cents == 320 and len(fake.orders) == 5
    assert fake.orders[0]["amount_wei"] == 640000 and fake.orders[0]["from"] == w.guardian.address
    assert len(w.events.of("goatflow", status="PAYMENT_CONFIRMED")) == 5


def test_goat_settlement_payee_needs_owner_allowlist(tmp_path):
    w = world_with_goatflow(tmp_path, FakeGoatFlow(pay_to=SETTLEMENT))
    r = run_scene(w, 1)[0]
    assert r.code == "payee_mismatch" and r.paid_cents == 0
    w2 = world_with_goatflow(tmp_path / "b", FakeGoatFlow(pay_to=SETTLEMENT), trusted=[SETTLEMENT])
    r2 = run_scene(w2, 1)[0]
    assert r2.ok and w2.ledger.balance_cents(SETTLEMENT) == 320


def test_no_energy_unless_goatflow_confirms(tmp_path):
    w = world_with_goatflow(tmp_path, FakeGoatFlow(final_status="FAILED"))
    r = run_scene(w, 1)[0]
    assert r.code == "payment_rejected" and r.kwh == 0


def test_attacks_unchanged_with_goatflow_merchant(tmp_path):
    w = world_with_goatflow(tmp_path, FakeGoatFlow())
    assert run_scene(w, 3)[0].code == "payee_mismatch"
    assert run_scene(w, 4)[0].code == "short_delivery"


def test_extract_terms_from_x402_shape():
    t = extract_terms({"order_id": "o1", "accepts": [{"payTo": "0xabc", "maxAmountRequired": "640000"}]})
    assert t["order_id"] == "o1" and t["pay_to"] == "0xabc" and t["amount_wei"] == "640000"


def test_signature_string_matches_docs():
    import hashlib
    import hmac
    got = sign({"amount_wei": "640000", "chain_id": 48816, "token_contract": ""}, "K", "S", "1", "nonce1234")
    msg = "amount_wei=640000&api_key=K&chain_id=48816&nonce=nonce1234&timestamp=1"
    assert got == hmac.new(b"S", msg.encode(), hashlib.sha256).hexdigest()


REAL_REPLY = {  # GOAT Flow Testnet3 reply captured by scripts/goatflow_probe.py on 14 Nov 2026
    "x402Version": 2,
    "resource": {"url": "https://flow-api.testnet3.goat.network/api/v1/orders/cc2e0d15-9baf-428f-9e2d-62926c4cfd43",
                 "description": "Payment: 640000 USDC", "mimeType": "application/json"},
    "accepts": [{"scheme": "exact", "network": "eip155:48816", "amount": "640000",
                 "asset": "0x29d1ee93e9ecf6e50f309f498e40a6b42d352fa1",
                 "payTo": "0x96A85E3e31b396a78Dd4afd9CB9817894cAFdf8D", "maxTimeoutSeconds": 1200,
                 "extra": {"flow": "ERC20_DIRECT", "tokenSymbol": "USDC"}}],
    "extensions": {"goatx402": {"destinationChain": "eip155:48816", "expiresAt": 1794684610,
                                "paymentMethod": "transfer", "receiveType": "DIRECT"}},
    "order_id": "cc2e0d15-9baf-428f-9e2d-62926c4cfd43", "flow": "ERC20_DIRECT", "token_symbol": "USDC"}


def test_extract_terms_from_real_goatflow_reply():
    t = extract_terms(REAL_REPLY)
    assert t["order_id"] == "cc2e0d15-9baf-428f-9e2d-62926c4cfd43"
    assert t["pay_to"] == "0x96A85E3e31b396a78Dd4afd9CB9817894cAFdf8D"
    assert t["amount_wei"] == "640000"
    assert t["token_contract"] == "0x29d1ee93e9ecf6e50f309f498e40a6b42d352fa1"
    assert t["expires_at"] == 1794684610
