from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from eth_abi import encode
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak
import pytest

from agentonomy_commerce.budget_backend import BudgetBackend
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig
from apps.facilitator.budget_protocol import SpendGrant, sign_grant
from examples.monad_commerce.hosted_feedback import FeedbackStore, canonical_document
from examples.monad_commerce.local_chain import LocalChain, calldata


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8080"


def _artifact(contract: str) -> dict[str, object]:
    path = ROOT / "contracts" / "out" / "ERC8004RegistryFixture.sol" / f"{contract}.json"
    assert path.exists(), f"missing {path}; run forge build in contracts first"
    return json.loads(path.read_text(encoding="utf-8"))


def _deploy_fixture(chain: LocalChain, contract: str, constructor_types: list[str], constructor_args: list[object]) -> str:
    bytecode = str(_artifact(contract)["bytecode"]["object"]).removeprefix("0x")
    deployment = chain.transact(
        chain.owner,
        data="0x" + bytecode + encode(constructor_types, constructor_args).hex(),
        gas=2_000_000,
    )
    return deployment["contractAddress"]


def _runtime_hash(chain: LocalChain, address: str) -> str:
    code = chain.rpc.call("eth_getCode", [address, "latest"])
    return "0x" + keccak(bytes.fromhex(code[2:])).hex()


def _config(
    network: NetworkConfig,
    identity: str,
    reputation: str,
    owner: str,
    agent_id: int | None,
    identity_code_hash: str,
    reputation_code_hash: str,
) -> RegistryConfig:
    return RegistryConfig.from_dict(
        {
            "identity_registry": identity,
            "reputation_registry": reputation,
            "agent_id": agent_id,
            "agent_owner": owner,
            "agent_uri": ORIGIN + "/agent.json",
            "identity_code_hash": identity_code_hash,
            "reputation_code_hash": reputation_code_hash,
            "identity_implementation": None,
            "identity_implementation_code_hash": None,
            "reputation_implementation": None,
            "reputation_implementation_code_hash": None,
        },
        network=network,
        origin=ORIGIN,
    )


def _sign_digest(digest: bytes, key: bytes) -> bytes:
    signature = keys.PrivateKey(bytes(key)).sign_msg_hash(digest)
    return signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27])


def _settled_payment(chain: LocalChain, network: NetworkConfig) -> dict[str, object]:
    chain.approve(1_000_000)
    now = int(time.time())
    grant = SpendGrant(
        "0x" + "aa" * 32,
        chain.owner.address,
        "0x" + "bb" * 32,
        network.token,
        network.payee,
        500_000,
        1_000_000,
        now - 60,
        now + 3_600,
        chain.execution_signer.address,
    )
    binding = SimpleNamespace(
        grant=grant.to_json(),
        owner_signature="0x" + sign_grant(grant, chain.owner.key, network.chain_id, network.executor).hex(),
    )
    backend = BudgetBackend(
        network,
        relayer_address=chain.relayer.address,
        execution_signer_address=chain.execution_signer.address,
        execution_sign=lambda digest: _sign_digest(digest, chain.execution_signer.key),
        transaction_sign=lambda tx: chain.relayer.sign_transaction(tx).raw_transaction,
    )
    row = {
        "purchase_id": "purchase-erc8004-local",
        "quote_hash": "aa" * 32,
        "amount_atomic": "300000",
    }
    attempt = backend.prepare(row, binding, backend.pending_nonce())
    assert backend.verify(row, binding, attempt) is None
    assert backend.broadcast(attempt) == attempt["tx_hash"]
    chain.mine(5)
    evidence = backend.verify(row, binding, attempt)
    assert evidence is not None
    assert evidence["status"] == "verified"
    assert evidence["two_rpc_verified"] is True
    return {
        "owner": chain.owner.address.lower(),
        "payee": network.payee,
        "transaction_hash": evidence["transaction_hash"],
        "state": "settled",
        "settlement_rail": "budget_contract",
    }


