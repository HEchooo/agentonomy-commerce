from __future__ import annotations

import json
from pathlib import Path
import subprocess


UI_ROOT = Path(__file__).resolve().parents[2] / "examples/monad_commerce/hosted_web"
UI_SOURCE = UI_ROOT / "app.js"


def _run_node(program: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", program, str(UI_SOURCE)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_hosted_wallet_ui_bootstraps_cookie_login_claim_and_opc_in_order() -> None:
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
const CLAIM = '0x5555555555555555555555555555555555555555';
const HASH = '0x' + 'aa'.repeat(32);
const claimData = '0x4e71d92d';
const ids = [
  'connect', 'switch-network', 'logout', 'wallet-verify', 'claim-approve', 'claim-verify', 'claim-hash',
  'grant-verify', 'budget-bind', 'allowance-approve', 'allowance-verify', 'allowance-hash',
  'opc-prepare', 'opc-approve', 'opc-message', 'preview', 'execute', 'purchase-query', 'purchase-recover',
  'purchase-id', 'revoke-prepare', 'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'message',
  'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated', 'opc-status',
  'claim-status', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result',
  'commerce', 'tx-links', 'wallet-address', 'wallet-chain', 'wallet-help',
];
const nodes = Object.fromEntries(ids.map(id => [id, {
  id, value: '', textContent: '', disabled: false, style: {}, hidden: false, children: [],
  replaceChildren(...children) { this.children = children; },
  append(...children) { this.children.push(...children); },
}]));
const listeners = {};
const document = {
  readyState: 'complete',
  getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener(name, fn) { listeners[name] = fn; },
};

let account = OWNER;
let chain = '0x279f';
let authenticated = false;
let claimed = false;
let opcActive = false;
let sessionCount = 0;
let loginChallengeCount = 0;
let loginVerifyCount = 0;
let claimSendCount = 0;
let claimVerifyCount = 0;
let opcPrepareCount = 0;
let opcApproveCount = 0;
let rejectSignOnce = true;
let initialUnauthStatusHadOwner = null;
const signedMessages = [];
const providerMethods = [];
const providerListeners = {};
const calls = [];
const provider = {
  on(name, fn) { providerListeners[name] = fn; },
  async request({method, params}) {
    providerMethods.push(method);
    if (method === 'eth_requestAccounts' || method === 'eth_accounts') return [account];
    if (method === 'eth_chainId') return chain;
    if (method === 'personal_sign') {
      signedMessages.push(params[0]);
      if (rejectSignOnce) { rejectSignOnce = false; const error = new Error('rejected'); error.code = 4001; throw error; }
      return '0x' + '12'.repeat(65);
    }
    if (method === 'eth_signTypedData_v4') return '0x' + '34'.repeat(65);
    if (method === 'eth_sendTransaction') { claimSendCount += 1; return HASH; }
    throw new Error('unexpected provider method ' + method);
  },
};

const grantId = '0x' + '11'.repeat(32);
const agentScope = '0x' + '22'.repeat(32);
const typedMessage = {
  grantId, owner: OWNER, agentScope, token: TOKEN, payee: PAYEE,
  maxPerPayment: '500000', maxTotal: '1000000', validAfter: '1700000000',
  validUntil: '1700086460', executionSigner: OWNER,
};
const typedPayload = {
  typed_data: {
    types: {
      EIP712Domain: [
        {name: 'name', type: 'string'}, {name: 'version', type: 'string'},
        {name: 'chainId', type: 'uint256'}, {name: 'verifyingContract', type: 'address'},
      ],
      SpendGrant: [
        {name: 'grantId', type: 'bytes32'}, {name: 'owner', type: 'address'},
        {name: 'agentScope', type: 'bytes32'}, {name: 'token', type: 'address'},
        {name: 'payee', type: 'address'}, {name: 'maxPerPayment', type: 'uint256'},
        {name: 'maxTotal', type: 'uint256'}, {name: 'validAfter', type: 'uint256'},
        {name: 'validUntil', type: 'uint256'}, {name: 'executionSigner', type: 'address'},
      ],
    },
    primaryType: 'SpendGrant',
    domain: {name: 'Agentonomy Budget Executor', version: '1', chainId: 10143, verifyingContract: EXECUTOR},
    message: typedMessage,
  },
  grant: typedMessage,
};

function response(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return payload; }};
}
function statusPayload() {
  const value = {
    authenticated,
    session: {authenticated},
    onboarding: {phase: authenticated ? (claimed ? 'core_grant' : 'wallet') : 'wallet'},
    claim: claimed ? {status: 'verified', verified: true, amount_atomic: '1000000', transaction_hash: HASH} : null,
    opcstatus: opcActive ? {status: 'active', installation_id: 'installation-1'} : {status: 'pending'},
    commerce: authenticated ? {purchase_id: 'purchase-old', state: 'delivered'} : null,
  };
  if (authenticated) Object.assign(value, {
    owner: OWNER, chain_id: 10143, token: TOKEN, claim_contract: CLAIM, executor: EXECUTOR,
    execution_signer: OWNER, payee: PAYEE, mode: 'monad_testnet', token_symbol: 'TestUSD',
    terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
  });
  return value;
}
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET', body: options.body || null,
    headers: options.headers || null, credentials: options.credentials || null});
  if (path === '/api/session') { sessionCount += 1; return response({csrf_token: 'csrf-' + sessionCount, authenticated}); }
  if (path === '/api/status') {
    const payload = statusPayload();
    if (!authenticated && initialUnauthStatusHadOwner === null) {
      initialUnauthStatusHadOwner = Object.prototype.hasOwnProperty.call(payload, 'owner');
    }
    return response(payload);
  }
  if (path === '/api/login/challenge') { loginChallengeCount += 1; return response({session_id: 'login-1', message_to_sign: 'Clink Wallet Identity\nWallet Address: ' + OWNER}); }
  if (path === '/api/login/verify') { loginVerifyCount += 1; authenticated = true; return response({status: 'authorized'}); }
  if (path === '/api/budget/payload') return response(typedPayload);
  if (path === '/api/budget/bind') return response({status: 'bound'});
  if (path === '/api/faucet/transaction') return response({amount_atomic: '1000000', transaction: {
    from: OWNER, to: CLAIM, value: '0x0', data: claimData, chainId: '0x279f', gas: '0x5208', gasPrice: '0x1',
  }});
  if (path === '/api/faucet/verify') { claimVerifyCount += 1; claimed = true; return response({status: 'verified', verified: true}); }
  if (path === '/api/opc/prepare') { opcPrepareCount += 1; return response({installation_id: 'installation-1', session_id: 'opc-1', message_to_sign: 'install hosted agent'}); }
  if (path === '/api/opc/approve') { opcApproveCount += 1; opcActive = true; return response({status: 'active', installation_id: 'installation-1'}); }
  if (path === '/api/logout') { authenticated = false; claimed = false; opcActive = false; return response({status: 'logged_out'}); }
  throw new Error('unexpected fetch ' + path);
}

