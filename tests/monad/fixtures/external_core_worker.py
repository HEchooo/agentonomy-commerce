"""Test-only Core worker with an in-memory fake KMS boundary.

The worker receives ephemeral execution and relayer private keys over its
startup pipe, converts them to DER-backed fake KMS clients, and never writes
the key material to the Core state directory.  Marketplace code does not
import this module; the parent fixture talks to it through CoreBridge pipes.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from eth_keys import keys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "apps" / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "core"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentonomy_commerce.budget_network import NetworkConfig, RpcClient, quantity  # noqa: E402
from agentonomy_commerce.kms_adapter import KmsDigestSigner  # noqa: E402
from agentonomy_commerce.kms_scope import ScopedSigner, SigningScope  # noqa: E402
from examples.monad_commerce.core_worker import METHODS, _dispatch  # noqa: E402
from examples.monad_commerce.public_core import ExternalWalletCore  # noqa: E402


EXECUTION_KEY_ARN = (
    "arn:aws:kms:us-east-1:123456789012:key/"
    "11111111-2222-3333-4444-555555555555"
)
GAS_KEY_ARN = (
    "arn:aws:kms:us-east-1:123456789012:key/"
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)


class _FakeKms:
    """A KMS-shaped client backed by one ephemeral secp256k1 key."""

    def __init__(self, private_key: bytes) -> None:
        self._private_key = private_key
        private = ec.derive_private_key(int.from_bytes(private_key, "big"), ec.SECP256K1())
        self._public_der = private.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )

    def get_public_key(self, **_kwargs: object) -> dict[str, object]:
        return {
            "KeySpec": "ECC_SECG_P256K1",
            "KeyUsage": "SIGN_VERIFY",
            "PublicKey": self._public_der,
        }

    def sign(self, **kwargs: object) -> dict[str, bytes]:
        digest = kwargs.get("Message")
        if not isinstance(digest, bytes) or len(digest) != 32:
            raise ValueError("fake KMS digest must be 32 bytes")
        signature = keys.PrivateKey(self._private_key).sign_msg_hash(digest)
        return {"Signature": encode_dss_signature(signature.r, signature.s)}


def _private_key(value: object) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("external signer key framing is invalid")
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError:
        raise ValueError("external signer key framing is invalid") from None
    if len(raw) != 32 or int.from_bytes(raw, "big") == 0:
        raise ValueError("external signer key framing is invalid")
    return raw


def _scope_and_signer(startup: dict[str, object]) -> ScopedSigner:
    deployment = startup.get("deployment")
    if not isinstance(deployment, dict):
        raise ValueError("external deployment framing is invalid")
    network_fields = {"mode", "chain_id", "rpc_urls", "token", "executor", "payee", "token_decimals", "gas_limit", "max_gas_price_wei"}
    network = NetworkConfig(**{key: deployment[key] for key in network_fields if key in deployment})
    if network.mode != "local_anvil" or network.chain_id != 31337:
        raise ValueError("external fixture requires local Anvil")
    pending = quantity(
        RpcClient(network.rpc_urls[0]).call(
            "eth_getTransactionCount",
            [deployment["relayer"], "pending"],
        )
    )
    if pending > 2**64 - 9:
        raise ValueError("external signer nonce range is exhausted")
    scope = SigningScope(
        network=network,
        owner=deployment["owner"],
        execution_address=deployment["execution_signer"],
        relayer_address=deployment["relayer"],
        nonce_min=pending,
        nonce_max=pending + 8,
    )
    execution_key = _private_key(startup.get("execution_private_key"))
    gas_key = _private_key(startup.get("gas_private_key"))
    execution = KmsDigestSigner(
        _FakeKms(execution_key),
        EXECUTION_KEY_ARN,
        scope.execution_address,
    )
    gas = KmsDigestSigner(
        _FakeKms(gas_key),
        GAS_KEY_ARN,
        scope.relayer_address,
    )
    execution.validate()
    gas.validate()
    return ScopedSigner(scope, execution, gas)


def _dispatch_external(runtime: ExternalWalletCore, method: str, params: object) -> object:
    if not isinstance(params, dict):
        raise ValueError("Core request parameters are invalid")
    no_params = {"onboarding_status", "wallet_challenge", "grant_challenge", "budget_payload"}
    signatures = {"wallet_verify", "grant_verify", "budget_bind"}
    if method in no_params and params == {}:
        return getattr(runtime, method)()
    if method in signatures and set(params) == {"signature"}:
        return getattr(runtime, method)(params["signature"])
    if method == "allowance_verify" and set(params) == {"transaction_hash"}:
        return runtime.allowance_verify(params["transaction_hash"])
    if method in METHODS:
        return _dispatch(runtime, method, params)
    raise ValueError("Core operation is outside the fixture scope")


def _error_response(request_id: object) -> dict[str, object]:
    return {
        "id": request_id,
        "ok": False,
        "error": {"message": "external Core operation failed; inspect the existing state"},
    }


def main() -> int:
    runtime: ExternalWalletCore | None = None
    try:
        startup_line = sys.stdin.buffer.readline(1_048_577)
        if not startup_line or len(startup_line) > 1_048_576:
            raise ValueError("external Core startup framing is invalid")
        startup = json.loads(startup_line)
        if not isinstance(startup, dict):
            raise ValueError("external Core startup framing is invalid")
        signer = _scope_and_signer(startup)
        deployment = startup.get("deployment")
        if not isinstance(deployment, dict):
            raise ValueError("external deployment framing is invalid")
        runtime = ExternalWalletCore(
            Path(sys.argv[1]),
            deployment,
            signer,
            allow_local=True,
        )
    except Exception:
        print(json.dumps(_error_response(None), separators=(",", ":")), flush=True)
        return 2

    try:
        for line in sys.stdin.buffer:
            if len(line) > 1_048_576:
                return 2
            try:
                request = json.loads(line)
                if (
                    not isinstance(request, dict)
                    or set(request) != {"id", "method", "params"}
                    or type(request["id"]) is not int
                ):
                    return 2
                result = _dispatch_external(runtime, request["method"], request["params"])
                response = {"id": request["id"], "ok": True, "result": result}
            except Exception:
                response = _error_response(request.get("id") if isinstance(request, dict) else None)
            print(json.dumps(response, default=str, separators=(",", ":")), flush=True)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
