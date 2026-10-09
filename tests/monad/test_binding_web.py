"""Contract tests for the standalone hosted wallet binding page.

The page is deliberately exercised in a small Node VM instead of a browser.  This
keeps the tests independent of a wallet extension while still checking the
EIP-6963 boundary, request ordering, CSRF headers, and transaction recovery.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "examples" / "monad_commerce" / "hosted_web"
SOURCE = UI_ROOT / "binding.js"
WALLET_SOURCE = UI_ROOT / "wallet_selection.js"
HTML_SOURCE = UI_ROOT / "binding.html"


def _run_node(body: str) -> dict:
    program = r"""
const fs = require("fs");
const vm = require("vm");
const bindingSource = fs.readFileSync(process.argv[2], "utf8");
const walletSource = fs.readFileSync(process.argv[3], "utf8");

function fail(message) { throw new Error(message); }
function equal(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail(label + " expected " + JSON.stringify(expected) + " got " + JSON.stringify(actual));
  }
}
function ok(value, label) { if (!value) fail(label); }
function response(value, status = 200) {
  return { ok: status >= 200 && status < 300, status,
    async json() { return value; } };
}
class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatchEvent(event) {
    for (const listener of [...(this.listeners.get(event.type) || [])]) listener(event);
    return true;
  }
}
class Node {
  constructor(id = "") {
    this.id = id; this.value = ""; this.textContent = ""; this.innerHTML = "";
    this.disabled = false; this.hidden = false; this.checked = false; this.children = [];
    this.listeners = new Map(); this.dataset = {};
  }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatchEvent(event) { for (const listener of [...(this.listeners.get(event.type) || [])]) listener(event); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
}
function makeDocument() {
  const elements = new Map();
  const ids = [
    "message", "wallet-list", "account-list", "network-select", "wallet-state",
    "auth-state", "phase-state", "budget-state", "device-state", "revoke-state",
    "owner", "chain", "token", "terms-state", "device-list", "wallet-empty", "advanced-details", "claim-hash", "allowance-hash",
    "revoke-hash", "login", "grant", "budget", "claim", "allowance", "revoke",
    "claim-verify", "allowance-verify", "revoke-verify", "device-refresh", "device-claim",
    "device-approve", "logout",
  ];
  for (const id of ids) elements.set(id, new Node(id));
  const listeners = new Map();
  return {
    readyState: "complete",
    getElementById(id) { return elements.get(id) || null; },
    createElement() { return new Node(); },
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) {
      for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
      return true;
    },
    listeners,
    elements,
  };
}
function makeProvider(accounts, chainId) {
  const listeners = new Map();
  const provider = {
    calls: [],
    currentAccounts: accounts.slice(), currentChain: chainId,
    on(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeListener(type, listener) { listeners.get(type)?.delete(listener); },
    emit(type, value) { for (const listener of [...(listeners.get(type) || [])]) listener(value); },
    request({method, params}) {
      this.calls.push({method, params});
      if (method === "eth_requestAccounts") return Promise.resolve(this.currentAccounts.slice());
      if (method === "eth_chainId") return Promise.resolve(this.currentChain);
      if (method === "personal_sign") return Promise.resolve("0x" + "11".repeat(65));
      if (method === "eth_signTypedData_v4") return Promise.resolve("0x" + "22".repeat(65));
      if (method === "eth_sendTransaction") return Promise.resolve("0x" + "aa".repeat(32));
      return Promise.reject(new Error("unexpected RPC " + method));
    },
  };
  return provider;
}
function makeConfig(overrides = {}) {
  return { networks: [{
    chain_id: 777, network: "configured-network", token: "0x" + "ab".repeat(20),
    executor: "0x" + "cd".repeat(20), payee: "0x" + "ef".repeat(20),
    token_symbol: "UNIT", token_decimals: 18, test_asset: true,
    terms: { claim_atomic: "123456789012345678", total_atomic: "987654321000000000",
      per_payment_atomic: "123456789000000000", validity_seconds: 3600 },
    wallet_transaction: { max_gas: "210000", max_gas_price_wei: "1230000000" },
    budget_domain: { name: "Configured Budget", version: "7" },
    ...overrides,
  }] };
}
function makeHarness({config = makeConfig(), providers = [], fetchImpl, location = "/", pollIntervalMs = 5,
  readyState = "complete", initOptions = {session: false}} = {}) {
  const target = new Target();
  const document = makeDocument();
  document.readyState = readyState;
  const context = {
    console, TextEncoder, TextDecoder, setTimeout, clearTimeout,
    document, location: {pathname: location, search: ""}, fetch: fetchImpl,
    __AgentonomyBindingInitOptions: initOptions,
    Event: class { constructor(type) { this.type = type; } },
  };
  context.globalThis = context;
  for (const method of ["addEventListener", "removeEventListener", "dispatchEvent"]) {
    context[method] = target[method].bind(target);
  }
  target.addEventListener("eip6963:requestProvider", () => {
    for (const entry of providers) target.dispatchEvent({
      type: "eip6963:announceProvider", detail: entry,
    });
  });
  vm.runInNewContext(walletSource, context, {filename: "wallet_selection.js"});
  vm.runInNewContext(bindingSource, context, {filename: "binding.js"});
  const harness = {context, target, document, ui: context.AgentonomyBindingInstance || null,
    providers, config, pollIntervalMs};
  harness.waitReady = async () => {
    if (context.AgentonomyBindingReady) await context.AgentonomyBindingReady;
    harness.ui = context.AgentonomyBindingInstance || harness.ui;
    return harness.ui;
  };
  return harness;
}
function providerEntry(uuid, name, provider) {
  return {info: {uuid, name, rdns: "com.example." + uuid}, provider};
}
async function boot(harness) {
  await harness.waitReady();
}
async function main() {
""" + body + r"""
}
main().then(() => process.stdout.write(JSON.stringify({ok: true})))
  .catch((error) => { process.stderr.write((error && error.stack) || String(error)); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-", str(SOURCE), str(WALLET_SOURCE)],
        input=program,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(
            f"node scenario failed ({completed.returncode}):\n{completed.stderr}\n{completed.stdout}"
        )
    return json.loads(completed.stdout)


def test_two_wallets_require_explicit_second_account() -> None:
    _run_node(r'''
  const one = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const two = makeProvider([
    "0x" + "22".repeat(20), "0x" + "33".repeat(20), "0x" + "22".repeat(20),
  ], "0x309");
  const fetchImpl = async (url) => url === "/wallet-config.json" ? response(makeConfig()) : response({});
  const h = makeHarness({providers: [
    providerEntry("one", "Wallet One", one), providerEntry("two", "Wallet Two", two),
  ], fetchImpl});
  await boot(h);
  equal(h.ui.wallets().map((wallet) => wallet.uuid), ["one", "two"], "wallets");
  equal(h.ui.state().account, null, "no account is auto-selected");
  await h.ui.selectProvider("two");
  equal(h.ui.state().accounts, ["0x" + "22".repeat(20), "0x" + "33".repeat(20)], "all accounts");
  equal(h.ui.state().account, null, "provider selection does not choose account");
  await h.ui.selectAccount("0x" + "33".repeat(20));
  equal(h.ui.state().account, "0x" + "33".repeat(20), "second account");
  equal(two.calls.filter((call) => call.method === "eth_requestAccounts").length, 1, "explicit account RPC");
''');


def test_configured_network_values_are_dynamic() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const config = makeConfig();
  const fetchImpl = async (url) => url === "/wallet-config.json" ? response(config) : response({});
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  const state = h.ui.state();
  equal(state.network.chain_id, 777, "configured chain");
  equal(state.network.token_decimals, 18, "configured decimals");
  equal(state.network.terms.claim_atomic, "123456789012345678", "configured claim terms");
  equal(state.network.budget_domain.name, "Configured Budget", "configured budget domain");
''');


def test_chain_mismatch_does_not_sign_or_send() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x378");
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: false});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  let rejected = false;
  try { await h.ui.login(); } catch (_error) { rejected = true; }
  ok(rejected, "mismatch rejects login");
  ok(!provider.calls.some((call) => ["personal_sign", "eth_signTypedData_v4", "eth_sendTransaction"].includes(call.method)), "no sign or send");
''');


def test_stale_account_after_await_cannot_sign_or_verify() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  let releaseChallenge;
  const challenge = new Promise((resolve) => { releaseChallenge = resolve; });
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: false});
    if (url === "/api/login/challenge") return challenge;
    if (url === "/api/login/verify") return response({authenticated: true});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  const pending = h.ui.login();
  await Promise.resolve();
  provider.emit("accountsChanged", []);
  releaseChallenge(response({challenge_id: "c1", message_to_sign: "sign me"}));
  let rejected = false;
  try { await pending; } catch (_error) { rejected = true; }
  ok(rejected, "stale login rejects");
  ok(!provider.calls.some((call) => ["personal_sign", "eth_signTypedData_v4", "eth_sendTransaction"].includes(call.method)), "stale login cannot sign");
  ok(!calls.some((call) => call.url === "/api/login/verify"), "stale login cannot verify");
