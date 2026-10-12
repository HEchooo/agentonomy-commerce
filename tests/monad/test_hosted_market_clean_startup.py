"""Clean subprocess startup checks for the hosted Marketplace worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]

# Public validation-only values. No child process opens these paths or signs;
# the fixture exercises import and scope validation without network access.
PUBLIC_BOOTSTRAP = {
    "deployment": {
        "mode": "monad_testnet",
        "chain_id": 10143,
        "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
        "token": "0x" + "11" * 20,
        "executor": "0x" + "22" * 20,
        "payee": "0x" + "33" * 20,
        "owner": "0x" + "aa" * 20,
        "execution_signer": "0x" + "44" * 20,
        "relayer": "0x" + "55" * 20,
    },
    "signer_configuration": {
        "profile": "hackathon",
        "region": "ap-southeast-1",
        "account_id": "123456789012",
        "expected_role_arn": "arn:aws:iam::123456789012:role/test-runtime",
        "credential_directory": "/tmp/agentonomy-commerce-clean-startup",
        "execution_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "11111111-1111-4111-8111-111111111111"
        ),
        "gas_key_arn": (
            "arn:aws:kms:ap-southeast-1:123456789012:key/"
            "22222222-2222-4222-8222-222222222222"
        ),
        "scope": {
            "network": {
                "mode": "monad_testnet",
                "chain_id": 10143,
                "rpc_urls": ["https://rpc.example.org", "https://rpc.example.net"],
                "token": "0x" + "11" * 20,
                "executor": "0x" + "22" * 20,
                "payee": "0x" + "33" * 20,
            },
            "owner": "0x" + "aa" * 20,
            "execution_address": "0x" + "44" * 20,
            "relayer_address": "0x" + "55" * 20,
            "nonce_min": 3,
            "nonce_max": 5,
        },
    },
    "public_origin": "https://review.agentonomy.xyz",
}


def _clean_worker_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }


def _run(args: list[str], state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=_clean_worker_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_market_worker_empty_stdin_exits_cleanly_in_a_real_cli() -> None:
    state_dir = Path("/private/tmp/commerce-market-clean-startup")
    result = _run(
        [
            sys.executable,
            "-m",
            "examples.monad_commerce.hosted_process",
            str(state_dir),
            "market",
        ],
        state_dir,
    )
    assert result.returncode == 0, (
        "market worker failed before receiving a request:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout == ""


def test_market_worker_uses_marketplace_namespace_and_core_keeps_core_namespace(
    tmp_path: Path,
) -> None:
    namespace_check = r'''
import json
from pathlib import Path
import sys

from examples.monad_commerce import hosted_process

hosted_process._market_main(Path(sys.argv[1]))
bootstrap = json.loads(sys.argv[2])
assert hosted_process.validate_hosted_bootstrap(bootstrap).network.chain_id == 10143
import services.marketplace_repository as marketplace_repository
import shared.models as marketplace_models

root = Path.cwd()
assert str(root / "apps" / "marketplace") in str(Path(marketplace_repository.__file__).resolve())
assert str(root / "apps" / "marketplace") in str(Path(marketplace_models.__file__).resolve())
print(json.dumps({
    "marketplace_repository": str(Path(marketplace_repository.__file__).resolve()),
    "marketplace_models": str(Path(marketplace_models.__file__).resolve()),
}))
'''
    market = _run(
        [
            sys.executable,
            "-c",
            namespace_check,
            str(tmp_path / "market"),
            json.dumps(PUBLIC_BOOTSTRAP, separators=(",", ":")),
        ],
        tmp_path / "market",
    )
    assert market.returncode == 0, market.stderr
    paths = json.loads(market.stdout)
    assert "/apps/marketplace/" in paths["marketplace_repository"]
    assert "/apps/marketplace/" in paths["marketplace_models"]

    core_check = r'''
import json
from pathlib import Path

from examples.monad_commerce import hosted_process
import services.account_service.repository as account_repository
import services.account_service.schemas as account_schemas
import shared.opc_protocol as core_opc_protocol

root = Path.cwd()
assert str(root / "apps" / "core") in str(Path(account_repository.__file__).resolve())
assert str(root / "apps" / "core") in str(Path(account_schemas.__file__).resolve())
assert str(root / "apps" / "core") in str(Path(core_opc_protocol.__file__).resolve())
print(json.dumps({
    "account_repository": str(Path(account_repository.__file__).resolve()),
    "account_schemas": str(Path(account_schemas.__file__).resolve()),
    "opc_protocol": str(Path(core_opc_protocol.__file__).resolve()),
}))
'''
    core = _run(
        [sys.executable, "-c", core_check],
        tmp_path / "core",
    )
    assert core.returncode == 0, core.stderr
    paths = json.loads(core.stdout)
    assert "/apps/core/" in paths["account_repository"]
    assert "/apps/core/" in paths["account_schemas"]
    assert "/apps/core/" in paths["opc_protocol"]
