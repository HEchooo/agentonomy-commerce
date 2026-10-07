from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import time

import httpx
import pytest
from eth_abi import encode
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak

from scripts.monad.deployment_plan import DEPLOYER, build_plan
from scripts.monad import deploy


PAYEE = DEPLOYER


def _write_artifact(root: Path, name: str, bytecode: str) -> None:
    path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bytecode": {"object": bytecode}}) + "\n")


def _root_with_artifacts(tmp_path: Path) -> Path:
    _write_artifact(tmp_path, "AgentonomyTestUSD", "0x6001600055")
    _write_artifact(tmp_path, "AgentonomyBudgetExecutor", "0x6002600055")
    return tmp_path


def _plan(tmp_path: Path, *, nonce: int = 7, gas_price: int = 2_000_000_000) -> dict:
    return build_plan(
        nonce=nonce,
        gas_price_wei=gas_price,
        payee=PAYEE,
        gas_limit=100_000,
        root=_root_with_artifacts(tmp_path),
    )


class _NoopSigner:
    def __init__(self) -> None:
        self.calls = 0

    def sign_digest(self, _digest: bytes) -> bytes:
        self.calls += 1
        raise AssertionError("test signer must not be called")


class _Rpc:
    def __init__(self, _url: str, *, writable: bool = False) -> None:
        self.writable = writable

    def call(self, method: str, _params: list):
        if method == "eth_chainId":
            return hex(deploy.CHAIN_ID)
        raise AssertionError(f"unexpected RPC call {method}")


def test_validate_plan_rejects_tampering_and_stale_artifact(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    deploy.validate_plan(plan, root=tmp_path)

    tampered = copy.deepcopy(plan)
    tampered["payee"] = "0x" + "44" * 20
    with pytest.raises(ValueError, match="plan"):
        deploy.validate_plan(tampered, root=tmp_path)

    _write_artifact(tmp_path, "AgentonomyTestUSD", "0x6003600055")
    with pytest.raises(ValueError, match="artifact|plan"):
        deploy.validate_plan(plan, root=tmp_path)


def test_read_only_run_does_not_instantiate_signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path)
    signer_calls: list[object] = []

    def signer_factory():
        signer_calls.append(object())
        return _NoopSigner()

    result = deploy.run_deployment(
        plan,
        tmp_path / "journal.json",
        execute=False,
        signer_factory=signer_factory,
        rpc_factory=_Rpc,
        root=tmp_path,
    )

    assert result["status"] == "validated"
    assert signer_calls == []


def test_cli_read_only_checks_exact_hash_without_loading_signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _plan(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    monkeypatch.setattr(
        deploy,
        "_aws_signer_factory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read-only CLI must not load signer")),
    )

    result = deploy.main(
        [
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan["plan_sha256"],
            "--journal",
            str(tmp_path / "cli-journal.json"),
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


def test_cli_post_send_journal_failure_reports_unknown_hash_without_false_no_broadcast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.monad import deployment_plan

    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)
    monkeypatch.setattr(__import__(__name__), "PAYEE", relayer)
    plan = _plan(tmp_path)
    plan_path = tmp_path / "execute-plan.json"
    plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr(deploy, "ROOT", tmp_path)

    class Signer:
        def sign_digest(self, digest: bytes) -> bytes:
            signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
            return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])

    class Rpc(_Rpc):
        send_count = 0

        def call(self, method: str, params: list):
            if method == "eth_chainId":
                return hex(deploy.CHAIN_ID)
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"])
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return hex(plan["required_funding_wei"])
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(90_000)
            if method == "eth_sendRawTransaction":
                assert self.writable
                self.__class__.send_count += 1
                return "0x" + "22" * 32
            raise AssertionError(f"unexpected RPC call {method}")

    monkeypatch.setattr(deploy, "_rpc_factory_default", lambda: Rpc)
    monkeypatch.setattr(deploy, "_aws_signer_factory", lambda *_args, **_kwargs: (lambda: Signer()))
    real_save = deploy._save_journal
    save_calls = 0

    def flaky_save(path: Path, journal: dict[str, object], concrete_plan: dict[str, object]) -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 3:
            raise deploy.OperatorError("simulated post-send journal failure")
        real_save(path, journal, concrete_plan)

    monkeypatch.setattr(deploy, "_save_journal", flaky_save)
    journal = tmp_path / "execute-journal.json"
    exit_code = deploy.main(
        [
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan["plan_sha256"],
            "--journal",
            str(journal),
            "--signer-config",
            str(tmp_path / "signer-config.json"),
            "--execute",
        ]
    )
    stdout = capsys.readouterr().out
    output = json.loads(stdout)
    assert exit_code == 0, output
    assert output["status"] == "unknown"
    assert output["broadcast"] == "unknown"
    assert output["pending"] is True
    assert output["transactions"][0]["tx_hash"] == json.loads(journal.read_text())["transactions"][0]["tx_hash"]
    assert "raw_transaction" not in stdout
    assert Rpc.send_count == 1


