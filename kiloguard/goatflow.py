"""Minimal GOAT Flow merchant API client (REST, HMAC-signed), from docs.goat.network:
GOAT Flow -> Reference -> API Reference.

  POST /api/v1/orders            create an order; GOAT answers 402 with the payment terms
  GET  /api/v1/orders/{order_id} read the order status

Signing: HMAC-SHA256 (lowercase hex) over "k=v&k=v" of the top-level body fields plus api_key,
timestamp and nonce, sorted by name, with empty strings removed and no URL-encoding.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

TESTNET3_API = "https://flow-api.testnet3.goat.network"
PAID = {"PAYMENT_CONFIRMED", "INVOICED"}
FAILED = {"FAILED", "EXPIRED", "CANCELLED"}


def _s(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sign(params: dict, api_key: str, api_secret: str, timestamp: str, nonce: str) -> str:
    fields = {k: _s(v) for k, v in params.items() if not isinstance(v, (dict, list))}
    fields.update({"api_key": api_key, "timestamp": timestamp, "nonce": nonce})
    fields.pop("sign", None)
    message = "&".join(f"{k}={v}" for k, v in sorted(fields.items()) if v != "")
    return hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class GoatFlowClient:
    def __init__(self, api_key: str, api_secret: str, merchant_id: str, base_url: str = TESTNET3_API):
        import requests

        self.api_key, self.api_secret, self.merchant_id = api_key, api_secret, merchant_id
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()

    def _headers(self, params: dict) -> dict:
        ts, nonce = str(int(time.time())), uuid.uuid4().hex
        return {"X-API-Key": self.api_key, "X-Timestamp": ts, "X-Nonce": nonce,
                "X-Sign": sign(params, self.api_key, self.api_secret, ts, nonce), "Content-Type": "application/json"}

    def create_order(self, dapp_order_id: str, chain_id: int, token_symbol: str, token_contract: str,
                     from_address: str, amount_wei: int) -> tuple[int, dict]:
        body = {"dapp_order_id": dapp_order_id, "chain_id": chain_id, "token_symbol": token_symbol,
                "token_contract": token_contract, "from_address": from_address, "amount_wei": str(amount_wei),
                "merchant_id": self.merchant_id}
        body = {k: v for k, v in body.items() if v not in ("", None)}
        r = self.http.post(self.base_url + "/api/v1/orders", json=body, headers=self._headers(body), timeout=30)
        return r.status_code, _json(r)

    def get_order(self, order_id: str) -> tuple[int, dict]:
        r = self.http.get(f"{self.base_url}/api/v1/orders/{order_id}", headers=self._headers({}), timeout=30)
        return r.status_code, _json(r)


def _json(resp) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500], "headers": dict(resp.headers)}
