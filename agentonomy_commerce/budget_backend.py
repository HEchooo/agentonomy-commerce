"""Trusted EVM transport for Core's budget-contract settlement adapter.

Signers are injected capabilities. This module never loads or persists keys.
Preparation is side-effect free on chain; Core must journal its output before
broadcast. Missing receipts always mean unknown, never an unpaid order.
"""
from __future__ import annotations

from collections.abc import Callable
import time

from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_abi import encode
from eth_utils import keccak, to_checksum_address
import rlp

from apps.facilitator.budget_protocol import (
    SpendGrant, PurchaseExecution, hash_grant, hash_execution,
    verify_grant_signature, verify_execution_signature, encode_execute_calldata_hex,
)
from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, address, quantity, verified_boundary


def purchase_commitment(purchase_id: str) -> str:
    if not isinstance(purchase_id, str) or not purchase_id or len(purchase_id) > 256:
        raise ValueError("canonical purchase ID required")
    return "0x" + keccak(b"agentonomy:purchase:v1:" + purchase_id.encode()).hex()


def quote_commitment(quote_hash: str) -> str:
    raw = bytes.fromhex(quote_hash.removeprefix("0x"))
    if len(raw) != 32 or not any(raw):
        raise ValueError("immutable quote commitment required")
    return "0x" + raw.hex()


