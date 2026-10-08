"""Behavioral wallet tests use real Python-generated ABI and disclosure payloads."""
import json
from pathlib import Path
import subprocess

import pytest

from test_hosted_erc8004_wire import feedback_wire


PROGRAM = r'''
const fs = require('fs'), vm = require('vm');
const wire = JSON.parse(process.argv[2]), kind = process.argv[3];
const TX = '0x' + '34'.repeat(32), OTHER = '0x' + 'ab'.repeat(20);
const values = {}, calls = [], sends = [], storageValues = {};
const heldLocks = new Set();
const locks = {async request(name, options, action) {
  if (heldLocks.has(name)) return action(null);
  heldLocks.add(name);
  try { return await action({name}); } finally { heldLocks.delete(name); }
}};
const storage = {
  getItem: key => storageValues[key] || null,
  setItem(key, value) { if (kind === 'storage-failure' && key.includes('feedback')) throw Error('storage full'); storageValues[key] = value; },
  removeItem: key => delete storageValues[key],
};
const response = value => ({ok: true, status: 200, async json() { return value; }});
function defer() { let resolve; return {promise: new Promise(r => resolve = r), resolve: value => resolve(value)}; }
function boot() {
  let account = wire.buyer, authenticated = true;
  const listeners = {}, nodes = new Proxy({}, {get(target, id) {
    return target[id] || (target[id] = {value: id === 'feedback-score' ? '87' : id === 'csv' ? 'a,b\n1,2\n' : '',
      textContent: '', disabled: false, replaceChildren() {}, append() {}});
  }});
  const sendStarted = defer(), sendResult = defer();
  const provider = {on(name, fn) { listeners[name] = fn; }, async request({method, params}) {
    if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
    if (method === 'eth_chainId') return '0x279f';
    if (method === 'eth_sendTransaction') {
      sends.push(params[0]); sendStarted.resolve();
      if (['unknown', 'manual-unknown', 'stale-tab', 'recover-unknown', 'server-recovered'].includes(kind)) throw Error('transport timeout');
      if (['cancel', 'cancel-server-recovered'].includes(kind) || kind === 'cancel-retry' && sends.length === 1) {
        const error = Error('rejected'); error.code = 4001; throw error;
      }
      if (['switch', 'order-during-send'].includes(kind) || kind === 'concurrent-tabs' && sends.length === 1) return sendResult.promise;
      return TX;
    }
    throw Error('unexpected wallet method ' + method);
  }};
  function status() {
    if (!authenticated) return {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}};
    return {authenticated: true, session: {authenticated: true, wallet_address: wire.buyer}, owner: wire.buyer,
      wallet_address: wire.buyer, chain_id: 10143, token: wire.token, executor: wire.executor, payee: wire.payee,
      mode: 'monad_testnet', onboarding: {phase: 'ready'}, opc: {status: 'active'}, opcstatus: {status: 'active'},
      terms: {total: '1.00', per_payment: '0.50', price: '0.30'}};
  }
  async function fetch(path, options = {}) {
    calls.push({path, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
    if (path === '/api/session') return response({csrf_token: 'csrf', authenticated,
      ...(authenticated ? {wallet_address: wire.buyer} : {})});
    if (path === '/api/logout') { authenticated = false; return response({authenticated: false}); }
    if (path === '/api/status') return response(status());
    if (path === '/api/agent') return response(wire.identity);
    if (path === '/api/preview') return response({preview_id: 'preview_wire', purchase_id: 'purchase_wire'});
    if (/^\/api\/purchases\/purchase_(wire|other)$/.test(path)) {
      const id = path.split('/').pop();
      return response({purchase_id: id, preview_id: id.replace('purchase_', 'preview_'), state: 'delivered',
        settlement: {verified: true, status: 'verified', chain_id: 10143, receipt_status: 1, two_rpc_verified: true,
          transaction_hash: TX, token: wire.token, amount_atomic: '300000'},
        service_result: {report: 'PRIVATE CSV REPORT'}});
    }
    if (path.endsWith('/feedback/prepare')) {
      const value = JSON.parse(JSON.stringify(wire.prepared));
      if (kind === 'recipient') value.transaction.to = OTHER;
      if (kind === 'calldata') value.transaction.data = '0xdeadbeef';
      return response(value);
    }
    if (path.endsWith('/feedback/verify')) return response({status: 'verified', verified: true,
      transaction_hash: TX, feedback_hash: wire.prepared.feedback_hash, feedback_uri: wire.prepared.feedback_uri,
      score: 87, feedback_index: 1, is_revoked: false});
    if (path.endsWith('/feedback')) return response({
      ...wire.prepared, transaction: undefined, disclosure: undefined,
      ...(['server-recovered', 'cancel-server-recovered'].includes(kind) ? {
        status: 'verified', verified: true, transaction_hash: TX, feedback_index: 1, is_revoked: false} : {})});
    throw Error('unexpected request ' + path);
  }
  const context = {TextEncoder, console, fetch, localStorage: storage, location: {origin: wire.origin},
    crypto: {randomUUID: () => 'test-id-1'}, navigator: kind === 'no-locks' ? {} : {locks},
    window: {ethereum: provider, location: {origin: wire.origin}},
    document: {readyState: 'loading', addEventListener() {}, getElementById: id => nodes[id],
      createElement: () => ({append() {}})}};
  vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
  return {ui: context.PublicWalletUI, nodes, listeners, sendStarted, sendResult,
    changeAccount() { account = OTHER; if (listeners.accountsChanged) listeners.accountsChanged([OTHER]); }};
}
async function main() {
  let page = boot();
  await page.ui.init(); await page.ui.refreshAgentIdentity();
  if (kind === 'ordinary') {
    await page.ui.preview();
    const before = page.ui.state();
    page = boot(); await page.ui.init();
    return {before, after: page.ui.state(), calls, sends, storage: storageValues};
  }
  if (kind === 'unknown') await page.ui.preview();
  page.nodes['purchase-id'].value = 'purchase_wire'; await page.ui.queryPurchase();
  if (kind === 'no-prepare') {
    await page.ui.submitFeedback();
    return {state: page.ui.state(), calls, sends};
  }
  const prepared = await page.ui.prepareFeedback();
  if (!prepared) return {prepared, state: page.ui.state(), calls, sends, storage: storageValues};
  const disclosure = page.nodes['feedback-disclosure'].textContent;
  if (kind === 'stale-tab' || kind === 'concurrent-tabs') {
    const second = boot(); await second.ui.init(); await second.ui.refreshAgentIdentity();
    second.nodes['purchase-id'].value = 'purchase_wire'; await second.ui.queryPurchase();
    await second.ui.prepareFeedback();
    if (kind === 'concurrent-tabs') {
      const pending = page.ui.submitFeedback(); await page.sendStarted.promise;
      await second.ui.submitFeedback();
      page.sendResult.resolve(null); await pending;
    } else await page.ui.submitFeedback();
    await second.ui.prepareFeedback(); await second.ui.queryFeedback(); await second.ui.submitFeedback();
    return {state: second.ui.state(), calls, sends, storage: storageValues};
  }
  if (kind === 'score') { page.nodes['feedback-score'].value = '90'; page.nodes['feedback-score'].oninput(); }
  if (kind === 'order') { page.nodes['purchase-id'].value = 'purchase_other'; page.nodes['purchase-id'].oninput(); }
  if (kind === 'switch' || kind === 'order-during-send') {
    const pending = page.ui.submitFeedback(); await page.sendStarted.promise;
    if (kind === 'switch') page.changeAccount();
    else page.nodes['purchase-id'].value = 'purchase_other';
    page.sendResult.resolve(TX); await pending;
  } else await page.ui.submitFeedback();
  const afterSend = page.ui.state();
  if (kind === 'cancel-retry') await page.ui.submitFeedback();
  if (kind === 'recover-unknown') {
    await page.ui.verifyFeedback(TX);
    page = boot(); await page.ui.init(); await page.ui.refreshAgentIdentity();
    page.nodes['purchase-id'].value = 'purchase_wire'; await page.ui.queryPurchase();
    await page.ui.submitFeedback();
    await page.ui.verifyFeedback('0x' + '56'.repeat(32));
  }
  if (kind === 'server-recovered' || kind === 'cancel-server-recovered') {
    await page.ui.queryFeedback();
    page = boot(); await page.ui.init(); await page.ui.refreshAgentIdentity();
    page.nodes['purchase-id'].value = 'purchase_wire'; await page.ui.queryPurchase();
    await page.ui.submitFeedback();
    await page.ui.verifyFeedback('0x' + '56'.repeat(32));
  }
  if (kind === 'unknown' || kind === 'manual-unknown' || kind === 'switch') {
    await page.ui.submitFeedback();
    page = boot(); await page.ui.init(); await page.ui.refreshAgentIdentity();
    page.nodes['purchase-id'].value = 'purchase_wire'; await page.ui.queryPurchase();
    await page.ui.submitFeedback();
  }
  return {prepared, disclosure, afterSend, state: page.ui.state(), calls, sends, storage: storageValues};
}
main().then(value => process.stdout.write(JSON.stringify(value))).catch(error => {console.error(error); process.exit(1);});
'''


