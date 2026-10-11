"""Regression coverage for the hosted worker's clean child-process startup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://review.agentonomy.xyz"

# This is a validation-only scope.  The key ARNs and credential path are
# deliberately non-secret test values; the child never opens the path or signs.
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
    "public_origin": ORIGIN,
}


CHILD_CHECK = r'''
import json
import sys

from examples.monad_commerce.hosted_process import validate_hosted_bootstrap
from shared.opc_protocol import canonical_opc_origin

bootstrap = json.loads(sys.argv[1])
origin = bootstrap["public_origin"]
assert canonical_opc_origin(origin) == origin
scope = validate_hosted_bootstrap(bootstrap)
assert scope.network.chain_id == 10143
print(json.dumps({"origin": origin, "chain_id": scope.network.chain_id}))
'''


def test_hosted_worker_validates_public_bootstrap_in_clean_child() -> None:
    """Mirror the worker environment without PYTHONPATH or cloud credentials."""
    worker_env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL"}
    }
    worker_env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            CHILD_CHECK,
            json.dumps(PUBLIC_BOOTSTRAP, separators=(",", ":")),
        ],
        cwd=ROOT,
        env=worker_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, (
        "clean hosted worker could not load and validate its public bootstrap:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json.loads(result.stdout) == {"origin": ORIGIN, "chain_id": 10143}
