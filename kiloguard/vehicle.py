"""The vehicle agent: the car's brain. It plans, compares and negotiates. It holds no payment key.

Loop: Intent (needs_charge) -> Quote (discover, rank by true cost) -> Policy (guardian pre-check)
-> Settlement (handshake, then pay per chunk against signed meter readings) -> Audit (receipt,
on-chain anchor, reputation feedback).
"""
from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field

from .crypto import canonical, verify_obj
from .util import haversine_m, usd

CITY_SPEED_KMH = 25.0
CHARGER_KW = 50.0
# Results where the car's own guardian says stop: trying another charger would not help.
GUARDIAN_STOPS = {"session_rate", "kill_switch", "daily_cap", "payment_failed"}


@dataclass
class Option:
    entry: dict
    quote: dict
    record: object
    distance_km: float
    true_cost_cents: int
    breakdown: dict


@dataclass
class SessionResult:
    ok: bool
    code: str
    charger_id: str
    charger_name: str = ""
    kwh: float = 0.0
    paid_cents: int = 0
    receipt: dict | None = None
    tx_hashes: list = field(default_factory=list)
    anchor_tx: str | None = None
    minutes: float = 0.0


class VehicleAgent:
    def __init__(self, vehicle_id: str, identity, attestation: dict, guardian, registry, reasoner, events, clock,
                 policy, lat: float, lon: float, battery_kwh: float = 60.0, soc_kwh: float = 13.2,
                 kwh_per_km: float = 0.18, fare_cents_per_min: float = 60.0, demand_factor: float = 0.1,
                 advance_clock: bool = True):
        self.vehicle_id = vehicle_id
        self.identity = identity
        self.attestation = attestation
        self.guardian = guardian          # the car can ask; only the guardian can sign
        self.registry = registry
        self.reasoner = reasoner
        self.events = events
        self.clock = clock
        self.policy = policy
        self.lat, self.lon = lat, lon
        self.battery_kwh = battery_kwh
        self.soc_kwh = soc_kwh
        self.kwh_per_km = kwh_per_km
        self.fare_cents_per_min = fare_cents_per_min
        self.demand_factor = demand_factor
        self.advance_clock = advance_clock
        self.blocked: set[str] = set()

    # --- state ------------------------------------------------------------------------------
    @property
    def soc_pct(self) -> int:
        return int(round(100 * self.soc_kwh / self.battery_kwh))

    def position_event(self) -> None:
        self.events.emit("car_position", car_id=self.vehicle_id, lat=self.lat, lon=self.lon, soc_pct=self.soc_pct)

    # --- Intent -----------------------------------------------------------------------------
    def needs_charge(self, next_trip_kwh: float, cheap_power: bool) -> tuple[bool, str]:
        if self.soc_kwh < self.policy.reserve_kwh + next_trip_kwh:
            return True, f"battery {self.soc_pct}% will not cover the next forecast trip plus reserve"
        if cheap_power and self.soc_kwh < self.policy.opportunistic_soc * self.battery_kwh:
            return True, f"battery {self.soc_pct}% and power is cheap off-peak"
        return False, ""

    # --- Quote ------------------------------------------------------------------------------
    def discover(self, directory: list[dict], kwh: float, verbose: bool = True) -> list[Option]:
        options: list[Option] = []
        for entry in directory:
            if entry["key"] in self.blocked:
                continue
            dist_km = haversine_m(self.lat, self.lon, entry["lat"], entry["lon"]) / 1000
            if dist_km > self.policy.search_radius_km:
                continue
            try:
                resp = entry["client"].request("GET", "/quote", params={"vehicle_id": self.vehicle_id, "kwh": kwh})
            except Exception as exc:
                self.events.emit("quote", f"{entry['name']} unreachable ({exc.__class__.__name__})", level="warn")
                continue
            quote = resp.json.get("quote") if resp.status == 402 else None
            if not quote:
                continue
            record = self.registry.lookup(quote.get("agent_id", ""))
            if record is None or not verify_obj(record.signing_address, quote):
                self.blocked.add(entry["key"])
                claimed = record.name if record else quote.get("agent_id")
                self.events.emit("identity", f"{entry['name']} claims to be {claimed}, but its quote is not signed "
                                 "by that operator's registered key: cloned charger, ignored and reported",
                                 level="deny", charger=entry["key"], code="identity_fail", claimed=quote.get("agent_id"))
                continue
            if record.lat is not None and haversine_m(record.lat, record.lon, entry["lat"], entry["lon"]) > 50:
                self.blocked.add(entry["key"])
                self.events.emit("identity", f"{entry['name']} is not at {record.name}'s registered location",
                                 level="deny", charger=entry["key"], code="location_mismatch")
                continue
            pre = self.guardian.precheck(quote, record)
            if not pre.approved:
                continue
            price = int(quote["price_per_kwh_cents"])
            drive_min = dist_km / CITY_SPEED_KMH * 60
            charge_min = kwh / CHARGER_KW * 60
            idle_min = drive_min + float(quote.get("queue_wait_min", 0)) + charge_min
            breakdown = {"energy": round(price * kwh), "drive": round(dist_km * self.kwh_per_km * price),
                         "lost_fares": round(idle_min * self.fare_cents_per_min * self.demand_factor)}
            options.append(Option(entry, quote, record, dist_km, sum(breakdown.values()), breakdown))
        options.sort(key=lambda o: o.true_cost_cents)
        if verbose:
            self.events.emit("quote", f"{len(options)} chargers passed identity and policy checks"
                             + (f"; best: {options[0].record.name} at {usd(options[0].quote['price_per_kwh_cents'])}"
                                f"/kWh, true cost {usd(options[0].true_cost_cents)}" if options else ""),
                             level="info", options=[{"charger": o.entry["key"], "name": o.record.name,
                                                     "price": o.quote["price_per_kwh_cents"],
                                                     "true_cost_cents": o.true_cost_cents,
                                                     "distance_km": round(o.distance_km, 2)} for o in options[:6]])
        return options

    # --- the whole job --------------------------------------------------------------------------
    def charge(self, directory: list[dict], kwh_target: float, verbose: bool = True,
               max_attempts: int = 3) -> list[SessionResult]:
        chunk = self.policy.chunk_kwh
        remaining = math.ceil(kwh_target / chunk - 1e-9) * chunk
        results: list[SessionResult] = []
        for _ in range(max_attempts):
            if remaining <= 1e-9:
                break
            options = self.discover(directory, remaining, verbose)
            if not options:
                self.events.emit("car", f"{self.vehicle_id}: no acceptable charger; alerting the fleet owner",
                                 level="warn", car_id=self.vehicle_id)
                break
            best = options[0]
            if verbose and getattr(self.reasoner, "live", False):
                why = self.reasoner.explain_choice(options, remaining)
                if why:
                    self.events.emit("ai", f"AI: {why}", level="ai", reasoner=self.reasoner.name)
            self._read_listing(best)
            result = self._execute(best, remaining, verbose)
            results.append(result)
            remaining = max(0.0, remaining - result.kwh)
            remaining = math.ceil(remaining / chunk - 1e-9) * chunk if remaining > 0.05 else 0.0
            if not result.ok:
                if result.code in GUARDIAN_STOPS:
                    self.events.emit("car", f"{self.vehicle_id}: guardian says stop ({result.code}); not retrying",
                                     level="warn", car_id=self.vehicle_id)
                    break
                self.blocked.add(best.entry["key"])
        return results

    def _read_listing(self, option: Option) -> None:
        proposal = self.reasoner.read_listing(option.quote.get("listing", ""),
                                              {"soc_pct": self.soc_pct, "vehicle_id": self.vehicle_id})
        if proposal.get("action", "none") == "none":
            return
        self.events.emit("ai", f"Car's AI ({self.reasoner.name}) read {option.record.name}'s listing and wants to "
                         f"{proposal['action']} {usd(proposal.get('amount_cents', 0))}: \"{proposal.get('reason', '')}\"",
                         level="ai", proposal=proposal, charger=option.entry["key"], reasoner=self.reasoner.name)
        self.guardian.authorize_request(proposal, option.record)

    # --- Settlement -----------------------------------------------------------------------------
    def _drive_to(self, lat: float, lon: float, name: str) -> float:
        dist_km = haversine_m(self.lat, self.lon, lat, lon) / 1000
        self.soc_kwh = max(0.0, self.soc_kwh - dist_km * self.kwh_per_km)
        self.lat, self.lon = lat, lon
        minutes = dist_km / CITY_SPEED_KMH * 60
        if self.advance_clock:
            self.clock.advance(minutes)
        self.events.emit("car", f"{self.vehicle_id} drove {dist_km:.1f} km to {name}", level="info",
                         car_id=self.vehicle_id, lat=lat, lon=lon, soc_pct=self.soc_pct)
        self.position_event()
        return minutes

    def _execute(self, option: Option, kwh: float, verbose: bool) -> SessionResult:
        rec, client, quote = option.record, option.entry["client"], option.quote
        result = SessionResult(False, "started", rec.agent_id, rec.name)
        result.minutes += self._drive_to(option.entry["lat"], option.entry["lon"], rec.name)

        nonce = secrets.token_hex(8)
        body = {"vehicle_id": self.vehicle_id, "identity_address": self.identity.address,
                "wallet_address": self.guardian.address, "owner_attestation": self.attestation,
                "quote_id": quote["quote_id"], "kwh_requested": kwh, "nonce": nonce,
                "lat": self.lat, "lon": self.lon}
        body["signature"] = self.identity.sign(canonical(body))
        resp = client.request("POST", "/session", body=body)
        if resp.status != 200:
            result.code = "handshake_refused"
            self.events.emit("car", f"{rec.name} refused the handshake: {resp.json.get('error')}", level="deny",
                             charger=rec.agent_id)
            return result
        grant = resp.json
        if grant.get("nonce") != nonce or not verify_obj(rec.signing_address, grant):
            result.code = "spoofed_charger"
            self.events.emit("identity", f"{rec.name}'s session grant is not signed by its registered key",
                             level="deny", charger=rec.agent_id, code="spoofed_charger")
            return result
        sid = grant["session_id"]
        self.events.emit("car", f"Mutual handshake with {rec.name} verified; robotic arm connecting", level="ok",
                         charger=rec.agent_id, session_id=sid)
        opened = self.guardian.open_session(sid, rec, int(grant["price_per_kwh_cents"]))
        if not opened.approved:
            client.request("POST", f"/session/{sid}/stop")
            result.code = opened.code
            return result

        chunks = math.ceil(kwh / self.policy.chunk_kwh - 1e-9)
        stop_code = None
        for _ in range(chunks):
            r = client.request("POST", f"/session/{sid}/chunk")
            if r.status != 402 or not r.json.get("accepts"):
                stop_code = "bad_payment_request"
                break
            reqs = r.json["accepts"][0]
            decision = self.guardian.authorize_chunk(reqs, self.lat, self.lon)
            if not decision.approved:
                stop_code = decision.code
                self.registry.give_feedback(rec.agent_id, 1, decision.code, by=self.vehicle_id)
                break
            result.tx_hashes.append(decision.tx_hash)
            result.paid_cents += decision.amount_cents
            r2 = client.request("POST", f"/session/{sid}/chunk", headers={"X-PAYMENT": decision.payload})
            if r2.status != 200:
                stop_code = "payment_rejected"
                self.events.emit("car", f"{rec.name} rejected our payment: {r2.json}", level="warn",
                                 charger=rec.agent_id)
                break
            meter = r2.json.get("meter", {})
            if (not verify_obj(rec.signing_address, meter) or meter.get("tx_hash") != decision.tx_hash
                    or meter.get("session_id") != sid):
                stop_code = "meter_invalid"
                self.events.emit("meter", f"Meter reading from {rec.name} is not validly signed", level="deny",
                                 charger=rec.agent_id)
                break
            delivered = float(meter["kwh_delivered"])
            paid_for = float(reqs["chunk_kwh"])
            self.soc_kwh = min(self.battery_kwh, self.soc_kwh + delivered)
            result.kwh += delivered
            minutes = delivered / CHARGER_KW * 60
            result.minutes += minutes
            if self.advance_clock:
                self.clock.advance(minutes)
            ok = delivered >= paid_for * self.policy.meter_tolerance
            self.events.emit("meter", f"Signed meter reading: {delivered:g} of {paid_for:g} kWh delivered "
                             f"(battery {self.soc_pct}%)", level="ok" if ok else "deny", charger=rec.agent_id,
                             kwh_delivered=delivered, kwh_paid_for=paid_for, soc_pct=self.soc_pct, car_id=self.vehicle_id)
            if not ok:
                stop_code = "short_delivery"
                self.events.emit("dispute", f"Short delivery at {rec.name}: paid for {paid_for:g} kWh, got "
                                 f"{delivered:g}. Stopping after one chunk; signed meter reading and "
                                 f"transaction {decision.tx_hash[:12]}… kept as evidence", level="deny",
                                 charger=rec.agent_id, evidence={"meter": meter, "tx_hash": decision.tx_hash})
                self.registry.give_feedback(rec.agent_id, 1, "short delivery", by=self.vehicle_id)
                break

        end = client.request("POST", f"/session/{sid}/{'stop' if stop_code else 'release'}")
        receipt = end.json.get("receipt") if end.status == 200 else None
        self.guardian.close_session(sid)
        if receipt and verify_obj(rec.signing_address, receipt):
            result.receipt = receipt
            self.events.emit("receipt", f"Signed receipt from {rec.name}: {receipt['kwh_delivered']:g} kWh, "
                             f"{usd(receipt['paid_cents'])}, {len(receipt['tx_hashes'])} payments, status "
                             f"{receipt['status']}", level="ok" if not stop_code else "warn",
                             charger=rec.agent_id, receipt=receipt)
            if result.paid_cents:
                result.anchor_tx = self.guardian.anchor(receipt)
        elif end.status == 200:
            self.events.emit("receipt", f"Receipt from {rec.name} is not validly signed", level="deny",
                             charger=rec.agent_id)
        self.events.emit("car", f"Arm released from {rec.name}", level="info", charger=rec.agent_id)

        if stop_code:
            result.code = stop_code
            return result
        result.ok = True
        result.code = "charged"
        self.registry.give_feedback(rec.agent_id, 5, "delivered as metered", by=self.vehicle_id)
        return result