def test_preflight_fails_closed_before_signing_on_insufficient_balance(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    signer_calls: list[object] = []

    class Rpc(_Rpc):
        def call(self, method: str, _params: list):
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"])
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return "0x0"
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(90_000)
            return super().call(method, _params)

    def signer_factory():
        signer_calls.append(object())
        return _NoopSigner()

    result = deploy.run_deployment(
        plan,
        tmp_path / "journal.json",
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=Rpc,
        root=tmp_path,
    )

    assert result["status"] == "blocked"
    assert "insufficient" in result["reason"]
    assert signer_calls == []
    assert not (tmp_path / "journal.json").exists()


def test_signed_transaction_is_durable_before_broadcast_and_unknown_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from eth_account import Account
    from eth_keys import keys
    from scripts.monad import deployment_plan

    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)
    monkeypatch.setattr(__import__(__name__), "PAYEE", relayer)
    plan = _plan(tmp_path)
    signer_calls: list[bytes] = []
    broadcast_calls: list[str] = []
    evidence_reads: list[str] = []

    class Signer:
        def sign_digest(self, digest: bytes) -> bytes:
            signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
            signer_calls.append(digest)
            return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])

    class Rpc(_Rpc):
        def call(self, method: str, params: list):
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"])
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return hex(plan["required_funding_wei"])
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(90_000)
            if method == "eth_sendRawTransaction":
                broadcast_calls.append(params[0])
                raise RuntimeError("provider timeout after accepting request")
            if method in {"eth_getTransactionByHash", "eth_getTransactionReceipt"}:
                evidence_reads.append(method)
                return None
            return super().call(method, params)

    journal = tmp_path / "journal.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: Signer(),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert first["status"] == "pending"
    assert len(signer_calls) == 1
    assert len(broadcast_calls) == 1
    assert evidence_reads == []
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    saved = json.loads(journal.read_text())
    assert saved["transactions"][0]["attempted"] is True
    assert saved["transactions"][0]["raw_transaction"].startswith("0x")

    second = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("must not re-sign")),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert second["status"] == "pending"
    assert len(signer_calls) == 1
    assert len(broadcast_calls) == 1


@pytest.mark.parametrize("mode", ["receipt_gap", "tx_gap", "rpc_error"])
def test_attempted_transaction_stays_pending_for_temporary_rpc_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from scripts.monad import deployment_plan

    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)
    monkeypatch.setattr(__import__(__name__), "PAYEE", relayer)
    plan = _plan(tmp_path)
    signer_calls: list[bytes] = []

    class Signer:
        def sign_digest(self, digest: bytes) -> bytes:
            signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
            signer_calls.append(digest)
            return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])

    class Rpc(_Rpc):
        send_count = 0
        transaction: dict[str, object] | None = None

        def call(self, method: str, params: list):
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"])
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return hex(plan["required_funding_wei"])
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(90_000)
            if method == "eth_sendRawTransaction":
                assert self.writable
                raw = params[0]
                tx_hash = "0x" + keccak(bytes.fromhex(raw[2:])).hex()
                tx = plan["transactions"][0]
                self.__class__.transaction = {
                    "hash": tx_hash,
                    "chainId": hex(deploy.CHAIN_ID),
                    "from": deploy.DEPLOYER.lower(),
                    "to": None,
                    "nonce": hex(tx["nonce"]),
                    "value": "0x0",
                    "input": tx["data"],
                    "gas": hex(tx["gas"]),
                    "gasPrice": hex(tx["gasPrice"]),
                    "blockNumber": "0x1",
                    "blockHash": "0x" + "11" * 32,
                }
                self.__class__.send_count += 1
                return tx_hash
            if method == "eth_getTransactionByHash":
                if mode == "rpc_error":
                    raise RuntimeError("temporary provider read failure")
                if mode == "tx_gap":
                    return None
                return self.__class__.transaction
            if method == "eth_getTransactionReceipt":
                if mode == "tx_gap":
                    return {"transactionHash": self.__class__.transaction["hash"]}
                return None
            return super().call(method, params)

    journal = tmp_path / f"temporary-{mode}.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=Signer,
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert first["status"] == "pending"
    assert Rpc.send_count == 1
    assert len(signer_calls) == 1


