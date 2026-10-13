"""Subprocess worker for service-price configuration tests.

The Monad test command intentionally exposes Core and Node packages through a
separate ``PYTHONPATH``.  Catalog scenarios additionally expose the
Marketplace package because the commerce runtime imports its merchant
middleware; status and Core scenarios stay on the Monad path exactly as
production subprocesses do.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
NETWORK = "eip155:10143"
TOKEN = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20


def _prepare_catalog_imports() -> None:
    marketplace = str(ROOT / "apps" / "marketplace")
    if marketplace not in sys.path:
        sys.path.insert(0, marketplace)


class _CatalogRepository:
    def __init__(self):
        self.providers = []
        self.offerings = []
        self.historical_preview = {
            "preview_id": "preview_legacy",
            "payment": {
                "price_usd": "0.30",
                "amount_atomic": "300000",
            },
        }

    def upsert_provider(self, provider):
        self.providers.append(provider)

    def upsert_offering(self, offering, **_kwargs):
        self.offerings.append(offering)


def _runtime_for_catalog(runtime_class, bootstrap):
    runtime = runtime_class.__new__(runtime_class)
    runtime.repository = _CatalogRepository()
    runtime._network = NETWORK
    runtime._asset = TOKEN
    runtime._pay_to = PAY_TO
    runtime._configure_service_price(bootstrap)
    return runtime


def _catalog(amount_atomic: int, runtime_class_name: str = "BudgetCommerceRuntime"):
    _prepare_catalog_imports()
    if runtime_class_name == "HostedCommerceRuntime":
        from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime

        runtime_class = HostedCommerceRuntime
    else:
        from examples.monad_commerce.runtime import BudgetCommerceRuntime

        runtime_class = BudgetCommerceRuntime
    runtime = _runtime_for_catalog(
        runtime_class,
        {"signer_configuration": {"scope": {"service_amount_atomic": amount_atomic}}},
    )
    _provider, (offering,) = runtime._register_catalog()
    payment = offering.payment_options[0]
    return {
        "amount_atomic": payment.amount_atomic,
        "price_usd": str(payment.price_usd),
    }


def _catalog_legacy():
    _prepare_catalog_imports()
    from examples.monad_commerce.runtime import BudgetCommerceRuntime

    runtime = _runtime_for_catalog(BudgetCommerceRuntime, {})
    _provider, (offering,) = runtime._register_catalog()
    payment = offering.payment_options[0]
    return {
        "amount_atomic": payment.amount_atomic,
        "price_usd": str(payment.price_usd),
    }


def _catalog_invalid(raw_amount_atomic: str):
    _prepare_catalog_imports()
    from examples.monad_commerce.runtime import BudgetCommerceRuntime

    runtime = BudgetCommerceRuntime.__new__(BudgetCommerceRuntime)
    try:
        runtime._configure_service_price(
            {"signer_configuration": {"scope": {"service_amount_atomic": raw_amount_atomic}}}
        )
    except ValueError:
        return {"status": "rejected"}
    raise AssertionError("invalid service price was accepted")


def _constructors(amount_atomic: int):
    _prepare_catalog_imports()
    from examples.monad_commerce import hosted_market_runtime as hosted_runtime_module
    from examples.monad_commerce import public_runtime as public_runtime_module
    from examples.monad_commerce import runtime as runtime_module
    from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime
    from examples.monad_commerce.public_runtime import PublicCommerceRuntime
    from examples.monad_commerce.runtime import BudgetCommerceRuntime

    def fake_review_init(instance, state_dir, merchant_port):
        instance.state_dir = state_dir
        instance.merchant_port = merchant_port

    runtime_module.ReviewRuntime.__init__ = fake_review_init
    runtime_module.CoreBridge = lambda *_args: object()
    public_runtime_module.PublicCoreBridge = lambda *_args: object()
    hosted_runtime_module.HostedCoreBridge = lambda *_args: object()
    bootstrap = {"signer_configuration": {"scope": {"service_amount_atomic": amount_atomic}}}
    result = {}
    for runtime_class in (BudgetCommerceRuntime, PublicCommerceRuntime, HostedCommerceRuntime):
        runtime = runtime_class(ROOT / "tmp-service-price", 0, bootstrap)
        result[runtime_class.__name__] = dict(runtime._service_payment_terms)
    return {"prices": result}


def _hosted_historical():
    _prepare_catalog_imports()
    from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime

    runtime = _runtime_for_catalog(
        HostedCommerceRuntime,
        {"signer_configuration": {"scope": {"service_amount_atomic": 1000}}},
    )
    before = dict(runtime.repository.historical_preview["payment"])
    _provider, (offering,) = runtime._register_catalog()
    payment = offering.payment_options[0]
    assert runtime.repository.historical_preview["payment"] == before
    return {
        "current": {
            "amount_atomic": payment.amount_atomic,
            "price_usd": str(payment.price_usd),
        },
        "historical": before,
    }


def _canary_status(state_dir: Path, amount_atomic: int):
    from examples.monad_commerce import public_canary as public_canary_module
    from examples.monad_commerce.public_canary import PublicCanary

    network = SimpleNamespace(
        chain_id=10143,
        token=TOKEN,
        executor="0x" + "33" * 20,
        payee=PAY_TO,
        mode="monad_testnet",
    )
    scope = SimpleNamespace(network=network, owner="0x" + "44" * 20)
    if amount_atomic != 300000:
        scope.service_amount_atomic = amount_atomic
    public_canary_module.validate_bootstrap = lambda _bootstrap: scope

    canary = PublicCanary(
        state_dir,
        {"deployment": {}, "signer_configuration": {}},
    )
    canary.core = SimpleNamespace(onboarding=lambda _operation: {"phase": "ready"})
    return {"price": canary.status()["terms"]["price"]}


def _external_snapshot():
    from examples.monad_commerce.public_core import ExternalWalletCore

    ExternalWalletCore._require_onboarded = lambda _self: None
    runtime = ExternalWalletCore.__new__(ExternalWalletCore)
    runtime.network = SimpleNamespace(
        mode="monad_testnet",
        network=NETWORK,
        token=TOKEN,
        payee=PAY_TO,
        executor="0x" + "33" * 20,
    )
    runtime.owner_address = "0x" + "44" * 20
    runtime.execution_signer_address = "0x" + "55" * 20
    runtime.relayer_address = "0x" + "66" * 20
    runtime.config = SimpleNamespace(clink_receipt_signing_key="test-secret")
    runtime.grant = SimpleNamespace(spending_grant_id="grant-1")
    runtime.budget_binding = SimpleNamespace(grant_id="chain-grant", grant_hash="hash")
    runtime.backend = SimpleNamespace(pending_nonce=lambda: 0)
    runtime.repository = SimpleNamespace(
        spending_grant=lambda _grant_id: SimpleNamespace(
            spending_grant_id="grant-1",
            max_amount_usdc=Decimal("0.901"),
            used_amount_usdc=Decimal("0.802"),
            reserved_amount_usdc=Decimal("0"),
            status="active",
        )
    )
    snapshot = runtime.snapshot()
    return {
        key: snapshot[key]
        for key in ("budget_usdc", "used_amount_usdc", "reserved_amount_usdc")
    }


def _budget_snapshot():
    _prepare_catalog_imports()
    from examples.monad_commerce.runtime import BudgetCommerceRuntime

    runtime = BudgetCommerceRuntime.__new__(BudgetCommerceRuntime)
    runtime._entered = True
    runtime.provider = SimpleNamespace(provider_id="commerce_analytics")
    runtime.quotes = object()
    runtime.purchases = object()
    runtime.core = SimpleNamespace(
        snapshot=lambda: {
            "mode": "local_anvil",
            "simulation": True,
            "real_funds": False,
            "token_symbol": "TestUSD",
            "budget_usdc": "0.901",
            "used_amount_usdc": "0.802",
            "reserved_amount_usdc": "0.000",
            "grant_status": "active",
            "chain_grant_id": "grant",
            "grant_hash": "hash",
            "network": "eip155:31337",
            "token": TOKEN,
            "pay_to": PAY_TO,
            "executor": "0x" + "33" * 20,
            "wallet_address": "0x" + "44" * 20,
            "settlement_submissions": 0,
        }
    )
    runtime.merchant = SimpleNamespace(delivery_count=0)
    snapshot = runtime.snapshot()
    return {
        key: snapshot[key]
        for key in (
            "budget_usdc",
            "used_amount_usdc",
            "reserved_amount_usdc",
            "remaining_amount_usdc",
        )
    }


def main() -> int:
    mode = sys.argv[1]
    args = sys.argv[2:]
    state_dir = Path(args[-1])
    mode_args = args[:-1]
    if mode == "catalog":
        result = _catalog(int(mode_args[0]))
    elif mode == "catalog_legacy":
        result = _catalog_legacy()
    elif mode == "catalog_invalid":
        result = _catalog_invalid(mode_args[0])
    elif mode == "constructors":
        result = _constructors(int(mode_args[0]))
    elif mode == "hosted_historical":
        result = _hosted_historical()
    elif mode == "canary_status":
        result = _canary_status(state_dir, int(mode_args[0]))
    elif mode == "external_snapshot":
        result = _external_snapshot()
    elif mode == "budget_snapshot":
        result = _budget_snapshot()
    else:
        raise ValueError(f"unknown worker mode: {mode}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
