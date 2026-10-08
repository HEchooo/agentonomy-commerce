from __future__ import annotations

import pytest
from eth_abi import encode
from eth_utils import keccak

import agentonomy_commerce.budget_network as budget_network
from agentonomy_commerce.budget_network import NetworkConfig
from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig


TOKEN = "0x" + "11" * 20
EXECUTOR = "0x" + "22" * 20
PAYEE = "0x" + "33" * 20
OWNER = "0x" + "44" * 20
BUYER = "0x" + "55" * 20
IDENTITY = "0x" + "66" * 20
REPUTATION = "0x" + "77" * 20
ORIGIN = "http://127.0.0.1:8080"
AGENT_URI = ORIGIN + "/agent.json"
AGENT_ID = 0
BLOCK = 100
BLOCK_HASH = "0x" + "ab" * 32
CODE = "0x6000600055"
IDENTITY_CODE_HASH = "0x" + keccak(bytes.fromhex(CODE[2:])).hex()
REPUTATION_CODE_HASH = "0x" + keccak(bytes.fromhex(CODE[2:])).hex()
HASH = "0x" + "12" * 32
TX_HASH = "0x" + "34" * 32


def network(**changes):
    values = {
        "mode": "local_anvil",
        "chain_id": 31337,
        "rpc_urls": ("http://127.0.0.1:8545", "http://127.0.0.1:8546"),
        "token": TOKEN,
        "executor": EXECUTOR,
        "payee": PAYEE,
    }
    values.update(changes)
    return NetworkConfig(**values)


def config(**changes):
    values = {
        "identity_registry": IDENTITY,
        "reputation_registry": REPUTATION,
        "agent_id": AGENT_ID,
        "agent_owner": OWNER,
        "agent_uri": AGENT_URI,
        "identity_code_hash": IDENTITY_CODE_HASH,
        "reputation_code_hash": REPUTATION_CODE_HASH,
        "identity_implementation": None,
        "identity_implementation_code_hash": None,
        "reputation_implementation": None,
        "reputation_implementation_code_hash": None,
    }
    values.update(changes)
    return RegistryConfig.from_dict(
        values,
        network=network(),
        origin=ORIGIN,
    )


def test_registry_config_is_strict_and_requires_canonical_origin_uri():
    values = {
        "identity_registry": IDENTITY,
        "reputation_registry": REPUTATION,
        "agent_id": 0,
        "agent_owner": OWNER,
        "agent_uri": AGENT_URI,
        "identity_code_hash": IDENTITY_CODE_HASH,
        "reputation_code_hash": REPUTATION_CODE_HASH,
        "identity_implementation": None,
        "identity_implementation_code_hash": None,
        "reputation_implementation": None,
        "reputation_implementation_code_hash": None,
    }
    with pytest.raises(ValueError, match="unknown or missing"):
        RegistryConfig.from_dict(values | {"extra": True}, network=network(), origin=ORIGIN)
    with pytest.raises(ValueError, match="agent_uri"):
        RegistryConfig.from_dict(values | {"agent_uri": ORIGIN + "/other.json"}, network=network(), origin=ORIGIN)
    with pytest.raises(ValueError, match="implementation"):
        RegistryConfig.from_dict(
            values | {"identity_implementation": "0x" + "88" * 20},
            network=network(),
            origin=ORIGIN,
        )


@pytest.mark.parametrize("bad_id", [-1, True, "0"])
def test_registry_config_rejects_invalid_agent_ids(bad_id):
    with pytest.raises(ValueError, match="agent_id"):
        config(agent_id=bad_id)


def test_registry_config_rejects_non_local_chain():
    public = network(
        mode="monad_testnet",
        chain_id=10143,
        rpc_urls=("https://rpc1.example", "https://rpc2.example"),
    )
    with pytest.raises(ValueError, match="implementation"):
        RegistryConfig.from_dict(
            {
                "identity_registry": IDENTITY,
                "reputation_registry": REPUTATION,
                "agent_id": AGENT_ID,
                "agent_owner": OWNER,
                "agent_uri": "https://csv.example/agent.json",
                "identity_code_hash": IDENTITY_CODE_HASH,
                "reputation_code_hash": REPUTATION_CODE_HASH,
                "identity_implementation": None,
                "identity_implementation_code_hash": None,
                "reputation_implementation": None,
                "reputation_implementation_code_hash": None,
            },
            network=public,
            origin="https://csv.example",
        )


def _selector(signature):
    return keccak(text=signature)[:4].hex()


def _word(value):
    return f"{value:064x}"


