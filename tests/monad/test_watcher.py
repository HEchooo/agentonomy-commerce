from __future__ import annotations

from copy import deepcopy

import pytest
from eth_abi import encode
from eth_utils import keccak

from budget_protocol import encode_execute_calldata
from budget_watcher import (
    BudgetWatcherError,
    ExpectedPayment,
    RevertedVerificationResult,
    RpcObservation,
    VerificationResult,
    verify_payment,
    verify_reverted_payment,
)


CHAIN_ID = 31337
TX_HASH = "0x" + "10" * 32
BLOCK_HASH = "0x" + "20" * 32
FINALITY_BLOCK_HASH = "0x" + "30" * 32
EXECUTOR = "0x" + "99" * 20
TOKEN = "0x" + "44" * 20
OWNER = "0x" + "ed" * 20
PAYEE = "0x" + "22" * 20
GRANT_HASH = "0x" + "aa" * 32
GRANT_ID = "0x" + "bb" * 32
PURCHASE_ID = "0x" + "cc" * 32
QUOTE_HASH = "0x" + "dd" * 32
AMOUNT = 250
CALLDATA = "0xa4b6c2de" + "11" * 800
RELAYER = "0x" + "55" * 20


def _word_address(value: str) -> bytes:
    return bytes(12) + bytes.fromhex(value[2:])


def _payment_event(*, amount: int = AMOUNT, grant_hash: str = GRANT_HASH, log_index: int = 0) -> dict[str, object]:
    return {
        "address": EXECUTOR,
        "transactionHash": TX_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x2a",
        "logIndex": hex(log_index),
        "removed": False,
        "topics": [
            "0x" + keccak(text="PaymentExecuted(bytes32,bytes32,address,bytes32,bytes32,address,address,uint256)").hex(),
            grant_hash,
            PURCHASE_ID,
            "0x" + _word_address(OWNER).hex(),
        ],
        "data": "0x"
        + encode(
            ["bytes32", "bytes32", "address", "address", "uint256"],
            [bytes.fromhex(GRANT_ID[2:]), bytes.fromhex(QUOTE_HASH[2:]), TOKEN, PAYEE, amount],
        ).hex(),
    }


def _transfer_event(*, amount: int = AMOUNT, owner: str = OWNER, payee: str = PAYEE, log_index: int = 1) -> dict[str, object]:
    return {
        "address": TOKEN,
        "transactionHash": TX_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x2a",
        "logIndex": hex(log_index),
        "removed": False,
        "topics": [
            "0x" + keccak(text="Transfer(address,address,uint256)").hex(),
            "0x" + _word_address(owner).hex(),
            "0x" + _word_address(payee).hex(),
        ],
        "data": "0x" + amount.to_bytes(32, "big").hex(),
    }


def _expected() -> ExpectedPayment:
    return ExpectedPayment(
        tx_hash=TX_HASH,
        chain_id=CHAIN_ID,
        executor=EXECUTOR,
        owner=OWNER,
        payee=PAYEE,
        token=TOKEN,
        amount=AMOUNT,
        grant_hash=GRANT_HASH,
        grant_id=GRANT_ID,
        purchase_id=PURCHASE_ID,
        quote_hash=QUOTE_HASH,
        calldata=CALLDATA,
    )


def _observation(*, finality_kind: str = "local", chain_id: int = CHAIN_ID) -> dict[str, object]:
    return {
        "chain_id": chain_id,
        "transaction": {
            "hash": TX_HASH,
            "to": EXECUTOR,
            "from": RELAYER,
            "input": CALLDATA,
            "blockNumber": "0x2a",
            "blockHash": BLOCK_HASH,
        },
        "receipt": {
            "transactionHash": TX_HASH,
            "status": "0x1",
            "blockNumber": "0x2a",
            "blockHash": BLOCK_HASH,
            "to": EXECUTOR,
            "from": RELAYER,
            "logs": [_payment_event(), _transfer_event()],
        },
        "canonical_block": {"number": "0x2a", "hash": BLOCK_HASH},
        "finality": {
            "kind": finality_kind,
            "verified": True,
            "canonical_block": {"number": "0x2d", "hash": FINALITY_BLOCK_HASH},
        },
    }


