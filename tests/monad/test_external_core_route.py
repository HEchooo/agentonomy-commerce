from types import SimpleNamespace

import pytest

from examples.monad_commerce.hosted_process import dispatch_hosted


def test_device_revoke_reaches_canonical_core_without_active_grant_gate():
    calls = []
    def revoke(proof):
        calls.append(proof)
        return {'installation_id': 'device', 'status': 'revoked'}
    core = SimpleNamespace(opc_service=SimpleNamespace(revoke=revoke))
    assert dispatch_hosted(core, 'opc_revoke', {'proof': 'device-proof'})['status'] == 'revoked'
    assert calls == ['device-proof']
    with pytest.raises(ValueError):
        dispatch_hosted(core, 'opc_revoke', {'proof': 'device-proof', 'user_id': 'foreign'})
