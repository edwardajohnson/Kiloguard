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


    def wait_confirmed(self, order_id: str, timeout_s: int = 120, interval_s: float = 3.0) -> tuple[str, dict]:
        """Polls the order until GOAT reports paid, failed, or the timeout passes. Returns (status, body)."""
        deadline = time.time() + timeout_s
        status, body = "UNKNOWN", {}
        while time.time() < deadline:
            code, body = self.get_order(order_id)
            status = str(find(body, "status") or "UNKNOWN").upper()
            if status in PAID or status in FAILED or code in (401, 403, 404):
                return status, body
            time.sleep(interval_s)
        return status, body


def find(obj, *names):
    """Depth-first search for the first value under any of `names` (case-insensitive) in nested JSON."""
    wanted = {n.lower() for n in names}
    stack = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in wanted and v not in (None, ""):
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def extract_terms(body: dict) -> dict:
    """Pulls the payment terms out of GOAT Flow's 402 reply.

    GOAT's docs do not publish the full reply schema, so this looks under the usual names
    (GOAT Flow, x402 and camelCase variants). The raw reply is always kept alongside.
    """
    return {
        "order_id": find(body, "order_id", "orderId", "id"),
        "pay_to": find(body, "pay_to", "payTo", "to_address", "toAddress", "receiving_address", "recipient"),
        "amount_wei": find(body, "amount_wei", "amountWei", "maxAmountRequired", "amount"),
        "token_contract": find(body, "token_contract", "tokenContract", "asset"),
        "chain_id": find(body, "chain_id", "chainId"),
        "expires_at": find(body, "expiresAt", "expires_at", "expiry"),
        "typed_data": find(body, "eip712", "typedData", "typed_data", "calldataSignRequest", "sign_data"),
    }


def _json(resp) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500], "headers": dict(resp.headers)}