async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  const loadProviderMethods = providerMethods.slice();
  const beforeConnect = ui.state();
  await ui.connect();
  await ui.verifyWallet();
  const failedLogin = ui.state();
  await ui.verifyWallet();
  const afterLogin = ui.state();
  await ui.claimTestUsd();
  const afterClaim = ui.state();
  await ui.bindBudget();
  const afterBudget = ui.state();
  await ui.prepareOpc();
  const prepared = ui.state();
  await ui.approveOpc();
  const afterOpc = ui.state();

  const claimGood = ui.validateClaimTransaction({amount_atomic: '1000000', transaction: {
    from: OWNER, to: CLAIM, value: '0x0', data: claimData, chainId: 10143, gas: '0x5208', gasPrice: '0x1',
  }}, {owner: OWNER, claim_contract: CLAIM});
  let claimBad = true;
  try { ui.validateClaimTransaction({amount_atomic: '1000000', transaction: {
    from: OWNER, to: CLAIM, value: '0x0', data: '0x095ea7b3', chainId: 10143, gas: '0x5208', gasPrice: '0x1',
  }}, {owner: OWNER, claim_contract: CLAIM}); } catch (_) { claimBad = false; }

  account = OTHER;
  providerListeners.accountsChanged([OTHER]);
  const afterAccountEvent = ui.state();
  await new Promise(resolve => setTimeout(resolve, 0));
  const afterSessionReset = ui.state();

  process.stdout.write(JSON.stringify({
    loadProviderMethods, beforeConnect,
    failedLogin, afterLogin, afterClaim, afterBudget, prepared, afterOpc, afterAccountEvent, afterSessionReset,
    afterAccountCommerce: nodes.commerce.textContent,
    afterAccountOwner: nodes.owner.textContent,
    sessionCount, loginChallengeCount, loginVerifyCount, claimSendCount, claimVerifyCount,
    opcPrepareCount, opcApproveCount, calls,
    initialUnauthStatusHadOwner, signedMessages,
    claimSelector: claimGood.data, claimBad,
    noOldWalletRoute: calls.every(call => !call.path.includes('/api/wallet/')),
    noTokenRoute: calls.every(call => !call.path.includes('/api/opc/token')),
  }));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["sessionCount"] >= 1
    assert result["beforeConnect"]["connected"] is False
    assert result["failedLogin"]["authenticated"] is False
    assert result["failedLogin"]["login_challenge_id"] is None
    assert result["afterLogin"]["authenticated"] is True
    assert result["initialUnauthStatusHadOwner"] is False
    assert result["afterClaim"]["claim_verified"] is True
    assert result["claimSendCount"] == 1
    assert result["claimVerifyCount"] == 1
    assert result["prepared"]["opc_challenge_id"] == "opc-1"
    assert result["afterOpc"]["opc_status"]["status"] == "active"
    assert result["opcPrepareCount"] == 1
    assert result["opcApproveCount"] == 1
    assert result["loginChallengeCount"] == 2
    login_challenge_calls = [call for call in result["calls"] if call["path"] == "/api/login/challenge"]
    assert len(login_challenge_calls) == 2
    assert all(json.loads(call["body"]) == {"owner": "0x1111111111111111111111111111111111111111"}
               for call in login_challenge_calls)
    login_proofs = [
        bytes.fromhex(message[2:]).decode("utf-8")
        for message in result["signedMessages"][:2]
    ]
    assert all("0x1111111111111111111111111111111111111111" in message.lower()
               for message in login_proofs)
    budget_payload_calls = [call for call in result["calls"] if call["path"] == "/api/budget/payload"]
    assert len(budget_payload_calls) == 1
    assert budget_payload_calls[0]["method"] == "POST"
    assert json.loads(budget_payload_calls[0]["body"]) == {}
    assert result["claimSelector"] == "0x4e71d92d"
    assert result["claimBad"] is False
    assert result["noOldWalletRoute"] is True
    assert result["noTokenRoute"] is True
    assert result["afterAccountEvent"]["authenticated"] is False
    assert result["afterAccountEvent"]["purchase_id"] is None
    assert result["afterSessionReset"]["purchase_id"] is None
    assert result["afterSessionReset"]["claim_tx_hash"] is None
    assert result["afterAccountCommerce"] == "暂无订单快照"
    assert result["afterAccountOwner"] == "—"
    assert "personal_sign" not in result["loadProviderMethods"]
    assert "eth_sendTransaction" not in result["loadProviderMethods"]
    mutation_calls = [call for call in result["calls"] if call["method"] == "POST" and call["path"] != "/api/session"]
    assert mutation_calls
    assert all(call["headers"].get("X-Agentonomy-CSRF") for call in mutation_calls)


