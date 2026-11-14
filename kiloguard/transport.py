"""How the car talks to a charger: in-process (fast, for simulation) or over real HTTP.

Both clients speak exactly the same protocol, so a scene behaves the same either way.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Response:
    status: int
    headers: dict
    json: dict


class LocalChargerClient:
    def __init__(self, agent):
        self.agent = agent
        self.base_url = f"local://{agent.key}"

    def request(self, method: str, path: str, params: dict | None = None, headers: dict | None = None,
                body: dict | None = None) -> Response:
        params = {k: str(v) for k, v in (params or {}).items()}
        status, hdrs, payload = self.agent.handle(method, path, params, headers or {}, body)
        return Response(status, {k.lower(): v for k, v in hdrs.items()}, payload)


class HttpChargerClient:
    def __init__(self, base_url: str, timeout: float = 200.0):
        import requests

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def request(self, method: str, path: str, params: dict | None = None, headers: dict | None = None,
                body: dict | None = None) -> Response:
        resp = self._session.request(method, self.base_url + path, params=params, headers=headers or {},
                                     json=body if method == "POST" else None, timeout=self.timeout)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": "non_json_response", "text": resp.text[:200]}
        return Response(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, payload)
