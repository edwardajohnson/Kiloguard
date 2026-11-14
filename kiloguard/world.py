"""Builds the whole demo world from config: ledger, registry, chargers, the fleet owner and Unit 07."""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field

from .charger import BEHAVIOURS, ChargerAgent, GoatFlowCharger
from .config import Settings, load_json
from .crypto import make_signer, sign_obj
from .events import EventBus
from .guardian import Guardian
from .identity import AgentRecord, Erc8004Registry, LocalRegistry
from .ledger import GoatLedger, SimLedger
from .policy import SpendPolicy
from .reasoning import make_reasoner
from .transport import HttpChargerClient, LocalChargerClient
from .util import SimClock
from .vehicle import VehicleAgent

SIM_STARTING_BALANCE_CENTS = 5000


@dataclass
class World:
    settings: Settings
    events: EventBus
    clock: SimClock
    policy: SpendPolicy
    ledger: object
    registry: LocalRegistry
    owner: object
    attacker: object
    chargers: dict[str, ChargerAgent]
    clients: dict[str, object]
    car: VehicleAgent
    guardian: Guardian
    servers: list = field(default_factory=list)

    def directory(self, enabled_attacks: list[str] | None = None) -> list[dict]:
        """The public charger directory the car searches. Attack chargers appear only when a scene enables them."""
        enabled = set(enabled_attacks or [])
        out = []
        for key, agent in self.chargers.items():
            if agent.cfg.get("attack") and key not in enabled:
                continue
            out.append({"key": key, "name": agent.name, "lat": agent.lat, "lon": agent.lon,
                        "client": self.clients[key], "behaviour": agent.behaviour})
        return out

    def stop(self) -> None:
        for server in self.servers:
            server.should_exit = True


