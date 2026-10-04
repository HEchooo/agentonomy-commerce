from __future__ import annotations

import json
from pathlib import Path
import subprocess


UI_SOURCE = Path(__file__).resolve().parents[2] / "examples/monad_commerce/public_web/app.js"


def _run_node(program: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", program, str(UI_SOURCE)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_public_wallet_ui_enforces_wallet_chain_and_safe_retries() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const HASH = '0x' + 'aa'.repeat(32);
const approvalData = '0x095ea7b3' + EXECUTOR.slice(2).padStart(64, '0') + (1000000).toString(16).padStart(64, '0');
const ids = [
  'connect', 'switch-network', 'wallet-verify', 'grant-verify', 'budget-bind',
  'allowance-approve', 'allowance-verify', 'allowance-hash', 'preview', 'execute',
  'purchase-query', 'purchase-recover', 'purchase-id', 'revoke-prepare', 'revoke-chain', 'revoke-verify', 'revoke-hash',
  'csv', 'message', 'phase', 'owner', 'chain', 'token', 'executor', 'payee',
  'terms', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result',
  'commerce', 'tx-links', 'wallet-help',
];
const nodes = Object.fromEntries(ids.map(id => ({id, value: '', textContent: '', disabled: false,
  readOnly: false, style: {}, hidden: false, children: [],
  replaceChildren(...children) { this.children = children; },
  append(...children) { this.children.push(...children); },
})) .map(node => [node.id, node]));
const listeners = {};
const document = {
  readyState: 'complete',
  getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [],
    append(...children) { this.children.push(...children); }}; },
  addEventListener(name, fn) { listeners[name] = fn; },
};

let account = OWNER;
let chain = '0x279f';
let rejectNextSign = false;
let allowanceVerificationCount = 0;
let allowanceSendCount = 0;
let sessionCount = 0;
let walletVerifyCount = 0;
const providerMethods = [];
const providerListeners = {};
const provider = {
  on(name, fn) { providerListeners[name] = fn; },
  async request({method, params}) {
    providerMethods.push(method);
    if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [account];
    if (method === 'eth_chainId') return chain;
    if (method === 'wallet_switchEthereumChain') { chain = params[0].chainId; return null; }
    if (method === 'wallet_addEthereumChain') { chain = params[0].chainId; return null; }
    if (method === 'personal_sign') {
      if (rejectNextSign) { rejectNextSign = false; const e = new Error('rejected'); e.code = 4001; throw e; }
      return '0x' + '12'.repeat(65);
    }
    if (method === 'eth_signTypedData_v4') return '0x' + '34'.repeat(65);
    if (method === 'eth_sendTransaction') { allowanceSendCount += 1; return HASH; }
    throw new Error('unexpected provider method ' + method);
  },
};

const status = {
  owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, execution_signer: OWNER, payee: PAYEE,
  mode: 'monad_testnet', token_symbol: 'TestUSD',
  terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
  onboarding: {phase: 'wallet'}, commerce: null,
};
const calls = [];
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET', body: options.body || null, credentials: options.credentials || null});
  if (path === '/api/session') { sessionCount += 1; return {ok: true, status: 200, async json() { return {}; }}; }
  if (path === '/api/status') return {ok: true, status: 200, async json() { return status; }};
  if (path === '/api/wallet/challenge') return {ok: true, status: 200, async json() { return {message_to_sign: 'wallet challenge'}; }};
  if (path === '/api/wallet/verify') { walletVerifyCount += 1; return {ok: true, status: 200, async json() { return {verified: true}; }}; }
  if (path === '/api/allowance/transaction') return {ok: true, status: 200, async json() { return {transaction: {
    from: OWNER, to: TOKEN, value: '0x0', data: approvalData, chainId: '0x279f', gas: '0x5208', gasPrice: '0x1',
  }}; }};
  if (path === '/api/allowance/verify') {
    allowanceVerificationCount += 1;
    return {ok: true, status: 200, async json() { return allowanceVerificationCount === 1 ? {pending: true} : {verified: true}; }};
  }
  throw new Error('unexpected fetch ' + path);
}