class BudgetBackend:
    def __init__(self, config: NetworkConfig, *, relayer_address: str,
                 execution_signer_address: str, execution_sign: Callable,
                 transaction_sign: Callable, sign_execution: Callable | None = None):
        self.config = config
        self.relayer_address = address(relayer_address)
        self.execution_signer_address = address(execution_signer_address)
        self.execution_sign = execution_sign
        self.sign_execution = sign_execution
        self.transaction_sign = transaction_sign
        self.clients = tuple(RpcClient(url, writable=(index == 0)) for index, url in enumerate(config.rpc_urls))

    def _checked(self, method, params, *, index=0):
        client = self.clients[index]
        client.check_chain(self.config.chain_id)
        return client.call(method, params)

    def pending_nonce(self) -> int:
        return quantity(self._checked("eth_getTransactionCount", [self.relayer_address, "pending"]))

    def allowance(self, owner: str) -> int:
        data = "0x" + (keccak(text="allowance(address,address)")[:4] + encode(
            ["address", "address"], [address(owner), self.config.executor])).hex()
        values = [int(self._checked("eth_call", [{"to": self.config.token, "data": data}, "latest"], index=i), 16)
                  for i in range(2)]
        if values[0] != values[1] or not 0 <= values[0] < 2**256:
            raise ValueError("RPC allowance observations disagree")
        return values[0]

    def prepare(self, row, binding, nonce):
        grant = binding.grant if isinstance(binding.grant, SpendGrant) else SpendGrant.from_json(binding.grant)
        verify_grant_signature(grant, binding.owner_signature, self.config.chain_id, self.config.executor)
        if (grant.token, grant.payee, grant.execution_signer) != (
            self.config.token, self.config.payee, self.execution_signer_address
        ):
            raise ValueError("grant differs from trusted deployment")
        now = int(time.time())
        deadline = min(now + 120, grant.valid_until)
        if now < grant.valid_after or deadline <= now:
            raise ValueError("grant is not currently executable")
        execution = PurchaseExecution(
            "0x" + hash_grant(grant, self.config.chain_id, self.config.executor).hex(),
            purchase_commitment(row["purchase_id"]), quote_commitment(row["quote_hash"]),
            int(row["amount_atomic"]), deadline,
        )
        if execution.amount > grant.max_per_payment:
            raise ValueError("payment exceeds chain grant")
        signature = (self.sign_execution(grant, binding.owner_signature, execution)
                     if self.sign_execution is not None else
                     self.execution_sign(hash_execution(execution, self.config.chain_id, self.config.executor)))
        verify_execution_signature(execution, signature, self.execution_signer_address, self.config.chain_id, self.config.executor)
        data = encode_execute_calldata_hex(grant, binding.owner_signature, execution, signature)
        gas_price = quantity(self._checked("eth_gasPrice", []))
        if not 0 < gas_price <= self.config.max_gas_price_wei:
            raise ValueError("gas price exceeds authorized cap")
        tx = dict(chainId=self.config.chain_id, nonce=nonce, from_=to_checksum_address(self.relayer_address),
                  to=to_checksum_address(self.config.executor), value=0, data=data, gasPrice=gas_price)
        tx["from"] = tx.pop("from_")
        estimate = quantity(self._checked("eth_estimateGas", [{k: hex(v) if type(v) is int else v for k, v in tx.items()}]))
        tx["gas"] = (estimate * 120 + 99) // 100
        if tx["gas"] > self.config.gas_limit:
            raise ValueError("estimated gas exceeds authorized cap")
        raw = bytes(self.transaction_sign(dict(tx)))
        self._verify_signed_transaction(raw, tx)
        return dict(raw_transaction="0x" + raw.hex(), tx_hash="0x" + keccak(raw).hex(),
                    relayer=self.relayer_address, nonce=nonce, transaction=tx,
                    execution=execution.to_json(),
                    execution_digest="0x" + hash_execution(execution, self.config.chain_id, self.config.executor).hex(),
                    execution_signature="0x" + bytes(signature).hex())

    def _verify_signed_transaction(self, raw, tx):
        decoded = rlp.decode(raw, Transaction)
        if address(Account.recover_transaction(raw)) != self.relayer_address:
            raise ValueError("transaction signer mismatch")
        if (decoded.nonce, decoded.gasPrice, decoded.gas, decoded.value,
            decoded.to.hex(), decoded.data.hex(), (decoded.v - 35) // 2) != (
            tx["nonce"], tx["gasPrice"], tx["gas"], 0,
            self.config.executor[2:], tx["data"][2:], self.config.chain_id
        ):
            raise ValueError("signed transaction differs from authorized request")

    def broadcast(self, attempt):
        raw = bytes.fromhex(attempt["raw_transaction"].removeprefix("0x"))
        self._verify_signed_transaction(raw, attempt["transaction"])
        if "0x" + keccak(raw).hex() != attempt["tx_hash"]:
            raise ValueError("durable transaction hash mismatch")
        result = self._checked("eth_sendRawTransaction", [attempt["raw_transaction"]])
        if result != attempt["tx_hash"]:
            raise ValueError("broadcast outcome is unknown: returned hash mismatch")
        return result

    def verify(self, row, binding, attempt):
        return self._verify_observations(row, binding, attempt)

    def _verify_observations(self, row, binding, attempt):
        from apps.facilitator.budget_watcher import ExpectedPayment, verify_payment, verify_reverted_payment
        from agentonomy_commerce.budget_observations import fetch_observations

        grant = binding.grant if isinstance(binding.grant, SpendGrant) else SpendGrant.from_json(binding.grant)
        execution = PurchaseExecution.from_json(attempt["execution"])
        digest = "0x" + hash_grant(grant, self.config.chain_id, self.config.executor).hex()
        if (execution.grant_hash, execution.purchase_id, execution.quote_hash, execution.amount) != (
            digest, purchase_commitment(row["purchase_id"]), quote_commitment(row["quote_hash"]), int(row["amount_atomic"])
        ):
            raise ValueError("attempt no longer matches immutable Core purchase")
        execution_digest = "0x" + hash_execution(execution, self.config.chain_id, self.config.executor).hex()
        if attempt.get("execution_digest") != execution_digest:
            raise ValueError("durable execution digest mismatch")
        signature = attempt["execution_signature"]
        verify_grant_signature(grant, binding.owner_signature, self.config.chain_id, self.config.executor)
        verify_execution_signature(execution, signature, grant.execution_signer, self.config.chain_id, self.config.executor)
        calldata = encode_execute_calldata_hex(grant, binding.owner_signature, execution, signature)
        if attempt["transaction"]["data"] != calldata:
            raise ValueError("durable execution calldata mismatch")
        raw = bytes.fromhex(attempt["raw_transaction"][2:])
        self._verify_signed_transaction(raw, attempt["transaction"])
        if "0x" + keccak(raw).hex() != attempt["tx_hash"]:
            raise ValueError("attempt transaction hash mismatch")
        expected = ExpectedPayment(
            tx_hash=attempt["tx_hash"], chain_id=self.config.chain_id,
            executor=self.config.executor, owner=grant.owner, payee=grant.payee,
            token=grant.token, amount=execution.amount, grant_hash=digest,
            grant_id=grant.grant_id, purchase_id=execution.purchase_id,
            quote_hash=execution.quote_hash, calldata=calldata,
        )
        observations = fetch_observations(self.config, expected.tx_hash)
        if observations is None:
            return None
        for observation in observations:
            transaction = observation['transaction']
            receipt = observation['receipt']
            if (address(transaction["from"]) != self.relayer_address
                or quantity(transaction["nonce"]) != attempt["nonce"]
                or quantity(transaction["value"]) != 0
                or transaction["blockHash"] != receipt["blockHash"]
                or transaction["blockNumber"] != receipt["blockNumber"]):
                raise ValueError("transaction scope mismatch")
        reverted = quantity(observations[0]['receipt']['status']) == 0
        result = (verify_reverted_payment if reverted else verify_payment)(expected, *observations)
        return dict(verified=True, status="reverted" if reverted else "verified", receipt_status=0 if reverted else 1,
                    two_rpc_verified=True, transaction_hash=result.tx_hash, tx_hash=result.tx_hash,
                    chain_id=result.chain_id, executor=result.executor,
                    block_number=result.block_number, block_hash=result.block_hash,
                    finality_kind=result.finality_kind, finality_block_number=result.finality_block_number,
                    finality_block_hash=result.finality_block_hash, amount_atomic=str(execution.amount),
                    grant_hash=digest, purchase_id=row["purchase_id"],
                    quote_hash=row["quote_hash"], purchase_commitment=execution.purchase_id,
                    quote_commitment=execution.quote_hash, owner=grant.owner, token=grant.token, payee=grant.payee)
