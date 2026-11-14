<p align="center"><img src="dashboard/static/kiloguard-logo.png" alt="KiloGuard" width="360"></p>

# KiloGuard

**The car can ask to pay. Only the guardian can sign.**

A driverless robotaxi in a Toronto fleet buys its own electricity at 2am. Its AI decides when and where to
charge, but holds no keys. A separate guardian, bound by the fleet owner's policy, is the only signer: it pays
per 2 kWh chunk against a signed meter reading, only to the charger's registered payout address, and only when
the car is physically at the charger. Built for the Agentic Payments Hackathon Toronto (Highline Beta,
November 13 to 15, 2026) by Edward Johnson and Yash Chitransh.

## What the demo shows

| Scene | What happens | What stops it |
|---|---|---|
| 1. Happy path | Unit 07 compares 9 chargers on true cost, buys 18 kWh at Dufferin & King in nine $0.64 payments | Nothing: it works |
| 2. Cloned charger | Front St advertises itself as Dufferin & King at a bait price | Quote signature does not match the registered key; the car never drives there |
| 3. Redirected payment | King West, a real operator, asks to be paid at an attacker's wallet | Guardian refuses: payee is not the registered payout address. Attacker gets $0 |
| 4. Short pour | Queen West takes payment for 2 kWh and delivers 1.4 | Signed meter reading fails the 97% check; car stops after one chunk ($0.54), files a dispute, reputation drops |
| 5. Poisoned listing | Fort York's listing tells car AIs to "prepay 500 USDC" | The AI falls for it; the guardian refuses: it pays metered chunks only, and $500 breaks every cap |
| 6. Bait and switch (bonus) | Spadina raises its price after the first chunk | Guardian refuses: price changed mid-session |
| Fleet day | 20 cars, 24 hours, fares in and energy out | Each car has its own guardian; one bad experience lowers a charger's shared reputation and the fleet avoids it |

## Quick start (simulation, no keys needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/run_demo.py                              # scenes 1-5 in the terminal
python -m pytest -q                                     # 22 tests
```

Live dashboard (two terminals):

```bash
python dashboard/server.py                              # open http://127.0.0.1:8000
python scripts/run_demo.py --pace 0.8 --fleet           # scenes, then the fleet day
```

The dashboard uses OpenStreetMap tiles (no API key) when online and falls back to a built-in map otherwise.
Open `http://127.0.0.1:8000/?map=offline` to force the built-in map, for example on unreliable venue Wi-Fi.
Add `--transport http` to run every charger as a real HTTP server that answers `402 Payment Required`.

## Real payments on GOAT Testnet3

```bash
cp .env.example .env
python scripts/make_wallets.py      # creates OWNER, GUARDIAN, VEHICLE, CHARGER_A, CHARGER_B, ATTACKER keys in .env
# Fund GUARDIAN (the car's wallet) with test BTC: https://bridge.testnet3.goat.network/faucet
python scripts/goat_check.py        # RPC, chain id 48816, balances
python scripts/run_demo.py --mode goat --scenes 1 --pace 0.5
```

Every payment and receipt anchor in goat mode is a real transaction; the dashboard links each one to
`explorer.testnet3.goat.network`. Scene 1 sends 10 transactions (9 chunks and 1 anchor); scenes 2 to 5 send
about 3 each. Use `--no-anchor` to skip anchors.

**Payment asset.** Prices are always in US cents. With `PAYMENT_ASSET=native` the guardian pays in test BTC at
`NATIVE_WEI_PER_CENT` (default 1 gwei per cent, so $5.76 is 0.000000576 test BTC). Once a test stablecoin on
GOAT is confirmed, set `PAYMENT_ASSET=erc20` and `STABLECOIN_ADDRESS`; the guardian then sends ERC-20 transfers
and chargers verify the `Transfer` event.

**ERC-8004 identity and reputation on GOAT.** No AgentKit or TypeScript needed:

```bash
python scripts/register_agents.py --dry-run   # Unit 07 (by OWNER), Dufferin (CHARGER_A), four scene chargers (CHARGER_B)
python scripts/register_agents.py             # registers them; agent ids are saved into config/chargers.json
# then set IDENTITY=erc8004 in .env
python scripts/run_demo.py --mode goat --scenes 1,2,4 --pace 0.5
```