def make_attestation(owner, vehicle_id: str, identity, guardian) -> dict:
    return sign_obj(owner, {"vehicle_id": vehicle_id, "identity_address": identity.address,
                            "wallet_address": guardian.address, "owner_address": owner.address,
                            "statement": "authorised to buy charging within the fleet spending policy"})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_http(chargers: dict, use_config_ports: bool) -> tuple[dict, list]:
    import uvicorn

    from .charger_server import make_app

    clients, servers = {}, []
    for key, agent in chargers.items():
        port = agent.cfg.get("port") if use_config_ports else _free_port()
        server = uvicorn.Server(uvicorn.Config(make_app(agent), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        clients[key] = HttpChargerClient(f"http://127.0.0.1:{port}")
    deadline = time.time() + 15
    while not all(s.started for s in servers):
        if time.time() > deadline:
            raise RuntimeError("charger HTTP servers did not start")
        time.sleep(0.05)
    return clients, servers


def build_world(settings: Settings | None = None, events: EventBus | None = None, transport: str = "local",
                external: bool = False, use_config_ports: bool = True, reasoner=None,
                owner_approval=None) -> World:
    settings = settings or Settings.load()
    clock = SimClock()
    events = events or EventBus(settings.events_path, clock=clock)
    events.clock = clock
    cfg = load_json("chargers.json")
    policy = SpendPolicy.from_dict(load_json("policy.json"))

    if settings.mode == "goat":
        missing = [r for r in ("GUARDIAN", "OWNER", "VEHICLE", "CHARGER_A", "CHARGER_B", "ATTACKER")
                   if not settings.private_key(r)]
        if "GUARDIAN" in missing:
            raise SystemExit("goat mode needs GUARDIAN_PRIVATE_KEY in .env (run scripts/make_wallets.py, then fund it)")
        if missing:
            events.emit("run", f"No key for {', '.join(missing)}: using simulation keys for those roles "
                        "(fine for receiving payments, not for paying)", level="warn")
    ledger = GoatLedger(settings) if settings.mode == "goat" else SimLedger()
    pay_ledger, goatflow = ledger, None
    if settings.payment_rail == "goatflow":
        if settings.mode != "goat":
            raise SystemExit("PAYMENT_RAIL=goatflow needs KILOGUARD_MODE=goat")
        missing = [n for n in ("goatflow_api_key", "goatflow_api_secret", "goatflow_merchant_id",
                               "goatflow_token_contract") if not getattr(settings, n)]
        if missing:
            raise SystemExit("PAYMENT_RAIL=goatflow needs " + ", ".join(m.upper() for m in missing) + " in .env")
        from .goatflow import GoatFlowClient

        pay_ledger = GoatLedger(settings.payment_chain_settings())
        goatflow = GoatFlowClient(settings.goatflow_api_key, settings.goatflow_api_secret,
                                  settings.goatflow_merchant_id, settings.goatflow_api_url)
        events.emit("run", f"Payment rail: GOAT Flow merchant '{settings.goatflow_merchant_id}', orders settle in "
                    f"{settings.goatflow_token_symbol} on {pay_ledger.name}; receipts anchored on {ledger.name}",
                    level="info")
    if settings.identity == "erc8004":
        if settings.mode != "goat" or not settings.erc8004_registry:
            raise ValueError("IDENTITY=erc8004 needs KILOGUARD_MODE=goat and ERC8004_IDENTITY_REGISTRY")
        registry = Erc8004Registry(ledger, settings.erc8004_registry, settings.erc8004_reputation, events,
                                   onchain_feedback=settings.onchain_reputation)
    else:
        registry = LocalRegistry(events)

    owner = make_signer("fleet-owner", settings.private_key("OWNER"))
    attacker = make_signer("attacker", settings.private_key("ATTACKER"))
    operators = {"A": make_signer("operator-a", settings.private_key("CHARGER_A")),
                 "B": make_signer("operator-b", settings.private_key("CHARGER_B")),
                 "attacker": attacker}
    registry.register(AgentRecord("fleet-owner", "Fleet owner", "fleet_owner", owner.address, owner.address))

    chargers: dict[str, ChargerAgent] = {}
    for c in cfg["chargers"]:
        signer = operators.get(c.get("key", "sim")) or make_signer("operator-" + c["id"])
        behaviour = c.get("behaviour", "honest")
        kwargs = {"redirect_to": attacker.address} if behaviour == "redirect" else {}
        cls, charger_ledger = BEHAVIOURS[behaviour], pay_ledger   # every charger checks the asset the guardian pays in
        if goatflow is not None and c.get("key") == "A":   # Dufferin & King is the GOAT Flow merchant
            cls, charger_ledger = GoatFlowCharger, pay_ledger
            kwargs = {"goatflow": goatflow, "settings": settings}
        agent = cls(c, signer, signer.address, registry, charger_ledger, clock, events, policy,
                    settings.network_label, settings.asset_label, **kwargs)
        chargers[c["id"]] = agent
        if behaviour != "clone":  # a clone has no identity of its own; it borrows someone else's
            registry.register(AgentRecord(c["id"], c["name"], "charger", signer.address, signer.address,
                                          c["lat"], c["lon"], float(c.get("reputation", 4.5))))
    if isinstance(registry, Erc8004Registry):
        for c in cfg["chargers"]:
            if c.get("erc8004_agent_id") is not None:
                registry.bind_onchain(c["id"], int(c["erc8004_agent_id"]))

    v = cfg["vehicle"]
    guardian_signer = make_signer(f"{v['id']}-guardian", settings.private_key("GUARDIAN"))
    identity = make_signer(v["id"], settings.private_key("VEHICLE"))
    guardian = Guardian(guardian_signer, policy, pay_ledger, registry, events, clock, owner_approval=owner_approval,
                        anchor_receipts=settings.anchor_receipts, label=f"{v['name']} guardian",
                        anchor_ledger=ledger, trusted_settlement=settings.trusted_settlement_addresses)
    vehicle_rec = registry.register(AgentRecord(v["id"], v["name"], "vehicle", identity.address, guardian.address))
    if isinstance(registry, Erc8004Registry):
        registry.feedback_signer = guardian_signer   # the car's wallet is the "client" that rates chargers
        if v.get("erc8004_agent_id") is not None:
            vehicle_rec.onchain_id = int(v["erc8004_agent_id"])
            events.emit("identity", f"{v['name']} is ERC-8004 agent #{vehicle_rec.onchain_id} on GOAT",
                        level="ok", car_id=v["id"], onchain_id=vehicle_rec.onchain_id)
    if isinstance(ledger, SimLedger):
        ledger.fund(guardian.address, SIM_STARTING_BALANCE_CENTS)
    car = VehicleAgent(v["id"], identity, make_attestation(owner, v["id"], identity, guardian), guardian, registry,
                       reasoner or make_reasoner(settings, events), events, clock, policy, v["lat"], v["lon"],
                       battery_kwh=float(v["battery_kwh"]), soc_kwh=float(v["soc_kwh"]))

    servers: list = []
    if transport == "http" and external:
        clients = {key: HttpChargerClient(f"http://127.0.0.1:{a.cfg['port']}") for key, a in chargers.items()}
    elif transport == "http":
        clients, servers = _start_http(chargers, use_config_ports)
    else:
        clients = {key: LocalChargerClient(a) for key, a in chargers.items()}

    return World(settings, events, clock, policy, ledger, registry, owner, attacker, chargers, clients, car,
                 guardian, servers)
