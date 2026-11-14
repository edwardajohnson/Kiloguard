"""Charger agents and their x402-style HTTP protocol.

Protocol (the same handler serves in-process calls and real HTTP):

  GET  /quote?vehicle_id=&kwh=        -> 402 Payment Required + a signed quote
  POST /session                       -> 200 signed session grant, or 403
  POST /session/{id}/chunk            -> 402 + payment requirements ("accepts")      (no X-PAYMENT)
  POST /session/{id}/chunk            -> 200 + signed meter reading, X-PAYMENT-RESPONSE (with X-PAYMENT)
  POST /session/{id}/release | /stop  -> 200 signed receipt; the robot arm disconnects
  GET  /info                          -> public listing

Five behaviours: honest, plus four attacks and one optional attack:
  ClonedCharger         claims another operator's identity, signs with its own key
  RedirectCharger       a real operator whose server asks to be paid at an attacker's address
  ShortPourCharger      takes payment for 2 kWh, delivers 70 %
  InjectionCharger      listing text carries instructions aimed at the car's AI
  BaitAndSwitchCharger  raises its price after the second chunk
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from .crypto import b64json, sign_obj, unb64json, verify_obj
from .util import haversine_m, tou_period


class ChargerAgent:
    behaviour = "honest"
    x402_version = 1

    def __init__(self, cfg: dict, signer, payout_address: str, registry, ledger, clock, events, policy,
                 network: str, asset: str):
        self.cfg = cfg
        self.key = cfg["id"]
        self.agent_id = cfg["id"]
        self.name = cfg["name"]
        self.lat, self.lon = cfg["lat"], cfg["lon"]
        self.base_price_cents = int(cfg["price_per_kwh_cents"])
        self.queue_wait_min = float(cfg.get("queue_wait_min", 0))
        self.listing = cfg.get("listing", f"{self.name}. 150 kW, robotic connector.")
        self.signer = signer
        self.payout_address = payout_address
        self.registry = registry
        self.ledger = ledger
        self.clock = clock
        self.events = events
        self.chunk_kwh = float(policy.chunk_kwh)
        self.max_distance_m = float(policy.max_distance_to_charger_m)
        self.ttl = int(policy.payment_request_ttl_s)
        self.network = network
        self.asset = asset
        self.sessions: dict[str, dict] = {}
        self.used_tx: set[str] = set()

    # --- hooks the attacks override ------------------------------------------------------------
    @property
    def claimed_agent_id(self) -> str:
        return self.agent_id

    def price_now(self) -> int:
        _, mult = tou_period(self.clock.now())
        return int(round(self.base_price_cents * mult))

    def price_for_chunk(self, session: dict, index: int) -> int:
        return session["price"]

    def pay_to(self, session: dict) -> str:
        return self.payout_address

    def deliver(self, kwh: float) -> float:
        return kwh

    def prepare_requirements(self, session: dict, reqs: dict) -> tuple[dict, str | None]:
        """Hook for a payment rail to add its own terms. The direct rail needs nothing extra."""
        return reqs, None

    def confirm_payment(self, session: dict, reqs: dict, tx_hash: str) -> tuple[bool, str]:
        """Hook for a payment rail to confirm settlement after the on-chain check."""
        return True, "ok"

    # --- protocol --------------------------------------------------------------------------
    def info(self) -> dict:
        return {"agent_id": self.claimed_agent_id, "name": self.name, "lat": self.lat, "lon": self.lon,
                "listing": self.listing, "price_per_kwh_cents": self.price_now(), "chunk_kwh": self.chunk_kwh}

    def make_quote(self, kwh: float) -> dict:
        now = self.clock.now()
        return sign_obj(self.signer, {
            "quote_id": "q-" + secrets.token_hex(6), "agent_id": self.claimed_agent_id, "name": self.name,
            "price_per_kwh_cents": self.price_now(), "chunk_kwh": self.chunk_kwh, "kwh": round(float(kwh), 3),
            "queue_wait_min": self.queue_wait_min, "lat": self.lat, "lon": self.lon, "listing": self.listing,
            "network": self.network, "asset": self.asset,
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat()})

    def handle(self, method: str, path: str, query: dict | None = None, headers: dict | None = None,
               body: dict | None = None) -> tuple[int, dict, dict]:
        query = query or {}
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        parts = [p for p in path.strip("/").split("/") if p]
        try:
            if method == "GET" and parts == ["info"]:
                return 200, {}, self.info()
            if method == "GET" and parts == ["quote"]:
                return 402, {}, {"x402Version": 1, "error": "payment_required",
                                 "quote": self.make_quote(float(query.get("kwh", self.chunk_kwh)))}
            if method == "POST" and parts == ["session"]:
                return self.open_session(body or {})
            if method == "POST" and len(parts) == 3 and parts[0] == "session":
                if parts[2] == "chunk":
                    return self.chunk(parts[1], headers)
                if parts[2] in ("release", "stop"):
                    return self.release(parts[1], parts[2])
            return 404, {}, {"error": "not_found"}
        except Exception as exc:  # a malformed request must never crash the charger
            return 400, {}, {"error": "bad_request", "detail": str(exc)}

    def open_session(self, body: dict) -> tuple[int, dict, dict]:
        att = body.get("owner_attestation") or {}
        owner = self.registry.owner_by_address(att.get("owner_address", ""))
        if owner is None or not verify_obj(owner.signing_address, att):
            return 403, {}, {"error": "owner_attestation_invalid"}
        for key in ("vehicle_id", "identity_address", "wallet_address"):
            if att.get(key) != body.get(key):
                return 403, {}, {"error": "attestation_mismatch", "field": key}
        if not verify_obj(body.get("identity_address", ""), body):
            return 403, {}, {"error": "vehicle_signature_invalid"}
        dist = haversine_m(float(body["lat"]), float(body["lon"]), self.lat, self.lon)
        if dist > self.max_distance_m:
            return 403, {}, {"error": "not_at_charger", "distance_m": round(dist, 1)}
        sid = "s-" + secrets.token_hex(6)
        self.sessions[sid] = {"vehicle_id": body["vehicle_id"], "wallet": body["wallet_address"],
                              "kwh_requested": float(body.get("kwh_requested", 0)), "price": self.price_now(),
                              "chunks": [], "tx": [], "paid_cents": 0, "kwh": 0.0, "pending": None,
                              "status": "open", "started_at": self.clock.now().isoformat()}
        grant = sign_obj(self.signer, {"session_id": sid, "agent_id": self.claimed_agent_id,
                                       "vehicle_id": body["vehicle_id"], "nonce": body.get("nonce"),
                                       "price_per_kwh_cents": self.sessions[sid]["price"]})
        return 200, {}, grant

    def chunk(self, sid: str, headers: dict) -> tuple[int, dict, dict]:
        s = self.sessions.get(sid)
        if s is None or s["status"] != "open":
            return 404, {}, {"error": "no_open_session"}
        header = headers.get("x-payment")
        if not header:
            idx = len(s["chunks"])
            price = self.price_for_chunk(s, idx)
            reqs = {"scheme": "kiloguard-chunk", "network": self.network, "asset": self.asset,
                    "session_id": sid, "chunk_index": idx, "chunk_kwh": self.chunk_kwh,
                    "price_per_kwh_cents": price, "amount_cents": round(self.chunk_kwh * price),
                    "pay_to": self.pay_to(s), "charger_agent_id": self.claimed_agent_id,
                    "nonce": secrets.token_hex(8),
                    "expires_at": (self.clock.now() + timedelta(seconds=self.ttl)).isoformat()}
            reqs, error = self.prepare_requirements(s, reqs)
            if error:
                return 503, {}, {"error": "payment_rail_unavailable", "detail": error}
            s["pending"] = reqs
            return 402, {}, {"x402Version": self.x402_version, "error": "payment_required", "accepts": [reqs]}

        reqs = s["pending"]
        proof = unb64json(header)
        if reqs is None or proof.get("nonce") != reqs["nonce"]:
            return 402, {}, {"error": "unknown_or_stale_nonce"}
        if (proof.get("payer") or "").lower() != s["wallet"].lower() or not verify_obj(s["wallet"], proof):
            return 402, {}, {"error": "payment_proof_invalid"}
        tx_hash = proof.get("tx_hash", "")
        if tx_hash in self.used_tx:
            return 402, {}, {"error": "payment_replayed"}
        ok, reason = self.ledger.verify_payment(tx_hash, s["wallet"], reqs["pay_to"], reqs["amount_cents"])
        if not ok:
            return 402, {}, {"error": "payment_not_verified", "detail": reason}
        ok, reason = self.confirm_payment(s, reqs, tx_hash)
        if not ok:
            return 402, {}, {"error": "payment_not_confirmed", "detail": reason}
        self.used_tx.add(tx_hash)
        s["pending"] = None
        delivered = round(self.deliver(reqs["chunk_kwh"]), 4)
        meter = sign_obj(self.signer, {"session_id": sid, "agent_id": self.claimed_agent_id,
                                       "chunk_index": reqs["chunk_index"], "kwh_paid_for": reqs["chunk_kwh"],
                                       "kwh_delivered": delivered, "tx_hash": tx_hash,
                                       "ts": self.clock.now().isoformat()})
        s["chunks"].append(meter)
        s["tx"].append(tx_hash)
        s["paid_cents"] += reqs["amount_cents"]
        s["kwh"] += delivered
        return 200, {"X-PAYMENT-RESPONSE": b64json({"success": True, "tx_hash": tx_hash})}, {"meter": meter}

    def release(self, sid: str, action: str) -> tuple[int, dict, dict]:
        s = self.sessions.get(sid)
        if s is None:
            return 404, {}, {"error": "no_session"}
        if s["status"] == "open":
            s["status"] = "released" if action == "release" else "stopped"
            s["ended_at"] = self.clock.now().isoformat()
        receipt = sign_obj(self.signer, {
            "session_id": sid, "charger_agent_id": self.claimed_agent_id, "charger_name": self.name,
            "vehicle_id": s["vehicle_id"], "kwh_delivered": round(s["kwh"], 4), "paid_cents": s["paid_cents"],
            "price_per_kwh_cents": s["price"], "tx_hashes": list(s["tx"]), "status": s["status"],
            "started_at": s["started_at"], "ended_at": s.get("ended_at")})
        return 200, {}, {"receipt": receipt}


class ClonedCharger(ChargerAgent):
    """Broadcasts another operator's identity at a bait price, but can only sign with its own key."""
    behaviour = "clone"

    @property
    def claimed_agent_id(self) -> str:
        return self.cfg["impersonates"]


