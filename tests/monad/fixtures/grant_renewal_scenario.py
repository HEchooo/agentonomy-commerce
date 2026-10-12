"""Single local-Anvil acceptance flow for paid orders across grant renewal."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "apps/node",
    ROOT / "apps/core",
    ROOT / "apps/marketplace",
):
    source_text = str(source)
    if source_text in sys.path:
        sys.path.remove(source_text)
    sys.path.insert(0, source_text)
# The Core and Marketplace trees both expose top-level ``services`` and
# ``shared`` packages. Merge their search paths inside this child process so
# the real hosted composition can import both halves without leaking either
# namespace into the parent pytest process.
try:
    import services
except ImportError:
    services = None
if services is not None and hasattr(services, "__path__"):
    service_roots = [
        str(ROOT / "apps/core/services"),
        str(ROOT / "apps/marketplace/services"),
    ]
    services.__path__[:] = service_roots + [
        path for path in services.__path__ if path not in service_roots
    ]
try:
    import shared
except ImportError:
    shared = None
if shared is not None and hasattr(shared, "__path__"):
    shared_roots = [
        str(ROOT / "apps/core/shared"),
        str(ROOT / "apps/marketplace/shared"),
    ]
    shared.__path__[:] = shared_roots + [
        path for path in shared.__path__ if path not in shared_roots
    ]
# ``apps/core/shared/models.py`` is an unrelated, tiny model module. The
# marketplace runtime needs the marketplace model definitions (including
# ClinkServiceManifest), while the Core modules still resolve ``shared.config``
# from the core tree. Load only the conflicting models module explicitly.
sys.modules.pop("shared.models", None)
models_spec = importlib.util.spec_from_file_location(
    "shared.models", ROOT / "apps/marketplace/shared/models.py"
)
assert models_spec is not None and models_spec.loader is not None
models_module = importlib.util.module_from_spec(models_spec)
sys.modules["shared.models"] = models_module
models_spec.loader.exec_module(models_module)

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

# Core production modules use the package-qualified protocol, while a few
# legacy test commands put its directory directly on PYTHONPATH. Keep one
# module object for both names so dataclass identity cannot split between the
# two import forms during a real settlement.
from apps.facilitator import budget_protocol as _budget_protocol
sys.modules["budget_protocol"] = _budget_protocol
from apps.facilitator.budget_protocol import sign_execution, sign_grant
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.hosted_core import HostedWalletCore
from examples.monad_commerce.local_chain import LocalChain, calldata
from examples.monad_commerce.runtime import BudgetCommerceRuntime

CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "1,2026-10-03,Sale,10.00,USD,sales\n"
    "2,2026-10-03,Fee,-2.00,USD,fees\n"
)
TOTAL_ATOMIC = 1_000_000
PAYMENT_ATOMIC = 300_000


class _LocalSigner:
    """Ephemeral fixture signer; all payment effects still go through Anvil."""

    def __init__(self, chain: LocalChain):
        self.chain = chain

    def sign_execution(self, grant, _owner_signature, execution):
        return sign_execution(
            execution,
            self.chain.execution_signer.key,
            31337,
            self.chain.executor,
        )

    def sign_transaction(self, transaction):
        return self.chain.relayer.sign_transaction(transaction).raw_transaction


class _DirectHostedBridge:
    """Process boundary adapter used only to exercise real local Anvil state."""

    def __init__(self, core: HostedWalletCore):
        self.core = core

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.core.close()

    def __getattr__(self, name):
        return getattr(self.core, name)

    def onboarding(self, operation, **params):
        if operation == "onboarding_status":
            return self.core.onboarding_status()
        if operation in {"browser_challenge", "browser_verify", "browser_authorize", "browser_logout"}:
            return getattr(self.core, operation)(**params)
        if operation in {"grant_challenge", "grant_renew_challenge"}:
            return getattr(self.core, operation)(**params)
        if operation in {"grant_verify", "grant_renew_verify", "budget_bind"}:
            return getattr(self.core, operation)(params["signature"])
        if operation == "budget_payload":
            return self.core.budget_payload()
        if operation == "allowance_verify":
            return self.core.allowance_verify(params["transaction_hash"])
        raise ValueError(f"unsupported onboarding operation: {operation}")


def _bootstrap(chain: LocalChain) -> dict:
    return {
        "mode": "local_anvil",
        "chain_id": 31337,
        "rpc_urls": [chain.url, chain.url],
        "token": chain.token,
        "executor": chain.executor,
        "payee": chain.payee,
        "owner": chain.owner.address,
        "execution_signer": chain.execution_signer.address,
        "relayer": chain.relayer.address,
    }


def _sign_personal(message: str, owner: Account) -> str:
    return Account.sign_message(encode_defunct(text=message), owner.key).signature.hex()


def _browser_login(core: HostedWalletCore, label: str, owner: Account) -> tuple[str, str]:
    browser = hashlib.sha256(f"browser-{label}".encode()).hexdigest()
    csrf = hashlib.sha256(f"csrf-{label}".encode()).hexdigest()
    challenge = core.browser_challenge(browser, csrf)
    core.browser_verify(
        browser,
        csrf,
        challenge["session_id"],
        _sign_personal(challenge["message_to_sign"], owner),
    )
    return browser, csrf


def _onboard(core: HostedWalletCore, chain: LocalChain, label: str) -> tuple[str, str]:
    browser, csrf = _browser_login(core, label, chain.owner)
    challenge = core.grant_challenge(browser, csrf)
    core.grant_verify(_sign_personal(challenge["message_to_sign"], chain.owner))
    payload = core.budget_payload()
    core.budget_bind("0x" + sign_grant(payload["grant"], chain.owner.key, 31337, chain.executor).hex())
    return browser, csrf


def _token_balance(chain: LocalChain, owner: str) -> int:
    data = "0x" + (
        keccak(text="balanceOf(address)")[:4]
        + bytes.fromhex(owner[2:]).rjust(32, b"\0")
    ).hex()
    return int(chain.rpc.call("eth_call", [{"to": chain.token, "data": data}, "latest"]), 16)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _runtime(state_dir: Path, bootstrap: dict, core: HostedWalletCore) -> BudgetCommerceRuntime:
    runtime = BudgetCommerceRuntime(state_dir, _free_port(), bootstrap)
    runtime.core = _DirectHostedBridge(core)
    runtime.__enter__()
    return runtime


def _canary(state_dir: Path, core: HostedWalletCore) -> HostedCanary:
    operator = state_dir / "operator"
    operator.mkdir(mode=0o700, exist_ok=True)
    canary = HostedCanary.__new__(HostedCanary)
    canary.state_dir = state_dir
    canary.core = _DirectHostedBridge(core)
    canary.market = None
    canary.network = core.network
    canary.owner = core.owner_address
    canary.operations_path = operator / "wallet-operations.json"
    if canary.operations_path.exists():
        canary.wallet_operations = json.loads(canary.operations_path.read_text())
    else:
        canary.wallet_operations = {}
    return canary


def _delivered(runtime: BudgetCommerceRuntime, preview_id: str, chain: LocalChain) -> dict:
    result = runtime.execute(preview_id)
    # Core reconciliation is intentionally retryable. Mine bounded batches
    # until the local watcher observes the already-submitted transaction; no
    # retry can submit a second payment for the same preview.
    for _ in range(4):
        if result["state"] != "payment_submitted":
            break
        chain.mine(5)
        result = runtime.execute(preview_id)
    assert result["state"] == "delivered", result
    assert result["settlement"]["verified"] is True
    return result


def _recover_purchase(runtime: BudgetCommerceRuntime, purchase_id: str) -> dict:
    """Exercise the Monad purchase service's idempotent recovery path."""

    purchase = runtime.repository.get_purchase(purchase_id)
    assert purchase is not None
    return runtime._execution_payload(runtime.purchases.execute(purchase.preview_id))


