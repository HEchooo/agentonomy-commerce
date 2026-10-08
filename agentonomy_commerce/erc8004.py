"""Read-only ERC-8004 identity and reputation registry boundary.

The adapter deliberately stops at unsigned transaction construction and
independent verification.  It has no signer, wallet, credential or broadcast
path.  Registry addresses, runtime hashes, implementation pins and service
metadata are all operator supplied and validated before they are used.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from eth_abi import decode, encode
from eth_utils import keccak

from agentonomy_commerce.budget_network import (
    NetworkConfig,
    RpcClient,
    address,
    quantity,
    verified_boundary,
)
from agentonomy_commerce.budget_observations import fetch_observations


_CONFIG_FIELDS = frozenset(
    {
        "identity_registry",
        "reputation_registry",
        "agent_id",
        "agent_owner",
        "agent_uri",
        "identity_code_hash",
        "reputation_code_hash",
        "identity_implementation",
        "identity_implementation_code_hash",
        "reputation_implementation",
        "reputation_implementation_code_hash",
    }
)

_EIP1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)

_IDENTITY_OWNER = keccak(text="ownerOf(uint256)")[:4]
_IDENTITY_WALLET = keccak(text="getAgentWallet(uint256)")[:4]
_IDENTITY_URI = keccak(text="tokenURI(uint256)")[:4]
_REGISTER = keccak(text="register(string)")[:4]
_AUTHORIZED = keccak(text="isAuthorizedOrOwner(address,uint256)")[:4]
_GIVE_FEEDBACK = keccak(
    text="giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)"
)[:4]
_READ_FEEDBACK = keccak(text="readFeedback(uint256,address,uint64)")[:4]
_LINKED_IDENTITY = keccak(text="getIdentityRegistry()")[:4]
_NEW_FEEDBACK_TOPIC = keccak(
    text="NewFeedback(uint256,address,uint64,int128,uint8,string,string,string,string,string,bytes32)"
).hex()

_FEEDBACK_TYPES = (
    "uint256",
    "int128",
    "uint8",
    "string",
    "string",
    "string",
    "string",
    "bytes32",
)
_READ_FEEDBACK_TYPES = ("int128", "uint8", "string", "string", "bool")
_EVENT_DATA_TYPES = (
    "uint64",
    "int128",
    "uint8",
    "string",
    "string",
    "string",
    "string",
    "bytes32",
)


def _selector_bytes(value: bytes) -> str:
    return value.hex()


def _hex_bytes(value: object, *, name: str, size: int | None = None) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]*", value):
        raise ValueError(f"{name} must be hexadecimal")
    raw = bytes.fromhex(value[2:])
    if size is not None and len(raw) != size:
        raise ValueError(f"{name} must be {size} bytes")
    return raw


def _hash(value: object, *, name: str = "hash") -> str:
    raw = _hex_bytes(value, name=name, size=32)
    if not any(raw):
        raise ValueError(f"{name} must be nonzero")
    return "0x" + raw.hex()


def _tx_hash(value: object) -> str:
    return _hash(value, name="transaction hash")


def _block_hash(value: object) -> str:
    return _hash(value, name="block hash")


def _code_hash(value: object, *, name: str) -> str:
    raw = _hex_bytes(value, name=name, size=32)
    if not any(raw):
        raise ValueError(f"{name} must be nonzero")
    return "0x" + raw.hex()


def _validate_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or any(ord(ch) < 0x20 for ch in value):
        raise ValueError(f"{name} must be a nonempty text value")
    return value


def _origin(value: object, *, network: NetworkConfig) -> str:
    if not isinstance(value, str):
        raise ValueError("origin must be a URL")
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
    ):
        raise ValueError("origin must be an origin without credentials, path, query or fragment")
    try:
        parsed.port
    except ValueError:
        raise ValueError("origin has an invalid port") from None
    expected_scheme = "http" if network.mode == "local_anvil" else "https"
    if parsed.scheme != expected_scheme:
        raise ValueError(f"origin must use {expected_scheme}")
    if network.mode == "local_anvil" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("local origin must use loopback")
    return value.rstrip("/")


def _network_fingerprint(network: NetworkConfig) -> tuple[object, ...]:
    return (
        network.mode,
        network.chain_id,
        tuple(network.rpc_urls),
        network.token,
        network.executor,
        network.payee,
        network.token_decimals,
        network.gas_limit,
        network.max_gas_price_wei,
    )


@dataclass(frozen=True)
class RegistryConfig:
    identity_registry: str
    reputation_registry: str
    agent_id: int | None
    agent_owner: str
    agent_uri: str
    identity_code_hash: str
    reputation_code_hash: str
    identity_implementation: str | None
    identity_implementation_code_hash: str | None
    reputation_implementation: str | None
    reputation_implementation_code_hash: str | None

    def __post_init__(self) -> None:
        for field in ("identity_registry", "reputation_registry", "agent_owner"):
            object.__setattr__(self, field, address(getattr(self, field)))
        if self.identity_registry == self.reputation_registry:
            raise ValueError("identity and reputation registries must differ")
        if self.agent_id is not None and (
            type(self.agent_id) is not int or not 0 <= self.agent_id < 2**256
        ):
            raise ValueError("agent_id must be a nonnegative integer or None")
        object.__setattr__(self, "agent_uri", _validate_text(self.agent_uri, name="agent_uri"))
        object.__setattr__(
            self,
            "identity_code_hash",
            _code_hash(self.identity_code_hash, name="identity_code_hash"),
        )
        object.__setattr__(
            self,
            "reputation_code_hash",
            _code_hash(self.reputation_code_hash, name="reputation_code_hash"),
        )
        impl = (
            self.identity_implementation,
            self.identity_implementation_code_hash,
            self.reputation_implementation,
            self.reputation_implementation_code_hash,
        )
        if any(item is None for item in impl) and not all(item is None for item in impl):
            raise ValueError("implementation address and code hash pins must be complete")
        if self.identity_implementation is not None:
            object.__setattr__(self, "identity_implementation", address(self.identity_implementation))
            object.__setattr__(
                self,
                "reputation_implementation",
                address(self.reputation_implementation),
            )
            object.__setattr__(
                self,
                "identity_implementation_code_hash",
                _code_hash(
                    self.identity_implementation_code_hash,
                    name="identity_implementation_code_hash",
                ),
            )
            object.__setattr__(
                self,
                "reputation_implementation_code_hash",
                _code_hash(
                    self.reputation_implementation_code_hash,
                    name="reputation_implementation_code_hash",
                ),
            )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        *,
        network: NetworkConfig,
        origin: str,
    ) -> "RegistryConfig":
        if not isinstance(value, Mapping):
            raise ValueError("registry config must be an object")
        keys = set(value)
        if keys != _CONFIG_FIELDS:
            raise ValueError("registry config has unknown or missing fields")
        if not isinstance(network, NetworkConfig):
            raise ValueError("registry config requires a validated network")
        normalized_origin = _origin(origin, network=network)
        expected_uri = normalized_origin + "/agent.json"
        if value["agent_uri"] != expected_uri:
            raise ValueError("agent_uri must equal origin + '/agent.json'")
        implementations = (
            value["identity_implementation"],
            value["identity_implementation_code_hash"],
            value["reputation_implementation"],
            value["reputation_implementation_code_hash"],
        )
        if network.mode != "local_anvil" and any(item is None for item in implementations):
            raise ValueError("Monad registry implementation pins are required")
        if any(item is None for item in implementations) and not all(
            item is None for item in implementations
        ):
            raise ValueError("implementation address and code hash pins must be complete")
        config = cls(**dict(value))
        object.__setattr__(config, "_origin", normalized_origin)
        object.__setattr__(config, "_network_fingerprint", _network_fingerprint(network))
        return config


@dataclass(frozen=True)
class _Snapshot:
    clients: tuple[object, object]
    block_number: int
    block_hash: str
    identity: dict[str, object] | None


class ERC8004Client:
    """Pinned, read-only ERC-8004 registry adapter."""

    def __init__(
        self,
        network: NetworkConfig,
        config: RegistryConfig,
        *,
        client_factory=RpcClient,
    ) -> None:
        if not isinstance(network, NetworkConfig):
            raise ValueError("network must be a validated NetworkConfig")
        if not isinstance(config, RegistryConfig):
            raise ValueError("config must be a RegistryConfig")
        bound = getattr(config, "_network_fingerprint", None)
        if bound is not None and bound != _network_fingerprint(network):
            raise ValueError("registry config is bound to a different network")
        self._network = network
        self._config = config
        self._client_factory = client_factory

    @property
    def network(self) -> NetworkConfig:
        return self._network

    @property
    def config(self) -> RegistryConfig:
        return self._config

    @property
    def origin(self) -> str:
        return getattr(self._config, "_origin", self._config.agent_uri[: -len("/agent.json")])

    def _clients(self) -> tuple[object, object]:
        return tuple(self._client_factory(url) for url in self._network.rpc_urls)  # type: ignore[return-value]

    def _common_boundary(self, clients: tuple[object, object]) -> tuple[int, str]:
        boundaries: list[int] = []
        for client in clients:
            chain_id = quantity(client.call("eth_chainId", []))
            if chain_id != self._network.chain_id:
                raise ValueError("RPC chain ID mismatch")
            finalized = client.call("eth_getBlockByNumber", ["finalized", False])
            if not isinstance(finalized, Mapping):
                raise ValueError("RPC finalized block is invalid")
            try:
                finalized_height = quantity(finalized["number"])
            except (KeyError, ValueError):
                raise ValueError("RPC finalized block number is invalid") from None
            boundaries.append(verified_boundary(self._network, finalized_height))
        common = min(boundaries)
        hashes: list[str] = []
        for client in clients:
            block = client.call("eth_getBlockByNumber", [hex(common), False])
            if not isinstance(block, Mapping):
                raise ValueError("RPC verified block is invalid")
            try:
                number = quantity(block["number"])
                block_hash = _block_hash(block["hash"])
            except (KeyError, ValueError):
                raise ValueError("RPC verified block is invalid") from None
            if number != common:
                raise ValueError("RPC returned the wrong verified block")
            hashes.append(block_hash)
        if hashes[0] != hashes[1]:
            raise ValueError("RPC verified boundary hashes disagree")
        return common, hashes[0]

    def _assert_boundary_stable(
        self, clients: tuple[object, object], block_number: int, block_hash: str
    ) -> None:
        for client in clients:
            block = client.call("eth_getBlockByNumber", [hex(block_number), False])
            if not isinstance(block, Mapping):
                raise ValueError("RPC verified block is invalid")
            try:
                number = quantity(block["number"])
                observed_hash = _block_hash(block["hash"])
            except (KeyError, ValueError):
                raise ValueError("RPC verified block is invalid") from None
            if number != block_number or observed_hash != block_hash:
                raise ValueError("verified boundary changed during registry reads")

    def _code_at(
        self,
        client: object,
        registry: str,
        expected_code_hash: str,
        implementation: str | None,
        expected_implementation_hash: str | None,
        block_number: int,
    ) -> None:
        tag = hex(block_number)
        code = client.call("eth_getCode", [registry, tag])
        code_bytes = _hex_bytes(code, name="registry runtime code")
        if not code_bytes:
            raise ValueError("registry runtime code is missing")
        observed_code_hash = "0x" + keccak(code_bytes).hex()
        if observed_code_hash != expected_code_hash:
            raise ValueError("registry runtime code hash does not match operator pin")
        if implementation is None:
            return
        storage = client.call(
            "eth_getStorageAt",
            [registry, _EIP1967_IMPLEMENTATION_SLOT, tag],
        )
        storage_bytes = _hex_bytes(storage, name="EIP-1967 implementation slot", size=32)
        try:
            observed_implementation = address("0x" + storage_bytes[-20:].hex())
        except ValueError:
            raise ValueError("EIP-1967 implementation slot is invalid") from None
        if observed_implementation != implementation:
            raise ValueError("EIP-1967 implementation address does not match operator pin")
        implementation_code = client.call("eth_getCode", [implementation, tag])
        implementation_bytes = _hex_bytes(implementation_code, name="implementation runtime code")
        if not implementation_bytes:
            raise ValueError("implementation runtime code is missing")
        observed_implementation_hash = "0x" + keccak(implementation_bytes).hex()
        if observed_implementation_hash != expected_implementation_hash:
            raise ValueError("implementation runtime code hash does not match operator pin")

    def _runtime_at(self, clients: tuple[object, object], block_number: int) -> None:
        config = self._config
        for client in clients:
            self._code_at(
                client,
                config.identity_registry,
                config.identity_code_hash,
                config.identity_implementation,
                config.identity_implementation_code_hash,
                block_number,
            )
            self._code_at(
                client,
                config.reputation_registry,
                config.reputation_code_hash,
                config.reputation_implementation,
                config.reputation_implementation_code_hash,
                block_number,
            )
            linked_identity = address(self._decode(self._call(
                client, config.reputation_registry, _LINKED_IDENTITY, [], block_number
            ), ["address"], name="getIdentityRegistry")[0])
            if linked_identity != config.identity_registry:
                raise ValueError("reputation Identity registry binding does not match operator pin")

    @staticmethod
    def _call(client: object, target: str, selector: bytes, args: list[object], block_number: int) -> str:
        data = "0x" + _selector_bytes(selector) + encode(
            ["uint256" if isinstance(arg, int) else "address" for arg in args], args
        ).hex()
        result = client.call("eth_call", [{"to": target, "data": data}, hex(block_number)])
        if not isinstance(result, str):
            raise ValueError("RPC eth_call result is invalid")
        return result

    @staticmethod
    def _decode(result: str, types: list[str] | tuple[str, ...], *, name: str):
        try:
            raw = _hex_bytes(result, name=name)
            values = decode(types, raw)
            if encode(list(types), list(values)) != raw:
                raise ValueError("non-canonical ABI response")
            return values
        except Exception as exc:
            raise ValueError(f"{name} ABI response is invalid") from exc

    def _identity_at(
        self, clients: tuple[object, object], block_number: int
    ) -> dict[str, object] | None:
        if self._config.agent_id is None:
            return None
        agent_id = self._config.agent_id
        rows: list[tuple[str, str, str]] = []
        for client in clients:
            owner = address(
                self._decode(
                    self._call(client, self._config.identity_registry, _IDENTITY_OWNER, [agent_id], block_number),
                    ["address"],
                    name="ownerOf",
                )[0]
            )
            wallet = address(
                self._decode(
                    self._call(client, self._config.identity_registry, _IDENTITY_WALLET, [agent_id], block_number),
                    ["address"],
                    name="getAgentWallet",
                )[0]
            )
            uri = self._decode(
                self._call(client, self._config.identity_registry, _IDENTITY_URI, [agent_id], block_number),
                ["string"],
                name="tokenURI",
            )[0]
            if not isinstance(uri, str):
                raise ValueError("tokenURI response is invalid")
            rows.append((owner, wallet, uri))
        if rows[0] != rows[1]:
            raise ValueError("identity RPC observations disagree")
        owner, wallet, uri = rows[0]
        if owner != self._config.agent_owner:
            raise ValueError("registry owner does not match configured agent_owner")
        if wallet != self._network.payee:
            raise ValueError("registry agent wallet does not match network payee")
        if uri != self._config.agent_uri:
            raise ValueError("registry agent URI does not match configured agent_uri")
        return {
            "agent_registry": f"eip155:{self._network.chain_id}:{self._config.identity_registry}",
            "agent_id": agent_id,
            "agent_owner": owner,
            "agent_wallet": wallet,
            "agent_uri": uri,
        }

    def _snapshot(self) -> _Snapshot:
        clients, block_number, block_hash = self._runtime_snapshot()
        identity = self._identity_at(clients, block_number)
        self._assert_boundary_stable(clients, block_number, block_hash)
        return _Snapshot(clients, block_number, block_hash, identity)

    def _runtime_snapshot(self) -> tuple[tuple[object, object], int, str]:
        clients = self._clients()
        block_number, block_hash = self._common_boundary(clients)
        self._runtime_at(clients, block_number)
        self._assert_boundary_stable(clients, block_number, block_hash)
        return clients, block_number, block_hash

    def verify_identity(self) -> dict[str, object] | None:
        snapshot = self._snapshot()
        if snapshot.identity is None:
            return None
        return snapshot.identity | {
            "verified": True,
            "chain_id": self._network.chain_id,
            "block_number": snapshot.block_number,
            "block_hash": snapshot.block_hash,
        }

    def registration_document(self) -> dict[str, object]:
        snapshot = self._snapshot()
        identity = snapshot.identity
        registrations = []
        if identity is not None:
            registrations.append(
                {
                    "agentId": identity["agent_id"],
                    "agentRegistry": identity["agent_registry"],
                }
            )
        return {
            "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
            "name": "Agentonomy CSV Reconciliation",
            "description": "CSV service",
            "image": self.origin + "/agent.svg",
            "services": [{"name": "web", "endpoint": self.origin + "/"}],
            "x402Support": False,
            "active": identity is not None,
            "registrations": registrations,
            "supportedTrust": ["reputation"],
        }

    def _authorization(self, snapshot: _Snapshot, buyer: str) -> None:
        if self._config.agent_id is None:
            raise ValueError("feedback requires a registered agent")
        outcomes: list[bool] = []
        args = [buyer, self._config.agent_id]
        for client in snapshot.clients:
            result = self._call(
                client,
                self._config.identity_registry,
                _AUTHORIZED,
                args,
                snapshot.block_number,
            )
            decoded = self._decode(result, ["bool"], name="isAuthorizedOrOwner")
            if type(decoded[0]) is not bool:
                raise ValueError("isAuthorizedOrOwner response is invalid")
            outcomes.append(decoded[0])
        if outcomes[0] != outcomes[1]:
            raise ValueError("authorization RPC observations disagree")
        self._assert_boundary_stable(snapshot.clients, snapshot.block_number, snapshot.block_hash)
        if outcomes[0]:
            raise ValueError("buyer is the agent owner or authorized operator")

    def feedback_transaction(
        self,
        *,
        buyer: str,
        score: int,
        feedback_uri: str,
        feedback_hash: str,
    ) -> dict[str, object]:
        buyer = address(buyer)
        if type(score) is not int or not 0 <= score <= 100:
            raise ValueError("score must be an integer from 0 through 100")
        feedback_uri = _validate_text(feedback_uri, name="feedback_uri")
        feedback_hash = _hash(feedback_hash, name="feedback_hash")
        snapshot = self._snapshot()
        if snapshot.identity is None:
            raise ValueError("feedback requires a registered agent")
        self._authorization(snapshot, buyer)
        data = _selector_bytes(_GIVE_FEEDBACK) + encode(
            list(_FEEDBACK_TYPES),
            [
                self._config.agent_id,
                score,
                0,
                "starred",
                "csv-reconciliation",
                self.origin + "/",
                feedback_uri,
                bytes.fromhex(feedback_hash[2:]),
            ],
        ).hex()
        return {
            "from": buyer,
            "to": self._config.reputation_registry,
            "chainId": hex(self._network.chain_id),
            "value": "0x0",
            "data": "0x" + data,
            "gas": hex(self._network.gas_limit),
        }

    def registration_transaction(self) -> dict[str, object]:
        # Registration is the one identity operation that intentionally does
        # not require a pre-existing agent ID or tokenURI/owner observation.
        self._runtime_snapshot()
        data = _selector_bytes(_REGISTER) + encode(["string"], [self._config.agent_uri]).hex()
        return {
            "from": self._config.agent_owner,
            "to": self._config.identity_registry,
            "chainId": hex(self._network.chain_id),
            "value": "0x0",
            "data": "0x" + data,
            "gas": hex(self._network.gas_limit),
        }

    @staticmethod
    def _transaction_data(transaction: Mapping[str, object], buyer: str, expected: "ERC8004Client") -> tuple[int, str, str]:
        if not isinstance(transaction, Mapping):
            raise ValueError("feedback transaction is required")
        fields = {"from", "to", "chainId", "value", "data", "gas"}
        if set(transaction) != fields:
            raise ValueError("feedback transaction fields are not fixed")
        if address(transaction["from"]) != buyer or address(transaction["to"]) != expected.config.reputation_registry:
            raise ValueError("feedback transaction recipient or buyer mismatch")
        if not isinstance(transaction["chainId"], str) or quantity(transaction["chainId"]) != expected.network.chain_id:
            raise ValueError("feedback transaction chain mismatch")
        if transaction["value"] != "0x0":
            raise ValueError("feedback transaction must have zero native value")
        if not isinstance(transaction["gas"], str) or quantity(transaction["gas"]) != expected.network.gas_limit:
            raise ValueError("feedback transaction gas mismatch")
        data = transaction["data"]
        raw = _hex_bytes(data, name="feedback calldata")
        if raw[:4] != _GIVE_FEEDBACK:
            raise ValueError("feedback transaction selector mismatch")
        try:
            args = decode(list(_FEEDBACK_TYPES), raw[4:])
            if encode(list(_FEEDBACK_TYPES), list(args)) != raw[4:]:
                raise ValueError("non-canonical feedback calldata")
        except Exception as exc:
            raise ValueError("feedback calldata ABI is invalid") from exc
        if args[0] != expected.config.agent_id or args[2] != 0:
            raise ValueError("feedback calldata agent or decimals mismatch")
        if type(args[1]) is not int or not 0 <= args[1] <= 100:
            raise ValueError("feedback score is invalid")
        if args[3] != "starred" or args[4] != "csv-reconciliation" or args[5] != expected.origin + "/":
            raise ValueError("feedback calldata tags or endpoint mismatch")
        if not isinstance(args[6], str):
            raise ValueError("feedback URI is invalid")
        return args[1], args[6], "0x" + bytes(args[7]).hex()

    def _observed_feedback(
        self,
        *,
        buyer: str,
        tx_hash: str,
        transaction: Mapping[str, object],
        observations: list[Mapping[str, object]],
    ) -> tuple[dict[str, object], int, str, str, str]:
        if len(observations) != 2:
            raise ValueError("two RPC observations required")
        first = observations[0]
        second = observations[1]
        if not isinstance(first, Mapping) or not isinstance(second, Mapping):
            raise ValueError("feedback RPC observations are malformed")
        for key in ("transaction", "receipt", "canonical_block", "finality"):
            if first.get(key) != second.get(key):
                raise ValueError(f"feedback RPC {key} observations disagree")
        try:
            tx = first["transaction"]
            receipt = first["receipt"]
            canonical = first["canonical_block"]
            finality = first["finality"]
            if not all(isinstance(value, Mapping) for value in (tx, receipt, canonical, finality)):
                raise ValueError("feedback transaction observation is malformed")
            boundary = finality["canonical_block"]
            if not isinstance(boundary, Mapping):
                raise ValueError("feedback finality block is malformed")
            chain_id = quantity(first["chain_id"])
            block_number = quantity(tx["blockNumber"])
            receipt_block = quantity(receipt["blockNumber"])
            canonical_number = quantity(canonical["number"])
            boundary_number = quantity(boundary["number"])
            block_hash = _block_hash(tx["blockHash"])
            receipt_hash = _block_hash(receipt["blockHash"])
            canonical_hash = _block_hash(canonical["hash"])
            boundary_hash = _block_hash(boundary["hash"])
            observed_tx_hash = _tx_hash(tx["hash"])
            observed_receipt_hash = _tx_hash(receipt["transactionHash"])
            receipt_status = quantity(receipt["status"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("feedback transaction observation is malformed") from None
        if (
            chain_id != self._network.chain_id
            or finality.get("verified") is not True
            or finality.get("kind") != ("monad_verified" if self._network.mode == "monad_testnet" else "local")
            or observed_tx_hash != tx_hash
            or observed_receipt_hash != tx_hash
            or receipt_block != block_number
            or canonical_number != block_number
            or receipt_hash != block_hash
            or canonical_hash != block_hash
            or boundary_number < block_number
            or boundary_hash != _block_hash(boundary["hash"])
            or block_number > boundary_number
            or receipt_status != 1
        ):
            raise ValueError("feedback transaction is not independently final and canonical")
        try:
            sender = address(tx["from"])
            recipient = address(tx["to"])
            tx_chain_id = quantity(tx["chainId"])
            tx_value = quantity(tx["value"])
            tx_gas = quantity(tx["gas"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("feedback transaction fields are malformed") from None
        if sender != buyer or recipient != self._config.reputation_registry:
            raise ValueError("feedback transaction sender or recipient mismatch")
        if tx_chain_id != self._network.chain_id:
            raise ValueError("feedback transaction chain ID mismatch")
        if tx_value != 0 or tx_gas != self._network.gas_limit:
            raise ValueError("feedback transaction value or gas mismatch")
        observed_data = tx.get("input")
        if "data" in tx:
            if observed_data is not None and _hex_bytes(observed_data, name="transaction input") != _hex_bytes(tx["data"], name="transaction data"):
                raise ValueError("RPC transaction calldata fields disagree")
            observed_data = tx["data"]
        candidate_data = transaction.get("data")
        if not isinstance(candidate_data, str) or not isinstance(observed_data, str) or observed_data.lower() != candidate_data.lower():
            raise ValueError("feedback transaction calldata mismatch")
        if "from" not in receipt or "to" not in receipt:
            raise ValueError("feedback receipt sender or recipient is missing")
        if address(receipt["from"]) != buyer:
            raise ValueError("feedback receipt sender mismatch")
        if address(receipt["to"]) != self._config.reputation_registry:
            raise ValueError("feedback receipt recipient mismatch")
        score, feedback_uri, feedback_hash = self._transaction_data(transaction, buyer, self)
        logs = receipt.get("logs")
        if not isinstance(logs, list):
            raise ValueError("feedback receipt logs are invalid")
        expected_topic = "0x" + _NEW_FEEDBACK_TOPIC
        matches = []
        for log in logs:
            if not isinstance(log, Mapping):
                raise ValueError("feedback receipt log is invalid")
            topics = log.get("topics")
            if not isinstance(topics, list):
                raise ValueError("feedback receipt log topics are invalid")
            if not topics:
                continue
            if not isinstance(topics[0], str) or topics[0].lower() != expected_topic:
                continue
            if len(topics) != 4:
                raise ValueError("NewFeedback event topics are invalid")
            try:
                if address(log["address"]) != self._config.reputation_registry:
                    continue
                if (
                    quantity(log["blockNumber"]) != block_number
                    or _block_hash(log["blockHash"]) != block_hash
                    or _tx_hash(log["transactionHash"]) != tx_hash
                    or quantity(log["logIndex"]) < 0
                ):
                    continue
                if _hex_bytes(topics[1], name="NewFeedback agent ID", size=32) != self._config.agent_id.to_bytes(32, "big"):
                    continue
                if _hex_bytes(topics[2], name="NewFeedback client", size=32)[-20:] != bytes.fromhex(buyer[2:]):
                    continue
                if _hex_bytes(topics[3], name="NewFeedback tag", size=32) != keccak(text="starred"):
                    continue
                event_data = _hex_bytes(log.get("data"), name="NewFeedback data")
                values = decode(_EVENT_DATA_TYPES, event_data)
                if encode(_EVENT_DATA_TYPES, list(values)) != event_data:
                    raise ValueError("non-canonical NewFeedback data")
            except Exception as exc:
                raise ValueError("NewFeedback event data is invalid") from exc
            index, value, decimals, tag1, tag2, endpoint, uri, event_hash = values
            if (
                index == 0
                or value != score
                or decimals != 0
                or tag1 != "starred"
                or tag2 != "csv-reconciliation"
                or endpoint != self.origin + "/"
                or uri != feedback_uri
                or "0x" + bytes(event_hash).hex() != feedback_hash
            ):
                continue
            matches.append((log, index))
        if len(matches) != 1:
            raise ValueError("exactly one matching NewFeedback event is required")
        event_log, feedback_index = matches[0]
        return (
            dict(
                transaction=tx,
                receipt=receipt,
                block_number=block_number,
                block_hash=block_hash,
                finality_block_number=boundary_number,
                finality_block_hash=boundary_hash,
                feedback_index=feedback_index,
                score=score,
                feedback_uri=feedback_uri,
                feedback_hash=feedback_hash,
                event_log=event_log,
            ),
            boundary_number,
            boundary_hash,
            feedback_uri,
            feedback_hash,
        )

    def _feedback_state_at(
        self,
        clients: tuple[object, object],
        block_number: int,
        buyer: str,
        feedback_index: int,
        expected_score: int,
    ) -> bool:
        if self._config.agent_id is None:
            raise ValueError("feedback requires a registered agent")
        rows = []
        for client in clients:
            result = self._call(
                client,
                self._config.reputation_registry,
                _READ_FEEDBACK,
                [self._config.agent_id, buyer, feedback_index],
                block_number,
            )
            rows.append(self._decode(result, _READ_FEEDBACK_TYPES, name="readFeedback"))
        if rows[0] != rows[1]:
            raise ValueError("readFeedback RPC observations disagree")
        value, decimals, tag1, tag2, revoked = rows[0]
        if (
            value != expected_score
            or decimals != 0
            or tag1 != "starred"
            or tag2 != "csv-reconciliation"
            or type(revoked) is not bool
        ):
            raise ValueError("readFeedback state does not match NewFeedback")
        return revoked

    def verify_feedback(
        self,
        *,
        buyer: str,
        tx_hash: str,
        transaction: Mapping[str, object],
    ) -> dict[str, object] | None:
        buyer = address(buyer)
        tx_hash = _tx_hash(tx_hash)
        observations = fetch_observations(self._network, tx_hash)
        if observations is None:
            return None
        # Validate the candidate before accepting any observed evidence.  A
        # pending hash is intentionally returned as unknown without needing a
        # candidate transaction to be complete.
        if not isinstance(transaction, Mapping):
            raise ValueError("feedback transaction is required")
        self._transaction_data(transaction, buyer, self)
        evidence, boundary_number, boundary_hash, _feedback_uri, _feedback_hash = self._observed_feedback(
            buyer=buyer,
            tx_hash=tx_hash,
            transaction=transaction,
            observations=observations,
        )
        clients = self._clients()
        # The observation verifier proves the transaction boundary.  Re-read
        # the registry code and identity at that same block before accepting
        # reputation state, then re-check the boundary after all reads.
        for client in clients:
            chain_id = quantity(client.call("eth_chainId", []))
            if chain_id != self._network.chain_id:
                raise ValueError("RPC chain ID mismatch")
            block = client.call("eth_getBlockByNumber", [hex(boundary_number), False])
            if not isinstance(block, Mapping) or _block_hash(block.get("hash")) != boundary_hash:
                raise ValueError("feedback verified boundary changed")
        # A later restored implementation cannot justify feedback written in
        # an earlier block under different runtime or registry binding.
        self._runtime_at(clients, evidence["block_number"])
        self._assert_boundary_stable(clients, evidence["block_number"], evidence["block_hash"])
        if boundary_number != evidence["block_number"]:
            self._runtime_at(clients, boundary_number)
        self._identity_at(clients, boundary_number)
        revoked = self._feedback_state_at(
            clients,
            boundary_number,
            buyer,
            evidence["feedback_index"],
            evidence["score"],
        )
        self._assert_boundary_stable(clients, boundary_number, boundary_hash)
        return {
            "transaction_hash": tx_hash,
            "buyer": buyer,
            "agent_registry": f"eip155:{self._network.chain_id}:{self._config.identity_registry}",
            "agent_id": self._config.agent_id,
            "feedback_index": evidence["feedback_index"],
            "score": evidence["score"],
            "feedback_uri": evidence["feedback_uri"],
            "feedback_hash": evidence["feedback_hash"],
            "is_revoked": revoked,
            "chain_id": self._network.chain_id,
            "block_number": evidence["block_number"],
            "block_hash": evidence["block_hash"],
            "finality_block_number": evidence["finality_block_number"],
            "finality_block_hash": evidence["finality_block_hash"],
            "two_rpc_verified": True,
            "verified": True,
        }
