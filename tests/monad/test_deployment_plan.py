from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from eth_abi import encode
from eth_utils import keccak
import rlp

from scripts.monad.deployment_plan import (
    BUYER,
    CHAIN_ID,
    DEPLOYER,
    EXECUTION_SIGNER,
    MAX_GAS_PRICE_WEI,
    MAX_TX_GAS_LIMIT,
    SUPPLY,
    build_plan,
    read_rpc_check,
)


PAYEE = "0x3333333333333333333333333333333333333333"


def _write_artifact(root: Path, name: str, bytecode: str) -> None:
    path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bytecode": {"object": bytecode}}) + "\n")


def _root_with_artifacts(tmp_path: Path) -> Path:
    _write_artifact(tmp_path, "AgentonomyTestUSD", "0x6001600055")
    _write_artifact(tmp_path, "AgentonomyBudgetExecutor", "0x6002600055")
    return tmp_path


def _create_address(sender: str, nonce: int) -> str:
    encoded_nonce = b"" if nonce == 0 else nonce.to_bytes((nonce.bit_length() + 7) // 8, "big")
    return "0x" + keccak(rlp.encode([bytes.fromhex(sender[2:]), encoded_nonce]))[-20:].hex()


def _canonical_hash(value: dict) -> str:
    unsigned = dict(value)
    for runtime_field in ("plan_sha256", "status", "rpc_check"):
        unsigned.pop(runtime_field, None)
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def test_build_plan_predicts_two_create_addresses_and_pins_token(tmp_path: Path) -> None:
    plan = build_plan(
        nonce=7,
        gas_price_wei=2_000_000_000,
        payee=PAYEE,
        gas_limit=100_000,
        root=_root_with_artifacts(tmp_path),
    )

    token_address = _create_address(DEPLOYER, 7)
    executor_address = _create_address(DEPLOYER, 8)
    token_tx, executor_tx = plan["transactions"]

    assert plan["chain_id"] == CHAIN_ID == 10143
    assert plan["buyer"] == BUYER
    assert plan["supply"] == SUPPLY == 1_000_000
    assert plan["deployer"] == DEPLOYER
    assert plan["execution_signer"] == EXECUTION_SIGNER
    assert plan["predicted_addresses"] == {
        "token": token_address,
        "executor": executor_address,
    }
    assert token_tx["nonce"] == 7
    assert executor_tx["nonce"] == 8
    assert token_tx["to"] is None
    assert executor_tx["to"] is None
    assert token_tx["data"] == "0x6001600055" + encode(["address", "uint256"], [BUYER, SUPPLY]).hex()
    assert executor_tx["data"] == "0x6002600055" + encode(["address"], [token_address]).hex()
    assert executor_tx["constructor_arguments"] == {"tokenAddress": token_address}
    assert token_tx["constructor_arguments"] == {"owner": BUYER, "supply": SUPPLY}
    assert token_tx["type"] == executor_tx["type"] == "legacy"
    assert token_tx["gasPrice"] == executor_tx["gasPrice"] == 2_000_000_000
    assert "maxFeePerGas" not in token_tx
    assert "maxPriorityFeePerGas" not in token_tx
    assert "max_fee_per_gas_wei" not in token_tx
    assert "max_fee_wei" not in token_tx
    assert plan["max_fee_per_gas_wei"] == 2_000_000_000
    assert plan["max_fee_wei_per_transaction"] == 200_000_000_000_000
    assert plan["broadcast"] is False
    assert plan["signed"] is False
    assert plan["plan_sha256"] == _canonical_hash(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nonce", True),
        ("nonce", 1.0),
        ("nonce", -1),
        ("gas_price_wei", False),
        ("gas_price_wei", 0),
        ("gas_price_wei", MAX_GAS_PRICE_WEI + 1),
        ("gas_limit", True),
        ("gas_limit", 0),
        ("gas_limit", MAX_TX_GAS_LIMIT + 1),
    ],
)
def test_build_plan_rejects_non_strict_or_out_of_range_numbers(
    tmp_path: Path, field: str, value: object
) -> None:
    kwargs = dict(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    kwargs[field] = value
    with pytest.raises(ValueError):
        build_plan(**kwargs)


def test_build_plan_rejects_buyer_signer_or_predicted_contract_as_payee(tmp_path: Path) -> None:
    root = _root_with_artifacts(tmp_path)
    token_address = _create_address(DEPLOYER, 1)
    executor_address = _create_address(DEPLOYER, 2)
    for payee in (
        BUYER,
        EXECUTION_SIGNER,
        token_address,
        executor_address,
        "0x0000000000000000000000000000000000000000",
    ):
        with pytest.raises(ValueError):
            build_plan(nonce=1, gas_price_wei=1, payee=payee, root=root)


def test_build_plan_allows_gas_relayer_as_payee(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=DEPLOYER, root=_root_with_artifacts(tmp_path))

    assert plan["payee"] == DEPLOYER.lower()


def test_build_plan_requires_nonempty_local_creation_bytecode(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "AgentonomyTestUSD", "0x")
    _write_artifact(tmp_path, "AgentonomyBudgetExecutor", "0x6002")
    with pytest.raises(ValueError, match="bytecode"):
        build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=tmp_path)