class FakeRpc:
    def __init__(self, state, url):
        self.state = state
        self.url = url
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return hex(self.state.chain_id)
        if method == "eth_getBlockByNumber":
            tag = params[0]
            if tag == "finalized" or int(tag, 16) == BLOCK:
                return {"number": hex(BLOCK), "hash": self.state.block_hash}
        if method == "eth_getCode":
            code = self.state.code.get(params[0].lower(), CODE)
            return code
        if method == "eth_getStorageAt":
            return self.state.storage.get(params[0].lower(), "0x" + "00" * 32)
        if method == "eth_call":
            request, block = params
            assert block == hex(BLOCK)
            data = request["data"]
            if request["to"].lower() == IDENTITY:
                return self.state.identity_call(data)
            if request["to"].lower() == REPUTATION:
                return self.state.reputation_call(data)
        if method == "eth_getTransactionReceipt":
            return self.state.receipt
        if method == "eth_getTransactionByHash":
            return self.state.transaction
        raise AssertionError(f"unexpected RPC call {method} {params}")


class State:
    chain_id = 31337
    block_hash = BLOCK_HASH
    code = {IDENTITY: CODE, REPUTATION: CODE}
    storage = {}
    identity_owner = OWNER
    identity_wallet = PAYEE
    identity_uri = AGENT_URI
    authorized = True
    revoked = False
    feedback_index = 1
    score = 87
    feedback_uri = ORIGIN + "/erc8004/feedback/" + HASH[2:] + ".json"
    transaction = None
    receipt = None

    def identity_call(self, data):
        selector = data[2:10]
        argument = bytes.fromhex(data[10:])
        if selector == _selector("ownerOf(uint256)"):
            return "0x" + encode(["address"], [self.identity_owner]).hex()
        if selector == _selector("getAgentWallet(uint256)"):
            return "0x" + encode(["address"], [self.identity_wallet]).hex()
        if selector == _selector("tokenURI(uint256)"):
            return "0x" + encode(["string"], [self.identity_uri]).hex()
        if selector == _selector("isAuthorizedOrOwner(address,uint256)"):
            return "0x" + encode(["bool"], [self.authorized]).hex()
        raise AssertionError(f"unexpected identity selector {selector} {argument.hex()}")

    def reputation_call(self, data):
        selector = data[2:10]
        if selector == _selector("getIdentityRegistry()"):
            return "0x" + encode(["address"], [IDENTITY]).hex()
        if selector == _selector("readFeedback(uint256,address,uint64)"):
            return "0x" + encode(
                ["int128", "uint8", "string", "string", "bool"],
                [self.score, 0, "starred", "csv-reconciliation", self.revoked],
            ).hex()
        raise AssertionError(f"unexpected reputation selector {selector}")


def _factory(state):
    clients = []

    def factory(url):
        client = FakeRpc(state, url)
        clients.append(client)
        return client

    factory.clients = clients
    return factory


def _client(state=None, *, cfg=None):
    state = state or State()
    return ERC8004Client(network(), cfg or config(), client_factory=_factory(state))


def test_identity_is_verified_against_owner_wallet_uri_and_runtime_code():
    identity = _client().verify_identity()
    assert identity == {
        "verified": True,
        "agent_registry": f"eip155:31337:{IDENTITY}",
        "agent_id": 0,
        "agent_owner": OWNER,
        "agent_wallet": PAYEE,
        "agent_uri": AGENT_URI,
        "chain_id": 31337,
        "block_number": BLOCK,
        "block_hash": BLOCK_HASH,
    }


def test_registration_pending_is_inactive_but_still_performs_code_checks():
    state = State()
    pending = _client(state, cfg=config(agent_id=None))
    assert pending.verify_identity() is None
    document = pending.registration_document()
    assert document["active"] is False
    assert document["registrations"] == []
    assert document["image"] == ORIGIN + "/agent.svg"
    assert document["services"] == [{"name": "web", "endpoint": ORIGIN + "/"}]
    assert document["x402Support"] is False
    assert document["supportedTrust"] == ["reputation"]
    assert all(name != "MCP" for row in document["services"] for name in [row["name"]])


def test_feedback_transaction_uses_normative_abi_and_fixed_zero_value():
    state = State()
    state.authorized = False
    client = _client(state)
    transaction = client.feedback_transaction(
        buyer=BUYER,
        score=87,
        feedback_uri=state.feedback_uri,
        feedback_hash=HASH,
    )
    expected_args = encode(
        ["uint256", "int128", "uint8", "string", "string", "string", "string", "bytes32"],
        [AGENT_ID, 87, 0, "starred", "csv-reconciliation", ORIGIN + "/", state.feedback_uri, bytes.fromhex(HASH[2:])],
    )
    assert transaction == {
        "from": BUYER,
        "to": REPUTATION,
        "chainId": hex(31337),
        "value": "0x0",
        "data": "0x" + _selector("giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)") + expected_args.hex(),
        "gas": hex(network().gas_limit),
    }


