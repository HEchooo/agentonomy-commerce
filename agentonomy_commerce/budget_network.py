"""Explicit local/Monad testnet network boundary. No implicit production defaults."""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit

import httpx

READ_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getBlockByHash",
    "eth_getTransactionReceipt", "eth_getTransactionByHash", "eth_getCode",
    "eth_getBalance", "eth_call", "eth_estimateGas", "eth_gasPrice",
    "eth_getTransactionCount", "eth_maxPriorityFeePerGas", "web3_clientVersion",
})


class _OversizedResponse(ValueError):
    """Keep an oversized response fail-fast instead of retrying its payload."""


def address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or int(value, 16) == 0:
        raise ValueError("nonzero EVM address required")
    return value.lower()


def rpc_url(value: str, *, local: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("RPC URL required")
    url = urlsplit(value)
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise ValueError("RPC URL cannot contain credentials, query or fragment")
    if local:
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("local Anvil must use loopback HTTP")
    elif url.scheme != "https":
        raise ValueError("public testnet RPC must use HTTPS")
    return value.rstrip("/")


@dataclass(frozen=True)
class NetworkConfig:
    mode: str
    chain_id: int
    rpc_urls: tuple[str, str]
    token: str
    executor: str
    payee: str
    token_decimals: int = 6
    gas_limit: int = 500_000
    max_gas_price_wei: int = 500_000_000_000

    def __post_init__(self):
        if self.mode not in {"local_anvil", "monad_testnet"}:
            raise ValueError("mode must explicitly select local_anvil or monad_testnet")
        expected = 31337 if self.mode == "local_anvil" else 10143
        if type(self.chain_id) is not int or self.chain_id != expected:
            raise ValueError("chain ID does not match selected test environment")
        if type(self.token_decimals) is not int or self.token_decimals != 6:
            raise ValueError("v1 supports only a six-decimal TestUSD asset")
        if type(self.gas_limit) is not int or not 21_000 <= self.gas_limit <= 2_000_000:
            raise ValueError("gas limit exceeds bounded test payment range")
        if type(self.max_gas_price_wei) is not int or not 0 < self.max_gas_price_wei <= 10**13:
            raise ValueError("gas price cap is invalid")
        for key in ("token", "executor", "payee"):
            object.__setattr__(self, key, address(getattr(self, key)))
        if self.token == self.executor:
            raise ValueError("token and executor must differ")
        if not isinstance(self.rpc_urls, (tuple, list)) or len(self.rpc_urls) != 2:
            raise ValueError("exactly two RPC observations are required")
        urls = tuple(rpc_url(url, local=self.mode == "local_anvil") for url in self.rpc_urls)
        if self.mode == "monad_testnet" and urlsplit(urls[0]).hostname == urlsplit(urls[1]).hostname:
            raise ValueError("public testnet requires independently operated RPC hosts")
        object.__setattr__(self, "rpc_urls", urls)

    @property
    def network(self) -> str:
        return f"eip155:{self.chain_id}"


def verified_boundary(config: NetworkConfig, finalized_height: int) -> int:
    """Monad Verified = finalized - execution delay (currently three blocks).

    Source: docs.monad.xyz/monad-arch/consensus/block-states, checked 2026-10-03.
    This versioned rule must be rechecked before public deployment after upgrades.
    Local Anvil has synchronous execution and is explicitly a different mode.
    """
    delay = 3 if config.mode == "monad_testnet" else 0
    if type(finalized_height) is not int or finalized_height < delay:
        raise ValueError("verified execution boundary is unavailable")
    return finalized_height - delay


class RpcClient:
    def __init__(self, url: str, *, writable: bool = False):
        self.url = url
        self.writable = writable

    def call(self, method: str, params: list):
        if method not in READ_METHODS and not (self.writable and method == "eth_sendRawTransaction"):
            raise ValueError("RPC method is outside this client's scope")
        max_attempts = 3 if method in READ_METHODS else 1
        retry_delays = (0.1, 0.2)
        for attempt in range(max_attempts):
            try:
                with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                    response = client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise _OversizedResponse("RPC response too large")
                    data = response.json()
            except _OversizedResponse:
                raise RuntimeError("RPC request failed; status may be unknown") from None
            except (httpx.HTTPError, ValueError):
                if attempt + 1 < max_attempts:
                    time.sleep(retry_delays[attempt])
                    continue
                raise RuntimeError("RPC request failed; status may be unknown") from None
            if not isinstance(data, dict) or data.get("id") != 1 or "error" in data or "result" not in data:
                if attempt + 1 < max_attempts:
                    time.sleep(retry_delays[attempt])
                    continue
                raise RuntimeError("RPC returned invalid or failed response")
            return data["result"]

    def check_chain(self, chain_id: int):
        if quantity(self.call("eth_chainId", [])) != chain_id:
            raise ValueError("RPC chain ID mismatch")


def quantity(value) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
        raise ValueError("invalid RPC quantity")
    return int(value, 16)
