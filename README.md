# KiloGuard

> **The car can ask to pay. Only the guardian can sign.**

KiloGuard is a secure agentic-payment system for autonomous vehicles. It allows a driverless EV to discover charging stations, compare prices, purchase electricity, and verify delivery without giving the vehicle’s AI direct control of its wallet.

The prototype was created for the **Agentic Payments Hackathon in Toronto** and demonstrates the complete agent-payment lifecycle:

**Intent → Policy → Quote → Settlement → Audit**

[Demo video](VIDEO_URL) · [Application](APPLICATION_URL)

> Replace `VIDEO_URL` and `APPLICATION_URL` when the links are available.

---

## The Problem

Autonomous vehicles and machines increasingly need to purchase services without waiting for human approval. Giving an AI agent unrestricted access to a wallet, however, creates significant risks:

- A malicious charger could impersonate a trusted operator.
- A payment address could be redirected.
- A charger could accept payment without delivering the promised energy.
- Prompt injection could convince the vehicle agent to ignore spending limits.
- A compromised agent could repeatedly make unauthorized purchases.

KiloGuard separates **reasoning authority** from **payment authority**.

The vehicle agent may find, compare, and request purchases, but only an independent guardian can approve and sign payments.

---

## How It Works

A simulated autonomous taxi, **Unit 07**, finishes a trip in Toronto with a 22% battery. It estimates that it needs 18 kWh before the morning rush and searches for nearby charging stations.

The vehicle evaluates each charger using its total operational cost:

- Electricity price
- Travel distance
- Energy required to reach the charger
- Queue time
- Potential fares lost while charging
- Charger reputation

Before any payment is made, the guardian verifies that the proposed transaction complies with the fleet owner’s policy:

```yaml
max_session_spend: 40.00
max_daily_spend: 120.00
max_price_per_kwh: 0.45
minimum_charger_reputation: 4.0
payment_chunk_kwh: 2
```

The selected charger offers electricity at **0.32 USDC per kWh**. Unit 07 purchases 18 kWh through nine independently verified 2 kWh transactions:

```text
9 chunks × 2 kWh × 0.32 USDC = 5.76 USDC
```

After each payment, the charger provides a signed meter reading. The next payment is authorized only after delivery has been verified. This limits the maximum payment exposure to one chunk, or **0.64 USDC** in the demonstration.

---

## Architecture

The vehicle and charger negotiate directly, but all payments pass through the guardian.

```text
Fleet Owner
    │
    │ Spending policy
    ▼
Guardian ───────────────► GOAT x402 Facilitator
    ▲                              │
    │ Payment request              │ Settlement
    │                              ▼
Vehicle Agent ◄──────────────► Charger Agent
    │       Quote, identity,       │
    │       and meter readings     │
    │                              ▼
    └──────► ERC-8004 Registry   Receipt and Audit
```

### Main Components

| Component | Responsibility |
|---|---|
| Fleet owner | Funds the wallet and defines spending policies |
| Vehicle agent | Determines when, where, and how much energy to purchase |
| Guardian | Validates policy and acts as the only payment signer |
| Charger agent | Provides x402 quotes, energy delivery, and signed meter readings |
| x402 facilitator | Verifies and settles individual payment chunks |
| ERC-8004 registry | Stores agent identity, wallet address, and reputation |
| Rideshare network | Dispatches trips and pays fares to the vehicle |
| Audit service | Stores receipts, decisions, transaction hashes, and security events |

The settlement network is accessed through an adapter, allowing the payment layer to be replaced or extended without rewriting the vehicle, guardian, or charger services.

---

## Value Flow

1. **Fund**  
   The fleet owner funds the vehicle wallet and signs its spending policy.

2. **Earn**  
   The rideshare network deposits fares into the vehicle’s wallet.

3. **Discover**  
   The vehicle finds nearby chargers and requests their payment terms.

4. **Quote**  
   Each charger responds using HTTP `402 Payment Required` with its price, asset, network, payment address, chunk size, and quote expiry.

5. **Authorize**  
   The guardian checks the request against spending limits, proximity rules, charger identity, reputation, and registered payment address.

6. **Settle**  
   The guardian signs one payment chunk. The x402 facilitator verifies and settles it.