def run_scenario(state_dir: Path) -> None:
    """Run the complete paid-order renewal scenario in a child process."""

    tmp_path = state_dir
    with LocalChain() as chain:
        chain.deploy()
        # Leave exactly 1.00 TestUSD in the buyer wallet. approve(1.00) is
        # still valid after the first 0.30 payment leaves only 0.70.
        chain.transact(
            chain.owner,
            to=chain.token,
            data=calldata(
                "transfer(address,uint256)",
                ["address", "uint256"],
                [chain.payee, 19_000_000],
            ),
        )
        initial_balance = _token_balance(chain, chain.owner.address)
        assert initial_balance == TOTAL_ATOMIC
        initial_approval = chain.approve(TOTAL_ATOMIC)["transactionHash"]
        chain.mine(3)

        bootstrap = _bootstrap(chain)
        signer = _LocalSigner(chain)
        core = HostedWalletCore(
            tmp_path,
            bootstrap,
            signer,
            allow_local=True,
            opc_origin="http://127.0.0.1:8091",
            allow_loopback_http=True,
        )
        runtime = None
        restarted = None
        try:
            browser, csrf = _onboard(core, chain, "real-renewal")
            canary = _canary(tmp_path, core)
            canary.verify_approval(initial_approval)
            assert core._phase() == "ready"

            runtime = _runtime(tmp_path, bootstrap, core)
            first_preview = runtime.preview("csv-reconciliation-v1", CSV, "real-old-order")
            first = _delivered(runtime, first_preview["preview_id"], chain)
            first_purchase_id = first["purchase_id"]
            first_tx = first["settlement"]["transaction_hash"]
            assert _token_balance(chain, chain.owner.address) == 700_000

            old_grant_id = core.grant.spending_grant_id
            old_chain_grant_id = core.budget_binding.grant_id
            old_revoke = canary.revoke_prepare()
            assert old_revoke["spending_grant_id"] == old_grant_id
            assert old_revoke["chain_grant_id"] == old_chain_grant_id
            old_revoke_receipt = chain.transact(
                chain.owner,
                to=chain.executor,
                data=calldata(
                    "revoke(bytes32)",
                    ["bytes32"],
                    [bytes.fromhex(old_chain_grant_id[2:])],
                ),
            )
            old_revoke_tx = old_revoke_receipt["transactionHash"]
            chain.mine(3)
            verified_old_revoke = canary.verify_revocation(old_revoke_tx)
            assert verified_old_revoke["verified"] is True
            assert verified_old_revoke["transaction_hash"] == old_revoke_tx.lower()
            assert core._phase() == "revoked"
            renewal_challenge = canary.onboarding(
                "grant_renew_challenge", browser_digest=browser, csrf_digest=csrf
            )
            renewed = canary.onboarding(
                "grant_renew_verify",
                signature=_sign_personal(renewal_challenge["message_to_sign"], chain.owner),
            )
            new_grant_id = renewed["spending_grant_id"]
            assert new_grant_id != old_grant_id

            new_payload = canary.onboarding("budget_payload")
            new_budget_signature = "0x" + sign_grant(
                new_payload["grant"], chain.owner.key, 31337, chain.executor
            ).hex()
            bound = canary.onboarding("budget_bind", signature=new_budget_signature)
            new_chain_grant_id = bound["grant_id"]
            new_grant_hash = bound["grant_hash"]
            assert new_chain_grant_id != old_chain_grant_id
            assert canary.wallet_operations["approval_history"][old_grant_id]["transaction_hash"] == initial_approval.lower()
            assert canary.wallet_operations["revocation_history"][old_grant_id]["transaction_hash"] == old_revoke_tx.lower()

            # The old journal hash is archived and cannot block a fresh
            # allowance. The buyer has only 0.70 TestUSD, but approve does
            # not spend TestUSD and can set the finite allowance back to 1.00.
            new_approval_request = canary.approval_transaction()
            assert "transaction_hash" not in new_approval_request
            new_approval = chain.approve(TOTAL_ATOMIC)["transactionHash"]
            chain.mine(3)
            canary.verify_approval(new_approval)
            assert _token_balance(chain, chain.owner.address) == 700_000

            old_replay = runtime.execute(first_preview["preview_id"])
            old_query = runtime.purchase(first_purchase_id)
            old_recovery = _recover_purchase(runtime, first_purchase_id)
            assert old_replay["purchase_id"] == first_purchase_id
            assert old_replay["settlement"]["transaction_hash"] == first_tx
            assert old_query["purchase_id"] == first_purchase_id
            assert old_query["settlement"]["transaction_hash"] == first_tx
            assert old_recovery["purchase_id"] == first_purchase_id
            assert old_recovery["settlement"]["transaction_hash"] == first_tx
            assert _token_balance(chain, chain.owner.address) == 700_000

            second_preview = runtime.preview("csv-reconciliation-v1", CSV, "real-new-order")
            second = _delivered(runtime, second_preview["preview_id"], chain)
            second_tx = second["settlement"]["transaction_hash"]
            assert second["purchase_id"] != first_purchase_id
            assert second["settlement"]["grant_hash"] == new_grant_hash
            assert second["settlement"]["grant_hash"] != first["settlement"]["grant_hash"]
            assert second_tx != first_tx
            assert _token_balance(chain, chain.owner.address) == 400_000

            revoke = canary.revoke_prepare()
            assert revoke["chain_grant_id"] == new_chain_grant_id
            revoke_receipt = chain.transact(
                chain.owner,
                to=chain.executor,
                data=calldata(
                    "revoke(bytes32)",
                    ["bytes32"],
                    [bytes.fromhex(new_chain_grant_id[2:])],
                ),
            )
            revoke_tx = revoke_receipt["transactionHash"]
            chain.mine(3)
            verified_revoke = canary.verify_revocation(revoke_tx)
            assert verified_revoke["verified"] is True
            assert verified_revoke["transaction_hash"] == revoke_tx.lower()
            assert canary.wallet_operations["revocation"]["spending_grant_id"] == new_grant_id
            assert canary.wallet_operations["revocation"]["chain_grant_id"] == new_chain_grant_id
            assert _token_balance(chain, chain.owner.address) == 400_000

            # A quote can remain in the marketplace store after authorization
            # is revoked, but execution must fail closed at the Core gate and
            # must not create another on-chain payment.
            revoked_preview = runtime.preview(
                "csv-reconciliation-v1", CSV, "revoked-grant-order"
            )
            revoked_attempt = runtime.execute(revoked_preview["preview_id"])
            assert revoked_attempt["state"] == "confirmation_required", revoked_attempt
            assert _token_balance(chain, chain.owner.address) == 400_000
        finally:
            if runtime is not None:
                runtime.__exit__(None, None, None)
            elif not getattr(core, "_closed", False):
                core.close()

        restored_core = HostedWalletCore(
            tmp_path,
            bootstrap,
            _LocalSigner(chain),
            allow_local=True,
            opc_origin="http://127.0.0.1:8091",
            allow_loopback_http=True,
        )
        try:
            restarted = _runtime(tmp_path, bootstrap, restored_core)
            replay_after_restart = restarted.execute(first_preview["preview_id"])
            recovery_after_restart = _recover_purchase(restarted, first_purchase_id)
            queried_new = restarted.purchase(second["purchase_id"])
            assert replay_after_restart["purchase_id"] == first_purchase_id
            assert replay_after_restart["settlement"]["transaction_hash"] == first_tx
            assert recovery_after_restart["settlement"]["transaction_hash"] == first_tx
            assert queried_new["settlement"]["transaction_hash"] == second_tx
            assert restored_core.onboarding_status()["grant_status"] == "revoked"
            assert _token_balance(chain, chain.owner.address) == 400_000
            journal = json.loads((tmp_path / "operator" / "wallet-operations.json").read_text())
            assert journal["approval_history"][old_grant_id]["transaction_hash"] == initial_approval.lower()
            assert journal["approval"]["spending_grant_id"] == new_grant_id
            assert journal["revocation_history"][old_grant_id]["transaction_hash"] == old_revoke_tx.lower()
            assert journal["revocation"]["transaction_hash"] == revoke_tx.lower()
            assert journal["revocation"]["transaction_hash"] != old_revoke_tx.lower()
        finally:
            if restarted is not None:
                restarted.__exit__(None, None, None)
            elif not getattr(restored_core, "_closed", False):
                restored_core.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("state directory argument required")
    run_scenario(Path(sys.argv[1]))
    print("GRANT_RENEWAL_SCENARIO_OK")
