from __future__ import annotations

import json
from pathlib import Path
import subprocess

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "examples/monad_commerce/hosted_web/wallet_selection.js"
)


def _run_node(program: str) -> dict:
    program = (
        "(async () => {\n"
        + program
        + "\n})().catch((error) => { console.error(error); process.exit(1); });"
    )
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", program, str(SOURCE)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_discovery_is_eip6963_only_and_requires_explicit_provider_and_account() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

const targetListeners = new Map();
const targetEvents = [];
const providerMethods = [];
const wallet = {
  request({method}) {
    providerMethods.push(method);
    throw new Error('discovery must not call provider.request');
  },
  on() {},
  removeListener() {},
};
const target = {
  addEventListener(type, listener) {
    const listeners = targetListeners.get(type) || [];
    listeners.push(listener);
    targetListeners.set(type, listeners);
  },
  removeEventListener(type, listener) {
    targetListeners.set(type, (targetListeners.get(type) || []).filter((item) => item !== listener));
  },
  dispatchEvent(event) {
    targetEvents.push(event.type);
    if (event.type === 'eip6963:requestProvider') {
      const listeners = targetListeners.get('eip6963:announceProvider') || [];
      for (const listener of listeners.slice()) {
        listener({detail: {info: {
          uuid: 'wallet-1', name: 'Wallet One', rdns: 'com.example.one', icon: 'https://remote.invalid/icon.png'
        }, provider: wallet}});
      }
    }
    return true;
  },
};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const changes = [];
const controller = context.AgentonomyWalletSelection.create({
  eventTarget: target,
  onChange: (snapshot) => changes.push(snapshot),
});
const initial = controller.snapshot();
const wallets = controller.discover();
const after = controller.snapshot();
process.stdout.write(JSON.stringify({
  targetEvents,
  providerMethods,
  initial,
  wallets,
  after,
  changeCount: changes.length,
  provider: controller.provider(),
}));
'''
    )

    assert result["targetEvents"] == ["eip6963:requestProvider"]
    assert result["providerMethods"] == []
    assert result["initial"] == {
        "walletUuid": None,
        "walletName": None,
        "accounts": [],
        "account": None,
        "chainId": None,
        "connected": False,
        "generation": 0,
    }
    assert result["wallets"] == [
        {"uuid": "wallet-1", "name": "Wallet One", "rdns": "com.example.one"}
    ]
    assert result["after"]["walletUuid"] is None
    assert result["after"]["account"] is None
    assert result["after"]["connected"] is False
    assert result["after"]["generation"] == 0
    assert result["provider"] is None
    assert result["changeCount"] == 1


def test_two_wallets_require_explicit_provider_and_second_account_selection() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

function makeProvider(accounts, chainId) {
  const methods = [];
  const listeners = new Map();
  return {
    methods,
    listeners,
    request({method}) {
      methods.push(method);
      if (method === 'eth_requestAccounts') return Promise.resolve(accounts);
      if (method === 'eth_chainId') return Promise.resolve(chainId);
      throw new Error('unexpected RPC ' + method);
    },
    on(type, listener) { listeners.set(type, listener); },
    removeListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
  };
}
function makeTarget(details) {
  const listeners = new Map();
  return {
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    dispatchEvent(event) {
      if (event.type === 'eip6963:requestProvider') {
        for (const detail of details) listeners.get('eip6963:announceProvider')?.({detail});
      }
      return true;
    },
  };
}
const first = makeProvider(['0x' + '11'.repeat(20)], '0x1');
const second = makeProvider([
  '0x' + 'aa'.repeat(20),
  '0x' + 'BB'.repeat(20),
  '0x' + 'bb'.repeat(20),
  'not-an-address',
], '0x279f');
const target = makeTarget([
  {info: {uuid: 'one', name: 'One', rdns: 'com.example.one'}, provider: first},
  {info: {uuid: 'two', name: 'Two', rdns: 'com.example.two'}, provider: second},
]);
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const errors = [];
const controller = context.AgentonomyWalletSelection.create({eventTarget: target, onError: (error) => errors.push(error.message)});
controller.discover();
const before = controller.snapshot();
const selected = await controller.selectProvider('two');
const secondAccount = controller.selectAccount('0x' + 'BB'.repeat(20));
const after = controller.snapshot();
let invalidAccount = null;
try { controller.selectAccount('0x' + 'cc'.repeat(20)); } catch (error) { invalidAccount = error.message; }
process.stdout.write(JSON.stringify({
  wallets: controller.wallets(),
  before,
  selected,
  secondAccount,
  after,
  methods: second.methods,
  invalidAccount,
  errors,
}));
'''
    )

    assert result["wallets"] == [
        {"uuid": "one", "name": "One", "rdns": "com.example.one"},
        {"uuid": "two", "name": "Two", "rdns": "com.example.two"},
    ]
    assert result["before"]["walletUuid"] is None
    assert result["selected"]["walletUuid"] == "two"
    assert result["selected"]["walletName"] == "Two"
    assert result["selected"]["accounts"] == [
        "0x" + "aa" * 20,
        "0x" + "bb" * 20,
    ]
    assert result["selected"]["account"] is None
    assert result["selected"]["connected"] is False
    assert result["selected"]["chainId"] == 10143
    assert result["secondAccount"]["account"] == "0x" + "bb" * 20
    assert result["after"]["connected"] is True
    assert result["methods"] == ["eth_requestAccounts", "eth_chainId"]
    assert result["invalidAccount"]
    assert result["errors"]