def test_erc8004_fixture_accepts_registration_payment_feedback_and_revocation() -> None:
    """Exercise the adapter against a real ABI-compatible registry on local Anvil.

    The Solidity fixture is intentionally test-only; it proves calldata, event and
    read-state compatibility without claiming to be an official ERC-8004 deployment.
    """
    # Keep the service identity owner separate from the budget buyer so the
    # registry's owner/authorized-reviewer guard is exercised by the real buyer.
    service_owner = Account.create()

    with LocalChain() as chain:
        chain._local_call("anvil_setBalance", [service_owner.address, hex(100 * 10**18)])
        token, executor = chain.deploy()
        network = NetworkConfig(
            "local_anvil",
            31337,
            (chain.url, chain.url),
            token,
            executor,
            chain.payee,
        )

        identity_registry = _deploy_fixture(
            chain,
            "ERC8004IdentityRegistryFixture",
            ["address"],
            [chain.payee],
        )
        reputation_registry = _deploy_fixture(
            chain,
            "ERC8004ReputationRegistryFixture",
            ["address"],
            [identity_registry],
        )
        chain.mine(5)
        identity_code_hash = _runtime_hash(chain, identity_registry)
        reputation_code_hash = _runtime_hash(chain, reputation_registry)

        pending = ERC8004Client(
            network,
            _config(
                network,
                identity_registry,
                reputation_registry,
                service_owner.address,
                None,
                identity_code_hash,
                reputation_code_hash,
            ),
        )
        registration = pending.registration_transaction()
        assert registration["from"] == service_owner.address.lower()
        assert registration["to"] == identity_registry.lower()
        assert registration["chainId"] == hex(network.chain_id)
        assert registration["value"] == "0x0"
        assert registration["data"].startswith("0x" + keccak(text="register(string)")[:4].hex())
        assert "signature" not in registration
        registration_receipt = chain.transact(
            service_owner,
            to=registration["to"],
            data=registration["data"],
            gas=int(registration["gas"], 16),
        )
        assert registration_receipt["status"] == "0x1"
        chain.mine(5)

        client = ERC8004Client(
            network,
            _config(
                network,
                identity_registry,
                reputation_registry,
                service_owner.address,
                0,
                identity_code_hash,
                reputation_code_hash,
            ),
        )
        identity = client.verify_identity()
        assert identity == {
            "verified": True,
            "agent_registry": f"eip155:31337:{identity_registry.lower()}",
            "agent_id": 0,
            "agent_owner": service_owner.address.lower(),
            "agent_wallet": chain.payee.lower(),
            "agent_uri": ORIGIN + "/agent.json",
            "chain_id": 31337,
            "block_number": identity["block_number"],
            "block_hash": identity["block_hash"],
        }

        settlement = _settled_payment(chain, network)
        output_hash = hashlib.sha256(b"customer_id,total\n1,42\n").hexdigest()
        purchase = {
            "purchase_id": "purchase-erc8004-local",
            "state": "delivered",
            "offering_id": "csv-reconciliation-v1",
            "output_hash": "0x" + output_hash,
            "settlement": settlement,
        }
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        store = FeedbackStore(db, client, clock=lambda: 1_760_000_000)
        draft = store.prepare(
            tenant_id="tenant-local",
            purchase_id=purchase["purchase_id"],
            buyer=chain.owner.address,
            score=93,
            purchase=purchase,
            identity=identity,
        )
        assert draft["status"] == "prepared"
        assert draft["transaction"]["from"] == chain.owner.address.lower()
        assert draft["transaction"]["to"] == reputation_registry.lower()
        assert draft["transaction"]["value"] == "0x0"

        with pytest.raises(ValueError, match="owner or authorized"):
            client.feedback_transaction(
                buyer=service_owner.address,
                score=93,
                feedback_uri=draft["feedback_uri"],
                feedback_hash=draft["feedback_hash"],
            )
        with pytest.raises(RuntimeError, match="reverted"):
            chain.transact(
                service_owner,
                to=draft["transaction"]["to"],
                data=draft["transaction"]["data"],
                gas=int(draft["transaction"]["gas"], 16),
            )

        # The buyer signs the exact adapter payload in memory and sends it only
        # to the owned loopback Anvil instance.
        feedback_receipt = chain.transact(
            chain.owner,
            to=draft["transaction"]["to"],
            data=draft["transaction"]["data"],
            gas=int(draft["transaction"]["gas"], 16),
        )
        feedback_tx_hash = feedback_receipt["transactionHash"]
        chain.mine(5)
        verified = store.verify(
            tenant_id="tenant-local",
            purchase_id=purchase["purchase_id"],
            buyer=chain.owner.address,
            tx_hash=feedback_tx_hash,
        )
        assert verified["status"] == "verified"
        assert verified["verified"] is True
        assert verified["feedback_index"] == 1
        assert verified["is_revoked"] is False

        public_document = store.public(draft["feedback_hash"])
        assert public_document is not None
        assert "proofOfPayment" in public_document
        assert public_document["proofOfPayment"]["txHash"] == settlement["transaction_hash"]
        assert public_document["delivery"]["outputHash"] == "sha256:" + output_hash
        assert "purchase_id" not in public_document
        assert "customer_id,total" not in canonical_document(public_document)
        assert draft["feedback_hash"] == "0x" + keccak(
            canonical_document(public_document).encode("utf-8")
        ).hex()

        revoke = calldata(
            "revokeFeedback(uint256,uint64)",
            ["uint256", "uint64"],
            [0, verified["feedback_index"]],
        )
        revoke_receipt = chain.transact(
            chain.owner,
            to=reputation_registry,
            data=revoke,
        )
        assert revoke_receipt["status"] == "0x1"
        chain.mine(5)
        revoked = store.verify(
            tenant_id="tenant-local",
            purchase_id=purchase["purchase_id"],
            buyer=chain.owner.address,
            tx_hash=feedback_tx_hash,
        )
        assert revoked["status"] == "verified"
        assert revoked["is_revoked"] is True
        assert store.public(draft["feedback_hash"]) == public_document