''');


def test_login_sends_session_csrf_on_mutations() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf-token", authenticated: false});
    if (url === "/api/login/challenge") return response({challenge_id: "c1", message_to_sign: "Agentonomy login\nOrigin: https://review.agentonomy.xyz\nNonce: c1"});
    if (url === "/api/login/verify") return response({authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/grant/challenge") return response({challenge_id: "g1", message_to_sign: "Agentonomy grant\nChain: 777\nNonce: g1"});
    if (url === "/api/grant/verify") return response({status: "active"});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "wallet"}});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.login();
  await h.ui.grant();
  const mutated = calls.filter((call) => call.url.startsWith("/api/login/"));
  equal(mutated.length, 2, "login mutation count");
  for (const call of mutated) equal(call.options.headers["X-Agentonomy-CSRF"], "csrf-token", "login CSRF");
  ok(provider.calls.some((call) => call.method === "personal_sign"), "login signs only after checks");
''');


def test_device_claim_then_challenge_approval_is_explicit() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const calls = [];
  let statusReads = 0;
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf-token", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready"}});
    if (url.startsWith("/api/opc/external?")) {
      statusReads += 1;
      if (statusReads === 1) return response({status: "pending", installation_id: "i1", label: "device"});
      return response({status: "pending", installation_id: "i1", label: "device", challenge: {session_id: "s1", message_to_sign: "Agentonomy OPC approval\nRequest: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nNonce: s1", expires_at: "2099-01-01T00:00:00Z"}});
    }
    if (url === "/api/opc/external/claim") return response({status: "claimed", installation_id: "i1", label: "device"});
    if (url === "/api/opc/external/approve") return response({status: "active", installation_id: "i1", label: "device"});
    return response({});
  };
  const h = makeHarness({
    providers: [providerEntry("one", "Wallet One", provider)], fetchImpl,
    location: "/account/" + "a".repeat(43), pollIntervalMs: 1,
  });
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.refreshDevice();
  const beforeClaim = calls.filter((call) => call.url === "/api/opc/external/claim").length;
  equal(beforeClaim, 0, "claim is not automatic");
  await h.ui.claimDevice();
  equal(calls.filter((call) => call.url === "/api/opc/external/claim").length, 1, "one claim");
  await h.ui.refreshDevice();
  await h.ui.approveDevice();
  const approve = calls.find((call) => call.url === "/api/opc/external/approve");
  ok(approve, "approve request");
  equal(JSON.parse(approve.options.body).request, "a".repeat(43), "opaque request only");
  ok(provider.calls.some((call) => call.method === "personal_sign"), "approval signature");
