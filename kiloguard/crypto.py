"""Signatures for quotes, handshakes, meter readings, payment proofs and receipts.

Two kinds of signer:
- EthSigner: a real secp256k1 key (EIP-191 personal_sign), used in goat mode. Verifiable by anyone.
- SimSigner: a deterministic HMAC signer, used only in simulation mode and for the simulated
  background chargers. It lets the whole demo run on a laptop with no keys and no network.

Every signed object is a JSON dict with a "signature" field over the canonical JSON of the rest.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

_SIM_SECRETS: dict[str, bytes] = {}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return "0x" + hashlib.sha256(text.encode()).hexdigest()


def b64json(obj: dict) -> str:
    return base64.b64encode(canonical(obj).encode()).decode()


def unb64json(value: str) -> dict:
    return json.loads(base64.b64decode(value.encode()).decode())


def eth_available() -> bool:
    try:
        import eth_account  # noqa: F401
    except ImportError:
        return False
    return True


class SimSigner:
    kind = "sim"

    def __init__(self, label: str):
        self.label = label
        self._secret = hashlib.sha256(("kiloguard-sim:" + label).encode()).digest()
        self.address = "0x" + hashlib.sha256(b"addr" + self._secret).hexdigest()[:40]
        _SIM_SECRETS[self.address.lower()] = self._secret

    def sign(self, message: str) -> str:
        return "sim:" + hmac.new(self._secret, message.encode(), hashlib.sha256).hexdigest()


class EthSigner:
    kind = "eth"

    def __init__(self, private_key: str, label: str = ""):
        from eth_account import Account

        self.label = label
        self.account = Account.from_key(private_key)
        self.address = self.account.address

    def sign(self, message: str) -> str:
        from eth_account.messages import encode_defunct

        signed = self.account.sign_message(encode_defunct(text=message))
        return "0x" + bytes(signed.signature).hex()


def make_signer(label: str, private_key: str | None = None):
    if private_key:
        if not eth_available():
            raise RuntimeError("A private key was given but eth-account is not installed. Run: pip install -r requirements.txt")
        return EthSigner(private_key, label)
    return SimSigner(label)


def verify(address: str, message: str, signature: str) -> bool:
    if not address or not signature:
        return False
    if signature.startswith("sim:"):
        secret = _SIM_SECRETS.get(address.lower())
        if secret is None:
            return False
        expected = "sim:" + hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    if not eth_available():
        return False
    from eth_account import Account
    from eth_account.messages import encode_defunct

    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception:
        return False
    return recovered.lower() == address.lower()


def sign_obj(signer, obj: dict) -> dict:
    body = {k: v for k, v in obj.items() if k != "signature"}
    return {**body, "signature": signer.sign(canonical(body))}


def verify_obj(address: str, obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    body = {k: v for k, v in obj.items() if k != "signature"}
    return verify(address, canonical(body), obj.get("signature", ""))