async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.init();
  const beforeConnect = ui.state();
  const loadProviderMethods = providerMethods.slice();

  account = '0x9999999999999999999999999999999999999999';
  await ui.connect();
  const wrongWallet = ui.state();
  account = OWNER;
  chain = '0x1';
  await ui.connect();
  const wrongChain = ui.state();
  await ui.switchNetwork();
  await ui.connect();

  rejectNextSign = true;
  await ui.verifyWallet();
  const rejected = ui.state();

  const grantId = '0x' + '11'.repeat(32);
  const agentScope = '0x' + '22'.repeat(32);
  const validAfter = 1700000000;
  const validUntil = validAfter + 86460;
  const typedGood = {types: {EIP712Domain: [
    {name: 'name', type: 'string'}, {name: 'version', type: 'string'}, {name: 'chainId', type: 'uint256'},
    {name: 'verifyingContract', type: 'address'},
  ], SpendGrant: [
    {name: 'grantId', type: 'bytes32'}, {name: 'owner', type: 'address'}, {name: 'agentScope', type: 'bytes32'},
    {name: 'token', type: 'address'}, {name: 'payee', type: 'address'}, {name: 'maxPerPayment', type: 'uint256'},
    {name: 'maxTotal', type: 'uint256'}, {name: 'validAfter', type: 'uint256'}, {name: 'validUntil', type: 'uint256'},
    {name: 'executionSigner', type: 'address'},
  ]}, primaryType: 'SpendGrant', domain: {name: 'Agentonomy Budget Executor', version: '1', chainId: 10143, verifyingContract: EXECUTOR}, message: {
    grantId, owner: OWNER, agentScope, token: TOKEN, payee: PAYEE, maxTotal: 1000000, maxPerPayment: 500000,
    validAfter, validUntil, executionSigner: OWNER,
  }};
  const typedBad = {...typedGood, domain: {...typedGood.domain, chainId: 1}};
  const allowanceGood = {transaction: {from: OWNER, to: TOKEN, value: '0x0', data: approvalData,
    chainId: '0x279f', gas: '0x5208', gasPrice: '0x1'}};
  const allowanceBad = {transaction: {...allowanceGood.transaction, data: approvalData.slice(0, -1) + '1'}};

  await ui.approveAllowance();
  const afterPending = ui.state();
  await ui.verifyAllowance();
  const afterVerify = ui.state();
  providerListeners.accountsChanged(['0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa']);
  const afterAccountChange = ui.state();
  nodes['allowance-hash'].value = HASH;
  nodes['allowance-hash'].oninput();
  const allowanceRecoveryEnabled = !nodes['allowance-verify'].disabled;
  await ui.verifyAllowance();

  process.stdout.write(JSON.stringify({
    beforeConnect, wrongWallet, wrongChain, rejected, afterPending, afterVerify, afterAccountChange,
    sessionCount, walletVerifyCount, allowanceSendCount, allowanceVerificationCount, allowanceRecoveryEnabled,
    loadProviderMethods,
    typedGood: ui.validateTypedData(typedGood, {owner: OWNER, chain_id: 10143, executor: EXECUTOR, token: TOKEN, payee: PAYEE, execution_signer: OWNER}),
    typedBad: (() => { try { ui.validateTypedData(typedBad, {owner: OWNER, chain_id: 10143, executor: EXECUTOR, token: TOKEN, payee: PAYEE, execution_signer: OWNER}); return true; } catch (_) { return false; } })(),
    allowanceGood: ui.validateAllowanceTransaction(allowanceGood, {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR}),
    allowanceBad: (() => { try { ui.validateAllowanceTransaction(allowanceBad, {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR}); return true; } catch (_) { return false; } })(),
    signOrSendOnLoad: calls.slice(0, 2).some(call => call.path === '/api/wallet/verify' || call.path === '/api/allowance/verify'),
    calls,
  }));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["sessionCount"] == 1
    assert result["beforeConnect"]["connected"] is False
    assert result["wrongWallet"]["connected"] is False
    assert result["wrongChain"]["connected"] is False
    assert result["rejected"]["phase"] == "wallet"
    assert result["walletVerifyCount"] == 0
    assert result["typedGood"] is True
    assert result["typedBad"] is False
    assert result["allowanceGood"]["data"].lower() == result["allowanceGood"]["data"]
    assert result["allowanceGood"]["chainId"] == "0x279f"
    assert result["allowanceBad"] is False
    assert result["afterPending"]["allowance_tx_hash"]
    assert result["afterPending"]["allowance_verification_pending"] is True
    assert result["afterVerify"]["allowance_verified"] is True
    assert result["allowanceSendCount"] == 1
    assert result["allowanceVerificationCount"] == 3
    assert result["allowanceRecoveryEnabled"] is True
    assert result["afterAccountChange"]["allowance_tx_hash"] == "0x" + "aa" * 32
    assert result["afterAccountChange"]["connected"] is False
    assert "personal_sign" not in result["loadProviderMethods"]
    assert "eth_signTypedData_v4" not in result["loadProviderMethods"]
    assert "eth_sendTransaction" not in result["loadProviderMethods"]
    assert result["signOrSendOnLoad"] is False