''');


def test_known_transaction_hash_is_reused_after_verification_failure() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const calls = [];
  let verifyCount = 0;
  const tx = {from: "0x" + "11".repeat(20), to: "0x" + "ab".repeat(20), value: "0x0",
    data: "0x4e71d92d", chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80"};
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf-token", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready"}});
    if (url === "/api/faucet/transaction") return response({transaction: tx, amount_atomic: "123456789012345678"});
    if (url === "/api/faucet/verify") {
      verifyCount += 1;
      return response({status: "pending", transaction_hash: JSON.parse(options.body).transaction_hash}, verifyCount === 1 ? 409 : 200);
    }
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.claimAllowance();
  await h.ui.verifyAllowance();
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 1, "one send");
  equal(calls.filter((call) => call.url === "/api/faucet/verify").length, 2, "two verification attempts");
  equal(JSON.parse(calls.filter((call) => call.url === "/api/faucet/verify")[0].options.body).transaction_hash,
    JSON.parse(calls.filter((call) => call.url === "/api/faucet/verify")[1].options.body).transaction_hash, "same hash recovery");
''');


def test_real_script_bootstraps_after_dom_ready_once() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const fetchImpl = async (url) => url === "/wallet-config.json" ? response(makeConfig()) : response({});
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl, readyState: "loading"});
  ok(!h.ui, "binding waits for DOM ready");
  h.document.dispatchEvent({type: "DOMContentLoaded"});
  await h.waitReady();
  ok(h.ui, "binding starts from the page script");
  equal(h.document.listeners.get("DOMContentLoaded").size, 1, "one DOM ready listener");
  equal(h.document.getElementById("login").listeners.get("click").size, 1, "one login handler");