def test_feedback_transaction_rejects_authorized_agent_reviewer():
    state = State()
    state.authorized = True
    with pytest.raises(ValueError, match="owner or authorized"):
        _client(state).feedback_transaction(
            buyer=BUYER,
            score=1,
            feedback_uri=state.feedback_uri,
            feedback_hash=HASH,
        )


def _feedback_fixture(state):
    state.authorized = False
    client = _client(state)
    tx = client.feedback_transaction(
        buyer=BUYER,
        score=state.score,
        feedback_uri=state.feedback_uri,
        feedback_hash=HASH,
    )
    input_data = tx["data"]
    event_topic = "0x" + keccak(
        text="NewFeedback(uint256,address,uint64,int128,uint8,string,string,string,string,string,bytes32)"
    ).hex()
    event_data = encode(
        ["uint64", "int128", "uint8", "string", "string", "string", "string", "bytes32"],
        [state.feedback_index, state.score, 0, "starred", "csv-reconciliation", ORIGIN + "/", state.feedback_uri, bytes.fromhex(HASH[2:])],
    )
    topics = [
        event_topic,
        "0x" + _word(AGENT_ID),
        "0x" + "00" * 12 + BUYER[2:],
        "0x" + keccak(text="starred").hex(),
    ]
    state.transaction = {
        "hash": TX_HASH,
        "from": BUYER,
        "to": REPUTATION,
        "chainId": hex(31337),
        "value": "0x0",
        "input": input_data,
        "data": input_data,
        "gas": hex(network().gas_limit),
        "blockNumber": hex(BLOCK),
        "blockHash": BLOCK_HASH,
        "nonce": "0x1",
    }
    state.receipt = {
        "transactionHash": TX_HASH,
        "from": BUYER,
        "to": REPUTATION,
        "status": "0x1",
        "blockNumber": hex(BLOCK),
        "blockHash": BLOCK_HASH,
        "logs": [
            {
                "address": REPUTATION,
                "topics": topics,
                "data": "0x" + event_data.hex(),
                "blockNumber": hex(BLOCK),
                "blockHash": BLOCK_HASH,
                "transactionHash": TX_HASH,
                "logIndex": "0x0",
            }
        ],
    }
    return tx


def test_feedback_verification_accepts_exact_event_and_preserves_revocation_state(monkeypatch):
    state = State()
    tx = _feedback_fixture(state)
    import agentonomy_commerce.erc8004 as erc8004

    monkeypatch.setattr(erc8004, "fetch_observations", lambda _config, _tx_hash: [
        {
            "chain_id": hex(31337),
            "transaction": state.transaction,
            "receipt": state.receipt,
            "canonical_block": {"number": hex(BLOCK), "hash": BLOCK_HASH},
            "finality": {
                "kind": "local",
                "verified": True,
                "canonical_block": {"number": hex(BLOCK), "hash": BLOCK_HASH},
            },
        },
        {
            "chain_id": hex(31337),
            "transaction": dict(state.transaction),
            "receipt": dict(state.receipt),
            "canonical_block": {"number": hex(BLOCK), "hash": BLOCK_HASH},
            "finality": {
                "kind": "local",
                "verified": True,
                "canonical_block": {"number": hex(BLOCK), "hash": BLOCK_HASH},
            },
        },
    ])
    state.revoked = True
    evidence = _client(state).verify_feedback(buyer=BUYER, tx_hash=TX_HASH, transaction=tx)
    assert evidence["feedback_index"] == state.feedback_index
    assert evidence["is_revoked"] is True


def test_feedback_verification_returns_none_for_pending(monkeypatch):
    import agentonomy_commerce.erc8004 as erc8004

    monkeypatch.setattr(erc8004, "fetch_observations", lambda *_args: None)
    assert _client().verify_feedback(buyer=BUYER, tx_hash=TX_HASH, transaction={}) is None


def test_registration_transaction_is_unsigned_and_requires_no_agent_id():
    tx = _client(cfg=config(agent_id=None)).registration_transaction()
    args = encode(["string"], [AGENT_URI])
    assert tx == {
        "from": OWNER,
        "to": IDENTITY,
        "chainId": hex(31337),
        "value": "0x0",
        "data": "0x" + _selector("register(string)") + args.hex(),
        "gas": hex(network().gas_limit),
    }


def test_registration_transaction_does_not_read_a_preexisting_agent():
    state = State()
    state.identity_owner = "0x" + "99" * 20
    assert _client(state).registration_transaction()["from"] == OWNER


def test_storage_method_is_whitelisted_but_broadcast_methods_remain_blocked():
    assert "eth_getStorageAt" in budget_network.READ_METHODS
    rpc = budget_network.RpcClient("http://127.0.0.1:8545")
    with pytest.raises(ValueError):
        rpc.call("eth_sendTransaction", [])