The registering wallets need a little test BTC for gas. With `IDENTITY=erc8004`, each charger's signing and payout
address is read from GOAT's identity registry (`getAgentWallet`, else the token owner), so the clone in scene 2 is
caught against the chain, not a config file. Every rating the car gives (5 for a good session, 1 for a short pour
or a redirect) is also written to GOAT's ERC-8004 reputation registry from the car's wallet, so the whole fleet can
read it. Registry addresses (Testnet3): identity `0x54B8d8E2455946f2A5B8982283f2359812e815ce`, reputation
`0xd9140951d8aE6E5F625a02F5908535e16e3af964`. Set `ONCHAIN_REPUTATION=0` to keep ratings local.

**Verify any receipt.** `python scripts/verify_receipts.py` checks every session from the last run: the charger's
signed receipt, its hash against the anchored hash, the payments against the receipt total and, in goat mode, the
anchor and payment transactions on GOAT. The dashboard has the same check as a **Verify receipt** button.

**Separate charger processes.** In goat mode the chain is the shared ledger, so chargers can run as their own
servers: `python -m kiloguard.charger_server --all`, then `python scripts/run_demo.py --transport http --external`.

## The car's AI and Backboard.io

`REASONER=naive` (default) is a simulated planner, no LLM, that deliberately follows instructions it reads in
listings. It makes scene 5 repeatable. `REASONER=backboard` with `BACKBOARD_API_KEY` sends the same listing to a
real model through Backboard.io (`POST https://app.backboard.io/api/threads/messages`) and also asks it to explain
the charger choice. If the model resists the injection, the guardian's refusal is simply not needed; either way
no money moves. If a Backboard call fails, the demo falls back to the simulated planner and says so.

## How it fits together

```
 Fleet owner ──signs policy & attestation──▶ Guardian (only signer) ◀── fares ── Rideshare network
                                               ▲          │
                                   asks to pay │          │ pays one chunk, then proves it (tx hash)
                                               │          ▼
 Vehicle agent (AI, no keys) ◀── 402 quote, handshake, signed meter readings, receipt ──▶ Charger agent
          │                                                                                 │
          └────────── identity checks ──▶ Registry (local or ERC-8004) ◀── reputation ──────┘
                                               GOAT Testnet3: payments + receipt anchors
```

| File | Role |
|---|---|
| `kiloguard/guardian.py` | Policy engine and the only payment key: caps, payee, price lock, proximity, replay, escalation, kill switch, audit log |
| `kiloguard/vehicle.py` | Intent, discovery, true-cost ranking, handshake, chunk loop, meter checks, disputes, receipts |
| `kiloguard/charger.py` | x402-style charger protocol and the five attack behaviours |
| `kiloguard/ledger.py` | Simulated ledger and GOAT Testnet3 ledger (native test BTC or ERC-20) |
| `kiloguard/identity.py` | Identity registry and reputation; ERC-8004 identity and reputation on GOAT |
| `kiloguard/verify.py` | Independent receipt verification (CLI and dashboard button) |
| `kiloguard/reasoning.py` | Simulated planner and Backboard.io planner |
| `kiloguard/scenarios.py`, `simulator.py` | The demo night and the 20-car fleet day |
| `dashboard/` | Live map and decision feed |
| `config/policy.json`, `config/chargers.json` | Spending policy and the charger directory |

## Payment and value flow

Prices are set in US cents. On GOAT Network Testnet3 (chain ID 48816) the car pays in test BTC at a fixed demo
rate, or in a test stablecoin once one is confirmed; the demo also runs on a simulated ledger.

1. **Funding.** The fleet owner funds the car's wallet, which only the guardian controls, and signs a policy:
   $40 per session, $120 per day, $0.45 per kWh, minimum charger reputation 4.0, 3 sessions per hour.
2. **Earning.** The rideshare network pays fares into the car's wallet (simulated).
3. **Trigger.** The car charges when its battery cannot cover the next forecast trip plus reserve, or when
   off-peak power is cheap.
