"""Pure, fail-closed verifier for budget executor observations.

``verify_payment`` accepts two already-fetched RPC observations.  It never
contacts an RPC endpoint and never treats a receipt by itself as proof of
payment.  Both observations must independently contain the target transaction,
its successful receipt, the canonical target block, an explicit finality
boundary, one exact ``PaymentExecuted`` event, and one exact ERC-20
``Transfer`` event.

The public Monad adapter must supply ``finality.kind == "monad_verified"`` and
an independently fetched ``finality.canonical_block`` identity for the
concrete verified boundary.  A confirmation count or a plain ``finalized`` tag
is intentionally not accepted.  Tests may use ``finality.kind == "local"``
with an explicit verified block identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from eth_hash.auto import keccak


UINT256_MAX = 2**256 - 1
MONAD_TESTNET_CHAIN_ID = 10143
LOCAL_TEST_CHAIN_IDS = frozenset({31337})

PAYMENT_EXECUTED_TOPIC = "0x" + keccak(
    b"PaymentExecuted(bytes32,bytes32,address,bytes32,bytes32,address,address,uint256)"
).hex()
TRANSFER_TOPIC = "0x" + keccak(b"Transfer(address,address,uint256)").hex()

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_QUANTITY = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
_UINT_STRING = re.compile(r"^(?:0|[1-9][0-9]*)$")


class BudgetWatcherError(ValueError):
    """Evidence is incomplete, malformed, inconsistent, or mismatched."""


class RpcDisagreement(BudgetWatcherError):
    """The two independent observations do not prove the same payment."""


def _invalid(field: str, reason: str = "is invalid") -> BudgetWatcherError:
    return BudgetWatcherError(f"{field} {reason}")


def _required(mapping: Mapping[str, Any], *names: str, field: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    raise _invalid(field, "is missing")


def _address(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ADDRESS.fullmatch(value) is None:
        raise _invalid(field)
    raw = bytes.fromhex(value[2:])
    if raw == bytes(20):
        raise _invalid(field, "must be nonzero")
    return "0x" + raw.hex()


def _hash(value: object, *, field: str, nonzero: bool = True) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise _invalid(field)
    raw = bytes.fromhex(value[2:])
    if nonzero and raw == bytes(32):
        raise _invalid(field, "must be nonzero")
    return "0x" + raw.hex()


def _bytes(value: object, *, field: str, allow_empty: bool = False) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str) and value.startswith("0x"):
        if len(value[2:]) % 2:
            raise _invalid(field)
        try:
            raw = bytes.fromhex(value[2:])
        except ValueError as exc:
            raise _invalid(field) from exc
    else:
        raise _invalid(field)
    if not allow_empty and not raw:
        raise _invalid(field, "must not be empty")
    return raw


def _quantity(value: object, *, field: str) -> int:
    if type(value) is int:
        if value < 0 or value > UINT256_MAX:
            raise _invalid(field)
        return value
    if not isinstance(value, str) or _QUANTITY.fullmatch(value) is None:
        raise _invalid(field)
    try:
        return int(value, 16)
    except ValueError as exc:  # pragma: no cover - regex makes this unreachable
        raise _invalid(field) from exc


def _wire_uint(value: object, *, field: str) -> int:
    if type(value) is int:
        if value < 0 or value > UINT256_MAX:
            raise _invalid(field)
        return value
    if not isinstance(value, str) or _UINT_STRING.fullmatch(value) is None:
        raise _invalid(field)
    result = int(value, 10)
    if result > UINT256_MAX:
        raise _invalid(field)
    return result


def _word_address(value: bytes, *, field: str) -> str:
    if len(value) != 32 or value[:12] != bytes(12):
        raise _invalid(field)
    return _address("0x" + value[12:].hex(), field=field)


def _word_hash(value: bytes, *, field: str) -> str:
    if len(value) != 32:
        raise _invalid(field)
    return _hash("0x" + value.hex(), field=field)


def _word_uint(value: bytes, *, field: str) -> int:
    if len(value) != 32:
        raise _invalid(field)
    return int.from_bytes(value, "big")


@dataclass(frozen=True, slots=True)
class ExpectedPayment:
    """Trusted immutable payment projection supplied by Core.

    ``calldata`` is the exact signed executor call produced by
    :func:`budget_protocol.encode_execute_calldata`; it is compared byte for
    byte with the observed transaction input.  This projection contains no RPC
    data and cannot be replaced by a merchant response.
    """

    tx_hash: str
    chain_id: int
    executor: str
    owner: str
    payee: str
    token: str
    amount: int | str
    grant_hash: str
    grant_id: str
    purchase_id: str
    quote_hash: str
    calldata: bytes | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tx_hash", _hash(self.tx_hash, field="tx_hash"))
        if type(self.chain_id) is not int or self.chain_id < 0 or self.chain_id > UINT256_MAX:
            raise _invalid("chain_id")
        object.__setattr__(self, "executor", _address(self.executor, field="executor"))
        object.__setattr__(self, "owner", _address(self.owner, field="owner"))
        object.__setattr__(self, "payee", _address(self.payee, field="payee"))
        object.__setattr__(self, "token", _address(self.token, field="token"))
        if self.owner == self.payee:
            raise _invalid("owner", "must differ from payee")
        amount = _wire_uint(self.amount, field="amount")
        if amount == 0:
            raise _invalid("amount", "must be positive")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "grant_hash", _hash(self.grant_hash, field="grant_hash"))
        object.__setattr__(self, "grant_id", _hash(self.grant_id, field="grant_id"))
        object.__setattr__(self, "purchase_id", _hash(self.purchase_id, field="purchase_id"))
        object.__setattr__(self, "quote_hash", _hash(self.quote_hash, field="quote_hash"))
        object.__setattr__(self, "calldata", _bytes(self.calldata, field="calldata"))


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Minimal independent evidence returned after both observations agree."""

    verified: bool
    tx_hash: str
    chain_id: int
    executor: str
    block_number: int
    block_hash: str
    finality_kind: str
    finality_block_number: int
    finality_block_hash: str