def test_duplicate_and_spoof_announcements_are_ignored_without_rpc() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const announceListeners = [];
const target = {
  addEventListener(type, listener) { if (type === 'eip6963:announceProvider') announceListeners.push(listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const calls = [];
const original = {request() { calls.push('original');}, on() {}, removeListener() {}};
const spoof = {request() { calls.push('spoof');}, on() {}, removeListener() {}};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const changes = [];
const controller = context.AgentonomyWalletSelection.create({eventTarget: target, onChange: (snapshot) => changes.push(snapshot)});
controller.discover();
const announce = (detail) => announceListeners[0]({detail});
announce({info: {uuid: 'u', name: 'Original', rdns: 'com.original', icon: 'data:image/png;base64,secret'}, provider: original});
announce({info: {uuid: 'u', name: 'Duplicate', rdns: 'com.duplicate'}, provider: original});
announce({info: {uuid: 'u', name: 'Spoof', rdns: 'com.spoof'}, provider: spoof});
announce({info: {uuid: 'different', name: 'Same object', rdns: 'com.same'}, provider: original});
announce({info: {uuid: 'bad', name: 'Bad'}, provider: {}});
announce({info: {uuid: 'bad-control\n', name: 'Bad', rdns: 'com.bad'}, provider: spoof});
process.stdout.write(JSON.stringify({wallets: controller.wallets(), changes: changes.length, calls}));
'''
    )

    assert result["wallets"] == [
        {"uuid": "u", "name": "Original", "rdns": "com.original"}
    ]
    assert result["changes"] == 1
    assert result["calls"] == []


def test_stale_provider_requests_and_detached_events_cannot_rebind_selection() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

function deferredProvider(accounts, chainId) {
  const pending = [];
  const listeners = new Map();
  return {
    pending, listeners,
    request({method}) {
      if (method === 'eth_requestAccounts' || method === 'eth_chainId') {
        return new Promise((resolve, reject) => pending.push({method, resolve, reject, chainId, accounts}));
      }
      throw new Error('unexpected RPC ' + method);
    },
    on(type, listener) { listeners.set(type, listener); },
    removeListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    emit(type, value) { listeners.get(type)?.(value); },
  };
}
function immediateProvider(accounts, chainId) {
  const listeners = new Map();
  return {
    listeners,
    request({method}) {
      if (method === 'eth_requestAccounts') return Promise.resolve(accounts);
      if (method === 'eth_chainId') return Promise.resolve(chainId);
      throw new Error('unexpected RPC ' + method);
    },
    on(type, listener) { listeners.set(type, listener); },
    removeListener(type, listener) {
      if (listeners.get(type) === listener) listeners.delete(type);
    },
    emit(type, value) { listeners.get(type)?.(value); },
  };
}
const listeners = new Map();
const target = {
  addEventListener(type, listener) { listeners.set(type, listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const staleProvider = deferredProvider(['0x' + '33'.repeat(20)], '0x3');
const oldProvider = immediateProvider(['0x' + '11'.repeat(20)], '0x1');
const newProvider = immediateProvider(['0x' + '22'.repeat(20)], '0x2');
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const changes = [];
const controller = context.AgentonomyWalletSelection.create({eventTarget: target, onChange: (snapshot) => changes.push(snapshot)});
controller.discover();
const announce = (detail) => listeners.get('eip6963:announceProvider')?.({detail});
announce({info: {uuid: 'stale', name: 'Stale', rdns: 'com.stale'}, provider: staleProvider});
announce({info: {uuid: 'old', name: 'Old', rdns: 'com.old'}, provider: oldProvider});
announce({info: {uuid: 'new', name: 'New', rdns: 'com.new'}, provider: newProvider});
const oldRequest = controller.selectProvider('stale');
await Promise.resolve();
const newRequest = controller.selectProvider('new');
for (const request of staleProvider.pending.splice(0)) {
  request.resolve(request.method === 'eth_chainId' ? request.chainId : request.accounts);
}
const newSnapshot = await newRequest;
await oldRequest;
controller.selectAccount('0x' + '22'.repeat(20));
await controller.selectProvider('old');
controller.selectAccount('0x' + '11'.repeat(20));
const oldListener = oldProvider.listeners.get('accountsChanged');
await controller.selectProvider('new');
controller.selectAccount('0x' + '22'.repeat(20));
oldListener?.(['0x' + '33'.repeat(20)]);
const afterOldEvent = controller.snapshot();
const selectedSnapshot = afterOldEvent;
newProvider.emit('accountsChanged', []);
const afterAccounts = controller.snapshot();
await controller.selectProvider('new');
controller.selectAccount('0x' + '22'.repeat(20));
newProvider.emit('chainChanged', '0x3');
const afterChain = controller.snapshot();
await controller.selectProvider('new');
controller.selectAccount('0x' + '22'.repeat(20));
newProvider.emit('disconnect', {code: 4900});
const afterDisconnect = controller.snapshot();
let stale = null;
try { controller.assertSnapshot(selectedSnapshot); } catch (error) { stale = error.message; }
process.stdout.write(JSON.stringify({
  newSnapshot,
  afterOldEvent,
  afterAccounts,
  afterChain,
  afterDisconnect,
  stale,
  changes: changes.length,
}));
'''
    )

    assert result["newSnapshot"]["walletUuid"] == "new"
    assert result["newSnapshot"]["account"] is None
    assert result["afterOldEvent"]["walletUuid"] == "new"
    assert result["afterOldEvent"]["account"] == "0x" + "22" * 20
    assert result["afterAccounts"]["walletUuid"] is None
    assert result["afterChain"]["walletUuid"] is None
    assert result["afterDisconnect"]["walletUuid"] is None
    assert result["stale"]
    assert result["changes"] >= 9


def test_snapshot_assertion_includes_provider_generation_account_and_chain() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const targetListeners = new Map();
const target = {
  addEventListener(type, listener) { targetListeners.set(type, listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const provider = {
  request({method}) {
    if (method === 'eth_requestAccounts') return Promise.resolve(['0x' + 'ab'.repeat(20)]);
    if (method === 'eth_chainId') return Promise.resolve('0x2');
    throw new Error('unexpected RPC ' + method);
  },
  on() {},
  removeListener() {},
};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const controller = context.AgentonomyWalletSelection.create({eventTarget: target});
controller.discover();
targetListeners.get('eip6963:announceProvider')?.({detail: {info: {uuid: 'u', name: 'U', rdns: 'com.u'}, provider}});
await controller.selectProvider('u');
const providerSnapshot = controller.snapshot();
controller.assertSnapshot(providerSnapshot);
controller.selectAccount('0x' + 'AB'.repeat(20));
let accountStale = null;
try { controller.assertSnapshot(providerSnapshot); } catch (error) { accountStale = error.message; }
const current = controller.snapshot();
controller.clear();
let generationStale = null;
try { controller.assertSnapshot(current); } catch (error) { generationStale = error.message; }
process.stdout.write(JSON.stringify({providerSnapshot, current, accountStale, generationStale}));
'''
    )

    assert result["providerSnapshot"]["account"] is None
    assert result["current"]["account"] == "0x" + "ab" * 20
    assert result["accountStale"]
    assert result["generationStale"]


def test_listener_attach_failure_rejects_and_clears_the_binding() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const targetListeners = new Map();
const target = {
  addEventListener(type, listener) { targetListeners.set(type, listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const provider = {
  request({method}) {
    if (method === 'eth_requestAccounts') return Promise.resolve(['0x' + 'ab'.repeat(20)]);
    if (method === 'eth_chainId') return Promise.resolve('0x2');
    throw new Error('unexpected method ' + method);
  },
  on(type) {
    if (type === 'chainChanged') throw new Error('listener attach failed');
  },
  removeListener() {},
};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const controller = context.AgentonomyWalletSelection.create({eventTarget: target});
controller.discover();
targetListeners.get('eip6963:announceProvider')?.({detail: {
  info: {uuid: 'u', name: 'U', rdns: 'com.u'}, provider,
}});
let error = null;
try { await controller.selectProvider('u'); } catch (caught) { error = caught.message; }
process.stdout.write(JSON.stringify({error, snapshot: controller.snapshot(), provider: controller.provider()}));
'''
    )

    assert result["error"]
    assert result["snapshot"]["walletUuid"] is None
    assert result["snapshot"]["accounts"] == []
    assert result["snapshot"]["account"] is None
    assert result["snapshot"]["connected"] is False
    assert result["provider"] is None


def test_provider_event_during_selection_invalidates_pending_binding() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const targetListeners = new Map();
const target = {
  addEventListener(type, listener) { targetListeners.set(type, listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const provider = {
  listeners: new Map(),
  request({method}) {
    if (method === 'eth_requestAccounts') {
      this.listeners.get('accountsChanged')?.([]);
      return Promise.resolve(['0x' + 'ab'.repeat(20)]);
    }
    if (method === 'eth_chainId') return Promise.resolve('0x2');
    throw new Error('unexpected method ' + method);
  },
  on(type, listener) { this.listeners.set(type, listener); },
  removeListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  },
};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const controller = context.AgentonomyWalletSelection.create({eventTarget: target});
controller.discover();
targetListeners.get('eip6963:announceProvider')?.({detail: {
  info: {uuid: 'u', name: 'U', rdns: 'com.u'}, provider,
}});
const selected = await controller.selectProvider('u');
process.stdout.write(JSON.stringify({selected, snapshot: controller.snapshot(), provider: controller.provider()}));
'''
    )

    assert result["selected"]["walletUuid"] is None
    assert result["snapshot"]["walletUuid"] is None
    assert result["snapshot"]["accounts"] == []
    assert result["provider"] is None


def test_snapshot_assertion_rejects_mutated_visible_fields_and_accounts() -> None:
    result = _run_node(
        r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const targetListeners = new Map();
const target = {
  addEventListener(type, listener) { targetListeners.set(type, listener); },
  removeEventListener() {},
  dispatchEvent() { return true; },
};
const provider = {
  request({method}) {
    if (method === 'eth_requestAccounts') return Promise.resolve(['0x' + 'ab'.repeat(20)]);
    if (method === 'eth_chainId') return Promise.resolve('0x2');
    throw new Error('unexpected method ' + method);
  },
  on() {},
  removeListener() {},
};
const context = {console};
context.globalThis = context;
vm.runInNewContext(source, context, {filename: 'wallet_selection.js'});
const controller = context.AgentonomyWalletSelection.create({eventTarget: target});
controller.discover();
targetListeners.get('eip6963:announceProvider')?.({detail: {
  info: {uuid: 'u', name: 'U', rdns: 'com.u'}, provider,
}});
await controller.selectProvider('u');
controller.selectAccount('0x' + 'ab'.repeat(20));
const snapshot = controller.snapshot();
snapshot.walletName = 'spoofed';
snapshot.account = '0x' + 'cd'.repeat(20);
snapshot.chainId = 999;
snapshot.connected = false;
snapshot.generation = -1;
snapshot.accounts.push('0x' + 'cd'.repeat(20));
let error = null;
try { controller.assertSnapshot(snapshot); } catch (caught) { error = caught.message; }
process.stdout.write(JSON.stringify({error, snapshot}));
'''
    )

    assert result["error"]