class _FakeRpc:
    calls: list[tuple[str, list]] = []
    pending_by_url: dict[str, int] = {}
    balance_by_url: dict[str, int] = {}
    gas_price_by_url: dict[str, int] = {}

    def __init__(self, url: str):
        self.url = url

    def check_chain(self, chain_id: int) -> None:
        self.calls.append(("check_chain", [chain_id]))

    def call(self, method: str, params: list):
        self.calls.append((method, params))
        if method == "eth_getTransactionCount":
            return hex(self.pending_by_url[self.url])
        if method == "eth_gasPrice":
            return hex(self.gas_price_by_url[self.url])
        if method == "eth_getBalance":
            return hex(self.balance_by_url[self.url])
        raise AssertionError(method)


def _rpc_fixture(
    plan: dict,
    *,
    nonce_b: int | None = None,
    balance: int | None = None,
    gas_price: int | None = None,
) -> None:
    urls = plan["rpc_urls"]
    _FakeRpc.calls = []
    _FakeRpc.pending_by_url = {urls[0]: plan["nonce"], urls[1]: plan["nonce"] if nonce_b is None else nonce_b}
    observed_gas_price = plan["gas_price_wei"] if gas_price is None else gas_price
    _FakeRpc.gas_price_by_url = {urls[0]: observed_gas_price, urls[1]: observed_gas_price}
    funded = plan["required_funding_wei"] if balance is None else balance
    _FakeRpc.balance_by_url = {urls[0]: funded, urls[1]: funded}


def test_rpc_check_is_read_only_and_reports_agreement(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    _rpc_fixture(plan)
    report = read_rpc_check(plan, rpc_factory=_FakeRpc)

    assert report["status"] == "ready"
    assert report["rpc_disagreement"] is False
    assert report["insufficient_funding"] is False
    assert not any(method == "eth_sendRawTransaction" for method, _ in _FakeRpc.calls)


def test_rpc_check_reports_nonce_disagreement_without_signing(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    _rpc_fixture(plan, nonce_b=2)
    report = read_rpc_check(plan, rpc_factory=_FakeRpc)

    assert report["status"] == "blocked"
    assert report["rpc_disagreement"] is True
    assert "pending_nonce" in report["reasons"]
    assert not any(method == "eth_sendRawTransaction" for method, _ in _FakeRpc.calls)


def test_rpc_check_reports_insufficient_funding_without_signing(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    _rpc_fixture(plan, balance=plan["required_funding_wei"] - 1)
    report = read_rpc_check(plan, rpc_factory=_FakeRpc)

    assert report["status"] == "blocked"
    assert report["insufficient_funding"] is True
    assert "insufficient_funding" in report["reasons"]
    assert not any(method == "eth_sendRawTransaction" for method, _ in _FakeRpc.calls)


def test_rpc_check_blocks_gas_price_above_concrete_plan(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=100, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    _rpc_fixture(plan, gas_price=101)

    report = read_rpc_check(plan, rpc_factory=_FakeRpc)

    assert report["status"] == "blocked"
    assert report["rpc_disagreement"] is False
    assert "gas_price_above_plan" in report["reasons"]


@pytest.mark.parametrize(
    "rpc_urls",
    [
        ("https://user:secret@rpc-a.example", "https://rpc-b.example"),
        ("https://rpc-a.example?token=secret", "https://rpc-b.example"),
    ],
)
def test_rpc_check_rejects_rpc_credentials_or_query(tmp_path: Path, rpc_urls: tuple[str, str]) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    plan["rpc_urls"] = list(rpc_urls)

    with pytest.raises(ValueError, match="RPC URL"):
        read_rpc_check(plan, rpc_factory=_FakeRpc)


def test_plan_hash_is_stable_when_runtime_rpc_status_is_attached(tmp_path: Path) -> None:
    plan = build_plan(nonce=1, gas_price_wei=1, payee=PAYEE, root=_root_with_artifacts(tmp_path))
    original_hash = plan["plan_sha256"]
    plan["status"] = "blocked"
    plan["rpc_check"] = {"status": "blocked", "reasons": ["rpc_error"]}

    assert plan["plan_sha256"] == original_hash == _canonical_hash(plan)
