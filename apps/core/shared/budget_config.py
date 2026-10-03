"""Explicit, testnet-only configuration for the budget-contract rail.

The regular :class:`shared.config.AppConfig` deliberately knows only about the
existing production and rehearsal networks.  This profile is a separate
projection used by ``BudgetFundingService``; constructing it never adds a
network to the regular Core profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from services.account_service.schemas import canonicalize_evm_address
from shared.config import AppConfig


_LOCAL_CHAIN_ID = 31337
_MONAD_TESTNET_CHAIN_ID = 10143
_ALLOWED_MODES = {"local_anvil", "monad_testnet"}


def _clean_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    value = value.strip()
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must be non-empty and control-free")
    return value


def _address(value: object, *, field_name: str) -> str:
    try:
        return canonicalize_evm_address(_clean_text(value, field_name=field_name))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a canonical EVM address") from exc


def _allowlist(values: object, *, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (tuple, list)):
        raise ValueError(f"{field_name} must be a non-empty sequence")
    normalized = tuple(dict.fromkeys(_clean_text(value, field_name=field_name) for value in values))
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _rpc_urls(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,) if value.strip() else ()
    if value is None:
        value = ()
    if not isinstance(value, (tuple, list)):
        raise ValueError("budget_rpc_urls must be a sequence")
    result: list[str] = []
    for raw in value:
        url = _clean_text(raw, field_name="budget_rpc_url")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("budget RPC URLs must be absolute HTTP(S) URLs")
        if parsed.username or parsed.password:
            raise ValueError("budget RPC URLs must not contain credentials")
        if any(character.isspace() for character in url):
            raise ValueError("budget RPC URLs must not contain whitespace")
        result.append(url.rstrip("/"))
    return tuple(dict.fromkeys(result))


@dataclass
class BudgetAppConfig(AppConfig):
    """Single explicit TestUSD budget-contract profile.

    ``token_address``, ``contract_address`` and ``payee_address`` are accepted
    as concise aliases for callers constructing a local test profile.  The
    canonical values are stored in the ``budget_*`` fields and are the only
    values consumed by the budget service.
    """

    budget_mode: str = "local_anvil"
    budget_network: str = "eip155:31337"
    budget_chain_id: int = _LOCAL_CHAIN_ID
    budget_rpc_url: str = ""
    budget_rpc_urls: tuple[str, ...] = field(default_factory=tuple)
    budget_token_symbol: str = "TestUSD"
    budget_token_address: str = ""
    budget_token_decimals: int = 6
    budget_contract_address: str = ""
    budget_payee_address: str = ""
    budget_allowed_merchant_ids: tuple[str, ...] = field(default_factory=tuple)
    budget_allowed_resources: tuple[str, ...] = field(default_factory=tuple)
    budget_allowed_payees: tuple[str, ...] = field(default_factory=tuple)

    # The budget policy's first-party allowlist is the enforced risk gate for
    # this test-only profile.  It does not weaken or alter the regular Core
    # AppConfig profile.
    risk_mode: str = "enforce"

    # Constructor aliases retained for small local fixtures and integrations.
    token_address: str | None = None
    contract_address: str | None = None
    payee_address: str | None = None
    network: str | None = None
    chain_id: int | None = None
    rpc_url: str | None = None

    def __post_init__(self) -> None:
        # Run the existing structural validation first.  The base profile's
        # ``clink_live_funding`` branch requires a MistTrack credential; this
        # testnet rail has a separate first-party evidence provider and must
        # never satisfy that branch with a fake key.  Validate the base shape
        # with its live gate temporarily closed, then restore the caller's
        # explicit value for the budget service/readiness projection.
        live_funding = self.clink_live_funding
        if type(live_funding) is not bool:
            raise ValueError("clink_live_funding must be a boolean")
        self.clink_live_funding = False
        try:
            super().__post_init__()
        finally:
            self.clink_live_funding = live_funding

        if self.clink_facilitator_mode == "hosted":
            raise ValueError("BudgetAppConfig cannot use the Hosted facilitator rail")
        if self.risk_mode != "enforce":
            raise ValueError("BudgetAppConfig requires enforce risk mode")

        mode = _clean_text(self.budget_mode, field_name="budget_mode").lower()
        if mode not in _ALLOWED_MODES:
            raise ValueError("budget_mode must be local_anvil or monad_testnet")
        self.budget_mode = mode

        if self.network is not None:
            self.budget_network = self.network
        self.budget_network = _clean_text(self.budget_network, field_name="budget_network").lower()
        if not self.budget_network.startswith("eip155:"):
            raise ValueError("budget_network must be an eip155 network")
        try:
            network_chain_id = int(self.budget_network.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("budget_network must contain a decimal chain id") from exc
        if self.chain_id is not None:
            self.budget_chain_id = self.chain_id
        if type(self.budget_chain_id) is not int:
            raise ValueError("budget_chain_id must be an integer")
        expected_chain_id = (
            _LOCAL_CHAIN_ID if mode == "local_anvil" else _MONAD_TESTNET_CHAIN_ID
        )
        if self.budget_chain_id != expected_chain_id or network_chain_id != expected_chain_id:
            raise ValueError("budget mode, network and chain id must agree")

        if self.rpc_url is not None:
            self.budget_rpc_url = self.rpc_url
        urls = _rpc_urls(self.budget_rpc_urls)
        if self.budget_rpc_url:
            urls = _rpc_urls((self.budget_rpc_url, *urls))
        self.budget_rpc_urls = urls
        self.budget_rpc_url = urls[0] if urls else ""
        if mode == "monad_testnet" and len(urls) < 1:
            raise ValueError("monad_testnet requires an explicit RPC URL")

        if self.token_address is not None:
            self.budget_token_address = self.token_address
        if self.contract_address is not None:
            self.budget_contract_address = self.contract_address
        if self.payee_address is not None:
            self.budget_payee_address = self.payee_address
        self.budget_token_address = _address(
            self.budget_token_address, field_name="budget_token_address"
        )
        self.budget_contract_address = _address(
            self.budget_contract_address, field_name="budget_contract_address"
        )
        self.budget_payee_address = _address(
            self.budget_payee_address, field_name="budget_payee_address"
        )
        if self.budget_token_symbol != "TestUSD":
            raise ValueError("budget_token_symbol must be TestUSD")
        if type(self.budget_token_decimals) is not int or self.budget_token_decimals != 6:
            raise ValueError("TestUSD budget token decimals must be 6")
        if self.budget_contract_address == self.budget_payee_address:
            raise ValueError("budget contract and payee must be distinct")

        self.budget_allowed_merchant_ids = _allowlist(
            self.budget_allowed_merchant_ids,
            field_name="budget_allowed_merchant_ids",
        )
        self.budget_allowed_resources = _allowlist(
            self.budget_allowed_resources,
            field_name="budget_allowed_resources",
        )
        payees = self.budget_allowed_payees or (self.budget_payee_address,)
        self.budget_allowed_payees = tuple(
            _address(value, field_name="budget_allowed_payees") for value in payees
        )

        # Expose normalized aliases for small composition adapters while
        # retaining the explicit ``budget_*`` fields as the source of truth.
        self.token_address = self.budget_token_address
        self.contract_address = self.budget_contract_address
        self.payee_address = self.budget_payee_address
        self.network = self.budget_network
        self.chain_id = self.budget_chain_id
        self.rpc_url = self.budget_rpc_url

        # The inherited code may still inspect these fields when constructing
        # generic Core projections.  They remain scoped to this one profile.
        self.x402_payment_network = self.budget_network
        self.x402_payment_token = self.budget_token_symbol
        self.x402_payment_token_address = self.budget_token_address
        self.x402_payment_token_decimals = self.budget_token_decimals

    @property
    def allowed_evm_networks(self) -> tuple[str, ...]:
        return (self.budget_network,)

    @property
    def configured_rpc_urls(self) -> dict[str, str]:
        return {self.budget_network: self.budget_rpc_url} if self.budget_rpc_url else {}

    def rpc_url_for(self, network: str) -> str:
        if network != self.budget_network:
            raise ValueError(f"unsupported configured budget network: {network}")
        if not self.budget_rpc_url:
            raise RuntimeError("budget RPC URL is not configured")
        return self.budget_rpc_url