def test_nonmatching_broadcast_hash_returns_pending_before_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.monad import deployment_plan

    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)
    monkeypatch.setattr(__import__(__name__), "PAYEE", relayer)
    plan = _plan(tmp_path)
    signer_calls: list[bytes] = []

    class Signer:
        def sign_digest(self, digest: bytes) -> bytes:
            signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
            signer_calls.append(digest)
            return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])

    class Rpc(_Rpc):
        send_count = 0
        evidence_reads = 0

        def call(self, method: str, params: list):
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"])
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return hex(plan["required_funding_wei"])
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(90_000)
            if method == "eth_sendRawTransaction":
                assert self.writable
                self.__class__.send_count += 1
                return "0x" + "22" * 32
            if method in {"eth_getTransactionByHash", "eth_getTransactionReceipt"}:
                self.__class__.evidence_reads += 1
                raise AssertionError("mismatched send response must return before RPC reconciliation")
            return super().call(method, params)

    journal = tmp_path / "mismatched-broadcast.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=Signer,
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert first["status"] == "pending"
    assert Rpc.send_count == 1
    assert Rpc.evidence_reads == 0
    assert len(signer_calls) == 1

    second = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("mismatched send must not re-sign")),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert second["status"] == "pending"
    assert Rpc.send_count == 1

    second = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("temporary evidence must not re-sign")),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert second["status"] == "pending"
    assert Rpc.send_count == 1
    assert len(signer_calls) == 1


