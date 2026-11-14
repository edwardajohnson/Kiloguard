"""Where money actually moves.

SimLedger: in-memory balances and fake transaction hashes, for laptop demos and the fleet simulator.
GoatLedger: real transfers on GOAT Testnet3, either native test BTC or an ERC-20 test stablecoin.

Settlement model (both ledgers): the guardian sends the transfer, then hands the charger the
transaction hash inside the X-PAYMENT header. The charger checks the transaction on the ledger
(payer, payee, amount, success, not used before) before it delivers energy.
"""
from __future__ import annotations

import hashlib
import itertools
from collections import defaultdict

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class LedgerError(RuntimeError):
    pass


class SimLedger:
    name = "simulated ledger"

    def __init__(self):
        self.balances: dict[str, int] = defaultdict(int)
        self.txs: dict[str, dict] = {}
        self._counter = itertools.count(1)

    def fund(self, address: str, cents: int) -> None:
        self.balances[address.lower()] += cents

    def balance_cents(self, address: str) -> int:
        return self.balances[address.lower()]

    def _hash(self, *parts) -> str:
        return "0x" + hashlib.sha256("|".join(map(str, (*parts, next(self._counter)))).encode()).hexdigest()

    def pay(self, signer, to: str, cents: int, memo: str = "") -> str:
        payer = signer.address.lower()
        if cents <= 0:
            raise LedgerError("amount must be positive")
        if self.balances[payer] < cents:
            raise LedgerError(f"insufficient balance ({self.balances[payer]} < {cents} cents)")
        self.balances[payer] -= cents
        self.balances[to.lower()] += cents
        tx = self._hash(payer, to, cents, memo)
        self.txs[tx] = {"from": payer, "to": to.lower(), "cents": cents, "ok": True, "memo": memo}
        return tx

    def verify_payment(self, tx_hash: str, payer: str, pay_to: str, cents: int) -> tuple[bool, str]:
        tx = self.txs.get(tx_hash)
        if tx is None:
            return False, "transaction not found"
        if not tx["ok"]:
            return False, "transaction failed"
        if tx["from"] != payer.lower():
            return False, "wrong payer"
        if tx["to"] != pay_to.lower():
            return False, "wrong payee"
        if tx["cents"] < cents:
            return False, "amount too low"
        return True, "ok"

    def anchor(self, signer, digest_hex: str) -> str:
        tx = self._hash("anchor", signer.address, digest_hex)
        self.txs[tx] = {"from": signer.address.lower(), "to": signer.address.lower(), "cents": 0, "ok": True,
                        "memo": digest_hex}
        return tx

    def tx_url(self, tx_hash: str) -> str:
        return ""


class GoatLedger:
    name = "GOAT Testnet3"

    def __init__(self, settings):
        try:
            from web3 import Web3
        except ImportError as exc:
            raise RuntimeError("goat mode needs web3: pip install -r requirements.txt") from exc
        self.settings = settings
        self.name = getattr(settings, "ledger_name", "") or self.name
        self.w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            raise RuntimeError(f"Cannot reach the RPC at {settings.rpc_url}")
        chain_id = self.w3.eth.chain_id
        if chain_id != settings.chain_id:
            raise RuntimeError(f"RPC is chain {chain_id}, expected {settings.chain_id}")
        self.asset = settings.payment_asset
        self.token = None
        self.decimals = 18
        if self.asset == "erc20":
            self.token = self.w3.to_checksum_address(settings.stablecoin_address)
            abi = [{"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
                    "outputs": [{"name": "", "type": "uint8"}]},
                   {"name": "balanceOf", "type": "function", "stateMutability": "view",
                    "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]}]
            self._erc20 = self.w3.eth.contract(address=self.token, abi=abi)
            self.decimals = int(self._erc20.functions.decimals().call())

    # --- units -----------------------------------------------------------------------------
    def units(self, cents: int) -> int:
        if self.asset == "erc20":
            return cents * 10 ** (self.decimals - 2) if self.decimals >= 2 else cents // 10 ** (2 - self.decimals)
        return cents * self.settings.wei_per_cent

    def balance_cents(self, address: str) -> int:
        address = self.w3.to_checksum_address(address)
        if self.asset == "erc20":
            raw = self._erc20.functions.balanceOf(address).call()
            return int(raw // self.units(1))
        return int(self.w3.eth.get_balance(address) // self.settings.wei_per_cent)

    # --- sending -----------------------------------------------------------------------------
    @staticmethod
    def _hex(value) -> str:
        if isinstance(value, str):
            return value if value.startswith("0x") else "0x" + value
        return "0x" + bytes(value).hex()

    def _send(self, signer, tx: dict) -> str:
        if getattr(signer, "kind", "") != "eth":
            raise LedgerError("goat mode needs a real key for this signer (set its *_PRIVATE_KEY in .env)")
        sender = signer.address
        tx = {**tx, "from": sender, "chainId": self.settings.chain_id,
              "nonce": self.w3.eth.get_transaction_count(sender, "pending"),
              "gasPrice": self.w3.eth.gas_price}
        if "gas" not in tx:
            tx["gas"] = int(self.w3.eth.estimate_gas({k: tx[k] for k in ("from", "to", "value", "data") if k in tx}) * 1.25)
        tx.pop("from")
        signed = signer.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt["status"] != 1:
            raise LedgerError(f"transaction {self._hex(tx_hash)} reverted")
        return self._hex(tx_hash)

    def pay(self, signer, to: str, cents: int, memo: str = "") -> str:
        to = self.w3.to_checksum_address(to)
        amount = self.units(cents)
        if self.asset == "erc20":
            data = "0xa9059cbb" + to[2:].lower().rjust(64, "0") + hex(amount)[2:].rjust(64, "0")
            return self._send(signer, {"to": self.token, "value": 0, "data": data})
        return self._send(signer, {"to": to, "value": amount})

    def anchor(self, signer, digest_hex: str) -> str:
        """Writes the receipt hash on-chain: a zero-value transaction to self with the hash as data."""
        return self._send(signer, {"to": signer.address, "value": 0, "data": digest_hex})

    # --- verifying ---------------------------------------------------------------------------
    def verify_payment(self, tx_hash: str, payer: str, pay_to: str, cents: int) -> tuple[bool, str]:
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        except Exception as exc:
            return False, f"transaction not found ({exc.__class__.__name__})"
        if receipt["status"] != 1:
            return False, "transaction failed"
        amount = self.units(cents)
        if self.asset == "native":
            tx = self.w3.eth.get_transaction(tx_hash)
            if tx["from"].lower() != payer.lower():
                return False, "wrong payer"
            if (tx["to"] or "").lower() != pay_to.lower():
                return False, "wrong payee"
            if int(tx["value"]) < amount:
                return False, "amount too low"
            return True, "ok"
        for log in receipt["logs"]:
            if log["address"].lower() != self.token.lower() or len(log["topics"]) < 3:
                continue
            if self._hex(log["topics"][0]).lower() != TRANSFER_TOPIC:
                continue
            frm = "0x" + self._hex(log["topics"][1])[-40:]
            to = "0x" + self._hex(log["topics"][2])[-40:]
            value = int(self._hex(log["data"]), 16)
            if frm.lower() == payer.lower() and to.lower() == pay_to.lower() and value >= amount:
                return True, "ok"
        return False, "no matching stablecoin transfer in transaction"

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.settings.explorer_url}/tx/{tx_hash}"
