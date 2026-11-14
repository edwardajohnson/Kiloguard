"""Agent identity and reputation.

LocalRegistry keeps identities in memory (simulation, tests, and any agent without an on-chain id).
Erc8004Registry adds GOAT's ERC-8004 identity registry: for every agent bound to an on-chain
agent id, the signing and payout address come from the chain, not from config. A charger that
claims an identity it does not hold therefore fails signature checks.
"""
from __future__ import annotations

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


IDENTITY_ABI = [
    {"name": "ownerOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"name": "", "type": "address"}]},
    {"name": "tokenURI", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": [{"name": "", "type": "string"}]},
    {"name": "getMetadata", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "key", "type": "string"}],
     "outputs": [{"name": "", "type": "bytes"}]},
]


class Erc8004Registry(LocalRegistry):
    """Reads agent wallets from an ERC-8004 identity registry on GOAT.

    ERC-8004 stores an agent's wallet under the reserved "agentWallet" metadata key; if that is
    not set we fall back to the owner of the agent's identity token.
    """

    def __init__(self, w3, registry_address: str, events=None):
        super().__init__(events)
        self.w3 = w3
        self.contract = w3.eth.contract(address=w3.to_checksum_address(registry_address), abi=IDENTITY_ABI)

    def agent_wallet(self, token_id: int) -> str | None:
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