def test_two_independent_observations_require_complete_verified_evidence() -> None:
    result = verify_payment(_expected(), _observation(), _observation())
    assert isinstance(result, VerificationResult)
    assert result.verified is True
    assert result.tx_hash == TX_HASH
    assert result.block_hash == BLOCK_HASH
    assert result.finality_kind == "local"


def test_monad_requires_explicit_verified_boundary_kind() -> None:
    with pytest.raises(BudgetWatcherError):
        verify_payment(
            _expected(),
            _observation(finality_kind="monad_verified"),
            _observation(finality_kind="monad_verified"),
        )

    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), _observation(finality_kind="monad"), _observation(finality_kind="monad"))

    public_expected = ExpectedPayment(
        tx_hash=TX_HASH,
        chain_id=10143,
        executor=EXECUTOR,
        owner=OWNER,
        payee=PAYEE,
        token=TOKEN,
        amount=AMOUNT,
        grant_hash=GRANT_HASH,
        grant_id=GRANT_ID,
        purchase_id=PURCHASE_ID,
        quote_hash=QUOTE_HASH,
        calldata=CALLDATA,
    )
    result = verify_payment(
        public_expected,
        _observation(chain_id=10143, finality_kind="monad_verified"),
        _observation(chain_id=10143, finality_kind="monad_verified"),
    )
    assert result.finality_kind == "monad_verified"

    with pytest.raises(BudgetWatcherError):
        verify_payment(
            public_expected,
            _observation(chain_id=10143, finality_kind="local"),
            _observation(chain_id=10143, finality_kind="local"),
        )

    unsupported_expected = ExpectedPayment(
        tx_hash=TX_HASH,
        chain_id=1,
        executor=EXECUTOR,
        owner=OWNER,
        payee=PAYEE,
        token=TOKEN,
        amount=AMOUNT,
        grant_hash=GRANT_HASH,
        grant_id=GRANT_ID,
        purchase_id=PURCHASE_ID,
        quote_hash=QUOTE_HASH,
        calldata=CALLDATA,
    )
    with pytest.raises(BudgetWatcherError):
        verify_payment(
            unsupported_expected,
            _observation(chain_id=1, finality_kind="monad_verified"),
            _observation(chain_id=1, finality_kind="monad_verified"),
        )


def test_receipt_alone_is_not_success() -> None:
    observation = _observation()
    observation.pop("transaction")
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), observation, _observation())

    observation = _observation()
    observation.pop("canonical_block")
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), observation, _observation())


@pytest.mark.parametrize(
    "change",
    [
        lambda o: o.update(chain_id=CHAIN_ID + 1),
        lambda o: o["receipt"].update(status="0x0"),
        lambda o: o["receipt"].update(transactionHash="0x" + "11" * 32),
        lambda o: o["canonical_block"].update(hash="0x" + "21" * 32),
        lambda o: o["canonical_block"].update(number="0x2b"),
        lambda o: o["finality"].update(verified=False),
        lambda o: o["finality"]["canonical_block"].update(hash="0x" + "21" * 32),
        lambda o: o["transaction"].update(to="0x" + "98" * 20),
        lambda o: o["transaction"].update(input="0xa4b6c2de" + "12" * 800),
        lambda o: o["transaction"].update(blockHash="0x" + "21" * 32),
        lambda o: o["receipt"].update(to="0x" + "98" * 20),
        lambda o: o["receipt"]["logs"][0].update(transactionHash="0x" + "11" * 32),
        lambda o: o["receipt"]["logs"][0].update(blockNumber="0x2b"),
        lambda o: o["receipt"]["logs"][0].update(removed=True),
        lambda o: o["receipt"]["logs"][0].update(logIndex="0x1"),
        lambda o: o["receipt"]["logs"][0]["topics"].__setitem__(1, "0x" + "ab" * 32),
        lambda o: o["receipt"]["logs"][1].update(address="0x" + "45" * 20),
        lambda o: o["receipt"]["logs"][1]["topics"].__setitem__(2, "0x" + _word_address("0x" + "23" * 20).hex()),
        lambda o: o["receipt"]["logs"][1].update(data="0x" + (AMOUNT + 1).to_bytes(32, "big").hex()),
    ],
)
def test_any_payment_projection_mismatch_fails_closed(change) -> None:
    observation = _observation()
    change(observation)
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), observation, _observation())