''');


def test_dom_clicks_are_mutually_exclusive() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const originalRequest = provider.request;
  let releaseSignature;
  const signatureGate = new Promise((resolve) => { releaseSignature = resolve; });
  provider.request = function(request) {
    if (request.method === "personal_sign") {
      this.calls.push({method: request.method, params: request.params});
      return signatureGate;
    }
    return originalRequest.call(this, request);
  };
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: false});
    if (url === "/api/login/challenge") return response({challenge_id: "c1", message_to_sign: "login\nnonce: c1"});
    if (url === "/api/login/verify") return response({authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready"}});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  const login = h.document.getElementById("login");
  login.dispatchEvent({type: "click"});
  login.dispatchEvent({type: "click"});
  for (let i = 0; i < 20 && provider.calls.filter((call) => call.method === "personal_sign").length < 1; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  equal(provider.calls.filter((call) => call.method === "personal_sign").length, 1, "one signing RPC");
  releaseSignature("0x" + "11".repeat(65));
  await new Promise((resolve) => setTimeout(resolve, 5));
  equal(calls.filter((call) => call.url === "/api/login/verify").length, 1, "one verification mutation");
''');


def test_status_wallet_operations_hydrate_existing_hashes_and_verify_only() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const claimHash = "0x" + "aa".repeat(32);
  const allowanceHash = "0x" + "bb".repeat(32);
  const revokeHash = "0x" + "cc".repeat(32);
  const calls = [];
  const status = {authenticated: true, owner: "0x" + "11".repeat(20), terms: {total: "9.87", per_payment: "1.23"}, devices: [{label: "Codex Agent", status: "active", installation_id: "i1"}], onboarding: {phase: "ready", chain_grant_id: "0x" + "dd".repeat(32), grant_expires_at: "2099-01-01T00:00:00Z"},
    wallet_operations: {
      claim: {status: "pending", transaction_hash: claimHash},
      approval: {status: "pending", transaction_hash: allowanceHash},
      revocation: {status: "pending", transaction_hash: revokeHash},
    }};
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response(status);
    if (url === "/api/faucet/verify") return response({status: "verified"});
    if (url === "/api/allowance/verify") return response({status: "verified"});
    if (url === "/api/revoke/verify") return response({status: "verified"});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl, initOptions: {session: true}});
  await boot(h);
  equal(h.ui.state().claimHash, claimHash.toLowerCase(), "claim hash hydrated");
  equal(h.ui.state().allowanceHash, allowanceHash.toLowerCase(), "allowance hash hydrated");
  equal(h.ui.state().revokeHash, revokeHash.toLowerCase(), "revoke hash hydrated");
  equal(h.ui.state().devices[0].label, "Codex Agent", "bound device hydrated");
  ok(h.document.getElementById("terms-state").textContent.includes("expires 2099"), "terms expiry rendered");
  equal(h.document.getElementById("claim-hash").value, claimHash.toLowerCase(), "claim input hydrated");
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  for (const id of ["claim-verify", "allowance-verify", "revoke-verify"]) {
    h.document.getElementById(id).dispatchEvent({type: "click"});
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  equal(calls.filter((call) => call.url.endsWith("/verify")).length, 3, "three existing-hash verifications");
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 0, "hydrated hashes never resend");
  for (const call of calls.filter((item) => item.url.endsWith("/verify"))) {
    equal(call.options.headers["X-Agentonomy-CSRF"], "csrf", "verification CSRF");
  }