def test_hosted_wallet_static_files_pin_browser_boundary_and_claim_selector() -> None:
    html = (UI_ROOT / "index.html").read_text()
    css = (UI_ROOT / "app.css").read_text()
    js = UI_SOURCE.read_text()

    assert "Monad Testnet" in html
    assert "这是 Monad 测试网演示，测试资产无货币价值。页面不会索要或保存私钥、助记词。" in html
    assert "HttpOnly" not in html
    assert "CSRF" not in html
    assert "登录钱包" in html
    assert "TestUSD余额" in html
    assert "收款地址" in html
    assert "签名用于授权此页面的演示 Agent；授权后可以查看报价和购买。" in html
    assert "不会获取访问凭证" not in html
    assert "不会获取访问凭证" not in js
    assert "innerHTML" not in js
    assert "localhost" not in html
    assert "localhost" not in js
    assert "hosted Agent" not in html
    assert "hosted Agent" not in js
    assert "localStorage" in js
    assert "access_token" not in js
    assert "refresh_token" not in js
    assert "private_key" not in js
    assert "兼容浏览器钱包" in html
    assert "连接 OKX 钱包" not in html
    assert "eth_sendRawTransaction" not in js
    assert "Authorization" not in js
    assert "Bearer" not in js
    assert "/api/opc/token" not in js
    assert "/api/wallet/" not in js
    assert "CLAIM_SELECTOR" in js and "4e71d92d" in js
    assert "https://testnet.monadexplorer.com/tx/" in js
    assert "app.css" in html and "app.js" in html
    assert "@media" in css


