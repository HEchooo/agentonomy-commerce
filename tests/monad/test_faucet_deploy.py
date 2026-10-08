from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak

from scripts.monad import faucet_deploy, faucet_deployment_plan
from scripts.monad.faucet_deployment_plan import build_plan


PRIVATE_KEY = bytes.fromhex("11" * 32)
RELAYER = Account.from_key(PRIVATE_KEY).address.lower()
PAYEE = RELAYER


def _write_artifact(root: Path, name: str, bytecode: str, runtime: str) -> None:
    path = root / "contracts" / "out" / f"{name}.sol" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bytecode": {"object": bytecode}, "deployedBytecode": {"object": runtime}}) + "\n")


def _root(tmp_path: Path) -> Path:
    _write_artifact(tmp_path, "AgentonomyFaucetUSD", "0x6001600055", "0x6002600055")
    _write_artifact(tmp_path, "AgentonomyBudgetExecutor", "0x6003600055", "0x6004600055")
    return tmp_path


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, nonce: int = 0) -> dict[str, object]:
    root = _root(tmp_path)
    monkeypatch.setattr(faucet_deployment_plan, "DEPLOYER", RELAYER)
    monkeypatch.setattr(faucet_deploy, "DEPLOYER", RELAYER)
    return build_plan(
        nonce=nonce,
        gas_price_wei=1_000_000_000,
        payee=PAYEE,
        gas_limit=100_000,
        root=root,
    )


class _Signer:
    def __init__(self) -> None:
        self.calls = 0
        self.gas_signer = self

    def sign_digest(self, digest: bytes) -> bytes:
        self.calls += 1
        signature = keys.PrivateKey(PRIVATE_KEY).sign_msg_hash(digest)
        return (
            signature.r.to_bytes(32, "big")
            + signature.s.to_bytes(32, "big")
            + bytes([signature.v + 27])
        )