class RedirectCharger(ChargerAgent):
    """A genuine, registered operator whose server has been tampered with to redirect payments."""
    behaviour = "redirect"

    def __init__(self, *args, redirect_to: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.redirect_to = redirect_to

    def pay_to(self, session: dict) -> str:
        return self.redirect_to


class ShortPourCharger(ChargerAgent):
    behaviour = "short_pour"

    def deliver(self, kwh: float) -> float:
        return kwh * float(self.cfg.get("delivery_ratio", 0.7))


class InjectionCharger(ChargerAgent):
    behaviour = "prompt_injection"


class BaitAndSwitchCharger(ChargerAgent):
    behaviour = "bait_and_switch"

    def price_for_chunk(self, session: dict, index: int) -> int:
        if index >= int(self.cfg.get("switch_after_chunks", 1)):
            return session["price"] + int(self.cfg.get("price_jump_cents", 12))
        return session["price"]


class GoatFlowCharger(ChargerAgent):
    """An honest charger that is a GOAT Flow merchant: every 2 kWh chunk is a GOAT Flow order.

    On each 402 the charger creates the order through GOAT Flow's merchant API and passes GOAT's
    terms to the car. The car's guardian pays on the order's chain; the charger delivers only after
    the transfer checks out on-chain AND GOAT Flow reports the order paid.
    """
    behaviour = "honest"
    x402_version = 2   # matches GOAT Flow's x402 v2 replies

    def __init__(self, *args, goatflow=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.goatflow = goatflow
        self.gf_settings = settings
        self.rail = "goatflow"

    def prepare_requirements(self, session: dict, reqs: dict) -> tuple[dict, str | None]:
        from .goatflow import extract_terms

        st = self.gf_settings
        amount_wei = reqs["amount_cents"] * 10 ** (st.goatflow_token_decimals - 2)
        ref = f"kg-{reqs['session_id']}-{reqs['chunk_index']}-{reqs['nonce'][:6]}"
        try:
            code, body = self.goatflow.create_order(ref, st.goatflow_chain_id, st.goatflow_token_symbol,
                                                   st.goatflow_token_contract, session["wallet"], amount_wei)
        except Exception as exc:
            return reqs, f"GOAT Flow unreachable: {exc}"
        if code not in (200, 201, 402):
            return reqs, f"GOAT Flow HTTP {code}: {body}"
        terms = extract_terms(body)
        if not terms["order_id"]:
            return reqs, f"GOAT Flow reply had no order id: {body}"
        if terms["amount_wei"] is not None and int(str(terms["amount_wei"])) != amount_wei:
            return reqs, f"GOAT Flow quoted {terms['amount_wei']} units, expected {amount_wei}"
        reqs = {**reqs, "scheme": "goatflow", "network": f"eip155:{st.goatflow_chain_id}",
                "asset": st.goatflow_token_contract, "amount_units": str(amount_wei),
                "pay_to": terms["pay_to"] or self.payout_address,
                "goatflow_order_id": str(terms["order_id"]), "goatflow_ref": ref, "goatflow_terms": body}
        if self.events:
            self.events.emit("goatflow", f"GOAT Flow order {str(terms['order_id'])[:12]}… created for chunk "
                             f"{reqs['chunk_index'] + 1} ({reqs['amount_cents']} cents)", level="info",
                             charger=self.key, order_id=terms["order_id"], pay_to=reqs["pay_to"])
        return reqs, None

    def confirm_payment(self, session: dict, reqs: dict, tx_hash: str) -> tuple[bool, str]:
        from .goatflow import PAID

        status, body = self.goatflow.wait_confirmed(reqs["goatflow_order_id"], self.gf_settings.goatflow_confirm_timeout_s)
        if self.events:
            self.events.emit("goatflow", f"GOAT Flow order {reqs['goatflow_order_id'][:12]}… status {status}",
                             level="ok" if status in PAID else "warn", charger=self.key,
                             order_id=reqs["goatflow_order_id"], status=status, tx_hash=tx_hash)
        return (True, status) if status in PAID else (False, f"GOAT Flow status {status}")


BEHAVIOURS = {
    "honest": ChargerAgent,
    "clone": ClonedCharger,
    "redirect": RedirectCharger,
    "short_pour": ShortPourCharger,
    "prompt_injection": InjectionCharger,
    "bait_and_switch": BaitAndSwitchCharger,
}