def test_journal_parent_is_private_and_symlink_is_rejected(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    private_dir = tmp_path / "private"
    result = deploy.run_deployment(
        plan,
        private_dir / "journal.json",
        execute=False,
        root=tmp_path,
    )
    assert result["status"] == "validated"
    assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700

    target = tmp_path / "real-journal.json"
    target.write_text("{}")
    link = tmp_path / "journal-link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        deploy.run_deployment(plan, link, execute=False, root=tmp_path)


def test_existing_lock_special_paths_are_rejected_without_mutation(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    directory_lock = tmp_path / "directory-journal.json.lock"
    directory_lock.mkdir(mode=0o755)
    directory_mode = stat.S_IMODE(directory_lock.stat().st_mode)
    with pytest.raises(ValueError, match="lock"):
        deploy.run_deployment(plan, directory_lock.with_name("directory-journal.json"), execute=False, root=tmp_path)
    assert directory_lock.is_dir()
    assert stat.S_IMODE(directory_lock.stat().st_mode) == directory_mode

    fifo_lock = tmp_path / "fifo-journal.json.lock"
    os.mkfifo(fifo_lock, 0o600)
    fifo_mode = stat.S_IMODE(fifo_lock.stat().st_mode)
    with pytest.raises(ValueError, match="lock"):
        deploy.run_deployment(plan, fifo_lock.with_name("fifo-journal.json"), execute=False, root=tmp_path)
    assert stat.S_ISFIFO(fifo_lock.stat().st_mode)
    assert stat.S_IMODE(fifo_lock.stat().st_mode) == fifo_mode

    lock_source = tmp_path / "lock-source"
    lock_source.write_text("owned by another file")
    lock_source.chmod(0o600)
    hardlink_lock = tmp_path / "hardlink-journal.json.lock"
    os.link(lock_source, hardlink_lock)
    source_before = (lock_source.stat().st_ino, lock_source.stat().st_mode, lock_source.read_bytes())
    with pytest.raises(ValueError, match="lock"):
        deploy.run_deployment(plan, hardlink_lock.with_name("hardlink-journal.json"), execute=False, root=tmp_path)
    assert (lock_source.stat().st_ino, lock_source.stat().st_mode, lock_source.read_bytes()) == source_before


def test_preflight_rejects_nonce_disagreement_and_estimate_over_cap(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    class Rpc(_Rpc):
        disagree = False
        high_estimate = False

        def call(self, method: str, _params: list):
            if method == "eth_getTransactionCount":
                return hex(plan["nonce"] + (1 if self.disagree and not self.writable else 0))
            if method == "eth_gasPrice":
                return hex(plan["gas_price_wei"])
            if method == "eth_getBalance":
                return hex(plan["required_funding_wei"])
            if method == "eth_getCode":
                return "0x"
            if method == "eth_estimateGas":
                return hex(plan["transactions"][0]["gas"] + 1 if self.high_estimate else 90_000)
            return super().call(method, _params)

    Rpc.disagree = True
    result = deploy.run_deployment(
        plan,
        tmp_path / "nonce-journal.json",
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("must not sign")),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert result["status"] == "blocked"
    assert "nonce" in result["reason"]

    Rpc.disagree = False
    Rpc.high_estimate = True
    result = deploy.run_deployment(
        plan,
        tmp_path / "estimate-journal.json",
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("must not sign")),
        rpc_factory=Rpc,
        root=tmp_path,
    )
    assert result["status"] == "blocked"
    assert "estimate" in result["reason"]


def test_journal_tampering_is_detected_before_rpc_or_signer(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    journal = tmp_path / "tampered.json"
    journal.write_text(json.dumps({"schema_version": "monad-deployment-journal-v1"}))
    journal.chmod(0o600)

    with pytest.raises(ValueError, match="journal"):
        deploy.run_deployment(
            plan,
            journal,
            execute=False,
            rpc_factory=lambda _url: (_ for _ in ()).throw(AssertionError("must not call RPC")),
            root=tmp_path,
        )


def test_second_create_waits_for_first_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, signer_factory, rpc_factory = _successful_fixture(tmp_path, monkeypatch)
    rpc_factory.shared_state["finalized_height"] = 11
    result = deploy.run_deployment(
        plan,
        tmp_path / "pending-first.json",
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert result["status"] == "pending"
    assert rpc_factory.shared_state["send_count"] == 1
    assert signer_factory.calls == 1


def test_successful_deployment_and_replay_recheck_public_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, signer_factory, rpc_factory = _successful_fixture(tmp_path, monkeypatch)
    journal = tmp_path / "complete.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert first["status"] == "complete"
    assert len(first["transactions"]) == 2
    assert all(item["code_hash"] for item in first["transactions"])
    assert signer_factory.calls == 1
    assert rpc_factory.shared_state["send_count"] == 2

    replay = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("replay must not sign")),
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert replay["status"] == "complete"
    assert rpc_factory.shared_state["send_count"] == 2

    rpc_factory.shared_state["buyer_balance"] = 0
    replay_after_purchase = deploy.run_deployment(plan, journal, execute=False, rpc_factory=rpc_factory, root=tmp_path)
    assert replay_after_purchase["status"] == "complete"


def test_first_verified_record_is_rechecked_before_resolving_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, signer_factory, rpc_factory = _successful_fixture(tmp_path, monkeypatch)
    rpc_factory.shared_state["finalized_height"] = 13
    journal = tmp_path / "first-recheck.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert first["status"] == "pending"
    assert [record["status"] for record in first["transactions"]] == ["verified", "pending"]

    rpc_factory.shared_state["rows"][0]["receipt"]["contractAddress"] = "0x" + "44" * 20
    rpc_factory.shared_state["finalized_height"] = 20
    resolved = deploy.run_deployment(
        plan,
        journal,
        execute=False,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("recheck must not sign")),
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert resolved["status"] == "blocked"
    assert json.loads(journal.read_text())["transactions"][0]["status"] == "failed"


def test_first_verified_history_is_preserved_when_recheck_is_temporarily_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, signer_factory, rpc_factory = _successful_fixture(tmp_path, monkeypatch)
    rpc_factory.shared_state["finalized_height"] = 13
    journal = tmp_path / "first-history.json"
    first = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    original = copy.deepcopy(first["transactions"][0])
    tx_hash = original["tx_hash"]
    rpc_factory.shared_state["missing_hashes"] = {tx_hash}

    pending = deploy.run_deployment(
        plan,
        journal,
        execute=False,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("temporary recheck must not sign")),
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert pending["status"] == "pending"
    saved = json.loads(journal.read_text())
    assert saved["transactions"][0]["status"] == "verified"
    assert saved["transactions"][0]["summary"] == original


def test_journal_public_summary_values_and_complete_cardinality_are_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, signer_factory, rpc_factory = _successful_fixture(tmp_path, monkeypatch)
    journal = tmp_path / "strict-summary.json"
    result = deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=signer_factory,
        rpc_factory=rpc_factory,
        root=tmp_path,
    )
    assert result["status"] == "complete"

    original = json.loads(journal.read_text())
    tampered = copy.deepcopy(original)
    raw_transaction = tampered["transactions"][0]["raw_transaction"]
    for item in (tampered["transactions"][0]["summary"], tampered["summary"]["transactions"][0]):
        item["address"] = raw_transaction
        item["block_number"] = {}
    journal.write_text(json.dumps(tampered))
    journal.chmod(0o600)
    with pytest.raises(ValueError, match="public|address|block"):
        deploy.run_deployment(plan, journal, execute=False, rpc_factory=rpc_factory, root=tmp_path)

    # Restore the complete journal, then remove the second record while
    # keeping the top-level status marker.  A one-transaction journal cannot
    # claim deployment completion.
    journal.write_text(json.dumps(original))
    journal.chmod(0o600)
    restored = json.loads(journal.read_text())
    restored["transactions"] = restored["transactions"][:1]
    restored["summary"]["transactions"] = restored["summary"]["transactions"][:1]
    journal.write_text(json.dumps(restored))
    journal.chmod(0o600)
    with pytest.raises(ValueError, match="complete|transaction count"):
        deploy.run_deployment(plan, journal, execute=False, rpc_factory=rpc_factory, root=tmp_path)


def test_aws_environment_isolated_to_explicit_credential_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credential_directory = tmp_path / "runtime-credentials"
    credential_directory.mkdir(mode=0o700)
    monkeypatch.setenv("AWS_PROFILE", "operator-default")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/operator/default/credentials")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/operator/default/config")
    monkeypatch.setenv("HOME", "/operator/home")

    with deploy._sanitized_aws_environment({"credential_directory": str(credential_directory)}):
        assert os.environ["HOME"] == str(credential_directory)
        assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == str(credential_directory / "credentials")
        assert os.environ["AWS_CONFIG_FILE"] == str(credential_directory / "config")
        assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
        assert "AWS_PROFILE" not in os.environ

    assert os.environ["HOME"] == "/operator/home"
    assert os.environ["AWS_PROFILE"] == "operator-default"
    assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == "/operator/default/credentials"


class _SignerFactory:
    def __init__(self, private_key: bytes):
        self.private_key = private_key
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self

    def sign_digest(self, digest: bytes) -> bytes:
        signature = keys.PrivateKey(self.private_key).sign_msg_hash(digest)
        return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])