class _Rpc:
    shared: dict[str, object] = {}

    def __init__(self, url: str, *, writable: bool = False) -> None:
        self.url = url
        self.writable = writable
        self.state = self.shared

    def call(self, method: str, params: list[object]) -> object:
        plan = self.state["plan"]
        if method == "eth_chainId":
            return "0x279f"
        if method == "eth_getTransactionCount":
            return hex(plan["nonce"] + self.state["send_count"])
        if method == "eth_gasPrice":
            return hex(plan["gas_price_wei"])
        if method == "eth_getBalance":
            return hex(plan["required_funding_wei"])
        if method == "eth_estimateGas":
            return hex(90_000)
        if method == "eth_getCode":
            address = params[0].lower()
            return "0x6002" if address == plan["predicted_addresses"]["token"] and self.state["send_count"] else (
                "0x6004" if address == plan["predicted_addresses"]["executor"] and self.state["send_count"] > 1 else "0x"
            )
        if method == "eth_sendRawTransaction":
            assert self.writable
            raw = params[0]
            tx_hash = "0x" + keccak(bytes.fromhex(raw[2:])).hex()
            index = self.state["send_count"]
            tx = plan["transactions"][index]
            block_number = 10 + index
            block_hash = "0x" + ("10" if index == 0 else "11") * 32
            target = plan["predicted_addresses"]["token" if index == 0 else "executor"]
            transaction = {
                "hash": tx_hash,
                "chainId": "0x279f",
                "from": plan["deployer"].lower(),
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
            self.state["rows"].append({"transaction": transaction, "receipt": receipt, "target": target})
            self.state["send_count"] += 1
            return tx_hash
        if method == "eth_getTransactionByHash":
            for row in self.state["rows"]:
                if row["transaction"]["hash"] == params[0]:
                    return row["transaction"]
            return None
        if method == "eth_getTransactionReceipt":
            for row in self.state["rows"]:
                if row["receipt"]["transactionHash"] == params[0]:
                    if self.state.get("mutate_receipt"):
                        return dict(row["receipt"], contractAddress="0x" + "66" * 20)
                    return row["receipt"]
            return None
        if method == "eth_getBlockByNumber":
            if params[0] == "finalized":
                finalized_hash = "0x" + "ff" * 32
                if self.state.get("diverge_finalized_hash") and self.url == faucet_deploy.RPC_URLS[1]:
                    finalized_hash = "0x" + "aa" * 32
                return {"number": "0x14", "hash": finalized_hash}
            number = int(params[0], 16)
            if number in (10, 11):
                return {"number": hex(number), "hash": "0x" + ("10" if number == 10 else "11") * 32}
            if number == 17:
                return {"number": hex(number), "hash": "0x" + "ee" * 32}
            raise AssertionError(params[0])
        if method == "eth_call":
            # Identity reads are deliberately represented by stable values. The
            # implementation must still query both RPCs and compare them.
            data = bytes.fromhex(params[0]["data"][2:])
            selector = data[:4]
            token = plan["predicted_addresses"]["token"]
            executor = plan["predicted_addresses"]["executor"]
            if params[0]["to"].lower() == token:
                from eth_abi import encode
                argument_address = "0x" + data[-20:].hex() if len(data) >= 36 else None
                values = {
                    keccak(text="symbol()")[:4]: encode(["string"], ["TestUSD"]),
                    keccak(text="decimals()")[:4]: encode(["uint8"], [6]),
                    keccak(text="CLAIM_AMOUNT()")[:4]: encode(["uint256"], [plan["claim_amount_atomic"]]),
                    keccak(text="totalSupply()")[:4]: encode(["uint256"], [plan["faucet_supply_atomic"]]),
                    keccak(text="balanceOf(address)")[:4]: encode(
                        ["uint256"],
                        [plan["faucet_supply_atomic"] if argument_address == token else 0],
                    ),
                    keccak(text="claimed(address)")[:4]: encode(["bool"], [False]),
                }
            elif params[0]["to"].lower() == executor:
                from eth_abi import encode
                values = {
                    keccak(text="TOKEN()")[:4]: encode(["address"], [token]),
                    keccak(text="EXECUTION_CHAIN_ID()")[:4]: encode(["uint256"], [10143]),
                    keccak(text="DOMAIN_SEPARATOR()")[:4]: bytes.fromhex(faucet_deploy._expected_domain_separator(executor)[2:]),
                }
            else:
                raise AssertionError(params[0]["to"])
            return "0x" + values[selector].hex()
        raise AssertionError(f"unexpected RPC call: {method}")


class _DivergentFinalityRpc(_Rpc):
    def call(self, method: str, params: list[object]) -> object:
        if method == "eth_getBlockByNumber" and params[0] == "finalized":
            if self.url == faucet_deploy.RPC_URLS[1]:
                return {"number": "0x15", "hash": "0x" + "aa" * 32}
            return {"number": "0x14", "hash": "0x" + "ff" * 32}
        return super().call(method, params)


class _BoundaryDisagreementRpc(_Rpc):
    def call(self, method: str, params: list[object]) -> object:
        if (
            method == "eth_getBlockByNumber"
            and params[0] == "0x11"
            and self.url == faucet_deploy.RPC_URLS[1]
        ):
            return {"number": "0x11", "hash": "0x" + "dd" * 32}
        return super().call(method, params)


class _BoundaryChangesDuringQueryRpc(_Rpc):
    def call(self, method: str, params: list[object]) -> object:
        if method == "eth_getBlockByNumber" and params[0] == "0x11":
            reads = self.state.setdefault("boundary_reads", {})
            assert isinstance(reads, dict)
            read_count = reads.get(self.url, 0)
            assert isinstance(read_count, int)
            reads[self.url] = read_count + 1
            if read_count >= 1 and self.url == faucet_deploy.RPC_URLS[1]:
                return {"number": "0x12", "hash": "0x" + "dd" * 32}
        return super().call(method, params)


def _configure_rpc(plan: dict[str, object]) -> None:
    _Rpc.shared = {
        "plan": plan,
        "rows": [],
        "send_count": 0,
        "mutate_receipt": False,
        "diverge_finalized_hash": False,
    }


def test_preflight_and_execute_are_read_only_until_explicit_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    assert faucet_deploy.run_deployment(plan, tmp_path / "journal.json", execute=False, rpc_factory=_Rpc, root=tmp_path)["status"] == "validated"
    assert not (tmp_path / "journal.json").exists()


def test_two_create_execution_is_durable_and_replay_does_not_resign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    signer = _Signer()
    journal = tmp_path / "journal.json"
    result = faucet_deploy.run_deployment(plan, journal, execute=True, signer_factory=lambda: signer, rpc_factory=_Rpc, root=tmp_path)
    assert result["status"] == "complete"
    assert [item["address"] for item in result["transactions"]] == [
        plan["predicted_addresses"]["token"], plan["predicted_addresses"]["executor"]
    ]
    assert signer.calls == 2
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    saved = json.loads(journal.read_text())
    assert saved["schema_version"] == "monad-faucet-deployment-journal-v1"
    raw = journal.read_text()
    assert "raw_transaction" in raw
    replay = faucet_deploy.run_deployment(
        plan,
        journal,
        execute=False,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("replay must not sign")),
        rpc_factory=_Rpc,
        root=tmp_path,
    )
    assert replay["status"] == "complete"
    assert signer.calls == 2