def test_public_wallet_static_files_have_real_testnet_copy_and_no_secret_storage() -> None:
    root = UI_SOURCE.parent
    html = (root / "index.html").read_text()
    css = (root / "app.css").read_text()
    js = UI_SOURCE.read_text()

    assert "Monad Testnet" in html
    assert "no monetary value" in html
    assert "localStorage" not in js
    assert "private key" not in html.lower()
    assert "innerHTML" not in js
    assert "eth_sendRawTransaction" not in js
    assert "https://testnet.monadexplorer.com/tx/" in js
    assert "app.css" in html and "app.js" in html
    assert "@media" in css


def test_public_wallet_ui_keeps_broadcast_hash_before_post_send_wallet_check() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const OTHER = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const HASH = '0x' + 'ab'.repeat(32);
const approvalData = '0x095ea7b3' + EXECUTOR.slice(2).padStart(64, '0') + (1000000).toString(16).padStart(64, '0');
const ids = ['connect', 'switch-network', 'allowance-approve', 'allowance-verify', 'allowance-hash',
  'wallet-address', 'wallet-chain', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee',
  'terms', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'tx-links', 'commerce',
  'message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'revoke-prepare',
  'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'result'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {},
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }}]));
const providerListeners = {};
const providerMethods = [];
let account = OWNER;
let sends = 0;
let verifications = 0;
const provider = {
  on(name, fn) { providerListeners[name] = fn; },
  async request({method, params}) {
    providerMethods.push(method);
    if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [account];
    if (method === 'eth_chainId') return '0x279f';
    if (method === 'eth_sendTransaction') {
      sends += 1;
      account = OTHER;
      providerListeners.accountsChanged([account]);
      return HASH;
    }
    throw new Error('unexpected provider method ' + method);
  },
};
const status = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR,
  execution_signer: OWNER, payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD',
  terms: {total: '1.00', per_payment: '0.50', price: '0.30'}, onboarding: {phase: 'allowance'},
  wallet_operations: {}};
const calls = [];
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET', body: options.body || null});
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') return {ok: true, status: 200, async json() { return status; }};
  if (path === '/api/allowance/transaction') return {ok: true, status: 200, async json() { return {transaction: {
    from: OWNER, to: TOKEN, value: '0x0', data: approvalData, chainId: '0x279f', gas: '0x5208', gasPrice: '0x1'}}; }};
  if (path === '/api/allowance/verify') {
    verifications += 1;
    return {ok: true, status: 200, async json() { return {pending: true, transaction_hash: HASH}; }};
  }
  throw new Error('unexpected fetch ' + path);
}
const document = {
  readyState: 'complete',
  getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', replaceChildren() {}}; },
  addEventListener() {},
};
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.connect();
  await ui.approveAllowance();
  const afterBroadcast = ui.state();
  await ui.approveAllowance();
  process.stdout.write(JSON.stringify({afterBroadcast, sends, verifications, calls, providerMethods}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["sends"] == 1
    assert result["verifications"] == 2
    assert result["afterBroadcast"]["allowance_tx_hash"] == "0x" + "ab" * 32
    assert result["afterBroadcast"]["connected"] is False
    assert sum(call["path"] == "/api/allowance/verify" for call in result["calls"]) == 2
    assert result["providerMethods"].count("eth_sendTransaction") == 1


def test_public_wallet_ui_hydrates_operations_and_recovers_same_purchase() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const APPROVAL_HASH = '0x' + 'aa'.repeat(32);
const REVOKE_HASH = '0x' + 'bb'.repeat(32);
const PAYMENT_HASH = '0x' + 'cc'.repeat(32);
const GRANT_ID = '0x' + 'dd'.repeat(32);
const ids = ['connect', 'switch-network', 'wallet-verify', 'grant-verify', 'budget-bind', 'allowance-approve',
  'allowance-verify', 'allowance-hash', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id',
  'revoke-prepare', 'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'message', 'phase', 'owner', 'chain',
  'mode', 'token', 'executor', 'payee', 'terms', 'allowance-status', 'purchase-status', 'core-revoked',
  'chain-revoked', 'commerce', 'tx-links', 'wallet-help', 'result'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {},
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }}]));
const provider = {on() {}, async request({method}) {
  if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [OWNER];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected provider method ' + method);
}};
const status = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, execution_signer: OWNER,
  payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
  onboarding: {phase: 'revoked', chain_grant_id: GRANT_ID},
  wallet_operations: {
    approval: {transaction_hash: APPROVAL_HASH, status: 'pending'},
    revocation: {core_revoked: true, chain_grant_id: GRANT_ID, transaction_hash: REVOKE_HASH, status: 'pending'},
  }, commerce: null};
