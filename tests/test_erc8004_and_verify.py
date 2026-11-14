"""Tests for receipt verification and the ERC-8004 adapter.

The ERC-8004 tests use a small stand-in for web3 (no network), checking our logic: which address
we trust, what we send to the reputation registry, and how we read the agent id from a receipt.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings  # noqa: E402
from kiloguard.events import EventBus  # noqa: E402
from kiloguard.identity import AgentRecord, Erc8004Registry  # noqa: E402
from kiloguard.scenarios import run_scene  # noqa: E402
from kiloguard.verify import load_events, sessions_with_receipts, verify_session  # noqa: E402
from kiloguard.world import build_world  # noqa: E402


# --- receipt verification ----------------------------------------------------------------------

def _run(tmp_path, scenes=(1, 3)):
    settings = Settings.load(mode="sim", reasoner="naive", identity="local", events_path=tmp_path / "events.jsonl")
    world = build_world(settings, EventBus(settings.events_path, echo=False))
    world.events.emit("run", "test run", mode="sim")
    for n in scenes:
        run_scene(world, n)
    return settings.events_path


def test_every_receipt_verifies(tmp_path):
    events = load_events(_run(tmp_path))
    sessions = sessions_with_receipts(events)
    assert len(sessions) >= 3
    for sid in sessions:
        assert verify_session(events, sid)["ok"], sid


def test_tampered_receipt_fails(tmp_path):
    path = _run(tmp_path, scenes=(1,))
    events = load_events(path)
    sid = sessions_with_receipts(events)[0]
    for e in events:
        if e["kind"] == "receipt":
            e["receipt"]["paid_cents"] = 1   # someone edits the log after the fact
    result = verify_session(events, sid)
    assert not result["ok"]
    assert any("does NOT match" in s["text"] for s in result["steps"])


# --- ERC-8004 adapter with a stand-in chain -------------------------------------------------------

class FakeCall:
    def __init__(self, contract, name, args):
        self.contract, self.name, self.args = contract, name, args

    def call(self):
        return self.contract.reads[self.name](*self.args)

    def build_transaction(self, _tx):
        return {"data": "0x" + json.dumps([self.name, [str(a) for a in self.args]]).encode().hex()}


class FakeFunctions:
    def __init__(self, contract):
        self._c = contract

    def __getattr__(self, name):
        return lambda *args: FakeCall(self._c, name, args)


class FakeContract:
    def __init__(self, address, reads):
        self.address, self.reads = address, reads
        self.functions = FakeFunctions(self)


class FakeEth:
    chain_id = 48816

    def __init__(self, reads):
        self.reads = reads
        self.receipt = None

    def contract(self, address, abi):
        return FakeContract(address, self.reads)

    def get_transaction_receipt(self, tx):
        return self.receipt


class FakeW3:
    def __init__(self, reads):
        self.eth = FakeEth(reads)

    @staticmethod
    def to_checksum_address(a):
        return a

    @staticmethod
    def keccak(text):
        return bytes.fromhex("aa" * 32)


class FakeLedger:
    name = "fake"

    def __init__(self, reads):
        self.w3 = FakeW3(reads)
        self.sent = []

    def _send(self, signer, tx):
        self.sent.append(tx)
        return "0x" + "11" * 32

    @staticmethod
    def _hex(v):
        return v if isinstance(v, str) else "0x" + bytes(v).hex()

    def tx_url(self, h):
        return "https://explorer/tx/" + h


ZERO = "0x" + "00" * 20
WALLET = "0x" + "ab" * 20
OWNER = "0x" + "cd" * 20


def test_agent_wallet_prefers_getAgentWallet_then_owner():
    reads = {"getAgentWallet": lambda i: WALLET, "getMetadata": lambda i, k: b"", "ownerOf": lambda i: OWNER}
    reg = Erc8004Registry(FakeLedger(reads), "0xregistry", "0xreputation")
    assert reg.agent_wallet(7) == WALLET
    reads["getAgentWallet"] = lambda i: ZERO
    assert reg.agent_wallet(7) == OWNER


def test_bind_onchain_overrides_config_addresses():
    reads = {"getAgentWallet": lambda i: WALLET, "getMetadata": lambda i, k: b"", "ownerOf": lambda i: OWNER}
    reg = Erc8004Registry(FakeLedger(reads), "0xregistry", "0xreputation")
    reg.register(AgentRecord("dufferin", "Dufferin", "charger", "0xconfig", "0xconfig"))
    assert reg.bind_onchain("dufferin", 12)
    rec = reg.lookup("dufferin")
    assert rec.signing_address == rec.payout_address == WALLET and rec.onchain_id == 12


def test_feedback_goes_to_reputation_registry():
    reads = {"getAgentWallet": lambda i: WALLET, "getMetadata": lambda i, k: b"", "ownerOf": lambda i: OWNER}
    ledger = FakeLedger(reads)
    events = EventBus(None, echo=False)
    reg = Erc8004Registry(ledger, "0xregistry", "0xreputation", events, feedback_signer=object())
    reg.register(AgentRecord("queen-west", "Queen West", "charger", WALLET, WALLET, reputation=4.3))
    reg.bind_onchain("queen-west", 5)
    after = reg.give_feedback("queen-west", 1, "short delivery", by="unit-07")
    assert after < 4.0
    assert ledger.sent and ledger.sent[0]["to"] == "0xreputation"
    call = json.loads(bytes.fromhex(ledger.sent[0]["data"][2:]))
    assert call[0] == "giveFeedback" and call[1][:3] == ["5", "1", "0"]
    assert events.of("reputation_onchain")[0]["tx_url"].startswith("https://explorer/tx/")


def test_feedback_without_onchain_id_stays_local():
    ledger = FakeLedger({})
    reg = Erc8004Registry(ledger, "0xregistry", "0xreputation", feedback_signer=object())
    reg.register(AgentRecord("annex", "Annex", "charger", WALLET, WALLET))
    reg.give_feedback("annex", 5, "ok", by="unit-07")
    assert ledger.sent == []


def test_register_agent_reads_id_from_registered_event():
    ledger = FakeLedger({})
    reg = Erc8004Registry(ledger, "0xregistry", "0xreputation")
    ledger.w3.eth.receipt = {"logs": [{"address": "0xregistry",
                                       "topics": ["0x" + "aa" * 32, "0x" + "00" * 31 + "2a", "0x" + "00" * 32]}]}
    agent_id, tx = reg.register_agent(object(), "data:application/json;base64,e30=")
    assert agent_id == 42 and tx.startswith("0x")
    sent = json.loads(bytes.fromhex(ledger.sent[0]["data"][2:]))
    assert sent[0] == "register"