class _SuccessfulRpc:
    def __init__(self, url: str, *, writable: bool = False):
        self.url = url
        self.writable = writable
        self.state = _SuccessfulRpc.shared_state

    shared_state: dict[str, object] = {}

    def call(self, method: str, params: list):
        plan = self.state["plan"]
        if method == "eth_chainId":
            return hex(deploy.CHAIN_ID)
        if method == "eth_getTransactionCount":
            return hex(plan["nonce"] + self.state["send_count"])
        if method == "eth_gasPrice":
            return hex(plan["gas_price_wei"])
        if method == "eth_getBalance":
            return hex(plan["required_funding_wei"])
        if method == "eth_estimateGas":
            return hex(90_000)
        if method == "eth_getCode":
            address, block = params
            if block == "latest":
                deployed = any(item["address"] == address.lower() for item in self.state["rows"])
                return "0x6001" if deployed and address.lower() == plan["predicted_addresses"]["token"] else (
                    "0x6002" if deployed else "0x"
                )
            return "0x6001" if address.lower() == plan["predicted_addresses"]["token"] else "0x6002"
        if method == "eth_sendRawTransaction":
            assert self.writable
            raw = params[0]
            tx_hash = "0x" + keccak(bytes.fromhex(raw[2:])).hex()
            index = self.state["send_count"]
            tx = plan["transactions"][index]
            block_number = 10 + index
            block_hash = "0x" + ("10" if index == 0 else "11") * 32
            target = plan["predicted_addresses"]["token" if index == 0 else "executor"]
            row = {
                "hash": tx_hash,
                "chainId": hex(deploy.CHAIN_ID),
                "from": deploy.DEPLOYER.lower(),
                "to": None,
                "nonce": hex(tx["nonce"]),
                "value": "0x0",
                "input": tx["data"],
                "data": tx["data"],
                "gas": hex(tx["gas"]),
                "gasPrice": hex(tx["gasPrice"]),
                "blockNumber": hex(block_number),
                "blockHash": block_hash,
            }
            receipt = {
                "transactionHash": tx_hash,
                "blockNumber": hex(block_number),
                "blockHash": block_hash,
                "status": "0x1",
                "contractAddress": target,
            }
            self.state["rows"].append({"address": target, "transaction": row, "receipt": receipt})
            self.state["send_count"] += 1
            return tx_hash
        if method == "eth_getTransactionByHash":
            if params[0] in self.state.get("missing_hashes", set()):
                return None
            for item in self.state["rows"]:
                if item["transaction"]["hash"] == params[0]:
                    return item["transaction"]
            return None
        if method == "eth_getTransactionReceipt":
            if params[0] in self.state.get("missing_hashes", set()):
                return None
            for item in self.state["rows"]:
                if item["receipt"]["transactionHash"] == params[0]:
                    return item["receipt"]
            return None
        if method == "eth_getBlockByNumber":
            tag = params[0]
            if tag == "finalized":
                return {"number": hex(self.state["finalized_height"]), "hash": "0x" + "ff" * 32}
            number = int(tag, 16)
            if number in {10, 11}:
                return {"number": hex(number), "hash": "0x" + ("10" if number == 10 else "11") * 32}
            if number == 17:
                return {"number": hex(number), "hash": "0x" + "ee" * 32}
            raise AssertionError(tag)
        if method == "eth_call":
            to = params[0]["to"].lower()
            data = bytes.fromhex(params[0]["data"][2:])
            selector = data[:4]
            block_tag = params[1]
            token = plan["predicted_addresses"]["token"]
            executor = plan["predicted_addresses"]["executor"]
            if to == token:
                buyer_balance = (
                    plan["supply"]
                    if selector == keccak(text="balanceOf(address)")[:4] and int(block_tag, 16) <= 10
                    else self.state["buyer_balance"]
                )
                values = {
                    keccak(text="symbol()")[:4]: encode(["string"], ["TestUSD"]),
                    keccak(text="decimals()")[:4]: encode(["uint8"], [6]),
                    keccak(text="totalSupply()")[:4]: encode(["uint256"], [plan["supply"]]),
                    keccak(text="balanceOf(address)")[:4]: encode(["uint256"], [buyer_balance]),
                }
            elif to == executor:
                values = {
                    keccak(text="TOKEN()")[:4]: encode(["address"], [token]),
                    keccak(text="EXECUTION_CHAIN_ID()")[:4]: encode(["uint256"], [deploy.CHAIN_ID]),
                    keccak(text="DOMAIN_SEPARATOR()")[:4]: bytes.fromhex(deploy._expected_domain_separator(executor)[2:]),
                }
            else:
                raise AssertionError(to)
            return "0x" + values[selector].hex()
        raise AssertionError(method)


