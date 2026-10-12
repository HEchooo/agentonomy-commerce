"""Contract tests for the standalone wallet feedback page.

The page is intentionally exercised in a small Node VM.  These tests cover the
browser boundary without requiring a wallet extension, a live RPC provider, or
an external account.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from test_hosted_erc8004_wire import feedback_wire


ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "examples" / "monad_commerce" / "hosted_web"
SOURCE = UI_ROOT / "feedback.js"
WALLET_SOURCE = UI_ROOT / "wallet_selection.js"
HTML_SOURCE = UI_ROOT / "feedback.html"

WIRE = feedback_wire()
OWNER = WIRE["buyer"]
OTHER = "0x" + "22" * 20
REGISTRY = WIRE["identity"]["reputation_registry"]
FEEDBACK_HASH = WIRE["prepared"]["feedback_hash"]
PAYMENT_HASH = WIRE["prepared"]["disclosure"]["payment_transaction_hash"]
TRANSACTION_HASH = "0x" + "66" * 32
CHAIN_ID = 10143
CHAIN_HEX = "0x279f"
INTENT = "a" * 43
ORIGIN = WIRE["origin"]
CSRF = "csrf-token-123456789012"


PROGRAM = r"""
const fs = require("fs");
const vm = require("vm");
const feedbackSource = fs.readFileSync(process.argv[2], "utf8");
const walletSource = fs.readFileSync(process.argv[3], "utf8");
const scenario = process.argv[4];

const WIRE = JSON.parse(process.env.TEST_WIRE);
const OWNER = process.env.TEST_OWNER;
const OTHER = process.env.TEST_OTHER;
const REGISTRY = process.env.TEST_REGISTRY;
const FEEDBACK_HASH = process.env.TEST_FEEDBACK_HASH;
const PAYMENT_HASH = process.env.TEST_PAYMENT_HASH;
const TRANSACTION_HASH = process.env.TEST_TRANSACTION_HASH;
const CHAIN_ID = 10143;
const CHAIN_HEX = "0x279f";
const INTENT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ORIGIN = process.env.TEST_ORIGIN;
const CSRF = "csrf-token-123456789012";
const DATA = process.env.TEST_DATA;
const AGENT_ID = process.env.TEST_AGENT_ID;
const AGENT_REGISTRY = process.env.TEST_AGENT_REGISTRY;
const heldLocks = new Set();
const locks = {
  async request(name, options, action) {
    if (heldLocks.has(name)) return action(null);
    heldLocks.add(name);
    try { return await action({name}); } finally { heldLocks.delete(name); }
  },
};

