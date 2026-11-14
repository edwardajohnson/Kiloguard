"""Settings, loaded from environment variables and an optional .env file.

No third-party dotenv package is needed: the .env parser here is deliberately small.
Values already set in the environment win over the .env file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_json(name: str) -> dict | list:
    return json.loads((CONFIG_DIR / name).read_text())


@dataclass
class Settings:
    mode: str = "sim"                      # "sim" (in-memory ledger) or "goat" (GOAT Testnet3)
    rpc_url: str = "https://rpc.testnet3.goat.network"
    chain_id: int = 48816
    explorer_url: str = "https://explorer.testnet3.goat.network"
    network_label: str = "goat-testnet3"
    payment_asset: str = "native"          # "native" (test BTC) or "erc20" (a test stablecoin)
    stablecoin_address: str = ""
    wei_per_cent: int = 1_000_000_000      # native mode: how many wei of test BTC stand for one US cent
    identity: str = "local"                # "local" registry or "erc8004" (reads GOAT's ERC-8004 registry)
    erc8004_registry: str = ""
    reasoner: str = "naive"                # "naive" (simulated, gullible) or "backboard"
    backboard_api_key: str = ""
    backboard_provider: str = "openai"
    backboard_model: str = "gpt-4o"
    anchor_receipts: bool = True
    events_path: Path = ROOT / "runs" / "events.jsonl"

    @classmethod
    def load(cls, **overrides) -> "Settings":
        load_env()
        s = cls(
            mode=env("KILOGUARD_MODE", "sim"),
            rpc_url=env("GOAT_RPC_URL", cls.rpc_url),
            chain_id=int(env("GOAT_CHAIN_ID", str(cls.chain_id))),
            explorer_url=env("GOAT_EXPLORER_URL", cls.explorer_url).rstrip("/"),
            payment_asset=env("PAYMENT_ASSET", "native"),
            stablecoin_address=env("STABLECOIN_ADDRESS"),
            wei_per_cent=int(env("NATIVE_WEI_PER_CENT", str(cls.wei_per_cent))),
            identity=env("IDENTITY", "local"),
            erc8004_registry=env("ERC8004_IDENTITY_REGISTRY"),
            reasoner=env("REASONER", "naive"),
            backboard_api_key=env("BACKBOARD_API_KEY"),
            backboard_provider=env("BACKBOARD_PROVIDER", "openai"),
            backboard_model=env("BACKBOARD_MODEL", "gpt-4o"),
            anchor_receipts=env("ANCHOR_RECEIPTS", "1") not in ("0", "false", "no"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(s, key, value)
        if s.mode not in ("sim", "goat"):
            raise ValueError(f"KILOGUARD_MODE must be 'sim' or 'goat', not {s.mode!r}")
        if s.payment_asset == "erc20" and not s.stablecoin_address:
            raise ValueError("PAYMENT_ASSET=erc20 needs STABLECOIN_ADDRESS")
        return s

    @property
    def asset_label(self) -> str:
        if self.mode == "sim":
            return "USD (simulated)"
        return "test stablecoin" if self.payment_asset == "erc20" else "test BTC (demo-scaled)"

    def private_key(self, role: str) -> str | None:
        """Role is OWNER, GUARDIAN, VEHICLE, CHARGER_A, CHARGER_B or ATTACKER. Only used in goat mode."""
        if self.mode != "goat":
            return None
        return env(f"{role}_PRIVATE_KEY") or None
