"""The demo night: one happy path and the attacks, each a short scene.

Scenes 2-6 buy 4 kWh (two chunks) to keep on-chain transactions few in goat mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .util import usd


@dataclass
class Scene:
    number: int
    title: str
    story: str
    attacks: list
    kwh: float


SCENES = {
    1: Scene(1, "Happy path: Unit 07 buys its own electricity",
             "2:07am in Liberty Village, battery 22%. The 7am rush needs about 18 kWh and off-peak power is cheap.",
             [], 18.0),
    2: Scene(2, "Attack 1: the cloned charger",
             "Front St Fast Charge advertises itself as Dufferin & King at a bait price.", ["front-st"], 4.0),
    3: Scene(3, "Attack 2: the redirected payment",
             "King West Charging is a real operator, but its server now asks to be paid at someone else's wallet.",
             ["king-west"], 4.0),
    4: Scene(4, "Attack 3: the short pour",
             "Queen West Charging takes payment for 2 kWh and delivers 1.4.", ["queen-west"], 4.0),
    5: Scene(5, "Attack 4: the poisoned listing",
             "Fort York Charging's listing hides an instruction aimed at the car's AI.", ["fort-york"], 4.0),
    6: Scene(6, "Bonus: bait and switch",
             "Spadina Charging locks a low price, then raises it after the first chunk.", ["spadina"], 4.0),
}

HOME = (43.6383, -79.4200)  # Liberty Village


def run_scene(world, number: int) -> list:
    scene = SCENES[number]
    car = world.car
    # The scenes happen through the same night, spaced 75 minutes apart.
    world.clock.set(datetime(2026, 11, 14, 2, 7) + timedelta(minutes=75 * (number - 1)))
    car.lat, car.lon = HOME
    car.blocked.clear()
    if number == 1:
        car.soc_kwh = 13.2
    else:
        car.soc_kwh = min(car.soc_kwh, 40.0)
    world.events.emit("scene", f"Scene {number}. {scene.title}", level="scene", scene=number, title=scene.title,
                      story=scene.story)
    world.events.emit("car", scene.story, level="info", car_id=car.vehicle_id)
    car.position_event()
    if number == 1:
        need, why = car.needs_charge(next_trip_kwh=18.0, cheap_power=True)
        world.events.emit("car", f"Intent: {why}" if need else "Intent: no charge needed", level="info",
                          car_id=car.vehicle_id)
    results = car.charge(world.directory(scene.attacks), scene.kwh)
    kwh = sum(r.kwh for r in results)
    paid = sum(r.paid_cents for r in results)
    world.events.emit("scene_result", f"Scene {number} done: {kwh:g} kWh for {usd(paid)} "
                      f"(spent today {usd(world.guardian.spent_today())})", level="ok", scene=number, kwh=kwh,
                      paid_cents=paid, results=[r.__dict__ for r in results])
    return results


def run_night(world, scenes: list[int]) -> dict:
    blocked_before = sum(1 for a in world.guardian.audit if not a["approved"])
    all_results = {}
    for n in scenes:
        all_results[n] = run_scene(world, n)
    blocked = sum(1 for a in world.guardian.audit if not a["approved"]) - blocked_before
    identity_blocks = len(world.events.of("identity", code="identity_fail"))
    disputes = len(world.events.of("dispute"))
    paid = sum(r.paid_cents for rs in all_results.values() for r in rs)
    kwh = sum(r.kwh for rs in all_results.values() for r in rs)
    lost = sum(r.paid_cents for rs in all_results.values() for r in rs if r.code == "short_delivery")
    summary = {"scenes": scenes, "kwh": round(kwh, 2), "paid_cents": paid, "guardian_blocks": blocked,
               "identity_blocks": identity_blocks, "disputes": disputes, "lost_to_bad_chargers_cents": lost,
               "balance_cents": world.ledger.balance_cents(world.guardian.address)}
    summary["attacks_stopped"] = blocked + identity_blocks + disputes
    n = summary["attacks_stopped"]
    lost_txt = f"lost to bad chargers: {usd(lost)} (one chunk)" if lost else "nothing lost to bad chargers"
    world.events.emit("summary", f"Night summary: {kwh:g} kWh bought for {usd(paid)}; "
                      f"{n} attack{'s' if n != 1 else ''} stopped; {lost_txt}", level="scene", **summary)
    return summary