function fail(message) { throw new Error(message); }
function ok(value, label) { if (!value) fail(label); }
function equal(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail(label + " expected " + JSON.stringify(expected) + " got " + JSON.stringify(actual));
  }
}
function response(payload, status = 200) {
  return {ok: status >= 200 && status < 300, status, async json() { return payload; }};
}
function deferred() { let resolve; const promise = new Promise((r) => { resolve = r; }); return {promise, resolve}; }
function makeStorage(seed = {}) {
  const values = {...seed};
  return {
    values,
    getItem(key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem(key, value) { values[key] = String(value); },
    removeItem(key) { delete values[key]; },
  };
}
function makeTarget(providers) {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      const current = listeners.get(type) || [];
      current.push(listener); listeners.set(type, current);
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) || []).filter((item) => item !== listener));
    },
    dispatchEvent(event) {
      if (event.type === "eip6963:requestProvider") {
        for (const detail of providers) {
          for (const listener of [...(listeners.get("eip6963:announceProvider") || [])]) {
            listener({type: "eip6963:announceProvider", detail});
          }
        }
      } else {
        for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
      }
      return true;
    },
  };
}
function makeDocument() {
  const elements = new Map();
  const ids = [
    "message", "wallet-list", "account-list", "wallet-empty", "owner", "wallet-chain",
    "request-chain", "auth-help", "auth-link", "score", "feedback-hash", "payment-hash",
    "output-hash", "feedback-state", "revoked-note", "service-identity", "registry", "native-value", "gas",
    "network-fee", "calldata", "send", "send-help", "recovery-card", "transaction-hash", "verify",
    "verified-card", "verified-kicker", "verified-title", "verified-copy", "verified-hash", "feedback-index", "last-verified-at", "verification-freshness",
  ];
  for (const id of ids) {
    elements.set(id, {
      id, value: "", textContent: "", hidden: false, disabled: false, children: [],
      listeners: new Map(), dataset: {},
      addEventListener(type, listener) {
        const current = this.listeners.get(type) || [];
        current.push(listener); this.listeners.set(type, current);
      },
      removeEventListener(type, listener) {
        this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
      },
      dispatchEvent(event) {
        for (const listener of [...(this.listeners.get(event.type) || [])]) listener(event);
      },
      setAttribute(name, value) { this[name] = value; },
      replaceChildren(...children) { this.children = children; },
    });
  }
  const listeners = new Map();
  return {
    readyState: "loading",
    cookie: "agentonomy_hosted_csrf=" + CSRF,
    getElementById(id) { return elements.get(id) || null; },
    createElement() {
      return {
        type: "", textContent: "", disabled: false, children: [], listeners: new Map(),
        setAttribute(name, value) { this[name] = value; },
        addEventListener(type, listener) {
          const current = this.listeners.get(type) || [];
          current.push(listener); this.listeners.set(type, current);
        },
        appendChild(child) { this.children.push(child); return child; },
      };
    },
    addEventListener(type, listener) {
      const current = listeners.get(type) || [];
      current.push(listener); listeners.set(type, current);
    },
    removeEventListener(type, listener) {
      listeners.set(type, (listeners.get(type) || []).filter((item) => item !== listener));
    },
    dispatchEvent(event) {
      for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
    },
    elements,
  };
}
function makeProvider(accounts, chain = CHAIN_HEX, behavior = {}) {
  const listeners = new Map();
  const methods = [];
  const sends = [];
  const provider = {
    methods, sends,
    on(type, listener) {
      const current = listeners.get(type) || [];
      current.push(listener); listeners.set(type, current);
    },
    removeListener(type, listener) {
      listeners.set(type, (listeners.get(type) || []).filter((item) => item !== listener));
    },
    async request({method, params}) {
      methods.push({method, params});
      if (method === "eth_requestAccounts") return accounts.slice();
      if (method === "eth_accounts") return accounts.slice();
      if (method === "eth_chainId") return chain;
      if (method === "eth_gasPrice") return behavior.gasPrice || "0x746a528800";
      if (method === "eth_sendTransaction") {
        sends.push(params[0]);
        if (behavior.hold) await behavior.hold;
        if (behavior.unknown) throw new Error("wallet response timeout");
        if (behavior.cancel) { const error = new Error("user rejected"); error.code = 4001; throw error; }
        return TRANSACTION_HASH;
      }
      throw new Error("unexpected wallet RPC " + method);
    },
  };
  return provider;
}
function providerEntry(uuid, name, provider) {
  return {info: {uuid, name, rdns: "com.example." + uuid}, provider};
}
function basePayload(overrides = {}) {
  return {
    owner: OWNER, chain_id: CHAIN_ID, status: "prepared", score: 87,
    feedback_hash: FEEDBACK_HASH,
    feedback_uri: ORIGIN + "/erc8004/feedback/" + FEEDBACK_HASH.slice(2) + ".json",
    reputation_registry: REGISTRY,
    agent_id: AGENT_ID,
    agent_registry: AGENT_REGISTRY,
    wallet_transaction: {max_gas: "0x7a120", max_gas_price_wei: "0x746a528800"},
    transaction: {
      from: OWNER, to: REGISTRY, value: "0x0", data: DATA, chainId: CHAIN_HEX, gas: "0x7a120",
    },
    disclosure: {
      buyer: OWNER, score: 87, payment_transaction_hash: PAYMENT_HASH,
      output_hash: process.env.TEST_OUTPUT_HASH,
    },
    verified: false,
    ...overrides,
  };
}
function unknownPayload() {
  const payload = basePayload({status: "unknown"});
  delete payload.transaction;
  delete payload.disclosure;
  return payload;
}
function verifiedPayload(verificationStatus = null, revoked = false) {
  const payload = basePayload({
    status: "verified", verified: true, transaction_hash: TRANSACTION_HASH,
    feedback_index: 3, is_revoked: revoked,
  });
  payload.last_verified_at = "2026-10-13T04:05:06Z";
  if (verificationStatus) payload.verification_status = verificationStatus;
  delete payload.transaction;
  delete payload.disclosure;
  delete payload.wallet_transaction;
  return payload;
}
function makeHarness({payload, providers, storage, fetchStatuses = {}, legacyProvider = null}) {
  const document = makeDocument();
  const target = makeTarget(providers);
  const calls = [];
  const fetchImpl = async (path, options = {}) => {
    calls.push({path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null});
    if (path === "/api/feedback/" + INTENT && (options.method || "GET") === "GET") {
      const status = fetchStatuses.get ? fetchStatuses.get("get") : 200;
      return status === 200 ? response(payload) : response({error: "private-marker", secret: "https://secret.invalid"}, status);
    }
    if (path === "/api/feedback/" + INTENT + "/verify" && options.method === "POST") {
      const status = fetchStatuses.get ? fetchStatuses.get("verify") : 200;
      if (status !== 200) return response({error: "private-marker"}, status);
      return response(verifiedPayload(scenario === "verify-unconfirmed" ? "unconfirmed" : "current"));
    }
    throw new Error("unexpected request " + path);
  };
  const context = {
    console, URL, TextEncoder, TextDecoder, BigInt,
    document, fetch: fetchImpl, localStorage: storage,
    location: {pathname: "/feedback/" + INTENT, origin: ORIGIN},
    navigator: scenario === "no-locks" ? {} : {locks},
    window: legacyProvider ? {ethereum: legacyProvider} : {},
    Event: class { constructor(type) { this.type = type; } },
  };
  context.globalThis = context;
  for (const method of ["addEventListener", "removeEventListener", "dispatchEvent"]) {
    context[method] = target[method].bind(target);
  }
  vm.runInNewContext(walletSource, context, {filename: "wallet_selection.js"});
  vm.runInNewContext(feedbackSource, context, {filename: "feedback.js"});
  const ui = context.AgentonomyFeedbackUI.create({
    document, eventTarget: target, fetch: fetchImpl, location: context.location, storage,
    csrfToken: CSRF,
  });
  return {ui, document, calls, providers, storage};
}
function unknownStorage() {
  return makeStorage({
    ["agentonomy.feedback.tx.v1." + OWNER.toLowerCase() + "." + FEEDBACK_HASH.toLowerCase()]: JSON.stringify({
      version: 1, owner: OWNER.toLowerCase(), feedback_hash: FEEDBACK_HASH.toLowerCase(),
      status: "unknown", transaction_hash: null,
    }),
  });
}
async function boot(harness) {
  await harness.ui.init();
  return harness;
}
async function main() {
  if (scenario === "owner-mismatch") {
    const provider = makeProvider([OTHER]);
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OTHER);
    await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends, methods: provider.methods};
  }
  if (scenario === "chain-mismatch") {
    const provider = makeProvider([OWNER], "0x1");
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
    await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends, methods: provider.methods};
  }
  if (scenario === "bad-abi") {
    const payload = basePayload({transaction: {...basePayload().transaction, data: "0x3c036a7e" + "00".repeat(32)}});
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload, providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER); await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends};
  }
  if (scenario === "gas-overcap") {
    const provider = makeProvider([OWNER], CHAIN_HEX, {gasPrice: "0x746a528801"});
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER); await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends, methods: provider.methods};
  }
  if (scenario === "raw-output") {
    const disclosure = {...basePayload().disclosure, output_hash: "0x" + WIRE.prepared.disclosure.output_hash.slice(7)};
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: basePayload({disclosure}), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    return {state: h.ui.state(), output: h.document.getElementById("output-hash").textContent};
  }
  if (scenario === "verified") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: verifiedPayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
    await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends, methods: provider.methods};
  }
  if (scenario === "history") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: verifiedPayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
    return {state: h.ui.state(), sends: provider.sends,
      label: h.document.getElementById("feedback-state").textContent,
      freshness: h.document.getElementById("verification-freshness").textContent,
      last: h.document.getElementById("last-verified-at").textContent,
      recoveryHidden: h.document.getElementById("recovery-card").hidden,
      message: h.document.getElementById("message").textContent};
  }
  if (scenario === "revoked") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: verifiedPayload(null, true), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER); await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends,
      label: h.document.getElementById("feedback-state").textContent,
      kicker: h.document.getElementById("verified-kicker").textContent,
      recoveryHidden: h.document.getElementById("recovery-card").hidden};
  }
  if (scenario === "verify-unconfirmed") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: unknownPayload(), providers: [providerEntry("one", "Wallet", provider)], storage: unknownStorage()}));
    h.document.getElementById("transaction-hash").value = TRANSACTION_HASH;
    const verified = await h.ui.verify();
    return {state: h.ui.state(), verified, sends: provider.sends,
      message: h.document.getElementById("message").textContent,
      title: h.document.getElementById("verified-title").textContent};
  }
  if (scenario === "unknown-reload") {
    const provider = makeProvider([OWNER], CHAIN_HEX);
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: unknownStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
    await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends};
  }
  if (scenario === "new-intent-same-draft") {
    const provider = makeProvider([OWNER], CHAIN_HEX);
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: unknownStorage()}));
    await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
    await h.ui.send();
    return {state: h.ui.state(), sends: provider.sends};
  }
  if (scenario === "recover") {
    const provider = makeProvider([OWNER]);
    const storage = unknownStorage();
    const h = await boot(makeHarness({payload: unknownPayload(), providers: [providerEntry("one", "Wallet", provider)], storage}));
    h.document.getElementById("transaction-hash").value = TRANSACTION_HASH;
    const verified = await h.ui.verify();
    return {state: h.ui.state(), verified, sends: provider.sends,
      verifies: h.calls.filter((call) => call.method === "POST")};
  }
  if (scenario === "error") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage(), fetchStatuses: {get: 500}}));
    return {state: h.ui.state(), calls: h.calls, message: h.document.getElementById("message").textContent};
  }
  if (scenario === "legacy") {
    const provider = makeProvider([OWNER]);
    const h = await boot(makeHarness({
      payload: basePayload(), providers: [], legacyProvider: provider, storage: makeStorage(),
    }));
    const wallets = h.ui.wallets();
    await h.ui.selectProvider("legacy-browser-wallet"); await h.ui.selectAccount(OWNER);
    return {wallets, state: h.ui.state(), methods: provider.methods};
  }
  if (scenario === "concurrent-tabs") {
    const gate = deferred();
    const storage = makeStorage();
    const firstProvider = makeProvider([OWNER], CHAIN_HEX, {hold: gate.promise});
    const secondProvider = makeProvider([OWNER]);
    const first = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", firstProvider)], storage}));
    const second = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("two", "Wallet", secondProvider)], storage}));
    await first.ui.selectProvider("one"); await first.ui.selectAccount(OWNER);
    await second.ui.selectProvider("two"); await second.ui.selectAccount(OWNER);
    const firstSend = first.ui.send();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const secondSend = await second.ui.send();
    gate.resolve(); await firstSend;
    return {secondSend, firstSends: firstProvider.sends, secondSends: secondProvider.sends,
      secondState: second.ui.state(), secondMessage: second.document.getElementById("message").textContent};
  }
  const provider = makeProvider([OWNER]);
  const h = await boot(makeHarness({payload: basePayload(), providers: [providerEntry("one", "Wallet", provider)], storage: makeStorage()}));
  await h.ui.selectProvider("one"); await h.ui.selectAccount(OWNER);
  const before = h.ui.state();
  await h.ui.send();
  const afterSend = h.ui.state();
  await h.ui.send();
  return {before, afterSend, final: h.ui.state(), sends: provider.sends, methods: provider.methods};
}
main().then((value) => process.stdout.write(JSON.stringify(value)))
  .catch((error) => { console.error(error.stack || error); process.exit(1); });
