from __future__ import annotations

from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import stat

import pytest

from agentonomy_commerce import relayer_gate
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.relayer_gate import (
    RelayerGate,
    RelayerGateBusy,
    RelayerGateError,
)


TOKEN = "0x" + "11" * 20
EXECUTOR = "0x" + "22" * 20
PAYEE = "0x" + "33" * 20
RELAYER = "0x" + "44" * 20
OWNER = "0x" + "55" * 20
OTHER_OWNER = "0x" + "66" * 20
TX_HASH = "0x" + "ab" * 32


def _network() -> NetworkConfig:
    return NetworkConfig(
        "monad_testnet",
        10143,
        ("https://testnet-rpc.monad.xyz", "https://rpc-testnet.monadinfra.com"),
        TOKEN,
        EXECUTOR,
        PAYEE,
    )


def _proof(
    *,
    tenant_id: str = "tenant-a",
    purchase_id: str = "purchase-a",
    owner: str = OWNER,
    **changes,
) -> dict[str, object]:
    proof: dict[str, object] = {
        "tenant_id": tenant_id,
        "purchase_id": purchase_id,
        "owner": owner,
        "verified": True,
        "two_rpc_verified": True,
        "chain_id": 10143,
        "receipt_status": 1,
        "transaction_hash": TX_HASH,
        "token": TOKEN,
        "payee": PAYEE,
        "amount_atomic": "300000",
    }
    proof.update(changes)
    return proof


def _abort_proof(
    *,
    tenant_id: str = "tenant-a",
    purchase_id: str = "purchase-a",
    owner: str = OWNER,
    **changes,
) -> dict[str, object]:
    proof: dict[str, object] = {
        "tenant_id": tenant_id,
        "purchase_id": purchase_id,
        "owner": owner,
        "no_broadcast": True,
        "source": "core_funding_ledger",
    }
    proof.update(changes)
    return proof