7. **Deliver**  
   The charger delivers 2 kWh and returns a signed meter reading.

8. **Verify**  
   The vehicle verifies delivery before requesting another payment.

9. **Audit**  
   The charger creates a signed receipt containing the delivered energy, total cost, and transaction hashes. The receipt hash is anchored on-chain.

10. **Handle exceptions**  
    If delivery is incomplete, the price changes, or verification fails, the session stops and evidence is retained for review.

Payments use a stable-value test asset, while network transaction fees are paid using the settlement network’s native token.

---

## Security Model

KiloGuard is built around one principle:

> **The agent that reasons is never the agent that signs.**

The vehicle’s AI can be manipulated or compromised without gaining unrestricted control over the wallet. The guardian independently enforces structured rules covering numbers, identities, addresses, and transaction limits.

### Demonstrated Attacks

| Attack | Protection |
|---|---|
| Cloned charger identity | Signed handshake is checked against the charger’s ERC-8004 identity |
| Payment redirection | The quote’s payment address must match the operator’s registered address |
| Short energy delivery | Energy is purchased in small chunks and each signed meter reading is verified |
| Prompt injection | The guardian ignores listing instructions and enforces deterministic policy |
| Mid-session price change | Every payment must match the original locked quote |
| Runaway payment loop | Daily limits, session limits, and rate limits prevent wallet draining |
| Compromised vehicle agent | The vehicle AI holds no signing key |
| Remote payment attempt | The vehicle must be within the approved distance of the charger |
| Replayed payment | Nonces and quote expiry prevent reuse |
| Charger failure | The maximum loss is limited to one payment chunk |

### Residual Risks

KiloGuard reduces payment risk but does not eliminate every physical or infrastructure threat:

- **GPS spoofing:** Production deployments should combine location checks with a signed physical plug identifier.
- **Dishonest metering:** Signed charger readings should be cross-checked against the vehicle’s battery telemetry.
- **Network outage:** Vehicles need a limited emergency budget and a pre-approved fallback charger list.
- **Guardian compromise:** Production signing keys should be protected by secure hardware and an owner-controlled recovery mechanism.

These risks are intentionally surfaced rather than hidden because failure-mode analysis is a core part of the prototype.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Settlement network | GOAT Network testnet |
| Payment protocol | x402 |
| Agent identity and reputation | ERC-8004 |
| Payment asset | Facilitator-supported test stablecoin |
| Backend services | Python and FastAPI |
| Agent reasoning | LLM tool calls |
| Dashboard | Streamlit or web application |
| Smart-contract integration | Web3-compatible Python library |
| Audit evidence | Signed receipts and on-chain receipt hashes |

---

## Project Structure

```text
kiloguard/
├── vehicle/
│   ├── agent.py
│   ├── charging_strategy.py
│   └── quote_ranking.py
├── guardian/
│   ├── policy_engine.py
│   ├── signer.py
│   └── risk_checks.py
├── charger/
│   ├── api.py
│   ├── meter.py
│   └── receipt.py
├── simulator/
│   ├── fleet.py
│   ├── chargers.py
│   └── attacks.py
├── dashboard/
│   └── app.py
├── registry/
│   └── identity.py
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

> Update this tree to match the final repository structure.

---

## Getting Started

### Prerequisites

- Python 3.11 or later
- Git
- A GOAT testnet wallet
- Testnet gas tokens
- A supported test stablecoin
- Access to an x402 facilitator
- Registered test identities for the vehicle and chargers

### Installation

```bash
git clone https://github.com/YOUR_ORG/kiloguard.git
cd kiloguard

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### Environment Configuration

Add the required values to `.env`:

```dotenv
GOAT_RPC_URL=
GOAT_CHAIN_ID=
X402_FACILITATOR_URL=

GUARDIAN_PRIVATE_KEY=
FLEET_OWNER_ADDRESS=
VEHICLE_AGENT_ADDRESS=

HONEST_CHARGER_ADDRESS=
ATTACKER_CHARGER_ADDRESS=
REGISTRY_CONTRACT_ADDRESS=

STABLECOIN_ADDRESS=
RECEIPT_CONTRACT_ADDRESS=
```