''');


def test_claim_status_refresh_makes_revoke_use_current_chain_grant() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const grantId = "0x" + "12".repeat(32);
  const claimTx = {from: "0x" + "11".repeat(20), to: "0x" + "ab".repeat(20), value: "0x0",
    data: "0x4e71d92d", chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80"};
  const revokeTx = {from: "0x" + "11".repeat(20), to: "0x" + "cd".repeat(20), value: "0x0",
    data: "0xb75c7dc6" + grantId.slice(2), chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80"};
  let statusReads = 0;
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") {
      statusReads += 1;
      return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready", ...(statusReads >= 1 ? {chain_grant_id: grantId} : {})}, wallet_operations: {}});
    }
    if (url === "/api/faucet/transaction") return response({transaction: claimTx, amount_atomic: "123456789012345678"});
    if (url === "/api/faucet/verify") return response({status: "verified", transaction_hash: "0x" + "aa".repeat(32)});
    if (url === "/api/revoke/prepare") return response({transaction: revokeTx, chain_grant_id: grantId});
    if (url === "/api/revoke/verify") return response({status: "revoked", transaction_hash: "0x" + "bb".repeat(32)});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.claimAllowance();
  await h.ui.revoke();
  equal(statusReads >= 2, true, "status refreshed before revoke");
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 2, "claim and revoke sent");
  equal(calls.filter((call) => call.url === "/api/revoke/verify").length, 1, "revoke verified");
''');


