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
    # ERC-8004 registries on GOAT Testnet3, from docs.goat.network (agents / agent-kit / erc-8004)
    erc8004_registry: str = "0x54B8d8E2455946f2A5B8982283f2359812e815ce"
    erc8004_reputation: str = "0xd9140951d8aE6E5F625a02F5908535e16e3af964"
    onchain_reputation: bool = True        # erc8004 mode: also post feedback to the reputation registry
    reasoner: str = "naive"              # "naive" (simulated, gullible) or "backboard"
    backboard_api_key: str = ""
    backboard_provider: str = "openai"
    backboard_model: str = "gpt-4o"
    anchor_receipts: bool = True
    events_path: Path = ROOT / "runs" / "events.jsonl"
    # Payment rail. "direct": the guardian pays the charger on GOAT and proves it by tx hash.
    # "goatflow": Dufferin & King is a GOAT Flow merchant; every chunk is a GOAT Flow order.
    payment_rail: str = "direct"
    goatflow_api_url: str = "https://flow-api.testnet3.goat.network"
    goatflow_merchant_id: str = ""
    goatflow_api_key: str = ""
    goatflow_api_secret: str = ""
    goatflow_chain_id: int = 48816
    goatflow_token_symbol: str = "USDC"
    goatflow_token_contract: str = ""
    goatflow_token_decimals: int = 6
    goatflow_confirm_timeout_s: int = 120
    payment_rpc_url: str = ""              # RPC of the chain GOAT Flow orders settle on (defaults by chain id)
    payment_explorer_url: str = ""
    trusted_settlement: str = ""           # comma list of GOAT Flow settlement addresses the owner allows as payee
    ledger_name: str = ""

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
            erc8004_registry=env("ERC8004_IDENTITY_REGISTRY", cls.erc8004_registry),
            erc8004_reputation=env("ERC8004_REPUTATION_REGISTRY", cls.erc8004_reputation),
            onchain_reputation=env("ONCHAIN_REPUTATION", "1") not in ("0", "false", "no"),
            reasoner=env("REASONER", "naive"),
            backboard_api_key=env("BACKBOARD_API_KEY"),
            backboard_provider=env("BACKBOARD_PROVIDER", "openai"),
            backboard_model=env("BACKBOARD_MODEL", "gpt-4o"),
            anchor_receipts=env("ANCHOR_RECEIPTS", "1") not in ("0", "false", "no"),
            payment_rail=env("PAYMENT_RAIL", "direct"),
            goatflow_api_url=env("GOATFLOW_API_URL", cls.goatflow_api_url),
            goatflow_merchant_id=env("GOATFLOW_MERCHANT_ID"),
            goatflow_api_key=env("GOATFLOW_API_KEY"),
            goatflow_api_secret=env("GOATFLOW_API_SECRET"),
            goatflow_chain_id=int(env("GOATFLOW_CHAIN_ID", "48816")),
            goatflow_token_symbol=env("GOATFLOW_TOKEN_SYMBOL", "USDC"),
            goatflow_token_contract=env("GOATFLOW_TOKEN_CONTRACT"),
            goatflow_token_decimals=int(env("GOATFLOW_TOKEN_DECIMALS", "6")),
            goatflow_confirm_timeout_s=int(env("GOATFLOW_CONFIRM_TIMEOUT_S", "120")),
            payment_rpc_url=env("PAYMENT_RPC_URL"),
            payment_explorer_url=env("PAYMENT_EXPLORER_URL"),
            trusted_settlement=env("TRUSTED_SETTLEMENT"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(s, key, value)
        if s.mode not in ("sim", "goat"):
            raise ValueError(f"KILOGUARD_MODE must be 'sim' or 'goat', not {s.mode!r}")
        if s.payment_asset == "erc20" and not s.stablecoin_address:
            raise ValueError("PAYMENT_ASSET=erc20 needs STABLECOIN_ADDRESS")
        return s

    def payment_chain_settings(self) -> "Settings":
        """Settings for the ledger that GOAT Flow orders settle on (GOAT Testnet3 or Ethereum Sepolia)."""
        from dataclasses import replace

        known = {48816: ("https://rpc.testnet3.goat.network", "https://explorer.testnet3.goat.network", "GOAT Testnet3"),
                 11155111: ("https://ethereum-sepolia-rpc.publicnode.com", "https://sepolia.etherscan.io",
                            "Ethereum Sepolia")}
        rpc, explorer, name = known.get(self.goatflow_chain_id, ("", "", f"chain {self.goatflow_chain_id}"))
        return replace(self, rpc_url=self.payment_rpc_url or rpc, explorer_url=(self.payment_explorer_url or explorer),
                       chain_id=self.goatflow_chain_id, payment_asset="erc20",
                       stablecoin_address=self.goatflow_token_contract, ledger_name=name)

    @property
    def trusted_settlement_addresses(self) -> set[str]:
        return {a.strip().lower() for a in self.trusted_settlement.split(",") if a.strip()}

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
