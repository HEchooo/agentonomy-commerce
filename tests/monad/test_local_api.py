from fastapi.testclient import TestClient
from examples.monad_commerce.api import create_app
from examples.monad_commerce.rehearsal import CSV
from pathlib import Path
import subprocess
from types import SimpleNamespace


def test_browser_can_buy_without_operator_token_and_rejects_cross_origin():
    with TestClient(create_app(origin='http://127.0.0.1:8090'),base_url='http://127.0.0.1:8090') as client:
        assert client.get('/').status_code==200
        assert client.post('/api/preview',json={}).status_code==403
        assert client.post('/api/preview',headers={'Origin':'https://attacker.example'},json={}).status_code==403
        headers={'Origin':'http://127.0.0.1:8090'}
        preview=client.post('/api/preview',headers=headers,json={'csv_text':CSV,'idempotency_key':'web-1'}).json()
        paid=client.post('/api/execute',headers=headers,json={'preview_id':preview['preview_id']}).json()
        assert paid['state']=='delivered'
        assert paid['settlement']['verified'] is True
        assert client.get('/api/status').json()['used_amount_usdc']=='0.30'
        assert client.get('/api/status',headers={'Host':'attacker.example'}).status_code==403


def test_missing_local_preview_or_purchase_is_a_controlled_404():
    def missing(*_args, **_kwargs):
        raise KeyError('local session is missing')

    app = create_app(origin='http://127.0.0.1:8090')
    app.state.runtime = SimpleNamespace(request=missing, execute=missing)
    client = TestClient(app, base_url='http://127.0.0.1:8090', raise_server_exceptions=False)
    try:
        headers = {'Origin': 'http://127.0.0.1:8090'}
        execute = client.post('/api/execute', headers=headers, json={'preview_id': 'preview_stale'})
        purchase = client.get('/api/purchases/purchase_stale')
        assert execute.status_code == 404
        assert purchase.status_code == 404
        assert execute.json()['detail'] == '本地订单不存在或会话已过期。'
        assert purchase.json()['detail'] == '订单不存在'
    finally:
        client.close()


def _run_browser_session(response_status: int) -> dict:
    script_path = Path(__file__).resolve().parents[2] / 'examples/monad_commerce/web/app.js'
    node_program = r'''
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.argv[1], 'utf8');
const responseStatus = Number(process.argv[2]);
const storageValues = new Map([
  ['budgetPreview', 'preview_existing'],
  ['budgetSubmitted', 'true'],
  ['budgetCsv', 'csv-content'],
]);
const calls = [];
const ids = [
  'csv', 'preview', 'execute', 'retry', 'new', 'message', 'state',
  'evidence', 'result', 'remaining', 'used', 'reserved', 'grant',
  'meter', 'revoke', 'revoke-status',
];
const nodes = Object.fromEntries(ids.map(id => [id, {
  id,
  value: '',
  disabled: false,
  readOnly: false,
  textContent: '',
  style: {},
  children: [],
  replaceChildren(...children) { this.children = children; },
  append(...children) { this.children.push(...children); },
}]));
const document = {
  getElementById(id) { return nodes[id]; },
  createElement(tag) {
    return {
      tag,
      textContent: '',
      children: [],
      append(...children) { this.children.push(...children); },
    };
  },
};
const sessionStorage = {
  getItem(key) { return storageValues.has(key) ? storageValues.get(key) : null; },
  setItem(key, value) { storageValues.set(key, String(value)); },
  removeItem(key) { storageValues.delete(key); },
};
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/status') {
    return {ok: true, status: 200, async json() {
      return {remaining_amount_usdc: '0.70', used_amount_usdc: '0.30', reserved_amount_usdc: '0.00', grant_status: 'active'};
    }};
  }
  if (path === '/api/execute') {
    return {ok: responseStatus < 400, status: responseStatus, async json() {
      return responseStatus === 404
        ? {detail: '本地订单不存在或会话已过期。'}
        : {detail: '购买尚未确认，请保留当前订单并查询状态。'};
    }};
  }
  if (path === '/api/preview') {
    return {ok: true, status: 200, async json() { return {preview_id: 'new-preview'}; }};
  }
  throw new Error(`unexpected request: ${path}`);
}

async function main() {
  const context = {
    console,
    crypto: {randomUUID: () => 'request-id'},
    document,
    fetch,
    sessionStorage,
  };
  vm.runInNewContext(source, context, {filename: 'app.js'});
  await Promise.resolve();
  await nodes.retry.onclick();
  const afterRetry = {
    newDisabled: nodes.new.disabled,
    newLabel: nodes.new.textContent,
    retryDisabled: nodes.retry.disabled,
    preview: storageValues.get('budgetPreview') || null,
    submitted: storageValues.get('budgetSubmitted') || null,
  };
  const callsAfterRetry = calls.length;
  await nodes.new.onclick();
  const callsAfterReset = calls.length;
  const afterReset = {
    callsAfterReset: callsAfterReset - callsAfterRetry,
    preview: storageValues.get('budgetPreview') || null,
    submitted: storageValues.get('budgetSubmitted') || null,
    csv: storageValues.get('budgetCsv') || null,
    previewDisabled: nodes.preview.disabled,
    executeDisabled: nodes.execute.disabled,
    retryDisabled: nodes.retry.disabled,
    csvReadOnly: nodes.csv.readOnly,
  };
  let afterPreview = null;
  if (responseStatus === 404) {
    await nodes.preview.onclick();
    afterPreview = {
      executeDisabled: nodes.execute.disabled,
      preview: storageValues.get('budgetPreview') || null,
      callsAfterPreview: calls.length - callsAfterReset,
    };
  }
  process.stdout.write(JSON.stringify({calls, afterRetry, afterReset, afterPreview}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    completed = subprocess.run(
        ['node', '-e', node_program, str(script_path), str(response_status)],
        check=True,
        capture_output=True,
        text=True,
    )
    import json
    return json.loads(completed.stdout)


def test_pending_existing_order_stays_locked():
    result = _run_browser_session(409)
    assert result['afterRetry']['newDisabled'] is True
    assert result['afterRetry']['retryDisabled'] is False
    assert result['afterRetry']['preview'] == 'preview_existing'
    assert result['afterRetry']['submitted'] == 'true'
    assert not any(call['path'] == '/api/preview' for call in result['calls'])


def test_missing_local_session_requires_explicit_reset_without_new_payment():
    result = _run_browser_session(404)
    assert result['afterRetry']['newDisabled'] is False
    assert result['afterRetry']['newLabel'] == '重置本地会话'
    assert result['afterRetry']['retryDisabled'] is True
    assert result['afterReset']['callsAfterReset'] == 0
    assert result['afterReset']['preview'] is None
    assert result['afterReset']['submitted'] is None
    assert result['afterReset']['csv'] is None
    assert result['afterReset']['previewDisabled'] is False
    assert result['afterReset']['executeDisabled'] is True
    assert result['afterReset']['retryDisabled'] is True
    assert result['afterReset']['csvReadOnly'] is False
    assert result['afterPreview']['executeDisabled'] is False
    assert result['afterPreview']['preview'] == 'new-preview'
    assert result['afterPreview']['callsAfterPreview'] == 2
    assert sum(call['path'] == '/api/preview' for call in result['calls']) == 1
    assert sum(call['path'] == '/api/execute' for call in result['calls']) == 1