const calls = [];
let recoverCount = 0;
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET', body: options.body || null});
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') return {ok: true, status: 200, async json() { return status; }};
  if (path === '/api/purchases/purchase-1') return {ok: true, status: 200, async json() { return {
    purchase_id: 'purchase-1', preview_id: 'preview-1', state: 'payment_submitted', input_hash: '0x' + '11'.repeat(32),
    settlement: {transaction_hash: PAYMENT_HASH, status: 'settled'}, service_result: null}; }};
  if (path === '/api/purchases/purchase-1/recover') {
    recoverCount += 1;
    const state = recoverCount === 1 ? 'paid_but_undelivered' : 'delivered';
    return {ok: true, status: 200, async json() { return {
      purchase_id: 'purchase-1', preview_id: 'preview-1', state, input_hash: '0x' + '11'.repeat(32),
      settlement: {transaction_hash: PAYMENT_HASH, status: 'settled'},
      service_result: state === 'delivered' ? {rows: 2} : null}; }};
  }
  if (path === '/api/revoke/verify') return {ok: true, status: 200, async json() { return {
    core_revoked: true, chain_revoked: false, status: 'pending', transaction_hash: REVOKE_HASH}; }};
  throw new Error('unexpected fetch ' + path);
}
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', replaceChildren() {}}; }, addEventListener() {}};
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  const hydrated = ui.state();
  nodes['purchase-id'].value = 'purchase-1';
  nodes['purchase-id'].oninput();
  const purchaseActionsEnabled = !nodes['purchase-query'].disabled && !nodes['purchase-recover'].disabled;
  await ui.queryPurchase();
  const queried = ui.state();
  await ui.recoverPurchase();
  const recoveredPending = ui.state();
  await ui.recoverPurchase();
  const recovered = ui.state();
  await ui.verifyRevoke();
  const afterRevokeRetry = ui.state();
  process.stdout.write(JSON.stringify({hydrated, queried, recoveredPending, recovered, afterRevokeRetry,
    purchaseInput: nodes['purchase-id'].value, purchaseActionsEnabled, calls, recoverCount}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["hydrated"]["allowance_tx_hash"] == "0x" + "aa" * 32
    assert result["hydrated"]["allowance_verification_pending"] is True
    assert result["hydrated"]["revoke_tx_hash"] == "0x" + "bb" * 32
    assert result["hydrated"]["core_revoked"] is True
    assert result["hydrated"]["chain_revoked"] is False
    assert result["queried"]["purchase_id"] == "purchase-1"
    assert result["queried"]["preview_id"] == "preview-1"
    assert result["purchaseActionsEnabled"] is True
    assert result["recoveredPending"]["purchase_state"] == "paid_but_undelivered"
    assert result["recovered"]["purchase_state"] == "delivered"
    assert result["purchaseInput"] == "purchase-1"
    assert result["recoverCount"] == 2
    assert result["afterRevokeRetry"]["revoke_tx_hash"] == "0x" + "bb" * 32
    assert result["afterRevokeRetry"]["chain_revoked"] is False
    assert not any(call["path"] in {"/api/preview", "/api/execute"} for call in result["calls"])
    assert [call["path"] for call in result["calls"]].count("/api/purchases/purchase-1/recover") == 2


def test_public_wallet_ui_rejects_typed_schema_duration_and_gas_drift() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const GRANT_ID = '0x' + '11'.repeat(32);
const AGENT_SCOPE = '0x' + '22'.repeat(32);
const START = 1700000000;
const END = START + 86460;
const expected = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
  onboarding: {execution_signer: '0x5555555555555555555555555555555555555555', phase: 'budget_grant',
    grant_starts_at: new Date(START * 1000).toISOString(), grant_expires_at: new Date(END * 1000).toISOString(), chain_grant_id: GRANT_ID}};
const grantTypes = [
  {name: 'grantId', type: 'bytes32'}, {name: 'owner', type: 'address'}, {name: 'agentScope', type: 'bytes32'},
  {name: 'token', type: 'address'}, {name: 'payee', type: 'address'}, {name: 'maxPerPayment', type: 'uint256'},
  {name: 'maxTotal', type: 'uint256'}, {name: 'validAfter', type: 'uint256'}, {name: 'validUntil', type: 'uint256'},
  {name: 'executionSigner', type: 'address'},
];
const domainTypes = [
  {name: 'name', type: 'string'}, {name: 'version', type: 'string'}, {name: 'chainId', type: 'uint256'},
  {name: 'verifyingContract', type: 'address'},
];
const message = {grantId: GRANT_ID, owner: OWNER, agentScope: AGENT_SCOPE, token: TOKEN, payee: PAYEE,
  maxPerPayment: 500000, maxTotal: 1000000, validAfter: START, validUntil: END,
  executionSigner: expected.onboarding.execution_signer};
const payload = {typed_data: {types: {EIP712Domain: domainTypes, SpendGrant: grantTypes}, primaryType: 'SpendGrant',
  domain: {name: 'Agentonomy Budget Executor', version: '1', chainId: 10143, verifyingContract: EXECUTOR}, message},
  grant: {grantId: GRANT_ID, owner: OWNER, agentScope: AGENT_SCOPE, token: TOKEN, payee: PAYEE,
    maxPerPayment: '500000', maxTotal: '1000000', validAfter: String(START), validUntil: String(END), executionSigner: expected.onboarding.execution_signer},
  digest: '0x' + '33'.repeat(32)};
const ids = ['connect', 'switch-network', 'message', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee',
  'terms', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'tx-links', 'commerce', 'revoke-hash'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {}, replaceChildren() {}}]));
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; }, createElement() { return {textContent: '', replaceChildren() {}}; }, addEventListener() {}};
async function fetch(path) {
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') return {ok: true, status: 200, async json() { return {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR,
    payee: PAYEE, mode: 'monad_testnet', terms: {}, onboarding: expected.onboarding}; }};
  throw new Error('unexpected fetch ' + path);
}
async function main() {
  const context = {console, document, fetch, TextEncoder, window: {}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  const valid = ui.validateTypedData(payload, expected);
  const badDomain = JSON.parse(JSON.stringify(payload));
  badDomain.typed_data.domain.name = 'wrong';
  const badSchema = JSON.parse(JSON.stringify(payload));
  badSchema.typed_data.types.SpendGrant.pop();
  const badSigner = JSON.parse(JSON.stringify(payload));
  badSigner.typed_data.message.executionSigner = OWNER;
  const badDuration = JSON.parse(JSON.stringify(payload));
  badDuration.typed_data.message.validUntil = START + 86461;
  const allowance = {transaction: {from: OWNER, to: TOKEN, value: '0x0', data: '0x095ea7b3' + EXECUTOR.slice(2).padStart(64, '0') + (1000000).toString(16).padStart(64, '0'), chainId: '0x279f', gas: '0x186a0', gasPrice: '0x1'}};
  const allowanceGasTooHigh = {transaction: {...allowance.transaction, gas: '0x186a1'}};
  const allowancePriceTooHigh = {transaction: {...allowance.transaction, gasPrice: '0x6d3c21bcecc000'}};
  const revoke = {grant_id: GRANT_ID, transaction: {from: OWNER, to: EXECUTOR, value: '0x0', data: '0xb75c7dc6' + GRANT_ID.slice(2), chainId: '0x279f', gas: '0x186a0', gasPrice: '0x1'}};
  const revokeBadGrant = {grant_id: '0x' + '44'.repeat(32), transaction: {...revoke.transaction}};
  const revokeMismatchedExplicitGrant = {grant_id: '0x' + '44'.repeat(32), transaction: {...revoke.transaction, data: '0xb75c7dc6' + '44'.repeat(32)}};
  const revokeGasTooHigh = {grant_id: GRANT_ID, transaction: {...revoke.transaction, gas: '0x186a1'}};
  const invalid = (fn) => { try { fn(); return false; } catch (_) { return true; } };
  process.stdout.write(JSON.stringify({valid,
    badDomain: invalid(() => ui.validateTypedData(badDomain, expected)),
    badSchema: invalid(() => ui.validateTypedData(badSchema, expected)),
    badSigner: invalid(() => ui.validateTypedData(badSigner, expected)),
    badDuration: invalid(() => ui.validateTypedData(badDuration, expected)),
    allowanceGasTooHigh: invalid(() => ui.validateAllowanceTransaction(allowanceGasTooHigh, expected)),
    allowancePriceTooHigh: invalid(() => ui.validateAllowanceTransaction(allowancePriceTooHigh, expected)),
    allowanceBoundary: ui.validateAllowanceTransaction(allowance, expected).gas,
    revokeGood: ui.validateRevokeTransaction(revoke, expected).data,
    revokeBadGrant: invalid(() => ui.validateRevokeTransaction(revokeBadGrant, expected)),
    revokeMismatchedExplicitGrant: invalid(() => ui.validateRevokeTransaction(revokeMismatchedExplicitGrant, expected)),
    revokeGasTooHigh: invalid(() => ui.validateRevokeTransaction(revokeGasTooHigh, expected)),
    revokeBoundary: ui.validateRevokeTransaction(revoke, expected).gas}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["valid"] is True
    assert result["badDomain"] is True
    assert result["badSchema"] is True
    assert result["badSigner"] is True
    assert result["badDuration"] is True
    assert result["allowanceGasTooHigh"] is True
    assert result["allowancePriceTooHigh"] is True
    assert result["allowanceBoundary"] == "0x186a0"
    assert result["revokeGood"] == "0xb75c7dc6" + "11" * 32
    assert result["revokeBadGrant"] is True
    assert result["revokeMismatchedExplicitGrant"] is True
    assert result["revokeGasTooHigh"] is True
    assert result["revokeBoundary"] == "0x186a0"


def test_public_wallet_ui_allows_explicit_correction_after_rejected_hash() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const BAD_HASH = '0x' + 'aa'.repeat(32);
const GOOD_HASH = '0x' + 'bb'.repeat(32);
const approvalData = '0x095ea7b3' + EXECUTOR.slice(2).padStart(64, '0') + (1000000).toString(16).padStart(64, '0');
const ids = ['connect', 'switch-network', 'allowance-approve', 'allowance-verify', 'allowance-hash',
  'wallet-address', 'wallet-chain', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee',
  'terms', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'tx-links', 'commerce',
  'message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'revoke-prepare',
  'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'result'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {},
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }}]));
const provider = {on() {}, async request({method}) {
  if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [OWNER];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected provider method ' + method);
}};
const status = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, execution_signer: OWNER,
  payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD', terms: {},
  onboarding: {phase: 'allowance'}, wallet_operations: {approval: {transaction_hash: BAD_HASH, status: 'pending'}}};
