"""Use the real Python adapter/store payload at the browser ABI boundary."""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess

from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig
from examples.monad_commerce.hosted_feedback import FeedbackStore
from test_erc8004 import (
    FakeRpc, State, config, network, BLOCK, IDENTITY, REPUTATION, BUYER,
    TX_HASH, PAYEE, IDENTITY_CODE_HASH,
)


def feedback_wire():
    origin = 'https://commerce.example'
    net = network(mode='monad_testnet', chain_id=10143,
                  rpc_urls=('https://rpc-a.example', 'https://rpc-b.example'))
    implementation_a, implementation_b = '0x' + '88' * 20, '0x' + '99' * 20
    pins = RegistryConfig.from_dict(asdict(config()) | {
        'agent_uri': origin + '/agent.json',
        'identity_implementation': implementation_a,
        'reputation_implementation': implementation_b,
        'identity_implementation_code_hash': IDENTITY_CODE_HASH,
        'reputation_implementation_code_hash': IDENTITY_CODE_HASH,
    }, network=net, origin=origin)
    state = State()
    state.chain_id, state.authorized, state.identity_uri = 10143, False, origin + '/agent.json'
    state.storage = {IDENTITY: '0x' + '00' * 12 + implementation_a[2:],
                     REPUTATION: '0x' + '00' * 12 + implementation_b[2:]}
    class Rpc(FakeRpc):
        def call(self, method, params):
            if method == 'eth_getBlockByNumber' and params[0] == 'finalized':
                return {'number': hex(BLOCK + 3), 'hash': state.block_hash}
            return super().call(method, params)
    registry = ERC8004Client(net, pins, client_factory=lambda url: Rpc(state, url))
    identity = registry.verify_identity() | {'reputation_registry': REPUTATION}
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        store = FeedbackStore(database, registry, clock=lambda: 1000)
        prepared = store.prepare(tenant_id='tenant-wire', purchase_id='purchase_wire', buyer=BUYER,
                                 score=87, identity=identity, purchase={
            'purchase_id': 'purchase_wire', 'state': 'delivered', 'offering_id': 'csv-reconciliation-v1',
            'output_hash': '0x' + '12' * 32,
            'settlement': {'owner': BUYER, 'payee': PAYEE, 'transaction_hash': TX_HASH},
        })
    return {'origin': origin, 'buyer': BUYER, 'identity': identity, 'prepared': prepared,
            'token': net.token, 'executor': net.executor, 'payee': net.payee}


def test_python_feedback_payload_matches_browser_calldata_and_schema():
    wire = feedback_wire()
    prepared = wire['prepared']
    assert prepared['transaction_hash'] is None
    assert prepared['disclosure']['output_hash'].startswith('sha256:')
    source = Path(__file__).resolve().parents[2] / 'examples/monad_commerce/hosted_web/app.js'
    program = r'''
const fs = require('fs');
const vm = require('vm');
const wire = JSON.parse(process.argv[2]);
const context = {TextEncoder, location: {origin: wire.origin}, window: {location: {origin: wire.origin}}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const transaction = context.PublicWalletUI.validateFeedbackTransaction(wire.prepared, {
  owner: wire.buyer, identity: wire.identity,
});
process.stdout.write(JSON.stringify(transaction));
'''
    result = subprocess.run(['node', '-e', program, str(source), json.dumps(wire)],
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == prepared['transaction']