@dataclass(frozen=True, slots=True)
class RevertedVerificationResult:
    """Minimal independent evidence for a finalized reverted transaction."""

    verified: bool
    tx_hash: str
    chain_id: int
    executor: str
    block_number: int
    block_hash: str
    finality_kind: str
    finality_block_number: int
    finality_block_hash: str


@dataclass(frozen=True, slots=True)
class RpcObservation:
    """Normalized observation container accepted by :func:`verify_payment`.

    The fields retain the JSON-RPC-shaped mappings so an adapter can preserve
    the raw receipt and logs while making the required observation boundary
    explicit.  ``finality.canonical_block`` must be the independently fetched
    boundary block identity.  Plain mappings with the same keys are accepted
    as well.
    """

    chain_id: int
    transaction: Mapping[str, Any]
    receipt: Mapping[str, Any]
    canonical_block: Mapping[str, Any]
    finality: Mapping[str, Any]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "transaction": self.transaction,
            "receipt": self.receipt,
            "canonical_block": self.canonical_block,
            "finality": self.finality,
        }


@dataclass(frozen=True, slots=True)
class _Evidence:
    chain_id: int
    tx_hash: str
    executor: str
    block_number: int
    block_hash: str
    finality_kind: str
    finality_block_number: int
    finality_block_hash: str
    calldata: bytes
    payment_event: tuple[str, str, str, str, str, int] | None
    transfer_event: tuple[str, str, int] | None


