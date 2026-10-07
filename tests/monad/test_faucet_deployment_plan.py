from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.monad.faucet_deployment_plan import (
    CLAIM_AMOUNT,
    DEPLOYER,
    FAUCET_SUPPLY,
    PLAN_SCHEMA,
    build_plan,
    predict_create_address,
)


def _write_artifact(root: Path, name: str, bytecode: str, runtime: str = "0x6001600055") -> None:
    path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "bytecode": {"object": bytecode},
                "deployedBytecode": {"object": runtime},
            }
        )
        + "\n"
    )


def _artifact_root(tmp_path: Path) -> Path:
    _write_artifact(tmp_path, "AgentonomyFaucetUSD", "0x6001600055", "0x6002600055")
    _write_artifact(tmp_path, "AgentonomyBudgetExecutor", "0x6003600055", "0x6004600055")
    return tmp_path


def test_build_plan_is_faucet_specific_and_has_exact_two_create_transactions(tmp_path: Path) -> None:
    plan = build_plan(
        nonce=7,
        gas_price_wei=2_000_000_000,
        payee=DEPLOYER,
        gas_limit=100_000,
        root=_artifact_root(tmp_path),
    )

    assert plan["schema_version"] == PLAN_SCHEMA == "monad-faucet-deployment-plan-v1"
    assert plan["broadcast"] is False
    assert plan["signed"] is False
    assert plan["faucet_supply_atomic"] == FAUCET_SUPPLY == 1_000_000_000
    assert plan["claim_amount_atomic"] == CLAIM_AMOUNT == 1_000_000
    assert plan["token_symbol"] == "TestUSD"
    assert plan["token_decimals"] == 6
    assert plan["initial_faucet_pool_balance_atomic"] == FAUCET_SUPPLY
    assert plan["initial_buyer_balance_atomic"] == 0
    assert plan["initial_buyer_claimed"] is False
    assert plan["required_funding_wei"] == 2 * 100_000 * 2_000_000_000

    transactions = plan["transactions"]
    assert len(transactions) == 2
    faucet_tx, executor_tx = transactions
    assert faucet_tx["nonce"] == 7
    assert faucet_tx["to"] is None
    assert faucet_tx["constructor_types"] == []
    assert faucet_tx["constructor_arguments"] == {}
    assert faucet_tx["data"] == "0x6001600055"
    assert executor_tx["nonce"] == 8
    assert executor_tx["to"] is None
    assert executor_tx["constructor_types"] == ["address"]
    assert executor_tx["constructor_arguments"] == {
        "tokenAddress": plan["predicted_addresses"]["token"]
    }
    assert executor_tx["data"].startswith("0x6003600055")
    assert plan["constructor_arguments"] == {
        "AgentonomyFaucetUSD": {},
        "AgentonomyBudgetExecutor": executor_tx["constructor_arguments"],
    }


def test_plan_predicts_create_addresses_and_binds_executor_to_faucet(tmp_path: Path) -> None:
    plan = build_plan(
        nonce=0,
        gas_price_wei=1,
        payee=DEPLOYER,
        root=_artifact_root(tmp_path),
    )
    addresses = plan["predicted_addresses"]
    assert addresses["token"] == predict_create_address(plan["deployer"], 0)
    assert addresses["executor"] == predict_create_address(plan["deployer"], 1)
    assert addresses["token"] != addresses["executor"]


def test_plan_rejects_unsafe_inputs_and_artifact_mutation(tmp_path: Path) -> None:
    root = _artifact_root(tmp_path)
    with pytest.raises(ValueError, match="gas_price_wei"):
        build_plan(nonce=0, gas_price_wei=500_000_000_001, payee=DEPLOYER, root=root)
    with pytest.raises(ValueError, match="nonce"):
        build_plan(nonce=-1, gas_price_wei=1, payee=DEPLOYER, root=root)

    plan = build_plan(nonce=0, gas_price_wei=1, payee=DEPLOYER, root=root)
    _write_artifact(root, "AgentonomyFaucetUSD", "0x6007600055", "0x6002600055")
    from scripts.monad import faucet_deploy

    with pytest.raises(ValueError, match="artifact|plan"):
        faucet_deploy.validate_plan(plan, root=root)
