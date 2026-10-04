"""Integration fixture for an external-wallet Core over a private pipe."""

from __future__ import annotations

from pathlib import Path
import json
import os
import socket
import subprocess
import sys

from eth_account import Account
from eth_account.messages import encode_defunct
from apps.facilitator.budget_protocol import sign_grant


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "apps" / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "core"))
if str(ROOT / "apps" / "marketplace") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "marketplace"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentonomy_commerce.budget_network import quantity  # noqa: E402
from examples.monad_commerce.core_bridge import CoreBridge  # noqa: E402
from examples.monad_commerce.local_chain import LocalChain  # noqa: E402
from examples.monad_commerce.public_runtime import PublicCommerceRuntime  # noqa: E402


WORKER = Path(__file__).with_name("external_core_worker.py")


class ExternalCoreBridge(CoreBridge):
    """CoreBridge transport for the fake-KMS ExternalWalletCore worker."""

    def __init__(
        self,
        state_dir: Path,
        deployment: dict[str, object],
        execution_private_key: str,
        gas_private_key: str,
    ) -> None:
        super().__init__(state_dir, deployment)
        self._execution_private_key = execution_private_key
        self._gas_private_key = gas_private_key

    def _start(self) -> None:
        if self._broken is not None:
            raise RuntimeError("external Core channel is unavailable")
        if self.process is not None:
            return
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL"}
        }
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, str(WORKER), str(self.state_dir)],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
        )
        startup = {
            "deployment": self.bootstrap,
            "execution_private_key": self._execution_private_key,
            "gas_private_key": self._gas_private_key,
        }
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def onboarding(self, method: str, **params: object) -> object:
        allowed = {
            "onboarding_status",
            "wallet_challenge",
            "wallet_verify",
            "grant_challenge",
            "grant_verify",
            "budget_payload",
            "budget_bind",
            "allowance_verify",
        }
        if method not in allowed:
            raise ValueError("external onboarding operation is outside the fixture scope")
        return self._call(method, params)


class ExternalCommerceRuntime(PublicCommerceRuntime):
    """Existing Marketplace/HTTP runtime using the external Core bridge."""

    def __init__(
        self,
        state_dir: Path,
        merchant_port: int,
        deployment: dict[str, object],
        execution_private_key: str,
        gas_private_key: str,
        fail_next_delivery: bool = False,
    ) -> None:
        super().__init__(state_dir, merchant_port, deployment)
        self.fail_next_delivery = fail_next_delivery
        self.core = ExternalCoreBridge(
            state_dir,
            deployment,
            execution_private_key,
            gas_private_key,
        )

    def __enter__(self):
        runtime = super().__enter__()
        if self.fail_next_delivery:
            merchant = self.merchant
            original_handle_post = merchant._handle_post

            def injected_failure(handler):
                self.fail_next_delivery = False
                handler._send(503, {"error": "injected_delivery_failure"})

            merchant._handle_post = injected_failure
            self._original_handle_post = original_handle_post
        return runtime