def _map(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(field, "must be an object")
    return value


def _parse_finality(
    observation: Mapping[str, Any],
    *,
    receipt_block_number: int,
    receipt_block_hash: str,
) -> tuple[str, int, str]:
    raw = observation.get("finality")
    finality = _map(raw, field="finality")
    kind = _required(finality, "kind", "finality_kind", field="finality.kind")
    if kind not in ("local", "monad_verified"):
        raise _invalid("finality.kind", "must be local or monad_verified")
    chain_id = _quantity(
        _required(observation, "chain_id", "chainId", field="chain_id"), field="chain_id"
    )
    if chain_id in LOCAL_TEST_CHAIN_IDS:
        if kind != "local":
            raise _invalid("finality.kind", "local test chains require local")
    elif chain_id == MONAD_TESTNET_CHAIN_ID:
        if kind != "monad_verified":
            raise _invalid("finality.kind", "Monad testnet requires monad_verified")
    else:
        raise _invalid("chain_id", "is unsupported")
    if finality.get("verified") is not True:
        raise _invalid("finality.verified", "must be true")
    boundary = _map(
        _required(finality, "canonical_block", field="finality.canonical_block"),
        field="finality.canonical_block",
    )
    boundary_number = _quantity(
        _required(boundary, "number", "block_number", "blockNumber", field="finality.canonical_block.number"),
        field="finality.canonical_block.number",
    )
    boundary_hash = _hash(
        _required(boundary, "hash", "block_hash", "blockHash", field="finality.canonical_block.hash"),
        field="finality.canonical_block.hash",
    )
    if boundary_number < receipt_block_number:
        raise _invalid("finality.canonical_block.number", "does not cover receipt block")
    if boundary_number == receipt_block_number and boundary_hash != receipt_block_hash:
        raise _invalid("finality.canonical_block.hash", "does not identify receipt block")
    return kind, boundary_number, boundary_hash


def _parse_payment_event(log: Mapping[str, Any], expected: ExpectedPayment) -> tuple[str, str, str, str, str, int]:
    address = _address(_required(log, "address", field="PaymentExecuted.address"), field="PaymentExecuted.address")
    if address != expected.executor:
        raise _invalid("PaymentExecuted.address", "does not match executor")
    topics = _required(log, "topics", field="PaymentExecuted.topics")
    if not isinstance(topics, list) or len(topics) != 4:
        raise _invalid("PaymentExecuted.topics")
    if _hash(topics[0], field="PaymentExecuted.topic0") != PAYMENT_EXECUTED_TOPIC:
        raise _invalid("PaymentExecuted.topic0", "does not match event")
    grant_hash = _hash(topics[1], field="PaymentExecuted.grantHash")
    purchase_id = _hash(topics[2], field="PaymentExecuted.purchaseId")
    owner = _word_address(_bytes(topics[3], field="PaymentExecuted.owner", allow_empty=False), field="PaymentExecuted.owner")
    if (grant_hash, purchase_id, owner) != (expected.grant_hash, expected.purchase_id, expected.owner):
        raise _invalid("PaymentExecuted", "scope does not match expected payment")
    data = _bytes(_required(log, "data", field="PaymentExecuted.data"), field="PaymentExecuted.data")
    if len(data) != 160:
        raise _invalid("PaymentExecuted.data")
    grant_id = _word_hash(data[:32], field="PaymentExecuted.grantId")
    quote_hash = _word_hash(data[32:64], field="PaymentExecuted.quoteHash")
    token = _word_address(data[64:96], field="PaymentExecuted.token")
    payee = _word_address(data[96:128], field="PaymentExecuted.payee")
    amount = _word_uint(data[128:160], field="PaymentExecuted.amount")
    if (grant_id, quote_hash, token, payee, amount) != (
        expected.grant_id,
        expected.quote_hash,
        expected.token,
        expected.payee,
        expected.amount,
    ):
        raise _invalid("PaymentExecuted", "data does not match expected payment")
    return grant_hash, purchase_id, owner, grant_id, quote_hash, amount


def _parse_transfer_event(log: Mapping[str, Any], expected: ExpectedPayment) -> tuple[str, str, int]:
    address = _address(_required(log, "address", field="Transfer.address"), field="Transfer.address")
    if address != expected.token:
        raise _invalid("Transfer.address", "does not match token")
    topics = _required(log, "topics", field="Transfer.topics")
    if not isinstance(topics, list) or len(topics) != 3:
        raise _invalid("Transfer.topics")
    if _hash(topics[0], field="Transfer.topic0") != TRANSFER_TOPIC:
        raise _invalid("Transfer.topic0", "does not match event")
    sender = _word_address(_bytes(topics[1], field="Transfer.from", allow_empty=False), field="Transfer.from")
    recipient = _word_address(_bytes(topics[2], field="Transfer.to", allow_empty=False), field="Transfer.to")
    data = _bytes(_required(log, "data", field="Transfer.data"), field="Transfer.data")
    if len(data) != 32:
        raise _invalid("Transfer.data")
    amount = _word_uint(data, field="Transfer.amount")
    if (sender, recipient, amount) != (expected.owner, expected.payee, expected.amount):
        raise _invalid("Transfer", "does not match expected payment")
    return sender, recipient, amount


def _verify_observation(
    expected: ExpectedPayment,
    observation: object,
    *,
    allow_reverted: bool = False,
) -> _Evidence:
    if isinstance(observation, RpcObservation):
        raw = observation.as_mapping()
    else:
        raw = _map(observation, field="observation")
    chain_id = _quantity(_required(raw, "chain_id", "chainId", field="chain_id"), field="chain_id")
    if chain_id != expected.chain_id:
        raise _invalid("chain_id", "does not match expected chain")

    transaction = _map(_required(raw, "transaction", "tx", field="transaction"), field="transaction")
    tx_hash = _hash(_required(transaction, "hash", "transactionHash", field="transaction.hash"), field="transaction.hash")
    if tx_hash != expected.tx_hash:
        raise _invalid("transaction.hash", "does not match expected transaction")
    executor = _address(_required(transaction, "to", field="transaction.to"), field="transaction.to")
    if executor != expected.executor:
        raise _invalid("transaction.to", "does not match executor")
    calldata = _bytes(_required(transaction, "input", "data", field="transaction.input"), field="transaction.input")
    if calldata != expected.calldata:
        raise _invalid("transaction.input", "does not match immutable execution calldata")
    if "chainId" in transaction or "chain_id" in transaction:
        transaction_chain_id = _quantity(
            _required(transaction, "chain_id", "chainId", field="transaction.chain_id"),
            field="transaction.chain_id",
        )
        if transaction_chain_id != expected.chain_id:
            raise _invalid("transaction.chain_id", "does not match expected chain")
    transaction_block_number = _quantity(
        _required(transaction, "blockNumber", "block_number", field="transaction.blockNumber"),
        field="transaction.blockNumber",
    )
    transaction_block_hash = _hash(
        _required(transaction, "blockHash", "block_hash", field="transaction.blockHash"),
        field="transaction.blockHash",
    )

    receipt = _map(_required(raw, "receipt", field="receipt"), field="receipt")
    receipt_tx_hash = _hash(
        _required(receipt, "transactionHash", "transaction_hash", field="receipt.transactionHash"),
        field="receipt.transactionHash",
    )
    if receipt_tx_hash != tx_hash:
        raise _invalid("receipt.transactionHash", "does not match transaction")
    status = _quantity(_required(receipt, "status", field="receipt.status"), field="receipt.status")
    if allow_reverted:
        if status != 0:
            raise _invalid("receipt.status", "must be reverted")
    elif status != 1:
        raise _invalid("receipt.status", "must be successful")
    block_number = _quantity(
        _required(receipt, "blockNumber", "block_number", field="receipt.blockNumber"),
        field="receipt.blockNumber",
    )
    block_hash = _hash(
        _required(receipt, "blockHash", "block_hash", field="receipt.blockHash"),
        field="receipt.blockHash",
    )
    if (transaction_block_number, transaction_block_hash) != (block_number, block_hash):
        raise _invalid("transaction.block", "does not match receipt block identity")
    if "to" in receipt:
        receipt_to = _address(receipt["to"], field="receipt.to")
        if receipt_to != expected.executor:
            raise _invalid("receipt.to", "does not match executor")
    if "from" in receipt:
        receipt_from = _address(receipt["from"], field="receipt.from")
        if "from" in transaction:
            transaction_from = _address(transaction["from"], field="transaction.from")
            if receipt_from != transaction_from:
                raise _invalid("receipt.from", "does not match transaction.from")
    elif "from" in transaction:
        _address(transaction["from"], field="transaction.from")
    canonical = _map(
        _required(raw, "canonical_block", "canonical", "block", field="canonical_block"),
        field="canonical_block",
    )
    canonical_number = _quantity(
        _required(canonical, "number", "block_number", field="canonical_block.number"),
        field="canonical_block.number",
    )
    canonical_hash = _hash(
        _required(canonical, "hash", "block_hash", field="canonical_block.hash"),
        field="canonical_block.hash",
    )
    if (canonical_number, canonical_hash) != (block_number, block_hash):
        raise _invalid("canonical_block", "does not match receipt block identity")
    logs = _required(receipt, "logs", field="receipt.logs")
    if allow_reverted:
        if not isinstance(logs, list) or logs:
            raise _invalid("receipt.logs", "must be empty for a reverted transaction")
        finality_kind, finality_number, finality_hash = _parse_finality(
            raw, receipt_block_number=block_number, receipt_block_hash=block_hash
        )
        return _Evidence(
            chain_id=chain_id,
            tx_hash=tx_hash,
            executor=executor,
            block_number=block_number,
            block_hash=block_hash,
            finality_kind=finality_kind,
            finality_block_number=finality_number,
            finality_block_hash=finality_hash,
            calldata=calldata,
            payment_event=None,
            transfer_event=None,
        )
    if not isinstance(logs, list) or len(logs) != 2:
        raise _invalid("receipt.logs", "must contain exactly PaymentExecuted and Transfer")
    payment_logs: list[tuple[str, str, str, str, str, int]] = []
    transfer_logs: list[tuple[str, str, int]] = []
    log_indexes: set[int] = set()
    for index, raw_log in enumerate(logs):
        log = _map(raw_log, field=f"receipt.logs[{index}]")
        log_tx_hash = _hash(
            _required(log, "transactionHash", "transaction_hash", field=f"receipt.logs[{index}].transactionHash"),
            field=f"receipt.logs[{index}].transactionHash",
        )
        log_block_hash = _hash(
            _required(log, "blockHash", "block_hash", field=f"receipt.logs[{index}].blockHash"),
            field=f"receipt.logs[{index}].blockHash",
        )
        log_block_number = _quantity(
            _required(log, "blockNumber", "block_number", field=f"receipt.logs[{index}].blockNumber"),
            field=f"receipt.logs[{index}].blockNumber",
        )
        if (log_tx_hash, log_block_hash, log_block_number) != (
            tx_hash,
            block_hash,
            block_number,
        ):
            raise _invalid(f"receipt.logs[{index}]", "does not match receipt identity")
        if log.get("removed") is not False:
            raise _invalid(f"receipt.logs[{index}].removed", "must be false")
        log_index = _quantity(
            _required(log, "logIndex", "log_index", field=f"receipt.logs[{index}].logIndex"),
            field=f"receipt.logs[{index}].logIndex",
        )
        if log_index in log_indexes:
            raise _invalid("receipt.logs.logIndex", "must be unique")
        log_indexes.add(log_index)
        topics = log.get("topics")
        if isinstance(topics, list) and topics:
            topic0 = topics[0]
            if isinstance(topic0, str) and _HASH.fullmatch(topic0) is not None:
                topic0_normalized = "0x" + bytes.fromhex(topic0[2:]).hex()
                if topic0_normalized == PAYMENT_EXECUTED_TOPIC:
                    payment_logs.append(_parse_payment_event(log, expected))
                    continue
                if topic0_normalized == TRANSFER_TOPIC:
                    transfer_logs.append(_parse_transfer_event(log, expected))
                    continue
        raise _invalid(f"receipt.logs[{index}]", "contains an unexpected event")
    if len(payment_logs) != 1:
        raise _invalid("receipt.logs", "must contain exactly one PaymentExecuted")
    if len(transfer_logs) != 1:
        raise _invalid("receipt.logs", "must contain exactly one Transfer")
    finality_kind, finality_number, finality_hash = _parse_finality(
        raw, receipt_block_number=block_number, receipt_block_hash=block_hash
    )
    return _Evidence(
        chain_id=chain_id,
        tx_hash=tx_hash,
        executor=executor,
        block_number=block_number,
        block_hash=block_hash,
        finality_kind=finality_kind,
        finality_block_number=finality_number,
        finality_block_hash=finality_hash,
        calldata=calldata,
        payment_event=payment_logs[0],
        transfer_event=transfer_logs[0],
    )


def _agree(first: _Evidence, second: _Evidence) -> None:
    for field in (
        "chain_id",
        "tx_hash",
        "executor",
        "block_number",
        "block_hash",
        "calldata",
        "payment_event",
        "transfer_event",
    ):
        if getattr(first, field) != getattr(second, field):
            raise RpcDisagreement(f"RPC observations disagree on {field}")
    if first.finality_kind != second.finality_kind:
        raise RpcDisagreement("RPC observations disagree on finality kind")
    if (first.finality_block_number, first.finality_block_hash) != (
        second.finality_block_number,
        second.finality_block_hash,
    ):
        raise RpcDisagreement("RPC observations disagree on finality boundary block")


def verify_payment(
    expected: ExpectedPayment,
    rpc_observation_a: Mapping[str, Any],
    rpc_observation_b: Mapping[str, Any],
) -> VerificationResult:
    """Verify two independent observations of one immutable payment.

    The function is pure with respect to its inputs.  It raises
    :class:`BudgetWatcherError` (or :class:`RpcDisagreement`) for any failure;
    a returned result with ``verified=True`` is the only success value.
    """

    if not isinstance(expected, ExpectedPayment):
        raise _invalid("expected", "must be ExpectedPayment")
    first = _verify_observation(expected, rpc_observation_a)
    second = _verify_observation(expected, rpc_observation_b)
    _agree(first, second)
    return VerificationResult(
        verified=True,
        tx_hash=first.tx_hash,
        chain_id=first.chain_id,
        executor=first.executor,
        block_number=first.block_number,
        block_hash=first.block_hash,
        finality_kind=first.finality_kind,
        finality_block_number=first.finality_block_number,
        finality_block_hash=first.finality_block_hash,
    )


def verify_reverted_payment(
    expected: ExpectedPayment,
    rpc_observation_a: Mapping[str, Any],
    rpc_observation_b: Mapping[str, Any],
) -> RevertedVerificationResult:
    """Verify two independent observations of one finalized reverted payment.

    This path is intentionally separate from :func:`verify_payment`: a
    reverted receipt must have status zero and an explicitly empty log list,
    while retaining the same transaction, block, canonicality, finality, and
    RPC-agreement checks.  A missing receipt or any nonempty log set is not a
    reverted-payment proof.
    """

    if not isinstance(expected, ExpectedPayment):
        raise _invalid("expected", "must be ExpectedPayment")
    first = _verify_observation(expected, rpc_observation_a, allow_reverted=True)
    second = _verify_observation(expected, rpc_observation_b, allow_reverted=True)
    _agree(first, second)
    return RevertedVerificationResult(
        verified=True,
        tx_hash=first.tx_hash,
        chain_id=first.chain_id,
        executor=first.executor,
        block_number=first.block_number,
        block_hash=first.block_hash,
        finality_kind=first.finality_kind,
        finality_block_number=first.finality_block_number,
        finality_block_hash=first.finality_block_hash,
    )


class BudgetWatcher:
    """Stateless object wrapper for callers that prefer dependency injection."""

    @staticmethod
    def verify(
        expected: ExpectedPayment,
        rpc_observation_a: Mapping[str, Any],
        rpc_observation_b: Mapping[str, Any],
    ) -> VerificationResult:
        return verify_payment(expected, rpc_observation_a, rpc_observation_b)

    @staticmethod
    def verify_reverted(
        expected: ExpectedPayment,
        rpc_observation_a: Mapping[str, Any],
        rpc_observation_b: Mapping[str, Any],
    ) -> RevertedVerificationResult:
        return verify_reverted_payment(expected, rpc_observation_a, rpc_observation_b)


ExpectedPaymentProjection = ExpectedPayment
PaymentProjection = ExpectedPayment
verify_budget_payment = verify_payment
verify = verify_payment


__all__ = [
    "PAYMENT_EXECUTED_TOPIC",
    "TRANSFER_TOPIC",
    "MONAD_TESTNET_CHAIN_ID",
    "LOCAL_TEST_CHAIN_IDS",
    "BudgetWatcherError",
    "RpcDisagreement",
    "ExpectedPayment",
    "ExpectedPaymentProjection",
    "PaymentProjection",
    "RpcObservation",
    "VerificationResult",
    "RevertedVerificationResult",
    "BudgetWatcher",
    "verify_payment",
    "verify_reverted_payment",
    "verify_budget_payment",
    "verify",
]
