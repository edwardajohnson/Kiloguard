"""The guardian: the only component that can sign a payment.

The car's AI can ask the guardian to pay. The guardian checks numbers and addresses against the
fleet owner's policy and the identity registry, and only then signs ONE metered chunk. It never
reads free text, so a prompt-injected AI gains nothing by asking.

Every decision, approved or denied, is written to the audit log with its reason.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .crypto import b64json, canonical, sha256_hex, sign_obj
from .util import haversine_m, usd


@dataclass
class Decision:
    approved: bool
    code: str
    reason: str
    amount_cents: int = 0
    tx_hash: str | None = None
    payload: str | None = None


@dataclass
class SessionBudget:
    session_id: str
    charger_id: str
    charger_name: str
    payout_address: str
    locked_price_cents: int
    charger_lat: float
    charger_lon: float
    opened_at: datetime
    paid_cents: int = 0
    chunks: int = 0
    tx_hashes: list = field(default_factory=list)


class Guardian:
    def __init__(self, signer, policy, ledger, registry, events, clock, owner_approval=None,
                 anchor_receipts: bool = True, label: str = "Guardian", anchor_ledger=None,
                 trusted_settlement: set | None = None):
        self._signer = signer           # never exposed to the vehicle's AI
        self.policy = policy
        self.ledger = ledger
        self.registry = registry
        self.events = events
        self.clock = clock
        self.owner_approval = owner_approval
        self.anchor_receipts = anchor_receipts
        self.label = label
        self.anchor_ledger = anchor_ledger or ledger      # receipts are anchored on GOAT even if payment is elsewhere
        # Settlement addresses the fleet owner allows for GOAT Flow orders (when GOAT, not the merchant, is the payee)
        self.trusted_settlement = {a.lower() for a in (trusted_settlement or set())}
        self.sessions: dict[str, SessionBudget] = {}
        self.spent_by_day: dict[str, int] = defaultdict(int)
        self.session_starts: list[datetime] = []
        self.used_nonces: set[str] = set()
        self.audit: list[dict] = []

    @property
    def address(self) -> str:
        return self._signer.address

    def spent_today(self) -> int:
        return self.spent_by_day[self.clock.now().date().isoformat()]

    # --- decisions --------------------------------------------------------------------------
    def _decide(self, approved: bool, code: str, reason: str, amount: int = 0, charger: str = "",
                quiet: bool = False, **extra) -> Decision:
        entry = {"ts": self.clock.now().isoformat(), "approved": approved, "code": code, "reason": reason,
                 "amount_cents": amount, "charger": charger, **extra}
        self.audit.append(entry)
        if not quiet:
            verb = "APPROVED" if approved else "BLOCKED"
            self.events.emit("guardian", f"{self.label} {verb}: {reason}", level="ok" if approved else "deny",
                             approved=approved, code=code, reason=reason, amount_cents=amount, charger=charger,
                             guardian=self.address, **extra)
        return Decision(approved, code, reason, amount)

    def precheck(self, quote: dict, record) -> Decision:
        """Cheap check before the car drives anywhere. Quiet when it passes."""
        name = record.name if record else quote.get("agent_id", "?")
        if self.policy.kill_switch:
            return self._decide(False, "kill_switch", "Fleet kill switch is on", charger=quote.get("agent_id", ""))
        if record is None:
            return self._decide(False, "identity_unknown", f"{name} is not in the identity registry",
                                charger=quote.get("agent_id", ""))
        if record.reputation < self.policy.min_charger_reputation:
            return self._decide(False, "low_reputation",
                                f"{name} reputation {record.reputation:.2f} is below the "
                                f"{self.policy.min_charger_reputation:.1f} minimum", charger=record.agent_id)
        price = int(quote.get("price_per_kwh_cents", 10**9))
        if price > self.policy.max_price_per_kwh_cents:
            return self._decide(False, "price_cap", f"{name} asks {usd(price)}/kWh, above the "
                                f"{usd(self.policy.max_price_per_kwh_cents)}/kWh cap", charger=record.agent_id)
        return self._decide(True, "precheck_ok", f"{name} passes pre-checks", charger=record.agent_id, quiet=True)

    def open_session(self, session_id: str, record, locked_price_cents: int) -> Decision:
        now = self.clock.now()
        if self.policy.kill_switch:
            return self._decide(False, "kill_switch", "Fleet kill switch is on", charger=record.agent_id)
        recent = [t for t in self.session_starts if now - t < timedelta(hours=1)]
        if len(recent) >= self.policy.max_sessions_per_hour:
            return self._decide(False, "session_rate",
                                f"{len(recent)} charging sessions in the last hour (limit "
                                f"{self.policy.max_sessions_per_hour}): possible runaway loop",
                                charger=record.agent_id)
        self.session_starts = recent + [now]
        self.sessions[session_id] = SessionBudget(
            session_id=session_id, charger_id=record.agent_id, charger_name=record.name,
            payout_address=record.payout_address, locked_price_cents=int(locked_price_cents),
            charger_lat=record.lat, charger_lon=record.lon, opened_at=now)
        return self._decide(True, "session_open", f"Session opened with {record.name} at "
                            f"{usd(locked_price_cents)}/kWh (price locked)", charger=record.agent_id, quiet=True)

    def authorize_chunk(self, reqs: dict, car_lat: float, car_lon: float) -> Decision:
        """Checks one HTTP 402 payment request. If everything holds, pays it and returns the proof."""
        p = self.policy
        sess = self.sessions.get(reqs.get("session_id", ""))
        charger = sess.charger_id if sess else reqs.get("charger_agent_id", "")
        amount = int(reqs.get("amount_cents", 0))
        price = int(reqs.get("price_per_kwh_cents", 0))
        kwh = float(reqs.get("chunk_kwh", 0))
        if p.kill_switch:
            return self._decide(False, "kill_switch", "Fleet kill switch is on", amount, charger)
        if sess is None:
            return self._decide(False, "no_session", "No open session for this payment request", amount, charger)
        if reqs.get("nonce") in self.used_nonces:
            return self._decide(False, "replay", "Payment request nonce was already used", amount, charger)
        try:
            expires = datetime.fromisoformat(reqs.get("expires_at", ""))
        except ValueError:
            return self._decide(False, "bad_request", "Payment request has no valid expiry", amount, charger)
        if expires < self.clock.now():
            return self._decide(False, "expired", "Payment request has expired", amount, charger)
        pay_to = (reqs.get("pay_to") or "").lower()
        trusted_rail = reqs.get("scheme") == "goatflow" and pay_to in self.trusted_settlement
        if pay_to != sess.payout_address.lower() and not trusted_rail:
            return self._decide(False, "payee_mismatch",
                                f"{sess.charger_name} asked to be paid at {str(reqs.get('pay_to'))[:10]}…, but its "
                                f"registered payout address is {sess.payout_address[:10]}…: payment redirection",
                                amount, charger, asked_pay_to=reqs.get("pay_to"), registered=sess.payout_address)
        if price != sess.locked_price_cents:
            return self._decide(False, "price_changed",
                                f"{sess.charger_name} changed its price mid-session from "
                                f"{usd(sess.locked_price_cents)} to {usd(price)}/kWh", amount, charger)
        if price > p.max_price_per_kwh_cents:
            return self._decide(False, "price_cap", f"{usd(price)}/kWh is above the cap", amount, charger)
        if kwh <= 0 or kwh > p.chunk_kwh + 1e-9:
            return self._decide(False, "chunk_size", f"Chunk of {kwh} kWh is outside the {p.chunk_kwh} kWh limit",
                                amount, charger)
        if amount != round(kwh * price):
            return self._decide(False, "amount_mismatch", f"{usd(amount)} does not equal {kwh} kWh × {usd(price)}",
                                amount, charger)
        dist = haversine_m(car_lat, car_lon, sess.charger_lat, sess.charger_lon)
        if dist > p.max_distance_to_charger_m:
            return self._decide(False, "not_at_charger", f"Car is {dist:,.0f} m from {sess.charger_name}; "
                                f"payment only within {p.max_distance_to_charger_m:.0f} m", amount, charger)
        if sess.paid_cents + amount > p.max_per_session_cents:
            return self._decide(False, "session_cap", f"Would take this session to {usd(sess.paid_cents + amount)}, "
                                f"above the {usd(p.max_per_session_cents)} cap", amount, charger)
        if self.spent_today() + amount > p.max_per_day_cents:
            approved = bool(self.owner_approval and self.owner_approval(amount, "daily cap"))
            if not approved:
                return self._decide(False, "daily_cap", f"Daily cap of {usd(p.max_per_day_cents)} reached; "
                                    "escalated to the fleet owner, who did not approve", amount, charger)
            self.events.emit("guardian", "Fleet owner approved spending above the daily cap", level="warn",
                             charger=charger)

        try:
            tx_hash = self.ledger.pay(self._signer, reqs["pay_to"] if trusted_rail else sess.payout_address, amount,
                                      memo=f"{sess.session_id}#{reqs.get('chunk_index')}")
        except Exception as exc:
            return self._decide(False, "payment_failed", f"Payment could not be sent: {exc}", amount, charger)

        self.used_nonces.add(reqs["nonce"])
        sess.paid_cents += amount
        sess.chunks += 1
        sess.tx_hashes.append(tx_hash)
        self.spent_by_day[self.clock.now().date().isoformat()] += amount
        proof = sign_obj(self._signer, {"nonce": reqs["nonce"], "tx_hash": tx_hash, "payer": self.address,
                                        "session_id": sess.session_id})
        decision = self._decide(True, "chunk_paid",
                                f"chunk {int(reqs.get('chunk_index', 0)) + 1}: {kwh:g} kWh × {usd(price)} = "
                                f"{usd(amount)} to {sess.charger_name}", amount, charger, quiet=True)
        decision.tx_hash = tx_hash
        decision.payload = b64json(proof)
        via = f" via GOAT Flow order {str(reqs.get('goatflow_order_id'))[:10]}…" if reqs.get("goatflow_order_id") else ""
        self.events.emit("payment", f"Paid {usd(amount)} for chunk {int(reqs.get('chunk_index', 0)) + 1} "
                         f"({kwh:g} kWh) to {sess.charger_name}{via} on {self.ledger.name}", level="money", charger=charger,
                         amount_cents=amount, tx_hash=tx_hash, tx_url=self.ledger.tx_url(tx_hash),
                         session_paid_cents=sess.paid_cents, guardian=self.address)
        return decision

    def authorize_request(self, proposal: dict, record) -> Decision:
        """Any payment the AI asks for outside a metered chunk. The guardian checks numbers, not words."""
        amount = int(proposal.get("amount_cents", 0))
        charger = record.agent_id if record else ""
        reasons = ["the guardian only pays metered 2 kWh chunks inside an open session"]
        if amount > self.policy.max_per_session_cents:
            reasons.append(f"{usd(amount)} is above the {usd(self.policy.max_per_session_cents)} session cap")
        if self.spent_today() + amount > self.policy.max_per_day_cents:
            reasons.append(f"it would exceed the {usd(self.policy.max_per_day_cents)} daily cap")
        return self._decide(False, "unmetered_payment",
                            f"AI asked to {proposal.get('action', 'pay')} {usd(amount)}: refused, "
                            + "; ".join(reasons), amount, charger)

    def close_session(self, session_id: str) -> SessionBudget | None:
        return self.sessions.pop(session_id, None)

    def anchor(self, receipt: dict) -> str | None:
        if not self.anchor_receipts:
            return None
        digest = sha256_hex(canonical(receipt))
        try:
            tx = self.anchor_ledger.anchor(self._signer, digest)
        except Exception as exc:
            self.events.emit("anchor", f"Receipt anchoring failed: {exc}", level="warn")
            return None
        self.events.emit("anchor", f"Receipt hash anchored on {self.anchor_ledger.name}", level="ok", digest=digest,
                         tx_hash=tx, tx_url=self.anchor_ledger.tx_url(tx), session_id=receipt.get("session_id"),
                         charger=receipt.get("charger_agent_id"))
        return tx