class ExternalWalletHarness:
    """Own local Anvil, external Core onboarding and Marketplace lifecycle."""

    def __init__(self, tmp_path: Path, *, fail_next_delivery: bool = False) -> None:
        self.state_dir = Path(tmp_path) / "external-wallet-state"
        self.chain: LocalChain | None = None
        self.runtime: ExternalCommerceRuntime | None = None
        self._onboarding_bridge: ExternalCoreBridge | None = None
        self.deployment: dict[str, object] | None = None
        self._execution_private_key = ""
        self._gas_private_key = ""
        self.fail_next_delivery = fail_next_delivery

    def __enter__(self) -> "ExternalWalletHarness":
        try:
            self.chain = LocalChain()
            self.chain.__enter__()
            self.chain.deploy()
            approval = self.chain.approve(1_000_000)
            self.chain.mine(5)
            self.deployment = {
                "mode": "local_anvil",
                "chain_id": 31337,
                "rpc_urls": [self.chain.url, self.chain.url],
                "token": self.chain.token,
                "executor": self.chain.executor,
                "payee": self.chain.payee,
                "owner": self.chain.owner.address,
                "execution_signer": self.chain.execution_signer.address,
                "relayer": self.chain.relayer.address,
                "domain": "localhost",
            }
            self._execution_private_key = "0x" + bytes(self.chain.execution_signer.key).hex()
            self._gas_private_key = "0x" + bytes(self.chain.relayer.key).hex()

            self._onboarding_bridge = ExternalCoreBridge(
                self.state_dir,
                self.deployment,
                self._execution_private_key,
                self._gas_private_key,
            )
            self._onboarding_bridge.__enter__()
            self._stage_owner_authorization(approval["transactionHash"])
            self._onboarding_bridge.close()
            self._onboarding_bridge = None
            self._start_runtime()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _stage_owner_authorization(self, approval_hash: str) -> None:
        assert self.chain is not None
        assert self._onboarding_bridge is not None
        bridge = self._onboarding_bridge

        wallet_challenge = bridge.onboarding("wallet_challenge")
        wallet_signature = Account.sign_message(
            encode_defunct(text=wallet_challenge["message_to_sign"]),
            self.chain.owner.key,
        ).signature.hex()
        bridge.onboarding("wallet_verify", signature=wallet_signature)

        grant_challenge = bridge.onboarding("grant_challenge")
        grant_signature = Account.sign_message(
            encode_defunct(text=grant_challenge["message_to_sign"]),
            self.chain.owner.key,
        ).signature.hex()
        bridge.onboarding("grant_verify", signature=grant_signature)

        budget_payload = bridge.onboarding("budget_payload")
        typed_signature = "0x" + sign_grant(
            budget_payload["grant"],
            self.chain.owner.key,
            31337,
            self.chain.executor,
        ).hex()
        bridge.onboarding("budget_bind", signature=typed_signature)
        bridge.onboarding("allowance_verify", transaction_hash=approval_hash)
        assert bridge.onboarding("onboarding_status")["phase"] == "ready"

    def _start_runtime(self) -> None:
        assert self.deployment is not None
        self.runtime = ExternalCommerceRuntime(
            self.state_dir,
            self._free_port(),
            self.deployment,
            self._execution_private_key,
            self._gas_private_key,
            self.fail_next_delivery,
        )
        self.runtime.__enter__()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def restart_runtime(self, *, fail_next_delivery: bool | None = None) -> None:
        if self.runtime is not None:
            self.runtime.__exit__(None, None, None)
            self.runtime = None
        if fail_next_delivery is not None:
            self.fail_next_delivery = fail_next_delivery
        self._start_runtime()

    def relayer_latest_nonce(self) -> int:
        assert self.chain is not None
        return quantity(
            self.chain.rpc.call(
                "eth_getTransactionCount",
                [self.chain.relayer.address, "latest"],
            )
        )

    def assert_private_keys_never_persisted(self) -> None:
        assert self.chain is not None
        key_bytes = (
            bytes(self.chain.owner.key),
            bytes.fromhex(self._execution_private_key[2:]),
            bytes.fromhex(self._gas_private_key[2:]),
        )
        key_text = tuple(
            value.hex().encode("ascii")
            for value in (
                bytes(self.chain.owner.key),
                bytes.fromhex(self._execution_private_key[2:]),
                bytes.fromhex(self._gas_private_key[2:]),
            )
        )
        for path in self.state_dir.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_bytes()
            assert all(raw not in content for raw in key_bytes), path
            assert all(text not in content for text in key_text), path

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self.runtime is not None:
            self.runtime.__exit__(None, None, None)
            self.runtime = None
        if self._onboarding_bridge is not None:
            self._onboarding_bridge.close()
            self._onboarding_bridge = None
        if self.chain is not None:
            self.chain.__exit__(None, None, None)
            self.chain = None
