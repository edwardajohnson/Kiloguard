"""End-to-end and unit tests. Run with:  python -m pytest -q

All tests use the simulated ledger, so they run anywhere without keys or network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiloguard.config import Settings  # noqa: E402
from kiloguard.crypto import b64json, unb64json  # noqa: E402
from kiloguard.events import EventBus  # noqa: E402
from kiloguard.scenarios import run_night, run_scene  # noqa: E402
from kiloguard.simulator import run_fleet  # noqa: E402
from kiloguard.world import build_world  # noqa: E402


def make_world(tmp_path, transport="local", **kw):
    settings = Settings.load(mode="sim", reasoner="naive", identity="local",
                             events_path=tmp_path / "events.jsonl")
    return build_world(settings, EventBus(settings.events_path, echo=False), transport=transport,
                       use_config_ports=False, **kw)


# --- the five demo scenes ----------------------------------------------------------------------

def test_happy_path_buys_10_kwh_in_five_metered_chunks(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 1)
    assert len(results) == 1 and results[0].ok
    r = results[0]
    assert r.charger_id == "dufferin"
    assert r.kwh == pytest.approx(10.0)
    assert r.paid_cents == 320 and len(r.tx_hashes) == 5
    assert r.receipt["status"] == "released" and r.receipt["paid_cents"] == 320
    assert r.anchor_tx
    dufferin = w.registry.lookup("dufferin").payout_address
    assert w.ledger.balance_cents(dufferin) == 320


def test_cloned_charger_is_blocked_before_the_car_moves(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 2)
    assert w.events.of("identity", code="identity_fail", charger="front-st")
    assert all(r.charger_id != "front-st" for r in results)
    assert w.ledger.balance_cents(w.attacker.address) == 0
    assert results[-1].ok and results[-1].charger_id == "dufferin"


def test_redirected_payment_is_refused_and_attacker_gets_nothing(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 3)
    assert results[0].charger_id == "king-west" and results[0].code == "payee_mismatch"
    assert results[0].paid_cents == 0
    assert w.ledger.balance_cents(w.attacker.address) == 0
    assert w.registry.lookup("king-west").reputation < w.policy.min_charger_reputation
    assert results[-1].ok  # recovered at an honest charger


def test_short_pour_costs_at_most_one_chunk(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 4)
    bad = results[0]
    assert bad.charger_id == "queen-west" and bad.code == "short_delivery"
    assert bad.paid_cents == 54 and len(bad.tx_hashes) == 1
    assert bad.kwh == pytest.approx(1.4)
    assert w.events.of("dispute", charger="queen-west")
    assert w.registry.lookup("queen-west").reputation < w.policy.min_charger_reputation
    assert results[-1].ok


def test_prompt_injection_is_refused_by_the_guardian(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 5)
    denied = [a for a in w.guardian.audit if a["code"] == "unmetered_payment"]
    assert denied and denied[0]["amount_cents"] == 50000
    assert w.events.of("ai", charger="fort-york")
    assert results[0].ok and results[0].paid_cents == 104  # it still charges normally, chunk by chunk


def test_bait_and_switch_stops_at_the_price_change(tmp_path):
    w = make_world(tmp_path)
    results = run_scene(w, 6)
    assert results[0].charger_id == "spadina" and results[0].code == "price_changed"
    assert results[0].paid_cents == 48


def test_whole_night_summary(tmp_path):
    w = make_world(tmp_path)
    summary = run_night(w, [1, 2, 3, 4, 5])
    assert summary["attacks_stopped"] == 4
    assert summary["lost_to_bad_chargers_cents"] == 54
    assert summary["balance_cents"] == 5000 - summary["paid_cents"]


def test_scenes_work_over_real_http(tmp_path):
    w = make_world(tmp_path, transport="http")
    try:
        results = run_scene(w, 1)
        assert results[0].ok and results[0].paid_cents == 320
        results = run_scene(w, 3)
        assert results[0].code == "payee_mismatch"
    finally:
        w.stop()


# --- guardian controls ------------------------------------------------------------------------

def test_kill_switch_stops_everything(tmp_path):
    w = make_world(tmp_path)
    w.policy.kill_switch = True
    results = w.car.charge(w.directory(), 4)
    assert sum(r.paid_cents for r in results) == 0
    assert any(a["code"] == "kill_switch" for a in w.guardian.audit)


def test_daily_cap_escalates_to_owner(tmp_path):
    w = make_world(tmp_path)
    w.policy.max_per_day_cents = 300
    results = run_scene(w, 1)
    assert results[0].code == "daily_cap" and results[0].paid_cents == 256  # 4 chunks; the 5th would pass $3.00
    asked = []
    w2 = make_world(tmp_path / "b", owner_approval=lambda amount, why: asked.append(amount) or True)
    w2.policy.max_per_day_cents = 300
    results = run_scene(w2, 1)
    assert results[0].ok and asked


def test_payment_only_at_the_charger(tmp_path):
    w = make_world(tmp_path)
    rec = w.registry.lookup("dufferin")
    w.guardian.open_session("s-test", rec, 32)
    reqs = {"session_id": "s-test", "nonce": "n1", "expires_at": "2099-01-01T00:00:00", "pay_to": rec.payout_address,
            "price_per_kwh_cents": 32, "chunk_kwh": 2.0, "amount_cents": 64, "chunk_index": 0}
    far = w.guardian.authorize_chunk(reqs, 43.70, -79.40)
    assert not far.approved and far.code == "not_at_charger"
    near = w.guardian.authorize_chunk(reqs, rec.lat, rec.lon)
    assert near.approved and near.tx_hash
    again = w.guardian.authorize_chunk(reqs, rec.lat, rec.lon)
    assert again.code == "replay"


def test_runaway_loop_is_capped(tmp_path):
    w = make_world(tmp_path)
    rec = w.registry.lookup("dufferin")
    codes = [w.guardian.open_session(f"s{i}", rec, 32).code for i in range(4)]
    assert codes[:3] == ["session_open"] * 3 and codes[3] == "session_rate"


def test_charger_rejects_replayed_payment_proof(tmp_path):
    w = make_world(tmp_path)
    car, charger = w.car, w.chargers["dufferin"]
    car.lat, car.lon = charger.lat, charger.lon
    client = w.clients["dufferin"]
    quote = client.request("GET", "/quote", params={"kwh": 4}).json["quote"]
    from kiloguard.crypto import canonical
    body = {"vehicle_id": car.vehicle_id, "identity_address": car.identity.address,
            "wallet_address": w.guardian.address, "owner_attestation": car.attestation, "quote_id": quote["quote_id"],
            "kwh_requested": 4, "nonce": "abc", "lat": car.lat, "lon": car.lon}
    body["signature"] = car.identity.sign(canonical(body))
    sid = client.request("POST", "/session", body=body).json["session_id"]
    w.guardian.open_session(sid, w.registry.lookup("dufferin"), 32)
    reqs = client.request("POST", f"/session/{sid}/chunk").json["accepts"][0]
    d = w.guardian.authorize_chunk(reqs, car.lat, car.lon)
    assert client.request("POST", f"/session/{sid}/chunk", headers={"X-PAYMENT": d.payload}).status == 200
    reqs2 = client.request("POST", f"/session/{sid}/chunk").json["accepts"][0]
    stale = unb64json(d.payload)
    stale["nonce"] = reqs2["nonce"]  # same old transaction, new nonce: signature no longer matches
    r = client.request("POST", f"/session/{sid}/chunk", headers={"X-PAYMENT": b64json(stale)})
    assert r.status == 402


def test_unsigned_vehicle_is_refused(tmp_path):
    w = make_world(tmp_path)
    charger = w.chargers["dufferin"]
    status, _, body = charger.handle("POST", "/session", body={
        "vehicle_id": "intruder", "identity_address": "0x0", "wallet_address": "0x0",
        "owner_attestation": {}, "lat": charger.lat, "lon": charger.lon})
    assert status == 403 and body["error"] == "owner_attestation_invalid"


def test_fleet_day_runs_and_stops_attacks(tmp_path):
    w = make_world(tmp_path)
    total = run_fleet(w, cars=8, hours=12, step_min=15)
    assert total["trips"] > 0 and total["sessions"] > 0
    assert total["fares_cents"] > total["energy_cents"]