def scenario(kind):
    source = Path(__file__).resolve().parents[2] / 'examples/monad_commerce/hosted_web/app.js'
    result = subprocess.run(['node', '-e', PROGRAM, str(source), json.dumps(feedback_wire()), kind],
                            check=True, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)


def test_explicit_prepare_discloses_actual_fields_before_separate_send():
    result = scenario('normal')
    assert result['prepared']
    assert result['state']['feedback_verified'] is True
    assert len(result['sends']) == 1
    assert json.loads(result['disclosure']) == feedback_wire()['prepared']['disclosure']
    assert result['sends'][0] == feedback_wire()['prepared']['transaction']
    assert 'PRIVATE CSV' not in json.dumps(result['storage'])


def test_submit_without_reviewed_draft_never_sends():
    result = scenario('no-prepare')
    assert not result['sends']
    assert not any(call['path'].endswith('/feedback/prepare') for call in result['calls'])


@pytest.mark.parametrize('kind', ['recipient', 'calldata', 'score', 'order', 'storage-failure'])
def test_invalid_or_changed_draft_never_sends(kind):
    result = scenario(kind)
    assert not result['sends']


@pytest.mark.parametrize('kind', ['unknown', 'manual-unknown', 'switch'])
def test_unknown_or_late_wallet_send_stays_locked_across_refresh(kind):
    result = scenario(kind)
    assert len(result['sends']) == 1
    assert result['state']['feedback_awaiting_recovery'] is True
    assert not any(call['path'].endswith('/feedback/verify') for call in result['calls'])
    assert result['state']['purchase_id'] == 'purchase_wire'


