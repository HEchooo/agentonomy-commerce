"""The delivered report must describe the configured settlement environment."""
from types import SimpleNamespace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from examples.monad_commerce.merchant import BudgetMerchant


CSV = (
    "transaction_id,date,description,amount,currency,category\n"
    "label-check,2026-10-13,Sample sale,10.00,USD,sales\n"
)


@pytest.mark.parametrize("mode,chain_id", [("local_anvil", 31337), ("monad_testnet", 10143)])
def test_report_and_replay_preserve_the_configured_settlement_label(tmp_path, monkeypatch, mode, chain_id):
    merchant = BudgetMerchant(
        tmp_path, receipt_secret="test-only-receipt-secret", network=f"eip155:{chain_id}",
        token="0x" + "11" * 20, pay_to="0x" + "22" * 20, port=0,
        settlement_mode=mode,
    )
    merchant._initialize_database()
    # Receipt validation is covered separately; this test begins at its result.
    monkeypatch.setattr(merchant, "_read_request", lambda handler: ("purchase_label", CSV, "input-hash"))
    responses = []
    handler = SimpleNamespace(_send=lambda status, payload: responses.append((status, payload)))
    merchant._handle_post(handler)
    merchant._handle_post(handler)
    assert [status for status, _ in responses] == [200, 200]
    assert responses[0][1] == responses[1][1]
    assert responses[0][1]["settlement_mode"] == mode
    assert responses[0][1]["real_funds"] is False
    assert merchant.delivery_count == 1


@pytest.mark.parametrize("mode,chain_id", [("simulated", 10143), ("monad_testnet", 31337), ("local_anvil", 10143)])
def test_merchant_rejects_an_untruthful_settlement_label(tmp_path, mode, chain_id):
    with pytest.raises(ValueError, match="settlement"):
        BudgetMerchant(
            tmp_path, receipt_secret="test-only-receipt-secret", network=f"eip155:{chain_id}",
            token="0x" + "11" * 20, pay_to="0x" + "22" * 20, port=0,
            settlement_mode=mode,
        )


@pytest.mark.parametrize("mode,chain_id", [("local_anvil", 31337), ("monad_testnet", 10143)])
def test_runtime_binds_the_merchant_label_to_its_core_snapshot(tmp_path, mode, chain_id):
    """Exercise actual runtime construction without starting a Core or signer."""
    root = Path(__file__).resolve().parents[2]
    script = '''
from pathlib import Path
import sys
from examples.monad_commerce.runtime import BudgetCommerceRuntime

class Core:
    def __init__(self, mode, chain_id):
        self.mode = mode
        self.network = "eip155:" + chain_id
    def __enter__(self): return self
    def close(self): pass
    def snapshot(self):
        return {"mode": self.mode, "network": self.network, "token": "0x" + "11" * 20,
                "pay_to": "0x" + "22" * 20, "receipt_signing_key": "test-only-receipt-secret"}

runtime = BudgetCommerceRuntime(Path(sys.argv[1]), 0, {})
runtime.core = Core(sys.argv[2], sys.argv[3])
with runtime:
    assert runtime.merchant.settlement_mode == sys.argv[2]
    assert runtime.merchant.network == "eip155:" + sys.argv[3]
print("configured merchant label verified")
'''
    env = {name: os.environ[name] for name in ("PATH", "LANG", "TMPDIR") if name in os.environ}
    env["PYTHONPATH"] = os.pathsep.join(str(root / path) for path in (
        "apps/marketplace", "apps/facilitator", "apps/core", "apps/node", "."))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), mode, str(chain_id)],
        cwd=root, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "configured merchant label verified"
