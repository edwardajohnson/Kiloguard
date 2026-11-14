"""Keep tests independent of whatever is in the developer's .env or shell.

Every test starts from the code defaults (simulation mode, direct rail, local identity), so a
.env set up for live GOAT runs (KILOGUARD_MODE=goat, PAYMENT_RAIL=goatflow, ...) cannot leak in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kiloguard.config as config  # noqa: E402

PREFIXES = ("KILOGUARD_", "PAYMENT_", "GOATFLOW_", "GOAT_", "IDENTITY", "ERC8004_", "ONCHAIN_", "REASONER",
            "BACKBOARD_", "ANCHOR_", "NATIVE_", "STABLECOIN_", "TRUSTED_")


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    monkeypatch.setattr(config, "load_env", lambda path=None: None)
    for name in list(os.environ):
        if name.startswith(PREFIXES) or name.endswith("_PRIVATE_KEY"):
            monkeypatch.delenv(name, raising=False)