def test_duplicate_payment_or_transfer_logs_are_rejected() -> None:
    for duplicate in (_payment_event(), _transfer_event()):
        observation = _observation()
        observation["receipt"]["logs"].append(deepcopy(duplicate))
        with pytest.raises(BudgetWatcherError):
            verify_payment(_expected(), observation, _observation())


def test_rpc_disagreement_on_any_verified_identity_is_not_success() -> None:
    second = _observation()
    second["receipt"]["blockHash"] = "0x" + "21" * 32
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), _observation(), second)

    second = _observation()
    second["finality"]["canonical_block"]["hash"] = "0x" + "31" * 32
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), _observation(), second)


def test_rpc_observation_container_has_the_same_pure_verification_contract() -> None:
    first = _observation()
    second = _observation()
    first_container = RpcObservation(
        chain_id=first["chain_id"],
        transaction=first["transaction"],
        receipt=first["receipt"],
        canonical_block=first["canonical_block"],
        finality=first["finality"],
    )
    second_container = RpcObservation(
        chain_id=second["chain_id"],
        transaction=second["transaction"],
        receipt=second["receipt"],
        canonical_block=second["canonical_block"],
        finality=second["finality"],
    )
    assert verify_payment(_expected(), first_container, second_container).verified is True

    second = _observation()
    second["finality"]["kind"] = "monad_verified"
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), _observation(), second)


def test_finality_requires_an_independently_supplied_canonical_boundary() -> None:
    observation = _observation()
    observation["finality"].pop("canonical_block")
    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), observation, _observation())


def test_reverted_proof_is_separate_and_requires_zero_status_without_logs() -> None:
    reverted = _observation()
    reverted["receipt"]["status"] = "0x0"
    reverted["receipt"]["logs"] = []
    result = verify_reverted_payment(_expected(), reverted, deepcopy(reverted))
    assert isinstance(result, RevertedVerificationResult)
    assert result.verified is True
    assert result.tx_hash == TX_HASH
    assert result.finality_block_number == 0x2D

    with pytest.raises(BudgetWatcherError):
        verify_payment(_expected(), reverted, deepcopy(reverted))

    successful = _observation()
    with pytest.raises(BudgetWatcherError):
        verify_reverted_payment(_expected(), successful, deepcopy(successful))

    with_logs = deepcopy(reverted)
    with_logs["receipt"]["logs"] = [_payment_event()]
    with pytest.raises(BudgetWatcherError):
        verify_reverted_payment(_expected(), with_logs, deepcopy(reverted))


def test_reverted_proof_reuses_transaction_block_and_finality_agreement() -> None:
    reverted = _observation()
    reverted["receipt"]["status"] = "0x0"
    reverted["receipt"]["logs"] = []

    second = deepcopy(reverted)
    second["transaction"]["blockHash"] = "0x" + "21" * 32
    with pytest.raises(BudgetWatcherError):
        verify_reverted_payment(_expected(), reverted, second)

    second = deepcopy(reverted)
    second["finality"]["canonical_block"]["number"] = "0x2e"
    with pytest.raises(BudgetWatcherError):
        verify_reverted_payment(_expected(), reverted, second)