"""


def _run_node(scenario: str) -> dict:
    env = {
        "TEST_OWNER": OWNER,
        "TEST_OTHER": OTHER,
        "TEST_REGISTRY": REGISTRY,
        "TEST_FEEDBACK_HASH": FEEDBACK_HASH,
        "TEST_PAYMENT_HASH": PAYMENT_HASH,
        "TEST_TRANSACTION_HASH": TRANSACTION_HASH,
        "TEST_ORIGIN": ORIGIN,
        "TEST_DATA": WIRE["prepared"]["transaction"]["data"],
        "TEST_OUTPUT_HASH": WIRE["prepared"]["disclosure"]["output_hash"],
        "TEST_AGENT_ID": str(WIRE["identity"]["agent_id"]),
        "TEST_AGENT_REGISTRY": WIRE["identity"]["agent_registry"],
        "TEST_WIRE": json.dumps(WIRE),
    }
    completed = subprocess.run(
        ["node", "-", str(SOURCE), str(WALLET_SOURCE), scenario],
        input=PROGRAM,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **env},
    )
    if completed.returncode:
        raise AssertionError(f"Node scenario failed ({completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    return json.loads(completed.stdout)


def test_feedback_page_only_exposes_disclosure_and_wallet_action() -> None:
    html = HTML_SOURCE.read_text()
    assert "/wallet_selection.js" in html
    assert "/feedback.js" in html
    assert "/api/services" not in html
    assert "/api/purchases" not in html
    assert "purchase dashboard" not in html.lower()
    assert "PRIVATE WALLET REVIEW" not in html
    assert "Payment hash (public)" in html
    assert "Result hash (public)" in html
    assert "service-identity" in html


def test_prepared_feedback_requires_explicit_send_and_never_switches_network() -> None:
    result = _run_node("normal")
    assert result["before"]["status"] == "prepared"
    assert result["afterSend"]["status"] == "pending"
    assert result["afterSend"]["awaiting_recovery"] is True
    assert len(result["sends"]) == 1
    assert result["sends"][0]["to"] == REGISTRY
    assert result["sends"][0]["chainId"] == CHAIN_HEX
    assert result["sends"][0]["value"] == "0x0"
    assert result["sends"][0]["data"].startswith("0x3c036a7e")
    assert not any(item["method"] in {"wallet_switchEthereumChain", "wallet_addEthereumChain"}
                   for item in result["methods"])


def test_owner_mismatch_blocks_wallet_send() -> None:
    result = _run_node("owner-mismatch")
    assert not result["sends"]
    assert result["state"]["verified"] is False


def test_chain_mismatch_blocks_wallet_send_without_auto_switch() -> None:
    result = _run_node("chain-mismatch")
    assert not result["sends"]
    assert not any(item["method"] in {"wallet_switchEthereumChain", "wallet_addEthereumChain"}
                   for item in result["methods"])


def test_full_erc8004_calldata_is_required() -> None:
    result = _run_node("bad-abi")
    assert not result["sends"]


def test_network_gas_price_is_checked_before_wallet_send() -> None:
    result = _run_node("gas-overcap")
    assert not result["sends"]
    assert any(item["method"] == "eth_gasPrice" for item in result["methods"])
    assert not any(item["method"] == "eth_sendTransaction" for item in result["methods"])


def test_canonical_raw_result_hash_is_displayed_as_labeled_public_sha256() -> None:
    result = _run_node("raw-output")
    assert result["output"] == "sha256:" + WIRE["prepared"]["disclosure"]["output_hash"].split(":", 1)[1]


def test_verified_feedback_is_read_only_and_cannot_send() -> None:
    result = _run_node("verified")
    assert result["state"]["verified"] is True
    assert result["state"]["transaction_hash"] == TRANSACTION_HASH.lower()
    assert not result["sends"]


def test_historical_verified_record_shows_last_observation_and_allows_recheck_only() -> None:
    result = _run_node("history")
    assert result["state"]["verified"] is True
    assert result["state"]["verification_status"] is None
    assert result["state"]["last_verified_at"] == "2026-10-13T04:05:06.000Z"
    assert result["label"] == "Verified record"
    assert result["freshness"] == "Verified record"
    assert result["last"] == "2026-10-13T04:05:06.000Z"
    assert result["recoveryHidden"] is False
    assert "earlier chain observation" in result["message"]
    assert not result["sends"]


def test_revoked_status_takes_priority_over_verified_record() -> None:
    result = _run_node("revoked")
    assert result["state"]["verified"] is True
    assert result["state"]["is_revoked"] is True
    assert result["label"] == "Revoked"
    assert result["kicker"] == "REVOKED"
    assert result["recoveryHidden"] is True
    assert not result["sends"]


def test_unconfirmed_recheck_does_not_claim_current_independent_success() -> None:
    result = _run_node("verify-unconfirmed")
    assert result["state"]["verified"] is True
    assert result["state"]["verification_status"] == "unconfirmed"
    assert "not confirmed" in result["message"]
    assert "independently verified against the current chain" not in result["message"]
    assert "verified record" in result["title"]
    assert not result["sends"]


def test_unknown_send_lock_survives_reload_and_same_draft_reissue() -> None:
    first = _run_node("unknown-reload")
    second = _run_node("new-intent-same-draft")
    assert first["state"]["awaiting_recovery"] is True
    assert second["state"]["awaiting_recovery"] is True
    assert not first["sends"]
    assert not second["sends"]


def test_browser_without_web_locks_fails_closed_before_wallet_send() -> None:
    result = _run_node("no-locks")
    assert not result["sends"]


def test_cross_tab_web_lock_allows_only_one_wallet_send() -> None:
    result = _run_node("concurrent-tabs")
    assert len(result["firstSends"]) == 1
    assert not result["secondSends"]
    assert "Another tab" in result["secondMessage"]


def test_original_transaction_recovery_verifies_same_hash_without_resend() -> None:
    result = _run_node("recover")
    assert result["state"]["verified"] is True
    assert result["state"]["transaction_hash"] == TRANSACTION_HASH.lower()
    assert not result["sends"]
    assert result["verifies"] == [{
        "path": "/api/feedback/" + INTENT + "/verify",
        "method": "POST",
        "body": {"transaction_hash": TRANSACTION_HASH},
    }]


def test_failed_status_does_not_echo_server_secret_or_url() -> None:
    result = _run_node("error")
    assert "private-marker" not in result["message"]
    assert "secret.invalid" not in result["message"]


def test_legacy_wallet_is_adapted_through_shared_selection_controller() -> None:
    result = _run_node("legacy")
    assert result["wallets"] == [{
        "uuid": "legacy-browser-wallet",
        "name": "Browser wallet",
        "rdns": "legacy.browser.wallet",
    }]
    assert result["state"]["wallet"]["account"] == OWNER.lower()
    assert [item["method"] for item in result["methods"]] == [
        "eth_requestAccounts", "eth_chainId",
    ]