def test_editing_order_during_send_verifies_captured_original_order():
    result = scenario('order-during-send')
    assert len(result['sends']) == 1
    paths = [call['path'] for call in result['calls'] if call['path'].endswith('/feedback/verify')]
    assert paths == ['/api/purchases/purchase_wire/feedback/verify']


def test_ordinary_purchase_references_still_restore_without_feedback():
    result = scenario('ordinary')
    assert result['before']['preview_id'] == result['after']['preview_id'] == 'preview_wire'
    assert result['after']['purchase_id'] == 'purchase_wire'
    assert result['after']['idempotency_key'] == 'test-id-1'
    assert not result['sends']


def test_explicit_wallet_rejection_does_not_leave_unknown_send_lock():
    result = scenario('cancel')
    assert len(result['sends']) == 1
    assert result['state']['feedback_awaiting_recovery'] is False


@pytest.mark.parametrize('kind', ['stale-tab', 'concurrent-tabs'])
def test_same_profile_tabs_cannot_erase_unknown_attempt_or_send_concurrently(kind):
    result = scenario(kind)
    assert len(result['sends']) == 1
    assert result['state']['feedback_awaiting_recovery'] is True
    records = json.loads(result['storage']['agentonomy.hosted.feedback_refs.v1'])
    assert records[feedback_wire()['buyer']]['orders']['purchase_wire']['send_unknown'] is True


def test_browser_without_cross_tab_locks_cannot_send_optional_feedback():
    assert not scenario('no-locks')['sends']


def test_explicit_wallet_cancel_under_original_lock_allows_reviewed_retry():
    result = scenario('cancel-retry')
    assert len(result['sends']) == 2
    assert result['state']['feedback_verified'] is True


def test_manual_unknown_recovery_retains_original_candidate_and_rejects_competing_hash():
    result = scenario('recover-unknown')
    assert len(result['sends']) == 1
    assert result['state']['feedback_tx_hash'] == '0x' + '34' * 32
    verifies = [call['body']['transaction_hash'] for call in result['calls'] if call['path'].endswith('/feedback/verify')]
    assert verifies == ['0x' + '34' * 32]


@pytest.mark.parametrize('kind', ['server-recovered', 'cancel-server-recovered'])
def test_canonical_server_candidate_survives_older_null_attempt_marker(kind):
    result = scenario(kind)
    assert len(result['sends']) == 1
    assert result['state']['feedback_tx_hash'] == '0x' + '34' * 32
    assert not any(call['path'].endswith('/feedback/verify') for call in result['calls'])