let verifyCount = 0;
async function fetch(path, options = {}) {
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') return {ok: true, status: 200, async json() { return status; }};
  if (path === '/api/allowance/verify') {
    verifyCount += 1;
    if (verifyCount === 1) {
      status.wallet_operations.approval = {status: 'rejected', rejected_transaction_hash: BAD_HASH};
      return {ok: true, status: 200, async json() { return {status: 'rejected', rejected_transaction_hash: BAD_HASH}; }};
    }
    return {ok: true, status: 200, async json() { return {status: 'pending', transaction_hash: GOOD_HASH}; }};
  }
  throw new Error('unexpected fetch ' + path + ' ' + (options.method || 'GET'));
}
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; },
  createElement() { return {textContent: '', replaceChildren() {}}; }, addEventListener() {}};
async function main() {
  const context = {console, document, fetch, TextEncoder, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  const hydrated = ui.state();
  await ui.verifyAllowance();
  const rejected = ui.state();
  const inputAfterReject = nodes['allowance-hash'].value;
  nodes['allowance-hash'].value = GOOD_HASH;
  nodes['allowance-hash'].oninput();
  await ui.verifyAllowance();
  const corrected = ui.state();
  process.stdout.write(JSON.stringify({hydrated, rejected, corrected, inputAfterReject, verifyCount}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["hydrated"]["allowance_tx_hash"] == "0x" + "aa" * 32
    assert result["rejected"]["allowance_tx_hash"] is None
    assert result["rejected"]["allowance_rejected_tx_hash"] == "0x" + "aa" * 32
    assert result["inputAfterReject"] == ""
    assert result["corrected"]["allowance_tx_hash"] == "0x" + "bb" * 32
    assert result["corrected"]["allowance_verification_pending"] is True
    assert result["verifyCount"] == 2


def test_public_wallet_ui_reconciles_409_and_preserves_unknown_hash_on_refresh_failure() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const BAD_HASH = '0x' + 'aa'.repeat(32);
const UNKNOWN_HASH = '0x' + 'bb'.repeat(32);
const NEW_HASH = '0x' + 'cc'.repeat(32);
const ids = ['connect', 'switch-network', 'allowance-approve', 'allowance-verify', 'allowance-hash',
  'wallet-address', 'wallet-chain', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee',
  'terms', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'tx-links', 'commerce',
  'message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'revoke-prepare',
  'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'result'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {},
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }}]));
const provider = {on() {}, async request({method}) {
  if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [OWNER];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected provider method ' + method);
}};
const status = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, execution_signer: OWNER,
  payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD', terms: {}, onboarding: {phase: 'allowance'},
  wallet_operations: {approval: {transaction_hash: BAD_HASH, status: 'pending'},
    revocation: {core_revoked: true, chain_grant_id: '0x' + '11'.repeat(32), transaction_hash: BAD_HASH, status: 'pending'}}};