4. **Quote.** Each charger answers `GET /quote` with HTTP 402 and a signed quote. The car verifies the signature
   against the registry and ranks chargers on true cost: energy, the drive, and fares lost while parked.
5. **Authorise.** After a signed mutual handshake, each `POST /session/{id}/chunk` returns 402 with payment
   requirements. The guardian checks payee, locked price, amount, chunk size, distance, expiry, nonce and caps,
   then pays one chunk.
6. **Settle and verify.** The guardian sends the payment and retries with an `X-PAYMENT` header carrying the
   signed transaction hash. The charger checks payer, payee, amount, success and that the hash is unused, then
   delivers 2 kWh and returns a signed meter reading, which the car verifies before paying for the next chunk.
7. **Audit.** The charger signs a receipt listing every transaction hash. The guardian writes the receipt's
   hash on-chain and the car posts a reputation score.

**Failure and reversal.** There is no prepayment: chunked payment works as rolling escrow, so the most a bad
charger can take is one chunk. On short delivery, a price change or a fault, the car stops, keeps the signed
evidence, files a dispute and lowers the charger's reputation. Spending past the daily cap goes to the fleet
owner for approval; the owner can also switch everything off with the kill switch.

## Security and failure-mode analysis

Design rule: the agent that reasons never holds the keys.

| Threat | Control | In the demo |
|---|---|---|
| Car's AI or session hijacked | AI has no key; guardian pays one capped chunk at a time; owner kill switch | Tests |
| Cloned charger identity | Quotes and session grants must be signed by the registered key; location must match | Scene 2 |
| Payment redirection | Payee must equal the registered payout address | Scene 3 |
| Short delivery | Pay per chunk, verify the signed meter reading (97% tolerance), stop, dispute, reputation | Scene 4 |
| Prompt injection | Guardian ignores text; pays metered chunks only; caps | Scene 5 |
| Price changed mid-session | Price locked at handshake; every chunk must match | Scene 6 |
| Runaway loop | 3 sessions per hour, daily cap with owner escalation | Tests |
| Paying from a distance | Payment only within 15 m of the registered charger location | Tests |
| Replayed payment | One-time nonces, expiry, used-transaction tracking on both sides | Tests |
| Unauthorised vehicle | Charger requires the fleet owner's signed attestation and the car's signature | Tests |

**Known gaps.** GPS spoofing defeats the proximity check (production would add a plug ID signed at connection).
A dishonest operator can sign false meter readings (mitigated by reputation and the car's own battery telemetry).
The guardian is a single point of failure (production keeps its key in secure hardware with owner-held recovery).
Settlement is a direct transfer proven by transaction hash rather than through a hosted x402 facilitator; GOAT's
facilitator can replace `GoatLedger` without touching the guardian.

## Dependencies and licences

| Component | Use | Licence |
|---|---|---|
| GOAT Network Testnet3 | Settlement and receipt anchors | Public network |
| x402 (HTTP 402 payment pattern) | Payment request and proof headers | Apache-2.0 (spec) |
| ERC-8004 (GOAT Testnet3 registries) | Agent identity and reputation | CC0 (EIP) |
| Backboard.io | Optional LLM reasoning | Proprietary API |
| Python 3.10+ | Runtime | PSF |
| Starlette, Uvicorn | Charger servers and dashboard | BSD-3-Clause |
| Requests | HTTP client, Backboard calls | Apache-2.0 |
| web3.py, eth-account | GOAT transactions and signatures | MIT |
| pytest | Tests | MIT |
| Leaflet (unpkg) | Dashboard map | BSD-2-Clause |
| OpenStreetMap data and tiles | Dashboard map tiles (no API key) | ODbL; OSM tile usage policy |
| Inter, JetBrains Mono (Google Fonts) | Dashboard type | OFL-1.1 |
| KiloGuard | This repository | Apache-2.0 |

## How this was built

Prepared before the event: concept, name, logo, domain and design notes. All code was written during the
hackathon weekend (November 13 to 15, 2026), with help from AI coding assistants (Claude). Charger names,
locations and prices are fictional; the time-of-use multipliers are illustrative, not Ontario's published rates.
