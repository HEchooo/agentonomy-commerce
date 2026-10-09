"""Run Marketplace namespace checks in its actual separate process namespace."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_hosted_runtime_injects_device_scope_and_isolates_preview_replay():
    program = r'''
import json
from types import SimpleNamespace
from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime
class Preview:
    def __init__(self, key, installation):
        self.preview_id, self.opc_installation_id = key, installation
    def model_dump(self, **_):
        return {'preview_id': self.preview_id, 'opc_installation_id': self.opc_installation_id}
class Store(dict):
    def set(self, key, value, _ttl): self[key] = value
previews, calls = {}, []
def create_preview(**kwargs):
    calls.append(kwargs)
    value = Preview('preview_' + str(len(calls)), kwargs['opc_installation_id'])
    previews[value.preview_id] = value
    return value
def execute(preview_id, **kwargs):
    calls.append({'execute': preview_id, **kwargs})
    return {'ok': True}
runtime = HostedCommerceRuntime.__new__(HostedCommerceRuntime)
runtime._require_started = lambda: None
runtime.store = Store()
runtime.repository = SimpleNamespace(get_preview=previews.get,
    get_purchase=lambda _id: SimpleNamespace(preview_id='preview_1'))
runtime.purchases = SimpleNamespace(create_preview=create_preview, execute=execute)
runtime._execution_payload = lambda result: result
csv = 'transaction_id,date,description,amount,currency,category\n1,2026-10-03,Sale,10.00,USD,sales\n'
first = runtime.preview('csv-reconciliation-v1', csv, 'same', opc_installation_id='opc_install_a')
replay = runtime.preview('csv-reconciliation-v1', csv, 'same', opc_installation_id='opc_install_a')
other = runtime.preview('csv-reconciliation-v1', csv, 'same', opc_installation_id='opc_install_b')
assert first == replay and other['preview_id'] != first['preview_id']
assert calls[0]['opc_installation_id'] == 'opc_install_a'
assert runtime.execute(first['preview_id'], opc_installation_id='opc_install_a') == {'ok': True}
assert calls[-1]['opc_installation_id'] == 'opc_install_a'
for method, kwargs in (
    ('execute', {'preview_id': first['preview_id']}),
    ('purchase', {'purchase_id': 'purchase_1'}),
    ('recover_purchase', {'purchase_id': 'purchase_1'}),
):
    count = len(calls)
    try:
        getattr(runtime, method)(**kwargs, opc_installation_id='opc_install_b')
        raise AssertionError('foreign device accepted')
    except (ValueError, PermissionError): pass
    assert len(calls) == count
print(json.dumps({'status': 'device_scoped'}))
'''
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root / 'apps/marketplace') + os.pathsep + str(root))
    result = subprocess.run([sys.executable, '-c', program], cwd=root, env=env,
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {'status': 'device_scoped'}
