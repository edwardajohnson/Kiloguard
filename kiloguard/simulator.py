"""Fleet simulator: 20 robotaxis, one day, every charge going through its own guardian.

Always runs on the simulated ledger. It shows the design at fleet scale: fares in, energy out,
attacks met at random and stopped. The dashboard shows car positions as they move.
"""
from __future__ import annotations

import random
from collections import defaultdict

from .crypto import make_signer
from .events import EventBus
from .guardian import Guardian
from .identity import AgentRecord
from .reasoning import NaiveReasoner
from .util import tou_period, usd
from .vehicle import VehicleAgent
from .world import make_attestation

BBOX = (43.63, 43.72, -79.47, -79.33)   # downtown Toronto, roughly
DEMAND_BY_HOUR = [0.15, 0.1, 0.08, 0.06, 0.06, 0.1, 0.35, 0.8, 0.9, 0.6, 0.5, 0.55,
                  0.6, 0.55, 0.5, 0.6, 0.8, 0.9, 0.85, 0.7, 0.6, 0.5, 0.4, 0.25]


def run_fleet(world, cars: int = 20, hours: int = 24, step_min: int = 10, seed: int = 7,
              with_attacks: bool = True) -> dict:
    if world.settings.mode != "sim":
        raise SystemExit("The fleet simulator runs on the simulated ledger only (KILOGUARD_MODE=sim).")
    rng = random.Random(seed)
    main = world.events
    clock = world.clock
    quiet = EventBus(None, echo=False, clock=clock)
    attacks = [k for k, a in world.chargers.items() if a.cfg.get("attack")] if with_attacks else []
    directory = world.directory(attacks)
    fleet = []
    stats = defaultdict(lambda: defaultdict(int))
    for i in range(1, cars + 1):
        vid = f"unit-{i:02d}"
        g_signer = make_signer(f"{vid}-guardian-fleet")
        identity = make_signer(f"{vid}-fleet")
        guardian = Guardian(g_signer, world.policy, world.ledger, world.registry, quiet, clock,
                            anchor_receipts=False, label=f"{vid} guardian")
        world.registry.register(AgentRecord(vid, vid, "vehicle", identity.address, guardian.address))
        world.ledger.fund(guardian.address, 2000)
        lat, lon = rng.uniform(BBOX[0], BBOX[1]), rng.uniform(BBOX[2], BBOX[3])
        car = VehicleAgent(vid, identity, make_attestation(world.owner, vid, identity, guardian), guardian,
                           world.registry, NaiveReasoner(), quiet, clock, world.policy, lat, lon,
                           soc_kwh=rng.uniform(12, 50), advance_clock=False)
        car.busy_until = clock.now()
        fleet.append(car)

    main.emit("scene", f"Fleet simulation: {cars} cars, {hours} hours", level="scene", scene="fleet",
              story="Every car has its own guardian; each remembers chargers that cheated it.")
    registry_events, world.registry.events = world.registry.events, quiet
    steps = int(hours * 60 / step_min)
    for _ in range(steps):
        now = clock.now()
        demand = DEMAND_BY_HOUR[now.hour]
        period, _ = tou_period(now)
        for car in fleet:
            s = stats[car.vehicle_id]
            if car.busy_until > now:
                continue
            next_trip = rng.uniform(2, 5)
            need, _why = car.needs_charge(next_trip, cheap_power=(period == "off-peak" and demand < 0.4))
            if need:
                target = min(30.0, car.battery_kwh * 0.9 - car.soc_kwh)
                results = car.charge(directory, max(2.0, target), verbose=False)
                for r in results:
                    s["sessions"] += 1
                    s["energy_cents"] += r.paid_cents
                    s["kwh"] += int(round(r.kwh))
                    if not r.ok:
                        s["stopped_bad_charger"] += 1
                minutes = sum(r.minutes for r in results) or step_min
                car.busy_until = now + _td(minutes)
            elif rng.random() < demand:
                dist = rng.uniform(3, 15)
                fare = int(300 + 150 * dist)
                car.soc_kwh = max(0.0, car.soc_kwh - dist * car.kwh_per_km)
                car.lat, car.lon = rng.uniform(BBOX[0], BBOX[1]), rng.uniform(BBOX[2], BBOX[3])
                world.ledger.fund(car.guardian.address, fare)   # the rideshare network pays the fare
                s["trips"] += 1
                s["fares_cents"] += fare
                car.busy_until = now + _td(dist / 25 * 60 + 5)
        main.emit("fleet_tick", cars=[{"car_id": c.vehicle_id, "lat": round(c.lat, 5), "lon": round(c.lon, 5),
                                       "soc_pct": c.soc_pct} for c in fleet])
        clock.advance(step_min)

    world.registry.events = registry_events
    denials = [e for e in quiet.events if e["kind"] == "guardian" and not e.get("approved")]
    blocks = [e for e in denials if e.get("code") != "low_reputation"]
    reputation_skips = [e for e in denials if e.get("code") == "low_reputation"]
    identity_blocks = [e for e in quiet.events if e["kind"] == "identity" and e.get("level") == "deny"]
    disputes = [e for e in quiet.events if e["kind"] == "dispute"]
    total = {k: sum(s[k] for s in stats.values()) for k in
             ("trips", "fares_cents", "sessions", "energy_cents", "kwh", "stopped_bad_charger")}
    total.update({"guardian_blocks": len(blocks), "identity_blocks": len(identity_blocks),
                  "disputes": len(disputes), "reputation_skips": len(reputation_skips),
                  "attacks_stopped": len(blocks) + len(identity_blocks) + len(disputes),
                  "net_cents": total["fares_cents"] - total["energy_cents"],
                  "blocks_by_code": _count(e.get("code", e["kind"]) for e in blocks + identity_blocks + disputes)})
    main.emit("fleet_summary", f"Fleet day: {total['trips']} trips, fares {usd(total['fares_cents'])}, "
              f"{total['kwh']} kWh for {usd(total['energy_cents'])}, net {usd(total['net_cents'])}. "
              f"{total['attacks_stopped']} attack attempts stopped; shared reputation steered cars away from "
              f"flagged chargers {total['reputation_skips']} times", level="scene", **total)
    return total


def _td(minutes: float):
    from datetime import timedelta
    return timedelta(minutes=minutes)


def _count(items) -> dict:
    out: dict = defaultdict(int)
    for i in items:
        out[i] += 1
    return dict(out)