Never commit `.env`, private keys, wallet seed phrases, or production credentials.

### Run the Services

Start the charger API:

```bash
uvicorn charger.api:app --reload --port 8001
```

Start the guardian:

```bash
uvicorn guardian.api:app --reload --port 8002
```

Start the simulator:

```bash
python -m simulator.run
```

Start the dashboard:

```bash
streamlit run dashboard/app.py
```

> Adjust these commands if the final module names differ.

---

## Demo Scenarios

### Happy Path

```bash
python -m simulator.run --scenario happy-path
```

Expected result:

1. Unit 07 detects that it needs energy.
2. Nearby chargers return x402 quotes.
3. The vehicle ranks chargers by total operating cost.
4. The guardian validates the selected quote.
5. Nine 2 kWh payments are settled.
6. Every meter reading is verified.
7. A signed receipt is generated and recorded.

### Security Scenarios

```bash
python -m simulator.run --scenario cloned-charger
python -m simulator.run --scenario payment-redirect
python -m simulator.run --scenario short-delivery
python -m simulator.run --scenario prompt-injection
```

Each scenario should produce a structured decision showing:

- Requested action
- Policy checks performed
- Approval or rejection
- Rejection reason, if applicable
- Transaction hash, if settled
- Evidence retained for audit

---

## Example Guardian Decision

```json
{
  "vehicle": "unit-07",
  "charger": "dufferin-03",
  "decision": "approved",
  "price_per_kwh": 0.32,
  "chunk_kwh": 2,
  "chunk_cost": 0.64,
  "checks": {
    "session_limit": true,
    "daily_limit": true,
    "price_limit": true,
    "registered_address": true,
    "minimum_reputation": true,
    "proximity": true
  }
}
```

A rejected payment-redirection attempt could return:

```json
{
  "vehicle": "unit-07",
  "charger": "front-st-clone",
  "decision": "rejected",
  "reason": "Quote payment address does not match registered operator address",
  "transaction_created": false
}
```

---

## Hackathon Track Coverage

### Use

- Autonomous procurement
- Paid APIs and metered access
- Agent-to-agent commerce
- Incremental settlement and payouts

### Control

- Per-session, daily, and per-unit spending limits
- Delegated payment authority
- Mid-session termination and dispute handling
- Reviewable decision and payment records

### Attack

- Credential-compromise protection
- Payment-redirection detection
- Overspending and runaway-loop prevention
- Live adversarial scenarios
- Documented residual-risk analysis

KiloGuard demonstrates all three areas through one vehicle, one guardian, and one end-to-end payment flow.

---

## Prototype Scope

The prototype combines real and simulated components:

- At least one payment is settled on the GOAT testnet.
- Vehicle movement, fleet activity, charger availability, and energy delivery are simulated.
- Security attacks run through controlled adversarial charger configurations.
- Test values do not represent current Toronto electricity or commercial charging prices.

The simulation uses a fixed random seed so demo runs remain reproducible.

---

## Roadmap

- Secure hardware-backed guardian signing
- Vehicle battery-telemetry verification
- Signed physical charger and plug identifiers
- Automated dispute and refund workflow
- Dynamic Ontario time-of-use pricing
- Production charger-reputation scoring
- Multi-fleet policy management
- Additional machine-payment use cases
- Hedera payment and receipt adapter
- BTC and GOAT-native settlement options

---

## Team

- **Yash Chitransh:** x402 charger service, testnet integration, identity registration, and receipt anchoring
- **Edward Johnson:** vehicle logic, guardian policy engine, adversarial simulations, dashboard, and security analysis
- **Shared:** handshake protocol, integration, testing, demonstration, and documentation

---

## Prepared Before the Event

The initial repository structure, development environment, service shells, documentation, and research may have been prepared before the hackathon.

The functional prototype, including vehicle decision logic, guardian controls, payment flow, security scenarios, and simulation behavior, is built during the hackathon in accordance with event rules.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

Third-party libraries, SDKs, protocols, smart contracts, and test assets remain subject to their respective licences and terms.

---

## Disclaimer

KiloGuard is a hackathon prototype intended for demonstration and research. It has not been independently audited and must not be used to control production funds, vehicles, charging infrastructure, or safety-critical systems.