let verifyCount = 0;
let revokeVerifyCount = 0;
let failStatusRefresh = false;
async function fetch(path, options = {}) {
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') {
    if (failStatusRefresh) throw new Error('status unavailable');
    return {ok: true, status: 200, async json() { return status; }};
  }
  if (path === '/api/allowance/verify') {
    verifyCount += 1;
    if (verifyCount === 1) {
      status.wallet_operations.approval = {status: 'rejected', rejected_transaction_hash: BAD_HASH};
    }
    return {ok: false, status: 409, async json() { return {detail: 'wallet candidate unresolved'}; }};
  }
  if (path === '/api/revoke/verify') {
    revokeVerifyCount += 1;
    if (revokeVerifyCount === 1) {
      status.wallet_operations.revocation = {core_revoked: true, chain_grant_id: '0x' + '11'.repeat(32),
        status: 'rejected', rejected_transaction_hash: BAD_HASH};
    }
    return {ok: false, status: 409, async json() { return {detail: 'wallet candidate unresolved'}; }};
  }
  throw new Error('unexpected fetch ' + path + ' ' + (options.method || 'GET'));
}
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; },
  createElement() { return {textContent: '', replaceChildren() {}}; }, addEventListener() {}};
async function main() {
  const context = {console, document, fetch, TextEncoder, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.verifyAllowance();
  const rejectedAfter409 = ui.state();
  nodes['allowance-hash'].value = UNKNOWN_HASH;
  nodes['allowance-hash'].oninput();
  await ui.verifyAllowance();
  const unknownAfterStaleRejectedRefresh = ui.state();
  nodes['allowance-hash'].value = NEW_HASH;
  nodes['allowance-hash'].oninput();
  failStatusRefresh = true;
  await ui.verifyAllowance(NEW_HASH);
  const unknownAfterRefreshFailure = ui.state();
  failStatusRefresh = false;
  await ui.verifyRevoke();
  const revokeRejectedAfter409 = ui.state();
  await ui.verifyRevoke(NEW_HASH);
  const revokeUnknownAfterStaleRejected = ui.state();
  process.stdout.write(JSON.stringify({rejectedAfter409, unknownAfterStaleRejectedRefresh,
    unknownAfterRefreshFailure, revokeRejectedAfter409, revokeUnknownAfterStaleRejected,
    verifyCount, revokeVerifyCount}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["rejectedAfter409"]["allowance_tx_hash"] is None
    assert result["rejectedAfter409"]["allowance_rejected_tx_hash"] == "0x" + "aa" * 32
    assert result["unknownAfterStaleRejectedRefresh"]["allowance_tx_hash"] == "0x" + "bb" * 32
    assert result["unknownAfterStaleRejectedRefresh"]["allowance_verification_pending"] is True
    assert result["unknownAfterRefreshFailure"]["allowance_tx_hash"] == "0x" + "cc" * 32
    assert result["unknownAfterRefreshFailure"]["allowance_verification_pending"] is True
    assert result["verifyCount"] == 3
    assert result["revokeRejectedAfter409"]["revoke_tx_hash"] is None
    assert result["revokeRejectedAfter409"]["revoke_rejected_tx_hash"] == "0x" + "aa" * 32
    assert result["revokeUnknownAfterStaleRejected"]["revoke_tx_hash"] == "0x" + "cc" * 32
    assert result["revokeVerifyCount"] == 2


def test_public_wallet_ui_reprepares_core_revoke_after_prebroadcast_wallet_change() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const GRANT_ID = '0x' + '11'.repeat(32);
const revokeData = '0xb75c7dc6' + GRANT_ID.slice(2);
const ids = ['connect', 'switch-network', 'wallet-verify', 'grant-verify', 'budget-bind', 'allowance-approve',
  'allowance-verify', 'allowance-hash', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id',
  'revoke-prepare', 'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'message', 'phase', 'owner', 'chain',
  'mode', 'token', 'executor', 'payee', 'terms', 'allowance-status', 'purchase-status', 'core-revoked',
  'chain-revoked', 'commerce', 'tx-links', 'wallet-help', 'result'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, style: {},
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children = children; }}]));
const providerListeners = {};
const providerMethods = [];
let account = OWNER;
let prepareCount = 0;
const provider = {on(name, fn) { providerListeners[name] = fn; }, async request({method}) {
  providerMethods.push(method);
  if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected provider method ' + method);
}};
const transaction = {from: OWNER, to: EXECUTOR, value: '0x0', data: revokeData, chainId: '0x279f', gas: '0x186a0', gasPrice: '0x1'};
const status = {owner: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, execution_signer: OWNER,
  payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD', terms: {},
  onboarding: {phase: 'ready', chain_grant_id: GRANT_ID}, wallet_operations: {}};
