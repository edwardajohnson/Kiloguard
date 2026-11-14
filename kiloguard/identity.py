"""Agent identity and reputation.

LocalRegistry keeps identities in memory (simulation, tests, and any agent without an on-chain id).
Erc8004Registry adds GOAT's ERC-8004 identity registry: for every agent bound to an on-chain
agent id, the signing and payout address come from the chain, not from config. A charger that
claims an identity it does not hold therefore fails signature checks.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    kind: str                      # "charger", "vehicle", "fleet_owner"
    signing_address: str
    payout_address: str
    lat: float | None = None
    lon: float | None = None
    reputation: float = 4.5
    ratings: int = 5
    onchain_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalRegistry:
    def __init__(self, events=None):
        self._agents: dict[str, AgentRecord] = {}
        self.events = events
        self.feedback: list[dict] = []

    def register(self, record: AgentRecord) -> AgentRecord:
        self._agents[record.agent_id] = record
        return record

    def lookup(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def all(self, kind: str | None = None) -> list[AgentRecord]:
        return [r for r in self._agents.values() if kind is None or r.kind == kind]

    def owner_by_address(self, address: str) -> AgentRecord | None:
        for rec in self._agents.values():
            if rec.kind == "fleet_owner" and rec.signing_address.lower() == (address or "").lower():
                return rec
        return None

    def give_feedback(self, agent_id: str, score: float, reason: str, by: str) -> float | None:
        rec = self.lookup(agent_id)
        if rec is None:
            return None
        before = rec.reputation
        rec.reputation = round((rec.reputation * rec.ratings + score) / (rec.ratings + 1), 2)
        rec.ratings += 1
        entry = {"agent_id": agent_id, "score": score, "reason": reason, "by": by,
                 "before": before, "after": rec.reputation}
        self.feedback.append(entry)
        if self.events:
            level = "deny" if score <= 2 else "info"
            self.events.emit("reputation", f"Reputation of {rec.name}: {before:.2f} → {rec.reputation:.2f} ({reason})",
                             level=level, charger=agent_id, reputation=rec.reputation, score=score)
        return rec.reputation


def _fn(name, inputs, outputs=(), mutability="view"):
    return {"name": name, "type": "function", "stateMutability": mutability,
            "inputs": [{"name": n, "type": t} for n, t in inputs],
            "outputs": [{"name": "", "type": t} for t in outputs]}


# Signatures from the ERC-8004 specification (ercs.ethereum.org/ERCS/erc-8004)
IDENTITY_ABI = [
    _fn("register", [("agentURI", "string")], ["uint256"], "nonpayable"),
    _fn("getAgentWallet", [("agentId", "uint256")], ["address"]),
    _fn("getMetadata", [("agentId", "uint256"), ("metadataKey", "string")], ["bytes"]),
    _fn("ownerOf", [("tokenId", "uint256")], ["address"]),
    _fn("tokenURI", [("tokenId", "uint256")], ["string"]),
    {"name": "Registered", "type": "event", "anonymous": False, "inputs": [
        {"name": "agentId", "type": "uint256", "indexed": True},
        {"name": "agentURI", "type": "string", "indexed": False},
        {"name": "owner", "type": "address", "indexed": True}]},
]
REPUTATION_ABI = [
    _fn("giveFeedback", [("agentId", "uint256"), ("value", "int128"), ("valueDecimals", "uint8"),
                         ("tag1", "string"), ("tag2", "string"), ("endpoint", "string"),
                         ("feedbackURI", "string"), ("feedbackHash", "bytes32")], [], "nonpayable"),
]
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def encode_call(w3, contract, fn_name: str, args: list) -> str:
    """ABI-encodes a call without sending it (works on web3.py v6 and v7)."""
    tx = getattr(contract.functions, fn_name)(*args).build_transaction(
        {"from": "0x" + "00" * 20, "gas": 1_000_000, "gasPrice": 0, "nonce": 0, "chainId": w3.eth.chain_id})
    data = tx["data"]
    return data if isinstance(data, str) else "0x" + bytes(data).hex()


class Erc8004Registry(LocalRegistry):
    """Agent identity and reputation on GOAT's ERC-8004 registries.

    - Identity: for every agent bound to an on-chain id, signing and payout addresses come from the
      identity registry (getAgentWallet, else the owner of the agent's token), not from config.
    - Reputation: feedback is still applied locally for instant decisions, and also posted to the
      reputation registry from the car's wallet, so every car in the fleet can read it.
    """

    def __init__(self, ledger, registry_address: str, reputation_address: str = "", events=None,
                 feedback_signer=None, onchain_feedback: bool = True):
        super().__init__(events)
        self.ledger = ledger
        self.w3 = ledger.w3
        self.contract = self.w3.eth.contract(address=self.w3.to_checksum_address(registry_address), abi=IDENTITY_ABI)
        self.reputation = (self.w3.eth.contract(address=self.w3.to_checksum_address(reputation_address),
                                                abi=REPUTATION_ABI) if reputation_address else None)
        self.feedback_signer = feedback_signer
        self.onchain_feedback = onchain_feedback

    def agent_wallet(self, token_id: int) -> str | None:
        try:
            wallet = self.contract.functions.getAgentWallet(token_id).call()
            if wallet and int(wallet, 16) != 0:
                return self.w3.to_checksum_address(wallet)
        except Exception:
            pass
        try:
            raw = bytes(self.contract.functions.getMetadata(token_id, "agentWallet").call())
            if len(raw) >= 20 and any(raw[-20:]):
                return self.w3.to_checksum_address("0x" + raw[-20:].hex())
        except Exception:
            pass
        try:
            return self.contract.functions.ownerOf(token_id).call()
        except Exception:
            return None

    def give_feedback(self, agent_id: str, score: float, reason: str, by: str) -> float | None:
        after = super().give_feedback(agent_id, score, reason, by)
        rec = self.lookup(agent_id)
        if (after is None or rec is None or rec.onchain_id is None or self.reputation is None
                or self.feedback_signer is None or not self.onchain_feedback):
            return after
        try:
            evidence = hashlib.sha256(f"{agent_id}|{score}|{reason}|{by}".encode()).digest()
            data = encode_call(self.w3, self.reputation, "giveFeedback",
                               [rec.onchain_id, int(score), 0, "charging", str(reason)[:32], "", "", evidence])
            tx = self.ledger._send(self.feedback_signer, {"to": self.reputation.address, "value": 0, "data": data})
            if self.events:
                self.events.emit("reputation_onchain", f"Feedback {int(score)}/5 for {rec.name} written to the "
                                 f"ERC-8004 reputation registry", level="ok", charger=agent_id, tx_hash=tx,
                                 tx_url=self.ledger.tx_url(tx), score=score)
        except Exception as exc:
            if self.events:
                self.events.emit("reputation_onchain", f"On-chain feedback for {rec.name} failed "
                                 f"({exc.__class__.__name__}); local reputation still applied", level="warn",
                                 charger=agent_id)
        return after

    def register_agent(self, signer, agent_uri: str) -> tuple[int, str]:
        """Registers a new agent from `signer`'s wallet. Returns (agentId, tx hash)."""
        data = encode_call(self.w3, self.contract, "register", [agent_uri])
        tx = self.ledger._send(signer, {"to": self.contract.address, "value": 0, "data": data})
        receipt = self.w3.eth.get_transaction_receipt(tx)
        registered = self.w3.keccak(text="Registered(uint256,string,address)").hex().lower()
        registered = registered if registered.startswith("0x") else "0x" + registered
        for log in receipt["logs"]:
            if log["address"].lower() != self.contract.address.lower() or len(log["topics"]) < 2:
                continue
            topic0 = self.ledger._hex(log["topics"][0]).lower()
            if topic0 == registered:
                return int(self.ledger._hex(log["topics"][1]), 16), tx
            if topic0 == TRANSFER_TOPIC and len(log["topics"]) >= 4:   # ERC-721 mint carries the token id too
                return int(self.ledger._hex(log["topics"][3]), 16), tx
        raise RuntimeError(f"registered in {tx} but no agent id found in the logs")

    def agent_uri(self, token_id: int) -> str | None:
        try:
            return self.contract.functions.tokenURI(token_id).call()
        except Exception:
            return None

    def bind_onchain(self, agent_id: str, token_id: int) -> bool:
        rec = self.lookup(agent_id)
        wallet = self.agent_wallet(token_id)
        if rec is None or wallet is None:
            if self.events:
                self.events.emit("identity", f"ERC-8004 agent #{token_id} for {agent_id} could not be read", level="warn")
            return False
        rec.signing_address = rec.payout_address = wallet
        rec.onchain_id = token_id
        if self.events:
            self.events.emit("identity", f"{rec.name} bound to ERC-8004 agent #{token_id} (wallet {wallet[:10]}…)",
                             level="ok", charger=agent_id, onchain_id=token_id, wallet=wallet)
        return True