def test_receipt_contract_mutation_blocks_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    journal = tmp_path / "mutated.json"
    complete = faucet_deploy.run_deployment(
        plan, journal, execute=True, signer_factory=lambda: _Signer(), rpc_factory=_Rpc, root=tmp_path
    )
    assert complete["status"] == "complete"
    _Rpc.shared["mutate_receipt"] = True
    replay = faucet_deploy.run_deployment(plan, journal, execute=False, rpc_factory=_Rpc, root=tmp_path)
    assert replay["status"] == "blocked"
    assert replay["pending"] is False


def test_replay_accepts_different_finalized_heads_when_common_boundary_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    journal = tmp_path / "different-finalized-heads.json"
    complete = faucet_deploy.run_deployment(
        plan, journal, execute=True, signer_factory=lambda: _Signer(), rpc_factory=_DivergentFinalityRpc, root=tmp_path
    )
    assert complete["status"] == "complete"
    assert complete["finalized_boundary"] == 17

    replay = faucet_deploy.run_deployment(plan, journal, execute=False, rpc_factory=_DivergentFinalityRpc, root=tmp_path)

    assert replay["status"] == "complete"
    assert replay["pending"] is False


def test_replay_waits_when_fixed_boundary_blocks_disagree_without_resigning_or_broadcasting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    journal = tmp_path / "boundary-disagreement.json"
    complete = faucet_deploy.run_deployment(
        plan, journal, execute=True, signer_factory=lambda: _Signer(), rpc_factory=_Rpc, root=tmp_path
    )
    assert complete["status"] == "complete"
    send_count = _Rpc.shared["send_count"]

    def fail_signer() -> object:
        raise AssertionError("boundary disagreement must not sign")

    replay = faucet_deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=fail_signer,
        rpc_factory=_BoundaryDisagreementRpc,
        root=tmp_path,
    )

    assert replay["status"] == "pending"
    assert replay["pending"] is True
    assert _Rpc.shared["send_count"] == send_count


def test_replay_waits_when_fixed_boundary_changes_during_recheck_without_resigning_or_broadcasting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)
    journal = tmp_path / "boundary-recheck-change.json"
    complete = faucet_deploy.run_deployment(
        plan, journal, execute=True, signer_factory=lambda: _Signer(), rpc_factory=_Rpc, root=tmp_path
    )
    assert complete["status"] == "complete"
    send_count = _Rpc.shared["send_count"]
    _Rpc.shared["boundary_reads"] = {}

    def fail_signer() -> object:
        raise AssertionError("boundary change must not sign")

    replay = faucet_deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=fail_signer,
        rpc_factory=_BoundaryChangesDuringQueryRpc,
        root=tmp_path,
    )

    assert replay["status"] == "pending"
    assert replay["pending"] is True
    assert _Rpc.shared["send_count"] == send_count


def test_unknown_broadcast_keeps_journal_and_does_not_retry_or_resign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    _configure_rpc(plan)

    class TimeoutRpc(_Rpc):
        def call(self, method: str, params: list[object]) -> object:
            if method == "eth_sendRawTransaction":
                raise RuntimeError("provider timeout")
            return super().call(method, params)

    signer = _Signer()
    journal = tmp_path / "unknown.json"
    first = faucet_deploy.run_deployment(plan, journal, execute=True, signer_factory=lambda: signer, rpc_factory=TimeoutRpc, root=tmp_path)
    assert first["status"] == "pending"
    assert signer.calls == 1
    saved = json.loads(journal.read_text())
    assert saved["transactions"][0]["attempted"] is True
    second = faucet_deploy.run_deployment(
        plan,
        journal,
        execute=True,
        signer_factory=lambda: (_ for _ in ()).throw(AssertionError("unknown must not resign")),
        rpc_factory=TimeoutRpc,
        root=tmp_path,
    )
    assert second["status"] == "pending"


def test_old_schema_and_orphan_journal_are_rejected_before_signer_or_rpc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema_version": "monad-deployment-journal-v1"}))
    old.chmod(0o600)
    with pytest.raises(ValueError, match="journal"):
        faucet_deploy.run_deployment(plan, old, execute=False, rpc_factory=lambda *_: (_ for _ in ()).throw(AssertionError("no RPC")), root=tmp_path)

    orphan = tmp_path / "orphan.json"
    orphan.write_text(json.dumps({"schema_version": "monad-faucet-deployment-journal-v1", "plan_sha256": plan["plan_sha256"], "transactions": []}))
    orphan.chmod(0o600)
    with pytest.raises(ValueError, match="journal"):
        faucet_deploy.run_deployment(plan, orphan, execute=False, rpc_factory=lambda *_: (_ for _ in ()).throw(AssertionError("no RPC")), root=tmp_path)


def test_tampered_creation_data_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(tmp_path, monkeypatch)
    tampered = json.loads(json.dumps(plan))
    tampered["transactions"][0]["data"] += "00"
    with pytest.raises(ValueError, match="plan"):
        faucet_deploy.validate_plan(tampered, root=tmp_path)