def _child_hold_gate(state_dir: str, ready, release) -> None:
    gate = RelayerGate(Path(state_dir), _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        ready.send(True)
        release.recv()


def test_enter_persists_active_and_verified_completion_clears_it(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)

    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        journal = json.loads((state_dir / "relayer-gate.json").read_text())
        assert journal["active"] == {
            "tenant_id": "tenant-a",
            "purchase_id": "purchase-a",
            "owner": OWNER.lower(),
        }
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE((state_dir / "relayer-gate.json").stat().st_mode) == 0o600
        result = lease.complete_verified_payment(_proof())
        assert result["transaction_hash"] == TX_HASH

    journal = json.loads((state_dir / "relayer-gate.json").read_text())
    assert journal["active"] is None
    assert journal["last_completed"] == {
        "tenant_id": "tenant-a",
        "purchase_id": "purchase-a",
        "owner": OWNER.lower(),
        "transaction_hash": TX_HASH,
        "amount_atomic": "300000",
    }


def test_unknown_exit_keeps_active_and_same_pair_can_recover_after_restart(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        pass

    restarted = RelayerGate(state_dir, _network(), RELAYER)
    with restarted.enter("tenant-a", "purchase-a", OWNER) as lease:
        lease.complete_verified_payment(_proof())


def test_different_pair_is_rejected_after_restart_until_current_pair_completes(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        pass

    restarted = RelayerGate(state_dir, _network(), RELAYER)
    with pytest.raises(RelayerGateError, match="active relayer pair"):
        with restarted.enter("tenant-b", "purchase-b", OTHER_OWNER):
            pass

    with restarted.enter("tenant-a", "purchase-a", OWNER) as lease:
        lease.complete_verified_payment(_proof())
    with restarted.enter("tenant-b", "purchase-b", OTHER_OWNER) as lease:
        lease.complete_verified_payment(
            _proof(
                tenant_id="tenant-b",
                purchase_id="purchase-b",
                owner=OTHER_OWNER,
                transaction_hash="0x" + "cd" * 32,
            )
        )


def test_one_shared_gate_serializes_multiple_tenants_until_verified_completion(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "shared-relayer"
    first = RelayerGate(state_dir, _network(), RELAYER)
    second = RelayerGate(state_dir, _network(), RELAYER)

    with first.enter("tenant-a", "purchase-a", OWNER):
        pass

    with pytest.raises(RelayerGateError, match="active relayer pair"):
        with second.enter("tenant-b", "purchase-b", OTHER_OWNER):
            pass

    with second.enter("tenant-a", "purchase-a", OWNER) as lease:
        lease.complete_verified_payment(_proof())

    with first.enter("tenant-b", "purchase-b", OTHER_OWNER) as lease:
        lease.complete_verified_payment(
            _proof(
                tenant_id="tenant-b",
                purchase_id="purchase-b",
                owner=OTHER_OWNER,
                transaction_hash="0x" + "cd" * 32,
            )
        )


def test_atomic_write_failure_preserves_previous_active_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        pass

    journal = state_dir / "relayer-gate.json"
    previous = journal.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(relayer_gate.os, "replace", fail_replace)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        with pytest.raises(RelayerGateError, match="journal"):
            lease.complete_verified_payment(_proof())

    assert journal.read_bytes() == previous
    assert json.loads(previous)["active"] == {
        "tenant_id": "tenant-a",
        "purchase_id": "purchase-a",
        "owner": OWNER.lower(),
    }
    assert not list(state_dir.glob(".relayer-gate.json.*"))


def test_process_lock_is_nonblocking_for_a_second_process(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    parent_conn, child_conn = multiprocessing.Pipe()
    release_parent, release_child = multiprocessing.Pipe()
    process = multiprocessing.get_context("spawn").Process(
        target=_child_hold_gate,
        args=(str(state_dir), child_conn, release_child),
    )
    process.start()
    try:
        assert parent_conn.recv() is True
        with pytest.raises(RelayerGateBusy):
            RelayerGate(state_dir, _network(), RELAYER)
    finally:
        release_parent.send(True)
        process.join(timeout=10)
    assert process.exitcode == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verified", False),
        ("two_rpc_verified", False),
        ("chain_id", 1),
        ("receipt_status", 0),
        ("transaction_hash", "0x" + "ab" * 31),
        ("token", "0x" + "77" * 20),
        ("payee", "0x" + "88" * 20),
        ("amount_atomic", "0300000"),
        ("amount_atomic", "500001"),
        ("owner", OTHER_OWNER),
        ("purchase_id", "purchase-other"),
    ],
)
def test_invalid_payment_proof_never_clears_active_state(
    tmp_path: Path, field: str, value: object
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        with pytest.raises(RelayerGateError):
            lease.complete_verified_payment(_proof(**{field: value}))

        journal = json.loads((state_dir / "relayer-gate.json").read_text())
        assert journal["active"] is not None


def test_payment_proof_schema_rejects_missing_or_extra_fields(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        missing = _proof()
        del missing["receipt_status"]
        with pytest.raises(RelayerGateError):
            lease.complete_verified_payment(missing)

        extra = _proof(raw_transaction="0xdeadbeef")
        with pytest.raises(RelayerGateError):
            lease.complete_verified_payment(extra)


def test_identifiers_are_path_safe_and_owner_is_frozen(tmp_path: Path) -> None:
    gate = RelayerGate(tmp_path / "gate", _network(), RELAYER)
    for value in ("../escape", "a/b", "", " ", "tenant\\x"):
        with pytest.raises(RelayerGateError):
            with gate.enter(value, "purchase-a", OWNER):
                pass

    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        with pytest.raises(RelayerGateError):
            lease.complete_verified_payment(_proof(owner=OTHER_OWNER))


def test_network_and_relayer_are_frozen_in_journal(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        pass

    changed_network = replace(_network(), token="0x" + "77" * 20)
    with pytest.raises(RelayerGateError, match="network"):
        RelayerGate(state_dir, changed_network, RELAYER)
    with pytest.raises(RelayerGateError, match="relayer"):
        RelayerGate(state_dir, _network(), "0x" + "99" * 20)


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    RelayerGate(state_dir, _network(), RELAYER)
    journal = state_dir / "relayer-gate.json"
    journal.write_text("{not-json")
    journal.chmod(0o600)
    with pytest.raises(RelayerGateError, match="journal"):
        RelayerGate(state_dir, _network(), RELAYER)


def test_state_and_journal_symlinks_fail_closed(tmp_path: Path) -> None:
    real_state = tmp_path / "real-state"
    RelayerGate(real_state, _network(), RELAYER)
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(real_state, target_is_directory=True)
    with pytest.raises(RelayerGateError, match="symlink"):
        RelayerGate(linked_state, _network(), RELAYER)

    journal = real_state / "relayer-gate.json"
    target = tmp_path / "journal-target"
    target.write_text(journal.read_text())
    journal.unlink()
    journal.symlink_to(target)
    with pytest.raises(RelayerGateError, match="symlink"):
        RelayerGate(real_state, _network(), RELAYER)


def test_lock_and_journal_permissions_and_types_fail_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    RelayerGate(state_dir, _network(), RELAYER)
    lock = state_dir / "relayer-gate.lock"
    journal = state_dir / "relayer-gate.json"

    lock.chmod(0o644)
    with pytest.raises(RelayerGateError, match="lock"):
        RelayerGate(state_dir, _network(), RELAYER)
    lock.chmod(0o600)

    journal.chmod(0o644)
    with pytest.raises(RelayerGateError, match="journal"):
        RelayerGate(state_dir, _network(), RELAYER)


def test_journal_contains_only_auditable_payment_identity(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        lease.complete_verified_payment(_proof())
    text = (state_dir / "relayer-gate.json").read_text()
    assert "raw_transaction" not in text
    assert "private_key" not in text
    assert "credential" not in text


def test_non_monad_network_is_rejected(tmp_path: Path) -> None:
    local = NetworkConfig(
        "local_anvil",
        31337,
        ("http://127.0.0.1:8545", "http://localhost:8546"),
        TOKEN,
        EXECUTOR,
        PAYEE,
    )
    with pytest.raises(RelayerGateError, match="10143"):
        RelayerGate(tmp_path / "gate", local, RELAYER)


def test_reconcile_verified_payment_clears_active_after_crash(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        pass

    result = gate.reconcile_verified_payment(_proof())

    assert result == {
        "status": "reconciled",
        "tenant_id": "tenant-a",
        "purchase_id": "purchase-a",
        "owner": OWNER.lower(),
        "transaction_hash": TX_HASH,
        "amount_atomic": "300000",
    }
    journal = json.loads((state_dir / "relayer-gate.json").read_text())
    assert journal["active"] is None
    assert journal["last_completed"] == {
        "tenant_id": "tenant-a",
        "purchase_id": "purchase-a",
        "owner": OWNER.lower(),
        "transaction_hash": TX_HASH,
        "amount_atomic": "300000",
    }


def test_late_reconcile_result_never_clears_different_active_pair(tmp_path: Path) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        lease.complete_verified_payment(_proof())
    # The recovery entry point takes the shared journal lock itself. Exit the
    # lease context first; an uncompleted lease deliberately leaves this
    # different active pair in the journal.
    with gate.enter("tenant-b", "purchase-b", OTHER_OWNER):
        pass

    result = gate.reconcile_verified_payment(_proof())
    assert result == {"status": "different_active"}
    journal = json.loads((state_dir / "relayer-gate.json").read_text())
    assert journal["active"] == {
        "tenant_id": "tenant-b",
        "purchase_id": "purchase-b",
        "owner": OTHER_OWNER.lower(),
    }


@pytest.mark.parametrize(
    "bad_proof",
    [
        _proof(purchase_id="purchase-other"),
        {key: value for key, value in _proof().items() if key != "receipt_status"},
        _proof(raw_transaction="0xdeadbeef"),
    ],
)
def test_reconcile_rejects_forged_or_non_exact_proof_without_clearing(
    tmp_path: Path, bad_proof: dict[str, object]
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER):
        with pytest.raises(RelayerGateError):
            gate.reconcile_verified_payment(bad_proof)
        journal = json.loads((state_dir / "relayer-gate.json").read_text())
        assert journal["active"] == {
            "tenant_id": "tenant-a",
            "purchase_id": "purchase-a",
            "owner": OWNER.lower(),
        }


def test_abort_unsubmitted_clears_only_active_pair_without_completion(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        result = lease.abort_unsubmitted(_abort_proof())
        assert result == {"status": "aborted"}
        journal = json.loads((state_dir / "relayer-gate.json").read_text())
        assert journal["active"] is None
        assert journal["last_completed"] is None

    with gate.enter("tenant-b", "purchase-b", OTHER_OWNER):
        pass


@pytest.mark.parametrize(
    "bad_proof",
    [
        _abort_proof(owner=OTHER_OWNER),
        {key: value for key, value in _abort_proof().items() if key != "source"},
        _abort_proof(attempt="unknown"),
        _abort_proof(no_broadcast=False),
    ],
)
def test_abort_unsubmitted_rejects_forged_or_unknown_attempt_proof(
    tmp_path: Path, bad_proof: dict[str, object]
) -> None:
    state_dir = tmp_path / "gate"
    gate = RelayerGate(state_dir, _network(), RELAYER)
    with gate.enter("tenant-a", "purchase-a", OWNER) as lease:
        with pytest.raises(RelayerGateError):
            lease.abort_unsubmitted(bad_proof)
        journal = json.loads((state_dir / "relayer-gate.json").read_text())
        assert journal["active"] == {
            "tenant_id": "tenant-a",
            "purchase_id": "purchase-a",
            "owner": OWNER.lower(),
        }