def _successful_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    from scripts.monad import deployment_plan

    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)
    monkeypatch.setattr(__import__(__name__), "PAYEE", relayer)
    plan = _plan(tmp_path)
    signer = _SignerFactory(private_key)
    _SuccessfulRpc.shared_state = {
        "plan": plan,
        "rows": [],
        "send_count": 0,
        "finalized_height": 20,
        "buyer_balance": plan["supply"],
        "missing_hashes": set(),
    }
    return plan, signer, _SuccessfulRpc


@pytest.mark.skipif(shutil.which("anvil") is None, reason="Anvil is required for the local deployment smoke")
def test_real_anvil_10143_deployment_and_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise real CREATE receipts, getters, finality and replay locally."""

    from agentonomy_commerce.budget_network import RpcClient
    from scripts.monad import deployment_plan

    private_key = bytes.fromhex("11" * 32)
    relayer = Account.from_key(private_key).address.lower()
    monkeypatch.setattr(deployment_plan, "DEPLOYER", relayer)
    monkeypatch.setattr(deploy, "DEPLOYER", relayer)

    root = Path(__file__).resolve().parents[2]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    local_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            "anvil",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            str(deploy.CHAIN_ID),
            "--gas-price",
            "1000000000",
            "--silent",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def admin_call(method: str, params: list[object]) -> object:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(
                local_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
            raise AssertionError(f"local Anvil RPC failed: {method}")
        return payload["result"]

    try:
        local_rpc = RpcClient(local_url)
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("local Anvil exited before becoming ready")
            try:
                if local_rpc.call("eth_chainId", []) == hex(deploy.CHAIN_ID):
                    break
            except Exception:
                time.sleep(0.05)
        else:
            raise AssertionError("local Anvil did not become ready")

        admin_call("anvil_setBalance", [relayer, hex(100 * 10**18)])

        class LocalAliasRpc:
            def __init__(self, _logical_url: str, *, writable: bool = False) -> None:
                self._client = RpcClient(local_url, writable=writable)

            def call(self, method: str, params: list[object]) -> object:
                if method == "eth_getBlockByNumber" and params and params[0] == "finalized":
                    # Anvil keeps the finalized tag at genesis.  The operator
                    # still applies the Monad three-block delay; map this
                    # local-only alias to the mined head so the smoke can
                    # exercise that boundary and replay path.
                    params = [self._client.call("eth_blockNumber", []), params[1]]
                return self._client.call(method, params)

        class LocalSigner:
            gas_signer: "LocalSigner"

            def __init__(self) -> None:
                self.gas_signer = self

            def sign_digest(self, digest: bytes) -> bytes:
                signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
                return (
                    signature.r.to_bytes(32, "big")
                    + signature.s.to_bytes(32, "big")
                    + bytes([signature.v + 27])
                )

        plan = deployment_plan.build_plan(
            nonce=0,
            gas_price_wei=10_000_000_000,
            payee=relayer,
            gas_limit=2_000_000,
            root=root,
        )
        journal = tmp_path / "anvil-10143-journal.json"

        first = deploy.run_deployment(
            plan,
            journal,
            execute=True,
            signer_factory=LocalSigner,
            rpc_factory=LocalAliasRpc,
            root=root,
        )
        assert first["status"] == "pending", first
        assert len(first["transactions"]) == 1

        admin_call("anvil_mine", [hex(3)])
        second = deploy.run_deployment(
            plan,
            journal,
            execute=True,
            signer_factory=LocalSigner,
            rpc_factory=LocalAliasRpc,
            root=root,
        )
        assert second["status"] == "pending", second
        assert len(second["transactions"]) == 2, second

        admin_call("anvil_mine", [hex(3)])
        complete = deploy.run_deployment(
            plan,
            journal,
            execute=False,
            signer_factory=lambda: (_ for _ in ()).throw(AssertionError("completed replay must not sign")),
            rpc_factory=LocalAliasRpc,
            root=root,
        )
        assert complete["status"] == "complete"
        assert [item["address"] for item in complete["transactions"]] == [
            plan["predicted_addresses"]["token"],
            plan["predicted_addresses"]["executor"],
        ]

        replay = deploy.run_deployment(
            plan,
            journal,
            execute=False,
            signer_factory=lambda: (_ for _ in ()).throw(AssertionError("replay must not sign")),
            rpc_factory=LocalAliasRpc,
            root=root,
        )
        assert replay["status"] == "complete"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