def test_hosted_wallet_reuses_authenticated_owner_and_recovers_previous_claim() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const HASH = '0x' + 'bb'.repeat(32);
const ids = ['connect', 'switch-network', 'logout', 'wallet-verify', 'claim-approve', 'claim-verify', 'claim-hash',
  'grant-verify', 'budget-bind', 'allowance-approve', 'allowance-verify', 'allowance-hash', 'opc-prepare',
  'opc-approve', 'opc-message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id',
  'revoke-prepare', 'revoke-chain', 'revoke-verify', 'revoke-hash', 'csv', 'message', 'phase', 'owner', 'chain',
  'mode', 'token', 'executor', 'payee', 'terms', 'authenticated', 'opc-status', 'claim-status', 'allowance-status',
  'purchase-status', 'core-revoked', 'chain-revoked', 'result', 'commerce', 'tx-links', 'wallet-address', 'wallet-chain',
  'wallet-help'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'complete', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
let account = OWNER;
const providerMethods = [];
const provider = {on() {}, async request({method}) {
  providerMethods.push(method);
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected provider method ' + method);
}};
const calls = [];
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/session') return {ok: true, status: 200, async json() {
    return {csrf_token: 'csrf-existing', authenticated: true, wallet_address: OWNER};
  }};
  if (path === '/api/status') return {ok: true, status: 200, async json() {
    return {authenticated: true, session: {authenticated: true, wallet_address: OWNER}, owner: OWNER,
      wallet_address: OWNER, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
      execution_signer: OWNER, mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
      onboarding: {phase: 'ready'}, opcstatus: {status: 'active', installation_id: 'installation-1'},
      wallet_operations: {claim: {status: 'verified', verified: true, onchain_claimed: true, transaction_hash: HASH}},
      commerce: null};
  }};
  throw new Error('unexpected fetch ' + path);
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {okxwallet: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  const beforeConnect = ui.state();
  await ui.connect();
  const afterConnect = ui.state();
  process.stdout.write(JSON.stringify({beforeConnect, afterConnect, providerMethods, calls}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["beforeConnect"]["authenticated"] is True
    assert result["beforeConnect"]["session_owner"].lower() == "0x" + "11" * 20
    assert result["beforeConnect"]["claim_verified"] is True
    assert result["beforeConnect"]["claim_tx_hash"] == "0x" + "bb" * 32
    assert result["afterConnect"]["authenticated"] is True
    assert result["afterConnect"]["connected"] is True
    assert result["afterConnect"]["account"].lower() == "0x" + "11" * 20
    assert "personal_sign" not in result["providerMethods"]
    assert not any(call["path"] == "/api/faucet/transaction" for call in result["calls"])


def test_hosted_wallet_keeps_same_order_reference_across_timeout_reload_and_owner_switch() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const ids = ['preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'csv', 'message',
  'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated', 'opc-status',
  'purchase-status', 'result', 'commerce', 'tx-links', 'wallet-address', 'wallet-chain'];
const storageData = Object.create(null);
const storage = {
  getItem(key) { return Object.prototype.hasOwnProperty.call(storageData, key) ? storageData[key] : null; },
  setItem(key, value) { storageData[key] = String(value); },
  removeItem(key) { delete storageData[key]; },
};
function response(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return payload; }};
}
function makeDocument() {
  const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); }}]));
  nodes.csv.value = 'transaction_id,date\n1,2026-10-03';
  return {nodes, document: {readyState: 'loading', getElementById(id) { return nodes[id]; },
    createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
    addEventListener() {}}};
}
function makeStatus(owner) {
  return {authenticated: true, session: {authenticated: true, wallet_address: owner}, owner,
    wallet_address: owner, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'active', installation_id: 'installation-1'}, commerce: null};
}
async function boot(owner, {timeoutExecute = false} = {}) {
  const {nodes, document} = makeDocument();
  const calls = [];
  let executeCalls = 0;
  let queryCalls = 0;
  const providerMethods = [];
  const provider = {on() {}, async request({method}) {
    providerMethods.push(method);
    if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [owner];
    if (method === 'eth_chainId') return '0x279f';
    throw new Error('unexpected wallet method ' + method);
  }};
  async function fetch(path, options = {}) {
    calls.push({path, method: options.method || 'GET', body: options.body || null});
    if (path === '/api/session') return response({csrf_token: 'csrf-' + owner.slice(2, 6), authenticated: true, wallet_address: owner});
    if (path === '/api/status') return response(makeStatus(owner));
    if (path === '/api/preview') return response({preview_id: 'preview_abc', idempotency_key: 'id-1'});
    if (path === '/api/execute') {
      executeCalls += 1;
      if (timeoutExecute) throw new Error('simulated timeout');
      return response({purchase_id: 'purchase_abc', preview_id: 'preview_abc', state: 'paid_but_undelivered'});
    }
    if (path === '/api/purchases/purchase_abc') {
      queryCalls += 1;
      return response({purchase_id: 'purchase_abc', preview_id: 'preview_abc', state: 'delivered',
        service_result: {report: 'ok'}});
    }
    throw new Error('unexpected fetch ' + path);
  }
  const context = {console, document, fetch, localStorage: storage, TextEncoder,
    crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider, localStorage: storage}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  return {ui, nodes, calls, providerMethods, provider, executeCalls: () => executeCalls, queryCalls: () => queryCalls};
}
async function main() {
  const first = await boot(OWNER_A, {timeoutExecute: true});
  await first.ui.preview();
  const afterPreview = first.ui.state();
  const previewPurchaseInput = first.nodes['purchase-id'].value;
  await first.ui.execute();
  const afterTimeout = first.ui.state();
  const executeEnabledAfterTimeout = !first.nodes.execute.disabled;
  const storageAfterTimeout = JSON.parse(storage.getItem('agentonomy.hosted.purchase_refs.v1'));
  await first.ui.queryPurchase();
  const afterQuery = first.ui.state();
  const afterQueryCalls = first.queryCalls();
  const executeDisabledAfterQuery = first.nodes.execute.disabled;

  const sameOwnerReload = await boot(OWNER_A);
  const restored = sameOwnerReload.ui.state();
  const otherOwnerReload = await boot(OWNER_B);
  const isolated = otherOwnerReload.ui.state();
  process.stdout.write(JSON.stringify({afterPreview, previewPurchaseInput, afterTimeout, executeEnabledAfterTimeout, storageAfterTimeout, afterQuery,
    executeDisabledAfterQuery, afterQueryCalls,
    executeCalls: first.executeCalls(), restored, isolated,
    restoredPurchaseInput: sameOwnerReload.nodes['purchase-id'].value,
    otherPurchaseInput: otherOwnerReload.nodes['purchase-id'].value,
    restoredProviderMethods: sameOwnerReload.providerMethods,
    otherProviderMethods: otherOwnerReload.providerMethods,
    sameOwnerCalls: sameOwnerReload.calls,
    otherOwnerCalls: otherOwnerReload.calls,
    storageKeys: Object.keys(storageData)}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["afterPreview"]["preview_id"] == "preview_abc"
    assert result["afterPreview"]["purchase_id"] == "purchase_abc"
    assert result["previewPurchaseInput"] == "purchase_abc"
    assert result["afterTimeout"]["purchase_id"] == "purchase_abc"
    assert result["executeEnabledAfterTimeout"] is True
    assert result["storageAfterTimeout"] == {
        "0x1111111111111111111111111111111111111111": {
            "preview_id": "preview_abc",
            "purchase_id": "purchase_abc",
            "idempotency_key": "id-1",
        }
    }
    assert result["afterQuery"]["purchase_state"] == "delivered"
    assert result["executeDisabledAfterQuery"] is True
    assert result["executeCalls"] == 1
    assert result["afterQueryCalls"] == 1
    assert result["restored"]["purchase_id"] == "purchase_abc"
    assert result["restored"]["preview_id"] == "preview_abc"
    assert result["restored"]["idempotency_key"] == "id-1"
    assert result["restoredPurchaseInput"] == "purchase_abc"
    assert result["isolated"]["purchase_id"] is None
    assert result["isolated"]["preview_id"] is None
    assert result["otherPurchaseInput"] == ""
    assert result["restoredProviderMethods"] == []
    assert result["otherProviderMethods"] == []
    assert all(call["path"] not in {"/api/execute", "/api/purchases/purchase_abc", "/api/purchases/purchase_abc/recover"}
               for call in result["sameOwnerCalls"] + result["otherOwnerCalls"])


def test_hosted_wallet_prefers_okx_and_falls_back_to_standard_ethereum_provider() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER = '0x1111111111111111111111111111111111111111';
function response(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return payload; }};
}
function boot({okxwallet, ethereum, globalEthereum = false}) {
  const nodes = {connect: {disabled: false}, 'switch-network': {disabled: false}, message: {textContent: ''},
    authenticated: {textContent: ''}, 'wallet-address': {textContent: ''}, 'wallet-chain': {textContent: ''}};
  const document = {readyState: 'loading', getElementById(id) { return nodes[id]; }, createElement() { return {append() {}}; }, addEventListener() {}};
  const used = [];
  const provider = (label) => ({on() {}, async request({method}) {
    used.push(label + ':' + method);
    if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [OWNER];
    if (method === 'eth_chainId') return '0x279f';
    throw new Error('unexpected wallet method ' + method);
  }});
  const window = {};
  if (okxwallet) window.okxwallet = provider('okx');
  if (ethereum) window.ethereum = provider('ethereum');
  async function fetch(path) {
    if (path === '/api/session') return response({csrf_token: 'csrf', authenticated: false});
    if (path === '/api/status') return response({authenticated: false, onboarding: {phase: 'wallet'}});
    throw new Error('unexpected fetch ' + path);
  }
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window};
  if (globalEthereum) context.ethereum = provider('global');
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  return (async () => { const ui = context.PublicWalletUI; await ui.init(); await ui.connect(); return used; })();
}
async function main() {
  const preferred = await boot({okxwallet: true, ethereum: true});
  const fallback = await boot({okxwallet: false, ethereum: true});
  const globalFallback = await boot({okxwallet: false, ethereum: false, globalEthereum: true});
  process.stdout.write(JSON.stringify({preferred, fallback, globalFallback}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["preferred"]
    assert all(method.startswith("okx:") for method in result["preferred"])
    assert result["fallback"]
    assert all(method.startswith("ethereum:") for method in result["fallback"])
    assert result["globalFallback"]
    assert all(method.startswith("global:") for method in result["globalFallback"])


def test_hosted_wallet_drops_stale_purchase_results_after_account_boundary_and_logout() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const ids = ['preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'csv', 'message',
  'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated', 'opc-status',
  'purchase-status', 'result', 'commerce', 'tx-links', 'wallet-address', 'wallet-chain'];
const storageData = Object.create(null);
const storage = {
  getItem(key) { return Object.prototype.hasOwnProperty.call(storageData, key) ? storageData[key] : null; },
  setItem(key, value) { storageData[key] = String(value); },
  removeItem(key) { delete storageData[key]; },
};
function response(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return payload; }};
}
function deferred() {
  let resolve;
  const promise = new Promise(value => { resolve = value; });
  return {promise, resolve};
}
function makeDocument() {
  const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); }}]));
  nodes.csv.value = 'transaction_id,date\n1,2026-10-03';
  return {nodes, document: {readyState: 'loading', getElementById(id) { return nodes[id]; },
    createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
    addEventListener() {}}};
}
function statusPayload(authenticated) {
  return authenticated ? {authenticated: true, session: {authenticated: true, wallet_address: OWNER_A}, owner: OWNER_A,
    wallet_address: OWNER_A, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'active'}, commerce: null}
    : {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}, commerce: null};
}
async function scenario(operation) {
  const {nodes, document} = makeDocument();
  const pending = deferred();
  const calls = [];
  const listeners = {};
  let authenticated = true;
  let account = OWNER_A;
  let previewCount = 0;
  const provider = {on(name, fn) { listeners[name] = fn; }, async request({method}) {
    if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
    if (method === 'eth_chainId') return '0x279f';
    throw new Error('unexpected provider method ' + method);
  }};
  async function fetch(path, options = {}) {
    calls.push({path, method: options.method || 'GET', body: options.body || null});
    if (path === '/api/session') return response({csrf_token: 'csrf', authenticated});
    if (path === '/api/status') return response(statusPayload(authenticated));
    if (path === '/api/logout') { authenticated = false; return response({status: 'logged_out'}); }
    if (path === '/api/preview') {
      previewCount += 1;
      if (operation === 'preview' && previewCount === 1) return pending.promise;
      return response({preview_id: 'preview_abc'});
    }
    if (path === '/api/execute' || path === '/api/purchases/purchase_abc' || path === '/api/purchases/purchase_abc/recover') {
      if (operation !== 'preview' && !pending.resolved) return pending.promise;
      return response({purchase_id: 'purchase_abc', preview_id: 'preview_abc', state: 'delivered', service_result: {report: 'old'}});
    }
    throw new Error('unexpected fetch ' + path);
  }
  const context = {console, document, fetch, localStorage: storage, TextEncoder,
    crypto: {randomUUID: () => 'id-' + operation}, window: {ethereum: provider, localStorage: storage}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  if (operation === 'preview') {
    const operationPromise = ui.preview();
    await Promise.resolve();
    account = OWNER_B;
    listeners.accountsChanged([OWNER_B]);
    await new Promise(resolve => setTimeout(resolve, 0));
    pending.resolved = true;
    pending.resolve(response({preview_id: 'preview_abc'}));
    await operationPromise;
  } else {
    await ui.preview();
    const operationPromise = operation === 'execute' || operation === 'execute_logout'
      ? ui.execute()
      : operation === 'query' ? ui.queryPurchase() : ui.recoverPurchase();
    await Promise.resolve();
    account = OWNER_B;
    listeners.accountsChanged([OWNER_B]);
    if (operation === 'execute_logout') await ui.logout();
    if (operation !== 'execute_logout') await new Promise(resolve => setTimeout(resolve, 0));
    pending.resolved = true;
    pending.resolve(response({purchase_id: 'purchase_abc', preview_id: 'preview_abc', state: 'delivered', service_result: {report: 'old'}}));
    await operationPromise;
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  const raw = storage.getItem('agentonomy.hosted.purchase_refs.v1');
  return {operation, state: ui.state(), storage: raw ? JSON.parse(raw) : null, calls, account, nodes};
}
async function main() {
  const values = [];
  for (const operation of ['preview', 'execute', 'query', 'recover', 'execute_logout']) {
    values.push(await scenario(operation));
  }
  process.stdout.write(JSON.stringify(values.map(value => ({operation: value.operation, state: value.state,
    storage: value.storage, account: value.account, purchaseText: value.nodes['purchase-status'].textContent,
    resultText: value.nodes.result.textContent,
    stalePurchaseCalls: value.calls.filter(call => call.path === '/api/execute' || call.path.includes('/api/purchases/'))}))));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert [item["operation"] for item in result] == ["preview", "execute", "query", "recover", "execute_logout"]
    for item in result:
        assert item["state"]["authenticated"] is False
        assert item["state"]["preview_id"] is None
        assert item["state"]["purchase_id"] is None
        assert item["purchaseText"] == "尚未创建订单"
        assert item["resultText"] == "请查询／恢复当前订单。"
        if item["operation"] == "preview":
            assert item["stalePurchaseCalls"] == []
        else:
            assert len(item["stalePurchaseCalls"]) == 1
        assert item["storage"] is None or "0x9999999999999999999999999999999999999999" not in item["storage"]


def test_hosted_wallet_drops_stale_opc_signature_before_approve_request() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const deferred = (() => { let resolve; const promise = new Promise(value => { resolve = value; }); return {promise, resolve}; })();
const ids = ['opc-prepare', 'opc-approve', 'opc-message', 'message', 'phase', 'owner', 'chain', 'mode', 'token',
  'executor', 'payee', 'terms', 'authenticated', 'opc-status', 'wallet-address', 'wallet-chain', 'tx-links'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'loading', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
const listeners = {};
const methods = [];
let account = OWNER_A;
let authenticated = true;
let approveCalls = 0;
const provider = {on(name, fn) { listeners[name] = fn; }, async request({method}) {
  methods.push(method);
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  if (method === 'personal_sign') return deferred.promise;
  throw new Error('unexpected provider method ' + method);
}};
function response(payload, status = 200) { return {ok: status >= 200 && status < 300, status, async json() { return payload; }}; }
function statusPayload() {
  return authenticated ? {authenticated: true, session: {authenticated: true, wallet_address: OWNER_A}, owner: OWNER_A,
    wallet_address: OWNER_A, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: '0x4444444444444444444444444444444444444444',
    onboarding: {phase: 'ready'}, opcstatus: {status: 'pending'}}
    : {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}};
}
async function fetch(path) {
  if (path === '/api/session') return response({csrf_token: 'csrf', authenticated});
  if (path === '/api/status') return response(statusPayload());
  if (path === '/api/logout') { authenticated = false; return response({status: 'logged_out'}); }
  if (path === '/api/opc/prepare') return response({session_id: 'opc-1', installation_id: 'inst-1', message_to_sign: 'install'});
  if (path === '/api/opc/approve') { approveCalls += 1; return response({status: 'active', installation_id: 'inst-1'}); }
  throw new Error('unexpected fetch ' + path);
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.prepareOpc();
  const approval = ui.approveOpc();
  await new Promise(resolve => setTimeout(resolve, 0));
  account = OWNER_B;
  listeners.accountsChanged([OWNER_B]);
  deferred.resolve('0x' + '12'.repeat(65));
  await approval;
  await new Promise(resolve => setTimeout(resolve, 0));
  process.stdout.write(JSON.stringify({state: ui.state(), approveCalls, methods}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["approveCalls"] == 0
    assert result["state"]["authenticated"] is False
    assert result["state"]["opc_challenge_id"] is None
    assert result["methods"].count("personal_sign") == 1


def test_hosted_wallet_stale_login_cannot_clear_new_owner_challenge() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const ids = ['connect', 'switch-network', 'logout', 'wallet-verify', 'claim-approve', 'claim-verify', 'claim-hash',
  'grant-verify', 'budget-bind', 'allowance-approve', 'allowance-verify', 'allowance-hash', 'opc-prepare',
  'opc-approve', 'opc-message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'csv',
  'message', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated',
  'opc-status', 'claim-status', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result',
  'commerce', 'tx-links', 'wallet-address', 'wallet-chain'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'loading', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
function response(payload, status = 200) { return {ok: status >= 200 && status < 300, status, async json() { return payload; }}; }
function deferred() { let resolve; const promise = new Promise(value => { resolve = value; }); return {promise, resolve}; }
const signatureA = deferred();
const signatureB = deferred();
const listeners = {};
const calls = [];
let account = OWNER_A;
let authenticated = false;
let challengeCount = 0;
let signCount = 0;
const provider = {on(name, fn) { listeners[name] = fn; }, async request({method}) {
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  if (method === 'personal_sign') {
    signCount += 1;
    return (signCount === 1 ? signatureA : signatureB).promise;
  }
  throw new Error('unexpected wallet method ' + method);
}};
function statusPayload() {
  if (!authenticated) return {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}, commerce: null};
  return {authenticated: true, session: {authenticated: true, wallet_address: account}, owner: account,
    wallet_address: account, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'pending'}, commerce: null};
}
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/session') return response({csrf_token: 'csrf-' + calls.length, authenticated});
  if (path === '/api/status') return response(statusPayload());
  if (path === '/api/logout') { authenticated = false; return response({status: 'logged_out'}); }
  if (path === '/api/login/challenge') {
    challengeCount += 1;
    return response({session_id: 'login-' + challengeCount, message_to_sign: 'Clink Wallet Identity\nWallet Address: ' + account});
  }
  if (path === '/api/login/verify') { authenticated = true; return response({status: 'authorized'}); }
  throw new Error('unexpected fetch ' + path);
}
async function waitFor(predicate) {
  for (let i = 0; i < 20 && !predicate(); i += 1) await new Promise(resolve => setTimeout(resolve, 0));
  if (!predicate()) throw new Error('condition not reached');
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.connect();
  const oldLogin = ui.verifyWallet();
  await waitFor(() => signCount === 1);
  account = OWNER_B;
  listeners.accountsChanged([OWNER_B]);
  await ui.connect();
  const newLogin = ui.verifyWallet();
  await waitFor(() => signCount === 2);
  const newChallenge = ui.state().login_challenge_id;
  signatureA.resolve('0x' + '11'.repeat(65));
  await oldLogin;
  const afterOld = ui.state();
  signatureB.resolve('0x' + '22'.repeat(65));
  await newLogin;
  process.stdout.write(JSON.stringify({newChallenge, afterOld, final: ui.state(), calls}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["newChallenge"] == "login-2"
    assert result["afterOld"]["login_challenge_id"] == "login-2"
    assert result["final"]["authenticated"] is True
    assert result["final"]["session_owner"].lower() == "0x" + "99" * 20


def test_hosted_wallet_stale_claim_status_cannot_replace_new_owner_message() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const CLAIM = '0x5555555555555555555555555555555555555555';
const ids = ['connect', 'switch-network', 'logout', 'wallet-verify', 'claim-approve', 'claim-verify', 'claim-hash',
  'grant-verify', 'budget-bind', 'allowance-approve', 'allowance-verify', 'allowance-hash', 'opc-prepare',
  'opc-approve', 'opc-message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'csv',
  'message', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated',
  'opc-status', 'claim-status', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result',
  'commerce', 'tx-links', 'wallet-address', 'wallet-chain'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'loading', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
function response(payload, status = 200) { return {ok: status >= 200 && status < 300, status, async json() { return payload; }}; }
function deferred() { let resolve; const promise = new Promise(value => { resolve = value; }); return {promise, resolve}; }
const pendingStatus = deferred();
const listeners = {};
const calls = [];
let account = OWNER_A;
let authenticated = true;
let deferClaimStatus = false;
let claimStatusStarted = false;
const provider = {on(name, fn) { listeners[name] = fn; }, async request({method}) {
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected wallet method ' + method);
}};
function statusPayload() {
  if (!authenticated) return {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}, commerce: null};
  return {authenticated: true, session: {authenticated: true, wallet_address: account}, owner: account,
    wallet_address: account, chain_id: 10143, token: TOKEN, claim_contract: CLAIM, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'wallet'}, opcstatus: {status: 'pending'}, commerce: null};
}
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/session') return response({csrf_token: 'csrf-' + calls.length, authenticated});
  if (path === '/api/status') {
    if (deferClaimStatus) { deferClaimStatus = false; claimStatusStarted = true; return pendingStatus.promise; }
    return response(statusPayload());
  }
  if (path === '/api/logout') { authenticated = false; return response({status: 'logged_out'}); }
  if (path === '/api/faucet/transaction') return response({status: 'busy'}, 409);
  throw new Error('unexpected fetch ' + path);
}
async function waitFor(predicate) {
  for (let i = 0; i < 20 && !predicate(); i += 1) await new Promise(resolve => setTimeout(resolve, 0));
  if (!predicate()) throw new Error('condition not reached');
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  await ui.connect();
  deferClaimStatus = true;
  const claim = ui.claimTestUsd();
  await waitFor(() => claimStatusStarted);
  account = OWNER_B;
  listeners.accountsChanged([OWNER_B]);
  const reconnect = ui.connect();
  await reconnect;
  const newOwnerMessage = ui.state().message;
  pendingStatus.resolve(response(statusPayload()));
  await claim;
  process.stdout.write(JSON.stringify({newOwnerMessage, state: ui.state(), calls}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["newOwnerMessage"] == "钱包已连接。请按当前阶段逐步完成授权。"
    assert result["state"]["message"] == result["newOwnerMessage"]
    assert result["state"]["authenticated"] is False
    assert result["state"]["account"].lower() == "0x" + "99" * 20


def test_hosted_wallet_serializes_delayed_session_cookie_before_owner_switch() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const ids = ['connect', 'switch-network', 'logout', 'wallet-verify', 'claim-approve', 'claim-verify', 'claim-hash',
  'grant-verify', 'budget-bind', 'allowance-approve', 'allowance-verify', 'allowance-hash', 'opc-prepare',
  'opc-approve', 'opc-message', 'preview', 'execute', 'purchase-query', 'purchase-recover', 'purchase-id', 'csv',
  'message', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms', 'authenticated',
  'opc-status', 'claim-status', 'allowance-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result',
  'commerce', 'tx-links', 'wallet-address', 'wallet-chain'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'loading', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
function response(payload, status = 200) { return {ok: status >= 200 && status < 300, status, async json() { return payload; }}; }
let resolveInitialSession;
const initialSession = new Promise(resolve => { resolveInitialSession = resolve; });
const listeners = {};
const calls = [];
let cookieJar = 'A';
let revokedA = false;
let account = OWNER_A;
let sessionCalls = 0;
let logoutCalls = 0;
const provider = {on(name, fn) { listeners[name] = fn; }, async request({method, params}) {
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  if (method === 'personal_sign') return '0x' + '11'.repeat(65);
  throw new Error('unexpected wallet method ' + method + ' ' + JSON.stringify(params || []));
}};
function sessionPayload() {
  if (cookieJar === 'A' && !revokedA) return {csrf_token: 'csrf-A', authenticated: true, wallet_address: OWNER_A, owner: OWNER_A};
  if (cookieJar === 'B') return {csrf_token: 'csrf-B', authenticated: true, wallet_address: OWNER_B, owner: OWNER_B};
  return {csrf_token: 'csrf-anon', authenticated: false};
}
function statusPayload() {
  if (cookieJar === 'A' && !revokedA) return {authenticated: true, session: {authenticated: true, wallet_address: OWNER_A},
    owner: OWNER_A, wallet_address: OWNER_A, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'pending'}, commerce: {purchase_id: 'purchase-A', state: 'delivered'}};
  if (cookieJar === 'B') return {authenticated: true, session: {authenticated: true, wallet_address: OWNER_B},
    owner: OWNER_B, wallet_address: OWNER_B, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'pending'}, commerce: null};
  return {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}, commerce: null};
}
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET', cookie: cookieJar,
    csrf: options.headers && options.headers['X-Agentonomy-CSRF'] || null});
  if (path === '/api/session') {
    sessionCalls += 1;
    if (sessionCalls === 1) return initialSession;
    return response(sessionPayload());
  }
  if (path === '/api/logout') {
    logoutCalls += 1;
    if (cookieJar !== 'A' || options.headers['X-Agentonomy-CSRF'] !== 'csrf-A') return response({detail: 'cookie mismatch'}, 403);
    revokedA = true;
    cookieJar = 'anonymous';
    return response({status: 'logged_out'});
  }
  if (path === '/api/status') return response(statusPayload());
  if (path === '/api/login/challenge') return response({session_id: 'login-B', message_to_sign: 'Clink Wallet Identity\nWallet Address: ' + account});
  if (path === '/api/login/verify') { cookieJar = 'B'; return response({status: 'authorized'}); }
  throw new Error('unexpected fetch ' + path);
}
async function waitFor(predicate) {
  for (let i = 0; i < 30 && !predicate(); i += 1) await new Promise(resolve => setTimeout(resolve, 0));
  if (!predicate()) throw new Error('condition not reached');
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  const initPromise = ui.init();
  await waitFor(() => sessionCalls === 1);
  account = OWNER_B;
  listeners.accountsChanged([OWNER_B]);
  const reconnect = ui.connect();
  await new Promise(resolve => setTimeout(resolve, 0));
  const beforeInitialSession = {state: ui.state(), calls: calls.slice(), sessionCalls, logoutCalls};
  resolveInitialSession(response(sessionPayload()));
  await initPromise;
  const connected = await reconnect;
  const afterReset = {state: ui.state(), cookieJar, revokedA, calls: calls.slice(), sessionCalls, logoutCalls,
    ownerText: nodes.owner.textContent, commerceText: nodes.commerce.textContent};
  await ui.verifyWallet();
  process.stdout.write(JSON.stringify({connected, beforeInitialSession, afterReset, final: ui.state(), cookieJar,
    revokedA, calls, sessionCalls, logoutCalls, ownerText: nodes.owner.textContent}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["beforeInitialSession"]["sessionCalls"] == 1
    assert result["beforeInitialSession"]["logoutCalls"] == 0
    assert [call["path"] for call in result["beforeInitialSession"]["calls"]] == ["/api/session"]
    assert result["connected"] is True
    assert result["afterReset"]["revokedA"] is True
    assert result["afterReset"]["cookieJar"] == "anonymous"
    assert result["afterReset"]["logoutCalls"] == 1
    assert result["afterReset"]["state"]["authenticated"] is False
    assert result["afterReset"]["state"]["session_owner"] is None
    assert result["afterReset"]["state"]["purchase_id"] is None
    assert result["afterReset"]["ownerText"] == "—"
    assert result["afterReset"]["commerceText"] == "暂无订单快照"
    assert all(not (call["path"] == "/api/status" and call["cookie"] == "A") for call in result["afterReset"]["calls"])
    assert result["final"]["authenticated"] is True
    assert result["final"]["session_owner"].lower() == "0x" + "99" * 20
    assert result["cookieJar"] == "B"


def test_hosted_wallet_waits_for_boundary_reset_before_reconnecting_new_owner() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const OWNER_A = '0x1111111111111111111111111111111111111111';
const OWNER_B = '0x9999999999999999999999999999999999999999';
const TOKEN = '0x2222222222222222222222222222222222222222';
const EXECUTOR = '0x3333333333333333333333333333333333333333';
const PAYEE = '0x4444444444444444444444444444444444444444';
const ids = ['connect', 'switch-network', 'logout', 'preview', 'execute', 'purchase-query', 'purchase-recover',
  'purchase-id', 'csv', 'message', 'phase', 'owner', 'chain', 'mode', 'token', 'executor', 'payee', 'terms',
  'authenticated', 'opc-status', 'purchase-status', 'core-revoked', 'chain-revoked', 'result', 'commerce',
  'tx-links', 'wallet-address', 'wallet-chain'];
const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', disabled: false, children: [],
  replaceChildren(...children) { this.children = children; }, append(...children) { this.children.push(...children); }}]));
const document = {readyState: 'loading', getElementById(id) { return nodes[id]; },
  createElement(tag) { return {tag, textContent: '', href: '', target: '', rel: '', children: [], append(...children) { this.children.push(...children); }}; },
  addEventListener() {}};
function response(payload, status = 200) { return {ok: status >= 200 && status < 300, status, async json() { return payload; }}; }
function deferred() { let resolve; const promise = new Promise(value => { resolve = value; }); return {promise, resolve}; }
const logoutResponse = deferred();
const listeners = {};
const calls = [];
let account = OWNER_A;
let authenticated = true;
let statusCalls = 0;
const provider = {on(name, fn) { listeners[name] = fn; }, async request({method}) {
  if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [account];
  if (method === 'eth_chainId') return '0x279f';
  throw new Error('unexpected wallet method ' + method);
}};
function statusPayload() {
  if (!authenticated) return {authenticated: false, session: {authenticated: false}, onboarding: {phase: 'wallet'}, commerce: null};
  return {authenticated: true, session: {authenticated: true, wallet_address: OWNER_A}, owner: OWNER_A,
    wallet_address: OWNER_A, chain_id: 10143, token: TOKEN, executor: EXECUTOR, payee: PAYEE,
    mode: 'monad_testnet', terms: {total: '1.00', per_payment: '0.50', price: '0.30'},
    onboarding: {phase: 'ready'}, opcstatus: {status: 'active'},
    commerce: {purchase_id: 'purchase_a', state: 'paid_but_undelivered'}};
}
async function fetch(path, options = {}) {
  calls.push({path, method: options.method || 'GET'});
  if (path === '/api/session') return response({csrf_token: 'csrf-' + calls.length, authenticated});
  if (path === '/api/status') { statusCalls += 1; return response(statusPayload()); }
  if (path === '/api/logout') return logoutResponse.promise;
  throw new Error('unexpected fetch ' + path);
}
async function main() {
  const context = {console, document, fetch, TextEncoder, crypto: {randomUUID: () => 'id-1'}, window: {ethereum: provider}};
  vm.runInNewContext(source, context, {filename: 'hosted-web/app.js'});
  const ui = context.PublicWalletUI;
  await ui.init();
  account = OWNER_B;
  listeners.accountsChanged([OWNER_B]);
  const reconnect = ui.connect();
  await new Promise(resolve => setTimeout(resolve, 0));
  const beforeRelease = {state: ui.state(), calls: calls.slice(), statusCalls};
  authenticated = false;
  logoutResponse.resolve(response({status: 'logged_out'}));
  const connected = await reconnect;
  await new Promise(resolve => setTimeout(resolve, 0));
  process.stdout.write(JSON.stringify({beforeRelease, connected, state: ui.state(), calls, statusCalls,
    ownerText: nodes.owner.textContent, commerceText: nodes.commerce.textContent}));
}
main().catch(error => { console.error(error); process.exit(1); });
'''
    )

    assert result["beforeRelease"]["state"]["authenticated"] is False
    assert result["beforeRelease"]["state"]["purchase_id"] is None
    assert result["beforeRelease"]["statusCalls"] == 1
    assert [call["path"] for call in result["beforeRelease"]["calls"]] == [
        "/api/session", "/api/status", "/api/session", "/api/logout",
    ]
    assert result["connected"] is True
    assert result["state"]["connected"] is True
    assert result["state"]["account"].lower() == "0x" + "99" * 20
    assert result["state"]["authenticated"] is False
    assert result["state"]["session_owner"] is None
    assert result["state"]["purchase_id"] is None
    assert result["ownerText"] == "—"
    assert result["commerceText"] == "暂无订单快照"