def test_stale_send_preserves_hash_for_original_owner_without_verifying_stale_session() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const originalRequest = provider.request;
  let releaseSend;
  const sendGate = new Promise((resolve) => { releaseSend = resolve; });
  provider.request = function(request) {
    if (request.method === "eth_sendTransaction") {
      this.calls.push({method: request.method, params: request.params});
      return sendGate;
    }
    return originalRequest.call(this, request);
  };
  const claimHash = "0x" + "ee".repeat(32);
  const claimTx = {from: "0x" + "11".repeat(20), to: "0x" + "ab".repeat(20), value: "0x0",
    data: "0x4e71d92d", chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80"};
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready"}, wallet_operations: {}});
    if (url === "/api/faucet/transaction") return response({transaction: claimTx, amount_atomic: "123456789012345678"});
    if (url === "/api/faucet/verify") return response({status: "verified", transaction_hash: claimHash});
    if (url === "/api/login/challenge") return response({challenge_id: "c1", message_to_sign: "login\nnonce"});
    if (url === "/api/login/verify") return response({authenticated: true, owner: "0x" + "11".repeat(20)});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  const pending = h.ui.claimAllowance();
  for (let i = 0; i < 20 && !provider.calls.some((call) => call.method === "eth_sendTransaction"); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  provider.currentAccounts = [];
  provider.emit("accountsChanged", []);
  releaseSend(claimHash);
  let stale = false;
  try { await pending; } catch (_error) { stale = true; }
  ok(stale, "stale send rejects its old session");
  equal(calls.filter((call) => call.url === "/api/faucet/verify").length, 0, "stale hash is not verified");
  provider.currentAccounts = ["0x" + "11".repeat(20)];
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.login();
  equal(h.ui.state().claimHash, claimHash.toLowerCase(), "original owner recovers sent hash");
  await h.ui.claimAllowance();
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 1, "recovery never resends");
  equal(calls.filter((call) => call.url === "/api/faucet/verify").length, 1, "recovery verifies once");
''');


def test_revoke_prepare_existing_hash_is_verified_without_resend() -> None:
    _run_node(r'''
  const provider = makeProvider(["0x" + "11".repeat(20)], "0x309");
  const revokeHash = "0x" + "fa".repeat(32);
  const grantId = "0x" + "12".repeat(32);
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: "0x" + "11".repeat(20)});
    if (url === "/api/status") return response({authenticated: true, owner: "0x" + "11".repeat(20), onboarding: {phase: "ready", chain_grant_id: grantId}, wallet_operations: {}});
    if (url === "/api/revoke/prepare") return response({transaction_hash: revokeHash, chain_grant_id: grantId, status: "pending"});
    if (url === "/api/revoke/verify") return response({status: "verified", transaction_hash: revokeHash});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount("0x" + "11".repeat(20));
  h.ui.selectNetwork(777);
  await h.ui.revoke();
  equal(calls.filter((call) => call.url === "/api/revoke/prepare").length, 1, "one revoke prepare");
  equal(calls.filter((call) => call.url === "/api/revoke/verify").length, 1, "existing revoke verified");
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 0, "existing revoke never resends");
''');


def test_recovery_hashes_are_scoped_by_owner_chain_and_kind() -> None:
    _run_node(r'''
  const accountA = "0x" + "11".repeat(20);
  const accountB = "0x" + "22".repeat(20);
  const providerA = makeProvider([accountA], "0x309");
  const providerB = makeProvider([accountB], "0x309");
  const originalA = providerA.request;
  const hashA = "0x" + "aa".repeat(32);
  const hashB = "0x" + "bb".repeat(32);
  let releaseA;
  let deferA = true;
  const gateA = new Promise((resolve) => { releaseA = resolve; });
  providerA.request = function(request) {
    if (request.method === "eth_sendTransaction" && deferA) {
      this.calls.push({method: request.method, params: request.params});
      return gateA;
    }
    if (request.method === "eth_sendTransaction") {
      this.calls.push({method: request.method, params: request.params});
      return Promise.resolve(hashA);
    }
    return originalA.call(this, request);
  };
  const originalB = providerB.request;
  providerB.request = function(request) {
    if (request.method === "eth_sendTransaction") {
      this.calls.push({method: request.method, params: request.params});
      return Promise.resolve(hashB);
    }
    return originalB.call(this, request);
  };
  let currentOwner = accountA;
  let transactionOwner = accountA;
  let transactionHash = hashA;
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: currentOwner});
    if (url === "/api/status") return response({authenticated: true, owner: currentOwner, onboarding: {phase: "ready"}, wallet_operations: {}});
    if (url === "/api/faucet/transaction") return response({transaction: {
      from: transactionOwner, to: "0x" + "ab".repeat(20), value: "0x0", data: "0x4e71d92d",
      chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80",
    }, amount_atomic: "123456789012345678"});
    if (url === "/api/faucet/verify") return response({status: "verified", transaction_hash: transactionHash});
    if (url === "/api/login/challenge") return response({challenge_id: "c1", message_to_sign: "login\nnonce"});
    if (url === "/api/login/verify") return response({authenticated: true, owner: currentOwner});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("a", "Wallet A", providerA), providerEntry("b", "Wallet B", providerB)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("a");
  await h.ui.selectAccount(accountA);
  h.ui.selectNetwork(777);
  const pending = h.ui.claimAllowance();
  for (let i = 0; i < 20 && !providerA.calls.some((call) => call.method === "eth_sendTransaction"); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  providerA.currentAccounts = [];
  providerA.emit("accountsChanged", []);
  releaseA(hashA);
  try { await pending; } catch (_error) {}
  deferA = false;
  currentOwner = accountB;
  transactionOwner = accountB;
  transactionHash = hashB;
  await h.ui.selectProvider("b");
  await h.ui.selectAccount(accountB);
  h.ui.selectNetwork(777);
  await h.ui.claimAllowance();
  currentOwner = accountA;
  transactionOwner = accountA;
  transactionHash = hashA;
  providerA.currentAccounts = [accountA];
  await h.ui.selectProvider("a");
  await h.ui.selectAccount(accountA);
  h.ui.selectNetwork(777);
  await h.ui.login();
  equal(h.ui.state().claimHash, hashA.toLowerCase(), "owner A recovers owner A hash");
  await h.ui.claimAllowance();
  equal(providerA.calls.filter((call) => call.method === "eth_sendTransaction").length, 1, "owner A does not resend");
  equal(providerB.calls.filter((call) => call.method === "eth_sendTransaction").length, 1, "owner B sends independently");
  equal(calls.filter((call) => call.url === "/api/faucet/verify").length, 2, "both scoped hashes verify once");
''');


def test_server_hash_conflict_is_visible_and_blocks_resend() -> None:
    _run_node(r'''
  const account = "0x" + "11".repeat(20);
  const provider = makeProvider([account], "0x309");
  const originalRequest = provider.request;
  const localHash = "0x" + "aa".repeat(32);
  const serverHash = "0x" + "bb".repeat(32);
  let releaseSend;
  const sendGate = new Promise((resolve) => { releaseSend = resolve; });
  provider.request = function(request) {
    if (request.method === "eth_sendTransaction") {
      this.calls.push({method: request.method, params: request.params});
      return sendGate;
    }
    return originalRequest.call(this, request);
  };
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: account});
    if (url === "/api/status") return response({authenticated: true, owner: account, onboarding: {phase: "ready"}, wallet_operations: {
      claim: {status: "pending", transaction_hash: serverHash},
    }});
    if (url === "/api/faucet/transaction") return response({transaction: {
      from: account, to: "0x" + "ab".repeat(20), value: "0x0", data: "0x4e71d92d",
      chainId: "0x309", gas: "0x33450", gasPrice: "0x49504f80",
    }, amount_atomic: "123456789012345678"});
    if (url === "/api/faucet/verify") return response({status: "verified", transaction_hash: serverHash});
    if (url === "/api/login/challenge") return response({challenge_id: "c1", message_to_sign: "login\nnonce"});
    if (url === "/api/login/verify") return response({authenticated: true, owner: account});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount(account);
  h.ui.selectNetwork(777);
  const pending = h.ui.claimAllowance();
  for (let i = 0; i < 20 && !provider.calls.some((call) => call.method === "eth_sendTransaction"); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  provider.currentAccounts = [];
  provider.emit("accountsChanged", []);
  releaseSend(localHash);
  try { await pending; } catch (_error) {}
  provider.currentAccounts = [account];
  await h.ui.selectProvider("one");
  await h.ui.selectAccount(account);
  h.ui.selectNetwork(777);
  await h.ui.login();
  equal(h.ui.state().claimHash, localHash.toLowerCase(), "local broadcast hash is retained");
  ok(h.ui.state().recoveryConflicts.claimHash, "server conflict is exposed");
  ok(h.ui.state().message.includes("recovery conflict"), "conflict message is shown");
  let rejected = false;
  try { await h.ui.claimAllowance(); } catch (_error) { rejected = true; }
  ok(rejected, "conflict blocks resend");
  equal(provider.calls.filter((call) => call.method === "eth_sendTransaction").length, 1, "conflict never resends");
  equal(calls.filter((call) => call.url === "/api/faucet/verify").length, 0, "conflict is not silently verified");
''');


def test_wallet_invalidation_clears_device_challenge_and_stops_polling() -> None:
    _run_node(r'''
  const account = "0x" + "11".repeat(20);
  const provider = makeProvider([account], "0x309");
  let deviceReads = 0;
  const fetchImpl = async (url, options = {}) => {
    if (url === "/wallet-config.json") return response(makeConfig());
    if (url === "/api/session") return response({csrf_token: "csrf", authenticated: true, owner: account});
    if (url === "/api/status") return response({authenticated: true, owner: account, onboarding: {phase: "ready"}, wallet_operations: {}});
    if (url.startsWith("/api/opc/external?")) {
      deviceReads += 1;
      return response({status: "pending", installation_id: "i1", label: "agent", challenge: {
        session_id: "s1", message_to_sign: "approve\nrequest", expires_at: "2099-01-01T00:00:00Z",
      }});
    }
    if (url === "/api/opc/external/claim") return response({status: "claimed", installation_id: "i1", label: "agent"});
    return response({});
  };
  const h = makeHarness({providers: [providerEntry("one", "Wallet One", provider)], fetchImpl,
    location: "/account/" + "a".repeat(43), pollIntervalMs: 1});
  await boot(h);
  await h.ui.selectProvider("one");
  await h.ui.selectAccount(account);
  h.ui.selectNetwork(777);
  await h.ui.claimDevice();
  await new Promise((resolve) => setTimeout(resolve, 5));
  provider.currentAccounts = [];
  provider.emit("accountsChanged", []);
  const readsAfterChange = deviceReads;
  await new Promise((resolve) => setTimeout(resolve, 10));
  equal(h.ui.state().device, null, "device state cleared on wallet change");
  equal(h.ui.state().deviceChallenge, null, "device challenge cleared on wallet change");
  equal(deviceReads, readsAfterChange, "device polling stopped on wallet change");
''');


def test_bootstrap_failure_is_caught_and_rendered() -> None:
    _run_node(r'''
  const fetchImpl = async (url) => url === "/wallet-config.json" ? response({error: "unavailable"}, 503) : response({});
  const h = makeHarness({fetchImpl});
  await h.waitReady();
  ok(h.context.AgentonomyBindingError.includes("Wallet configuration unavailable"), "bootstrap error captured");
  equal(h.document.getElementById("message").textContent, "Wallet configuration unavailable", "bootstrap error rendered");
''');


def test_binding_html_is_management_only() -> None:
    html = HTML_SOURCE.read_text(encoding="utf-8")
    lowered = html.lower()
    for forbidden in ("purchase", "preview", "search", "csv", "report", "feedback"):
        assert forbidden not in lowered
    assert 'id="network-select"' in html
    assert 'src="/wallet_selection.js"' in html
    assert 'src="/binding.js"' in html
    assert "<details" in html


def test_binding_source_has_no_chain_or_raw_bearer_fallbacks() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "window.ethereum" not in source
    assert "wallet_switchEthereumChain" not in source
    assert "wallet_addEthereumChain" not in source
    assert "Authorization" not in source
    assert "Bearer " not in source
    assert "10143" not in source