const calls = [];
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/session') return {ok: true, status: 200, async json() { return {}; }};
  if (path === '/api/status') return {ok: true, status: 200, async json() { return status; }};
  if (path === '/api/revoke/prepare') {
    prepareCount += 1;
    status.wallet_operations.revocation = {core_revoked: true, chain_grant_id: GRANT_ID};
    return {ok: true, status: 200, async json() { return {core_revoked: true, chain_grant_id: GRANT_ID, transaction}; }};
  }
  throw new Error('unexpected fetch ' + path + ' ' + (options.method || 'GET'));
}
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; },
  createElement() { return {textContent: '', replaceChildren() {}}; }, addEventListener() {}};
async function main() {
  const context = {console, document, fetch, TextEncoder, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.connect();
  await ui.prepareRevoke();
  const prepared = ui.state();
  account = '0x9999999999999999999999999999999999999999';
  providerListeners.accountsChanged([account]);
  const afterWalletChange = ui.state();
  account = OWNER;
  await ui.connect();
  const afterReconnect = ui.state();
  const prepareEnabledAfterReconnect = !nodes['revoke-prepare'].disabled;
  await ui.prepareRevoke();
  const reprepared = ui.state();
  process.stdout.write(JSON.stringify({prepared, afterWalletChange, afterReconnect, prepareEnabledAfterReconnect,
    reprepared, prepareCount, providerMethods, calls}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["prepared"]["revoke_prepared"] is True
    assert result["afterWalletChange"]["revoke_prepared"] is False
    assert result["afterWalletChange"]["revoke_tx_hash"] is None
    assert result["afterReconnect"]["connected"] is True
    assert result["prepareEnabledAfterReconnect"] is True
    assert result["reprepared"]["revoke_prepared"] is True
    assert result["prepareCount"] == 2
    assert "eth_sendTransaction" not in result["providerMethods"]
