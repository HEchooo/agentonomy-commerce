from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from examples.monad_commerce.hosted_canary import HostedCanary

OWNER, TOKEN, EXECUTOR, PAYEE = ['0x' + c*40 for c in '1234']


class FakeGate:
    def __init__(self):
        self.calls, self.proofs = [], []
    @contextmanager
    def enter(self, *scope):
        self.calls.append(scope)
        yield self
    def complete_verified_payment(self, proof):
        self.proofs.append(proof)
    def reconcile_verified_payment(self, proof):
        self.proofs.append(proof)


def evidence():
    return dict(verified=True, status='verified', two_rpc_verified=True,
                chain_id=10143, receipt_status=1, transaction_hash='0x'+'ab'*32,
                executor=EXECUTOR, owner=OWNER, token=TOKEN, payee=PAYEE,
                amount_atomic='300000', purchase_commitment='0x'+'cd'*32,
                quote_commitment='0x'+'ef'*32, grant_hash='0x'+'aa'*32,
                block_hash='0x'+'bb'*32, finality_block_hash='0x'+'cc'*32)


def setup(result, *, existing=None, expired=False):
    canary = HostedCanary.__new__(HostedCanary)
    canary.gate = FakeGate()
    canary.tenant_id, canary.owner = 'tenant_a', OWNER
    canary.network = SimpleNamespace(token=TOKEN, executor=EXECUTOR, payee=PAYEE)
    canary._start_market = lambda: None
    calls = []
    def request(method, args):
        calls.append(method)
        if method == 'preview_state':
            return {'preview_id': 'preview_a', 'expires_at': (datetime.now(UTC)+timedelta(seconds=-1 if expired else 300)).isoformat()}
        if method == 'purchase':
            if existing is None:
                raise RuntimeError('purchase_not_found')
            return existing
        if method in {'execute', 'recover_purchase'}:
            return result
        raise AssertionError(method)
    canary.market = SimpleNamespace(request=request)
    return canary, calls


def test_expired_and_nonexistent_preview_does_not_pin_global_lane():
    canary, calls = setup({}, expired=True)
    with pytest.raises(ValueError, match='expired'):
        canary.request('execute', {'preview_id': 'preview_a'})
    assert not canary.gate.calls and 'execute' not in calls


def test_verified_canonical_core_result_releases_lane():
    canary, _ = setup({'purchase_id': 'purchase_a', 'settlement': evidence()})
    canary.request('execute', {'preview_id': 'preview_a'})
    assert canary.gate.calls == [('tenant_a', 'purchase_a', OWNER)]
    assert canary.gate.proofs[0]['tenant_id'] == 'tenant_a'


def test_pending_result_retains_lane_and_recovery_uses_same_purchase():
    pending = {'purchase_id': 'purchase_a', 'preview_id': 'preview_a', 'state': 'payment_submitted', 'settlement': {'verified': False}}
    canary, _ = setup(pending, existing=pending)
    canary.request('recover_purchase', {'purchase_id': 'purchase_a'})
    assert canary.gate.calls == [('tenant_a', 'purchase_a', OWNER)]
    assert not canary.gate.proofs


def test_verified_order_after_crash_reconciles_gate_before_delivery_recovery():
    result = {'purchase_id': 'purchase_a', 'preview_id': 'preview_a', 'settlement': evidence()}
    canary, _ = setup(result, existing=result)
    canary.request('execute', {'preview_id': 'preview_a'})
    assert not canary.gate.calls
    assert len(canary.gate.proofs) == 1


def test_partial_or_crossowner_completion_never_clears_lane():
    proof = evidence() | {'owner': PAYEE}
    canary, _ = setup({'purchase_id': 'purchase_a', 'settlement': proof})
    with pytest.raises(ValueError, match='incomplete'):
        canary.request('execute', {'preview_id': 'preview_a'})
    assert not canary.gate.proofs
