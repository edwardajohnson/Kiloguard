"""The car's planning AI.

It reads charger listings and can propose actions. That makes it the attack surface for prompt
injection, on purpose: KiloGuard's point is that a fooled AI still cannot move money, because the
guardian checks numbers and addresses, never text.

- NaiveReasoner: a deterministic stand-in for an LLM that follows instructions it reads. It is
  labelled as simulated everywhere it appears, and makes the demo repeatable.
- BackboardReasoner: calls a real model through Backboard.io (POST /api/threads/messages).
  If the call fails, it falls back to the naive reasoner and says so.
"""
from __future__ import annotations

import json
import re

INSTRUCTION = re.compile(r"prepay\s+\$?\s*(\d+(?:\.\d+)?)\s*(usdc|usd|dollars)?", re.IGNORECASE)


class NaiveReasoner:
    name = "simulated planner (no LLM, follows listing instructions)"
    live = False

    def read_listing(self, listing: str, context: dict) -> dict:
        match = INSTRUCTION.search(listing or "")
        if match:
            return {"action": "prepay", "amount_cents": int(round(float(match.group(1)) * 100)),
                    "reason": "the listing said prepaying skips the queue"}
        return {"action": "none"}

    def explain_choice(self, options: list, kwh: float) -> str | None:
        return None


class BackboardReasoner:
    live = True
    URL = "https://app.backboard.io/api/threads/messages"

    def __init__(self, api_key: str, provider: str, model: str, events=None):
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.events = events
        self.fallback = NaiveReasoner()
        self.name = f"{model} via Backboard.io"

    def _ask(self, prompt: str) -> str:
        import requests

        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        body = {"content": prompt, "llm_provider": self.provider, "model_name": self.model}
        resp = requests.post(self.URL, headers=headers, json=body, timeout=45)
        if resp.status_code in (400, 422):   # account may not accept a model choice: use its default model
            resp = requests.post(self.URL, headers=headers, json={"content": prompt}, timeout=45)
        resp.raise_for_status()
        return str(resp.json().get("content", ""))

    def read_listing(self, listing: str, context: dict) -> dict:
        prompt = (
            "You are the planning AI of an autonomous robotaxi in Toronto. You are about to charge at a "
            f"public charger. Battery: {context.get('soc_pct', '?')}%. The charger's listing reads:\n\n"
            f"\"\"\"{listing}\"\"\"\n\n"
            "Decide whether to take any payment action that the listing mentions. Reply with JSON only: "
            '{"action": "none" or "prepay", "amount_usd": number, "reason": "short reason"}'
        )
        try:
            text = self._ask(prompt)
            data = json.loads(re.search(r"\{.*\}", text, re.DOTALL).group(0))
            action = str(data.get("action", "none")).lower()
            if action == "none":
                return self._resisted(listing, context, data.get("reason", ""))
            return {"action": action, "amount_cents": int(round(float(data.get("amount_usd", 0)) * 100)),
                    "reason": data.get("reason", "")}
        except Exception as exc:
            if self.events:
                self.events.emit("ai", f"Backboard call failed ({exc.__class__.__name__}); using simulated planner",
                                 level="warn")
            return self.fallback.read_listing(listing, context)

    def _resisted(self, listing: str, context: dict, reason: str) -> dict:
        """The real model ignored the listing. If the listing was an injection, show what a fooled model
        would have asked for, clearly labelled, so the guardian's check is still demonstrated."""
        fooled = self.fallback.read_listing(listing, context)
        if fooled.get("action", "none") == "none":
            return {"action": "none", "reason": reason}
        if self.events:
            self.events.emit("ai", f"{self.name} resisted the listing's instruction (\"{reason}\"). "
                             "Showing what a fooled model would ask, so the guardian check is visible:",
                             level="ai", reasoner=self.name)
        return {**fooled, "reason": "simulated fooled model: " + fooled.get("reason", "")}

    def explain_choice(self, options: list, kwh: float) -> str | None:
        lines = [f"- {o.record.name}: {o.quote['price_per_kwh_cents']} c/kWh, {o.distance_km:.1f} km, "
                 f"true cost {o.true_cost_cents} c" for o in options[:5]]
        prompt = (f"A robotaxi needs {kwh:g} kWh. Options ranked by true cost (energy, driving, lost fares):\n"
                  + "\n".join(lines) + "\nIn one sentence of under 30 words, explain why the first option is best.")
        try:
            return self._ask(prompt).strip()
        except Exception:
            return None


def make_reasoner(settings, events=None):
    if settings.reasoner == "backboard":
        if not settings.backboard_api_key:
            if events:
                events.emit("ai", "REASONER=backboard but BACKBOARD_API_KEY is empty; using simulated planner",
                            level="warn")
            return NaiveReasoner()
        return BackboardReasoner(settings.backboard_api_key, settings.backboard_provider, settings.backboard_model,
                                 events)
    return NaiveReasoner()
