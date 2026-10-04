"""External-wallet Core composition for the Monad TestUSD rail.

This adapter owns only the composition boundary.  Wallet identity, signed
business grants, allowance evidence, budget binding, policy and funding remain
the existing Core services.  The injected signer is a capability; this module
never accepts or persists a private key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = ROOT / "apps" / "core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
from eth_utils import keccak  # noqa: E402

from agentonomy_commerce.budget_backend import BudgetBackend  # noqa: E402
from agentonomy_commerce.budget_network import (  # noqa: E402
    NetworkConfig,
    RpcClient,
    address,
)
from apps.facilitator.budget_protocol import (  # noqa: E402
    SpendGrant,
    hash_grant,
    verify_grant_signature,
)
from examples.commerce.core_persistence import (  # noqa: E402
    CorePersistenceError,
    StateLock,
    _atomic_write_bytes,
    _atomic_write_json,
)
from examples.monad_commerce.core_worker import (  # noqa: E402
    AGENT_ID,
    MERCHANT_ID,
    RESOURCE,
    TRUST_TIER,
    USER_ID,
    VENUE,
    CoreRuntime,
)
from services.account_service.repository import AccountRepository  # noqa: E402
from services.account_service.schemas import (  # noqa: E402
    PublicAccountSession,
    SpendingGrantRequest,
    canonicalize_transaction_hash,
)
from services.account_service.service import AccountService  # noqa: E402
from services.action_service.service import ActionService  # noqa: E402
from services.audit_service.service import AuditService  # noqa: E402
from services.funding_service.budget_binding import derive_agent_scope  # noqa: E402
from services.funding_service.budget_service import BudgetFundingService  # noqa: E402
from services.policy_service.budget_policy import BudgetPolicyService  # noqa: E402
from shared.budget_config import BudgetAppConfig  # noqa: E402


PUBLIC_MODE = "monad_testnet"
LOCAL_MODE = "local_anvil"
PUBLIC_CHAIN_ID = 10143
LOCAL_CHAIN_ID = 31337
TOTAL_AMOUNT_ATOMIC = 1_000_000
PER_PAYMENT_AMOUNT_ATOMIC = 500_000
VALIDITY = timedelta(days=1)
CHALLENGE_TTL = timedelta(minutes=10)

_SIGNATURE = re.compile(r"^0x[0-9a-f]{130}$")
_STATE_VERSION = 1
_STATE_KEYS = frozenset(
    {
        "version",
        "deployment",
        "wallet_challenge",
        "wallet_signature",
        "wallet_identity_id",
        "grant_request",
        "grant_challenge",
        "grant_signature",
        "spending_grant_id",
        "budget_grant",
        "budget_signature",
        "budget_binding_id",
        "allowance_id",
        "allowance_tx_hash",
    }
)
_DEPLOYMENT_KEYS = frozenset(
    {
        "mode",
        "chain_id",
        "rpc_urls",
        "token",
        "executor",
        "payee",
        "token_decimals",
        "gas_limit",
        "max_gas_price_wei",
        "owner",
        "execution_signer",
        "relayer",
        "domain",
    }
)
_WALLET_CHALLENGE_KEYS = frozenset(
    {"public_account_session_id", "session_id", "message_to_sign", "expires_at"}
)
_GRANT_CHALLENGE_KEYS = frozenset({"session_id", "message_to_sign", "expires_at"})
_GRANT_TERM_FIELDS = (
    "user_id",
    "wallet_identity_id",
    "agent_id",
    "max_amount_usdc",
    "per_transaction_limit_usdc",
    "hourly_limit_usdc",
    "daily_limit_usdc",
    "product_scopes",
    "venue_scopes",
    "merchant_scopes",
    "merchant_trust_scopes",
    "notification_mode",
    "network_scopes",
    "asset_scopes",
    "starts_at",
    "expires_at",
)
_GRANT_DECIMAL_FIELDS = frozenset(
    {
        "max_amount_usdc",
        "per_transaction_limit_usdc",
        "hourly_limit_usdc",
        "daily_limit_usdc",
    }
)
_GRANT_DATETIME_FIELDS = frozenset({"starts_at", "expires_at"})


def _json_value(value: Any) -> Any:
    """Convert service models into values safe for the public JSON state file."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if hasattr(value, "to_json"):
        return _json_value(value.to_json())
    raise ValueError("public onboarding state contains a non-serializable value")


def _canonical_signature(value: object) -> str:
    if isinstance(value, bytes):
        value = "0x" + value.hex()
    if not isinstance(value, str):
        raise ValueError("signature must be a 65-byte hex value")
    value = value.lower()
    if not value.startswith("0x"):
        value = "0x" + value
    if _SIGNATURE.fullmatch(value) is None:
        raise ValueError("signature must be a 65-byte hex value")
    return value


def _now() -> datetime:
    return datetime.now(UTC)


def _whole_second_now() -> datetime:
    return _now().replace(microsecond=0)


def _atomic_amount(value: object, *, field_name: str) -> int:
    try:
        decimal_value = Decimal(str(value)) * 1_000_000
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not an exact six-decimal amount") from exc
    if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"{field_name} is not an exact six-decimal amount")
    amount = int(decimal_value)
    if amount <= 0 or amount > 2**256 - 1:
        raise ValueError(f"{field_name} is outside uint256")
    return amount


class ExternalWalletCore(CoreRuntime):
    """Core services composed around an externally owned wallet.

    ``signer`` is deliberately kept as an in-memory capability only.  It must
    provide the two restricted signing methods used by the budget backend;
    private keys and provider credentials never enter ``bootstrap`` or state.
    """

    def __init__(
        self,
        state_dir: str | os.PathLike[str],
        bootstrap: Mapping[str, Any],
        signer: Any,
        *,
        allow_local: bool = False,
    ) -> None:
        if type(allow_local) is not bool:
            raise ValueError("allow_local must be a boolean Python constructor option")
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self._state_lock = StateLock(self.state_dir)
        self._state_lock.acquire()
        self._closed = False
        try:
            self.network, self.owner_address, self.execution_signer_address, self.relayer_address, self.domain = (
                self._parse_bootstrap(bootstrap, allow_local=allow_local)
            )
            self._validate_signer(signer)
            self.signer = signer
            self.wallet = None
            self.execution_signer = None
            self.relayer = None
            self.wallet_address = self.owner_address
            self.persistent = True
            self._blocked_reason: str | None = None

            self._metadata_path = self.state_dir / "public-onboarding.json"
            self._receipt_secret_path = self.state_dir / "receipt.secret"
            self._database_path = self.state_dir / "core.sqlite3"
            self._metadata = self._prepare_persistence()
            self._receipt_secret = self._load_receipt_secret()

            self.config = BudgetAppConfig(
                funding_database_url=f"sqlite+pysqlite:///{self._database_path}",
                account_allowed_products=("marketplace",),
                clink_live_funding=True,
                budget_mode=self.network.mode,
                budget_network=self.network.network,
                budget_chain_id=self.network.chain_id,
                budget_rpc_urls=self.network.rpc_urls,
                budget_token_symbol="TestUSD",
                budget_token_address=self.network.token,
                budget_token_decimals=6,
                budget_contract_address=self.network.executor,
                budget_payee_address=self.network.payee,
                budget_allowed_merchant_ids=(MERCHANT_ID,),
                budget_allowed_resources=(RESOURCE,),
                budget_allowed_payees=(self.network.payee,),
                funding_destination_allowlist=(self.network.payee,),
                clink_receipt_signing_key=self._receipt_secret,
            )

            self.repository = AccountRepository(self.config.funding_database_url)
            try:
                os.chmod(self._database_path, 0o600)
            except OSError as exc:
                raise CorePersistenceError("persistent Core database permissions are unavailable") from exc

            self.rpc = RpcClient(self.network.rpc_urls[0])
            network_config = {
                self.network.network: {
                    "chain_id": self.network.chain_id,
                    "required_confirmations": 1
                    if self.network.mode == LOCAL_MODE
                    else 4,
                    "token_symbol": "TestUSD",
                    "token_decimals": 6,
                    "token_address": self.network.token,
                }
            }
            self.account_service = AccountService(
                self.repository,
                domain=self.domain,
                challenge_ttl=CHALLENGE_TTL,
                clock=_now,
                rpc_transport=self._rpc_transport,
                network_configs=network_config,
                allowed_products={"marketplace"},
            )
            self.action_service = ActionService(config=self.config)
            self.policy_service = BudgetPolicyService(config=self.config)
            self.audit_service = AuditService(
                database_url=self.config.funding_database_url,
                storage_file=self.state_dir / "audit.jsonl",
                clock=_now,
            )
            # The external signer signs the complete execution permit and the
            # relayer transaction.  No digest-only callback is accepted here.
            self.backend = BudgetBackend(
                self.network,
                relayer_address=self.relayer_address,
                execution_signer_address=self.execution_signer_address,
                execution_sign=None,
                transaction_sign=signer.sign_transaction,
                sign_execution=signer.sign_execution,
            )
            self.funding_service = BudgetFundingService(
                config=self.config,
                storage_file=self.state_dir / "funding.jsonl",
                rpc_transport=self._rpc_transport,
                policy_service=self.policy_service,
                backend=self.backend,
            )
            self.wallet_identity = None
            self.grant = None
            self.allowance = None
            self.budget_binding = None
            self._recover_persisted_authorization()
            if not self._metadata_path.exists():
                self._persist_state()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _validate_signer(signer: Any) -> None:
        if not callable(getattr(signer, "sign_execution", None)) or not callable(
            getattr(signer, "sign_transaction", None)
        ):
            raise ValueError("external signer capability is incomplete")

    @staticmethod
    def _parse_bootstrap(
        bootstrap: Mapping[str, Any], *, allow_local: bool
    ) -> tuple[NetworkConfig, str, str, str, str]:
        if not isinstance(bootstrap, Mapping):
            raise ValueError("bootstrap must be a public deployment mapping")
        network_fields = {field.name for field in fields(NetworkConfig)}
        allowed = network_fields | {"owner", "execution_signer", "relayer", "domain"}
        if set(bootstrap) - allowed:
            raise ValueError("bootstrap contains unsupported or secret fields")
        required = network_fields - {"token_decimals", "gas_limit", "max_gas_price_wei"}
        if not required.issubset(bootstrap) or not {
            "owner",
            "execution_signer",
            "relayer",
        }.issubset(bootstrap):
            raise ValueError("bootstrap is missing public deployment fields")
        network = NetworkConfig(
            **{name: bootstrap[name] for name in network_fields if name in bootstrap}
        )
        if network.mode == LOCAL_MODE and not allow_local:
            raise ValueError("local_anvil requires explicit Python allow_local opt-in")
        if network.mode == PUBLIC_MODE and network.chain_id != PUBLIC_CHAIN_ID:
            raise ValueError("public Core requires Monad testnet chain 10143")
        if network.mode not in {PUBLIC_MODE, LOCAL_MODE}:
            raise ValueError("unsupported public Core network mode")
        owner = address(bootstrap["owner"])
        execution_signer = address(bootstrap["execution_signer"])
        relayer = address(bootstrap["relayer"])
        if owner == network.payee:
            raise ValueError("owner and payee must differ")
        domain = bootstrap.get("domain", "localhost")
        if not isinstance(domain, str) or not domain.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in domain
        ):
            raise ValueError("domain must be a non-empty control-free string")
        return network, owner, execution_signer, relayer, domain.strip()

    def _deployment(self) -> dict[str, Any]:
        return {
            "mode": self.network.mode,
            "chain_id": self.network.chain_id,
            "rpc_urls": list(self.network.rpc_urls),
            "token": self.network.token,
            "executor": self.network.executor,
            "payee": self.network.payee,
            "token_decimals": self.network.token_decimals,
            "gas_limit": self.network.gas_limit,
            "max_gas_price_wei": self.network.max_gas_price_wei,
            "owner": self.owner_address,
            "execution_signer": self.execution_signer_address,
            "relayer": self.relayer_address,
            "domain": self.domain,
        }

    def _initial_metadata(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "deployment": self._deployment(),
            "wallet_challenge": None,
            "wallet_signature": None,
            "wallet_identity_id": None,
            "grant_request": None,
            "grant_challenge": None,
            "grant_signature": None,
            "spending_grant_id": None,
            "budget_grant": None,
            "budget_signature": None,
            "budget_binding_id": None,
            "allowance_id": None,
            "allowance_tx_hash": None,
        }

    def _prepare_persistence(self) -> dict[str, Any]:
        metadata_exists = self._metadata_path.exists()
        database_exists = self._database_path.exists()
        receipt_exists = self._receipt_secret_path.exists()
        if metadata_exists:
            if not database_exists or not receipt_exists:
                raise CorePersistenceError("persistent public Core state is incomplete")
            try:
                metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CorePersistenceError("persistent public Core metadata is unreadable") from exc
            self._validate_metadata(metadata)
            return metadata
        if database_exists or receipt_exists:
            raise CorePersistenceError("persistent public Core state requires inspection")
        secret = secrets.token_hex(32).encode("ascii")
        _atomic_write_bytes(self._receipt_secret_path, secret, mode=0o600)
        return self._initial_metadata()

    def _load_receipt_secret(self) -> str:
        try:
            mode = stat.S_IMODE(self._receipt_secret_path.stat().st_mode)
            if mode != 0o600:
                raise CorePersistenceError("persistent public Core receipt secret permissions are too broad")
            value = self._receipt_secret_path.read_text(encoding="ascii")
        except CorePersistenceError:
            raise
        except (OSError, UnicodeError) as exc:
            raise CorePersistenceError("persistent public Core receipt secret is unreadable") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise CorePersistenceError("persistent public Core receipt secret is invalid")
        return value

    @staticmethod
    def _nullable_identifier(value: object, *, field_name: str) -> None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise CorePersistenceError(f"persistent public Core {field_name} is invalid")

    def _validate_metadata(self, metadata: object) -> None:
        if not isinstance(metadata, dict) or set(metadata) != _STATE_KEYS:
            raise CorePersistenceError("persistent public Core metadata fields are invalid")
        if metadata.get("version") != _STATE_VERSION:
            raise CorePersistenceError("persistent public Core metadata version is unsupported")
        deployment = metadata.get("deployment")
        if not isinstance(deployment, dict) or set(deployment) != _DEPLOYMENT_KEYS:
            raise CorePersistenceError("persistent public Core deployment metadata is invalid")
        if deployment != self._deployment():
            raise CorePersistenceError("persistent public Core deployment binding changed")
        for key in (
            "wallet_identity_id",
            "spending_grant_id",
            "budget_binding_id",
            "allowance_id",
            "allowance_tx_hash",
        ):
            self._nullable_identifier(metadata.get(key), field_name=key)
        for key in ("wallet_signature", "grant_signature", "budget_signature"):
            value = metadata.get(key)
            if value is not None:
                try:
                    _canonical_signature(value)
                except ValueError as exc:
                    raise CorePersistenceError(f"persistent public Core {key} is invalid") from exc
        for key, expected_keys in (
            ("wallet_challenge", _WALLET_CHALLENGE_KEYS),
            ("grant_challenge", _GRANT_CHALLENGE_KEYS),
        ):
            value = metadata.get(key)
            if value is not None and (
                not isinstance(value, dict) or set(value) != expected_keys
            ):
                raise CorePersistenceError(f"persistent public Core {key} is invalid")
        if metadata.get("grant_request") is not None and not isinstance(
            metadata["grant_request"], dict
        ):
            raise CorePersistenceError("persistent public Core grant request is invalid")
        if metadata.get("budget_grant") is not None and not isinstance(
            metadata["budget_grant"], dict
        ):
            raise CorePersistenceError("persistent public Core budget grant is invalid")

    def _persist_state(self) -> None:
        self._validate_metadata(self._metadata)
        _atomic_write_json(self._metadata_path, _json_value(self._metadata))

    def _rpc_transport(self, network: str, method: str, params: list[Any]) -> Any:
        if network != self.network.network:
            raise ValueError("RPC network is outside the configured public Core profile")
        return self.rpc.call(method, params)

    def _recover_persisted_authorization(self) -> None:
        changed = False
        self.wallet_identity = self._recover_wallet_identity()
        if self.wallet_identity is not None and self._metadata.get("wallet_identity_id") != self.wallet_identity.wallet_identity_id:
            self._metadata["wallet_identity_id"] = self.wallet_identity.wallet_identity_id
            changed = True

        self.grant = self._recover_grant()
        if self.grant is not None and self._metadata.get("spending_grant_id") != self.grant.spending_grant_id:
            self._metadata["spending_grant_id"] = self.grant.spending_grant_id
            changed = True

        self.budget_binding = None
        if self.grant is not None:
            binding_id = self._metadata.get("budget_binding_id")
            try:
                if binding_id:
                    self.budget_binding = self.funding_service.get_budget_binding(
                        binding_id=binding_id
                    )
                else:
                    self.budget_binding = self.funding_service.get_budget_binding(
                        spending_grant_id=self.grant.spending_grant_id
                    )
                    if self.budget_binding is not None:
                        self._metadata["budget_binding_id"] = self.budget_binding.binding_id
                        if self._metadata.get("budget_signature") is None:
                            self._metadata["budget_signature"] = self.budget_binding.owner_signature
                        changed = True
            except Exception as exc:
                raise CorePersistenceError("persistent public Core budget binding is invalid") from exc
            if binding_id and self.budget_binding is None:
                raise CorePersistenceError("persistent public Core budget binding is missing")
            if self.budget_binding is not None:
                self._validate_recovered_budget_binding(self.budget_binding)
                if self._metadata.get("budget_grant") is None:
                    self._metadata["budget_grant"] = self.budget_binding.grant.to_json()
                    changed = True
                if self._metadata.get("budget_signature") is None:
                    self._metadata["budget_signature"] = self.budget_binding.owner_signature
                    changed = True

        self.allowance = self._recover_allowance()
        if self.allowance is not None and self._metadata.get("allowance_id") != self.allowance.asset_allowance_id:
            self._metadata["allowance_id"] = self.allowance.asset_allowance_id
            self._metadata["allowance_tx_hash"] = self.allowance.allowance_tx_hash
            changed = True
        if changed:
            self._persist_state()

    def _recover_wallet_identity(self):
        identity_id = self._metadata.get("wallet_identity_id")
        if identity_id:
            identity = self.repository.wallet_identity(identity_id)
            if identity is None:
                raise CorePersistenceError("persistent public Core wallet identity is missing")
            if identity.user_id != USER_ID or identity.wallet_address != self.owner_address:
                raise CorePersistenceError("persistent public Core wallet identity scope changed")
            return identity
        challenge = self._metadata.get("wallet_challenge")
        signature = self._metadata.get("wallet_signature")
        if not challenge:
            return None
        account_session = self.repository.account_session(challenge["session_id"])
        if account_session is None:
            raise CorePersistenceError("persistent public Core wallet challenge is missing")
        candidates = []
        if signature:
            proof_hash = "0x" + keccak(bytes.fromhex(signature[2:])).hex()
            candidates = [
                identity
                for identity in self.repository.active_wallet_identities(USER_ID)
                if identity.wallet_address == self.owner_address
                and identity.proof_hash == proof_hash
            ]
        if len(candidates) == 1:
            return candidates[0]
        if account_session.consumed_at is not None:
            raise CorePersistenceError("persistent public Core wallet challenge outcome requires inspection")
        return None

    def _grant_request(self) -> SpendingGrantRequest:
        raw = self._metadata.get("grant_request")
        if not isinstance(raw, dict):
            raise ValueError("Core grant challenge has not been created")
        public_session = None
        challenge = self._metadata.get("wallet_challenge")
        if challenge:
            public_session = challenge["public_account_session_id"]
        return SpendingGrantRequest.model_validate(
            raw
            | {
                "created_by_public_account_session_id": public_session,
                "session_id": None,
                "signed_message": None,
                "signature": None,
            }
        )

    @staticmethod
    def _normalized_grant_term(field_name: str, value: Any) -> Any:
        if value is None:
            return None
        if field_name in _GRANT_DECIMAL_FIELDS:
            return Decimal(str(value))
        if field_name in _GRANT_DATETIME_FIELDS:
            if not isinstance(value, datetime):
                raise ValueError("grant timestamp is invalid")
            return value.astimezone(UTC)
        if isinstance(value, list):
            return tuple(value)
        return value

    @classmethod
    def _grant_terms_equal(cls, left: Any, right: Any) -> bool:
        try:
            return all(
                cls._normalized_grant_term(field_name, getattr(left, field_name))
                == cls._normalized_grant_term(field_name, getattr(right, field_name))
                for field_name in _GRANT_TERM_FIELDS
            )
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            return False

    def _grant_request_has_fixed_terms(self, request: SpendingGrantRequest) -> bool:
        wallet_challenge = self._metadata.get("wallet_challenge")
        return (
            request.user_id == USER_ID
            and request.wallet_identity_id == self.wallet_identity.wallet_identity_id
            and request.agent_id == AGENT_ID
            and request.max_amount_usdc == Decimal("1.00")
            and request.per_transaction_limit_usdc == Decimal("0.50")
            and request.hourly_limit_usdc == Decimal("1.00")
            and request.daily_limit_usdc == Decimal("1.00")
            and request.product_scopes == ["marketplace"]
            and request.venue_scopes == [VENUE]
            and request.merchant_scopes == [MERCHANT_ID]
            and request.merchant_trust_scopes == [TRUST_TIER]
            and request.notification_mode == "silent_under_limits"
            and request.network_scopes == [self.network.network]
            and request.asset_scopes == [self.network.token]
            and request.amends_spending_grant_id is None
            and request.opc_installation is None
            and wallet_challenge is not None
            and request.created_by_public_account_session_id
            == wallet_challenge["public_account_session_id"]
            and request.starts_at.microsecond == 0
            and request.expires_at.microsecond == 0
            and request.expires_at - request.starts_at
            == VALIDITY + timedelta(seconds=60)
        )

    def _grant_matches_request(self, grant: Any, request: SpendingGrantRequest) -> bool:
        return self._grant_request_has_fixed_terms(request) and self._grant_terms_equal(
            grant, request
        )

    def _grant_challenge_session(self) -> Any:
        challenge = self._metadata.get("grant_challenge")
        if challenge is None:
            return None
        account_session = self.repository.account_session(challenge["session_id"])
        if account_session is None:
            raise CorePersistenceError("persistent public Core grant challenge is missing")
        if account_session.purpose != "clink_spending_grant":
            raise CorePersistenceError("persistent public Core grant challenge purpose changed")
        if account_session.wallet_identity_id != self.wallet_identity.wallet_identity_id:
            raise CorePersistenceError("persistent public Core grant challenge identity changed")
        payload = account_session.payload
        if not isinstance(payload, dict) or not isinstance(
            payload.get("spending_grant_id"), str
        ) or not isinstance(payload.get("terms"), dict):
            raise CorePersistenceError("persistent public Core grant challenge payload is invalid")
        if account_session.payload_hash:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")
            expected_hash = "0x" + hashlib.sha256(encoded).hexdigest()
            if account_session.payload_hash != expected_hash:
                raise CorePersistenceError("persistent public Core grant challenge payload changed")
        try:
            challenge_request = SpendingGrantRequest.model_validate(payload["terms"])
        except Exception as exc:
            raise CorePersistenceError("persistent public Core grant challenge terms are invalid") from exc
        try:
            request = self._grant_request()
        except Exception as exc:
            raise CorePersistenceError("persistent public Core grant request is invalid") from exc
        if not self._grant_request_has_fixed_terms(request) or not self._grant_terms_equal(
            challenge_request, request
        ):
            raise CorePersistenceError("persistent public Core grant challenge terms changed")
        return account_session

    def _recover_grant(self):
        request_raw = self._metadata.get("grant_request")
        grant_id = self._metadata.get("spending_grant_id")
        if not request_raw and not grant_id:
            return None
        if self.wallet_identity is None:
            if grant_id:
                raise CorePersistenceError("persistent public Core spending grant identity is unavailable")
            return None
        if not request_raw or self._metadata.get("grant_challenge") is None:
            raise CorePersistenceError("persistent public Core grant recovery proof is incomplete")
        request = self._grant_request()
        account_session = self._grant_challenge_session()
        challenge_grant_id = account_session.payload["spending_grant_id"]
        if grant_id is not None and grant_id != challenge_grant_id:
            raise CorePersistenceError("persistent public Core spending grant ID changed")

        recovered_id = grant_id or challenge_grant_id
        grant = self.repository.spending_grant(recovered_id)
        if grant is None:
            if account_session.consumed_at is not None:
                raise CorePersistenceError("persistent public Core spending grant is missing")
            return None
        if account_session.consumed_at is None:
            raise CorePersistenceError("persistent public Core spending grant is not backed by a consumed challenge")
        if grant.wallet_identity_id != self.wallet_identity.wallet_identity_id:
            raise CorePersistenceError("persistent public Core spending grant identity changed")
        if not self._grant_matches_request(grant, request):
            raise CorePersistenceError("persistent public Core spending grant terms changed")
        return grant

    def _validate_recovered_budget_binding(self, binding: Any) -> None:
        if self.grant is None or self.wallet_identity is None:
            raise CorePersistenceError("persistent public Core budget binding lacks Core authorization")
        try:
            expected = self._budget_grant_for_core_grant(self.grant)
            stored = binding.grant
            expected_hash = "0x" + hash_grant(
                expected, self.network.chain_id, self.network.executor
            ).hex()
            verify_grant_signature(
                expected,
                binding.owner_signature,
                self.network.chain_id,
                self.network.executor,
            )
        except Exception as exc:
            raise CorePersistenceError("persistent public Core budget binding signature is invalid") from exc
        if stored.to_json() != expected.to_json() or binding.grant_hash != expected_hash:
            raise CorePersistenceError("persistent public Core budget binding grant changed")
        expected_fields = {
            "spending_grant_id": self.grant.spending_grant_id,
            "wallet_identity_id": self.wallet_identity.wallet_identity_id,
            "user_id": self.grant.user_id,
            "agent_id": self.grant.agent_id,
            "owner": expected.owner,
            "agent_scope": expected.agent_scope,
            "network": self.network.network,
            "chain_id": self.network.chain_id,
            "executor_contract": self.network.executor,
            "token": self.network.token,
            "token_symbol": "TestUSD",
            "token_decimals": 6,
            "payee": self.network.payee,
            "max_per_payment": expected.max_per_payment,
            "max_total": expected.max_total,
            "valid_after": expected.valid_after,
            "valid_until": expected.valid_until,
            "execution_signer": self.execution_signer_address,
            "grant_id": expected.grant_id,
            "grant_hash": expected_hash,
        }
        if any(getattr(binding, field_name, None) != value for field_name, value in expected_fields.items()):
            raise CorePersistenceError("persistent public Core budget binding scope changed")
        saved_signature = self._metadata.get("budget_signature")
        if saved_signature is not None and saved_signature != binding.owner_signature:
            raise CorePersistenceError("persistent public Core budget binding signature changed")
        saved_payload = self._metadata.get("budget_grant")
        if saved_payload is not None:
            try:
                saved_model = SpendGrant.from_json(saved_payload)
            except Exception as exc:
                raise CorePersistenceError("persistent public Core budget grant is invalid") from exc
            if saved_model != expected:
                raise CorePersistenceError("persistent public Core budget grant changed")

    def _recover_allowance(self):
        if self.wallet_identity is None:
            return None
        allowance_id = self._metadata.get("allowance_id")
        if allowance_id:
            allowance = self.repository.asset_allowance(allowance_id)
            if allowance is None:
                raise CorePersistenceError("persistent public Core asset allowance is missing")
            self._validate_allowance_scope(allowance)
            return allowance
        candidates = [
            allowance
            for allowance in self.repository.asset_allowances(self.wallet_identity.wallet_identity_id)
            if allowance.network == self.network.network
            and allowance.token_address == self.network.token
            and allowance.spender_address == self.network.executor
        ]
        if len(candidates) > 1:
            raise CorePersistenceError("persistent public Core has conflicting asset allowances")
        if not candidates:
            return None
        candidate = candidates[0]
        # A proof written before the onboarding operation completed may remain
        # in the account repository with an insufficient current observation.
        # It must not be mistaken for a completed allowance stage on restart.
        if candidate.status != "active" or candidate.observed_allowance_atomic < TOTAL_AMOUNT_ATOMIC:
            return None
        return candidate

    def _validate_allowance_scope(self, allowance: Any) -> None:
        if (
            allowance.wallet_identity_id != self.wallet_identity.wallet_identity_id
            or allowance.network != self.network.network
            or allowance.token_address != self.network.token
            or allowance.spender_address != self.network.executor
        ):
            raise CorePersistenceError("persistent public Core allowance scope changed")

    def _wallet_result(self) -> dict[str, Any]:
        if self.wallet_identity is None:
            raise ValueError("wallet identity is not verified")
        return {
            "status": "verified",
            "wallet_identity_id": self.wallet_identity.wallet_identity_id,
            "wallet_address": self.wallet_identity.wallet_address,
        }

    def wallet_challenge(self) -> dict[str, Any]:
        if self.wallet_identity is not None:
            return self._wallet_result()
        existing = self._metadata.get("wallet_challenge")
        if existing is not None:
            try:
                expires_at = datetime.fromisoformat(existing["expires_at"])
            except (TypeError, ValueError) as exc:
                raise CorePersistenceError("persistent public Core wallet challenge expiry is invalid") from exc
            if _now() >= expires_at.astimezone(UTC):
                raise ValueError("wallet challenge expired; inspect state before requesting another")
            return {
                **existing,
                "signing_method": "personal_sign",
            }

        now = _now()
        public_session_id = f"public_{uuid4().hex}"
        public_session = PublicAccountSession(
            public_account_session_id=public_session_id,
            token_digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            browser_session_digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            csrf_token_digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            user_id=USER_ID,
            expires_at=now + CHALLENGE_TTL,
            exchanged_at=now,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_public_account_session(public_session)
        challenge = self.account_service.create_wallet_challenge(
            USER_ID,
            self.owner_address,
            created_by_public_account_session_id=public_session_id,
        )
        self._metadata["wallet_challenge"] = {
            "public_account_session_id": public_session_id,
            "session_id": challenge.session_id,
            "message_to_sign": challenge.message_to_sign,
            "expires_at": challenge.expires_at.astimezone(UTC).isoformat(),
        }
        self._persist_state()
        return {**self._metadata["wallet_challenge"], "signing_method": "personal_sign"}

    def wallet_verify(self, signature: str | bytes) -> dict[str, Any]:
        if self.wallet_identity is not None:
            expected = self._metadata.get("wallet_signature")
            canonical = _canonical_signature(signature)
            if expected != canonical:
                raise ValueError("completed wallet verification rejects a different signature")
            return self._wallet_result()
        challenge = self._metadata.get("wallet_challenge")
        if not challenge:
            raise ValueError("wallet challenge has not been created")
        canonical = _canonical_signature(signature)
        if self._metadata.get("wallet_signature") not in {None, canonical}:
            raise ValueError("wallet challenge already has a different signature")
        try:
            recovered = Account.recover_message(
                encode_defunct(text=challenge["message_to_sign"]), signature=canonical
            )
        except (TypeError, ValueError):
            raise ValueError("wallet signature is invalid") from None
        if address(recovered) != self.owner_address:
            raise ValueError("wallet signature does not match configured owner")
        self._metadata["wallet_signature"] = canonical
        self._persist_state()
        try:
            self.wallet_identity = self.account_service.verify_wallet_challenge(
                challenge["session_id"], challenge["message_to_sign"], canonical
            )
        except Exception:
            recovered_identity = self._recover_wallet_identity()
            if recovered_identity is None:
                raise
            self.wallet_identity = recovered_identity
        self._metadata["wallet_identity_id"] = self.wallet_identity.wallet_identity_id
        self._persist_state()
        return self._wallet_result()

    def grant_challenge(self) -> dict[str, Any]:
        if self.grant is not None:
            return {
                "status": "verified",
                "spending_grant_id": self.grant.spending_grant_id,
                "expires_at": self.grant.expires_at.astimezone(UTC).isoformat(),
            }
        if self.wallet_identity is None:
            raise ValueError("wallet identity must be verified before Core grant onboarding")
        existing = self._metadata.get("grant_challenge")
        if existing is not None:
            try:
                expires_at = datetime.fromisoformat(existing["expires_at"])
            except (TypeError, ValueError) as exc:
                raise CorePersistenceError("persistent public Core grant challenge expiry is invalid") from exc
            if _now() >= expires_at.astimezone(UTC):
                raise ValueError("grant challenge expired; inspect state before requesting another")
            return {**existing, "signing_method": "personal_sign", "grant_request": self._metadata["grant_request"]}

        now = _whole_second_now()
        request = SpendingGrantRequest(
            user_id=USER_ID,
            wallet_identity_id=self.wallet_identity.wallet_identity_id,
            agent_id=AGENT_ID,
            max_amount_usdc=Decimal("1.00"),
            per_transaction_limit_usdc=Decimal("0.50"),
            hourly_limit_usdc=Decimal("1.00"),
            daily_limit_usdc=Decimal("1.00"),
            product_scopes=["marketplace"],
            venue_scopes=[VENUE],
            merchant_scopes=[MERCHANT_ID],
            merchant_trust_scopes=[TRUST_TIER],
            notification_mode="silent_under_limits",
            network_scopes=[self.network.network],
            asset_scopes=[self.network.token],
            starts_at=now - timedelta(seconds=60),
            expires_at=now + VALIDITY,
            created_by_public_account_session_id=(
                self._metadata["wallet_challenge"]["public_account_session_id"]
                if self._metadata.get("wallet_challenge")
                else None
            ),
        )
        self._metadata["grant_request"] = request.terms_payload()
        self._persist_state()
        challenge = self.account_service.create_spending_grant_challenge(request)
        self._metadata["grant_challenge"] = {
            "session_id": challenge.session_id,
            "message_to_sign": challenge.message_to_sign,
            "expires_at": challenge.expires_at.astimezone(UTC).isoformat(),
        }
        self._persist_state()
        return {
            **self._metadata["grant_challenge"],
            "signing_method": "personal_sign",
            "grant_request": self._metadata["grant_request"],
        }

    def grant_verify(self, signature: str | bytes) -> dict[str, Any]:
        if self.grant is not None:
            canonical = _canonical_signature(signature)
            if self._metadata.get("grant_signature") != canonical:
                raise ValueError("completed Core grant rejects a different signature")
            return {
                "status": "verified",
                "spending_grant_id": self.grant.spending_grant_id,
                "expires_at": self.grant.expires_at.astimezone(UTC).isoformat(),
            }
        if self.wallet_identity is None:
            raise ValueError("wallet identity must be verified before Core grant onboarding")
        challenge = self._metadata.get("grant_challenge")
        if not challenge:
            raise ValueError("Core grant challenge has not been created")
        canonical = _canonical_signature(signature)
        if self._metadata.get("grant_signature") not in {None, canonical}:
            raise ValueError("Core grant challenge already has a different signature")
        try:
            recovered = Account.recover_message(
                encode_defunct(text=challenge["message_to_sign"]), signature=canonical
            )
        except (TypeError, ValueError):
            raise ValueError("Core grant signature is invalid") from None
        if address(recovered) != self.owner_address:
            raise ValueError("Core grant signature does not match configured owner")
        request = self._grant_request().model_copy(
            update={
                "session_id": challenge["session_id"],
                "signed_message": challenge["message_to_sign"],
                "signature": canonical,
            }
        )
        self._metadata["grant_signature"] = canonical
        self._persist_state()
        try:
            self.grant = self.account_service.create_spending_grant(request)
        except Exception:
            recovered_grant = self._recover_grant()
            if recovered_grant is None:
                raise
            self.grant = recovered_grant
        self._metadata["spending_grant_id"] = self.grant.spending_grant_id
        self._persist_state()
        return {
            "status": "verified",
            "spending_grant_id": self.grant.spending_grant_id,
            "expires_at": self.grant.expires_at.astimezone(UTC).isoformat(),
        }

    def _require_active_grant(self) -> Any:
        if self.grant is None:
            raise ValueError("Core spending grant has not been verified")
        current = self.repository.spending_grant(self.grant.spending_grant_id)
        if current is None:
            raise CorePersistenceError("persistent public Core spending grant is missing")
        self.grant = current
        if self.grant.status == "revoked":
            raise ValueError("Core spending grant is revoked")
        if self.grant.status in {"expired", "exhausted"}:
            raise ValueError("Core spending grant is expired")
        now = _now()
        if now < self.grant.starts_at or now >= self.grant.expires_at:
            raise ValueError("Core spending grant is outside its validity window")
        if self.grant.status != "active":
            raise ValueError("active Core spending grant is required")
        return self.grant

    def _budget_grant_for_core_grant(self, grant: Any) -> SpendGrant:
        if self.wallet_identity is None:
            raise ValueError("wallet identity is unavailable")
        return SpendGrant(
            "0x" + keccak(text="agentonomy:grant:v1:" + grant.spending_grant_id).hex(),
            self.wallet_identity.wallet_address,
            derive_agent_scope(AGENT_ID, grant.spending_grant_id),
            self.network.token,
            self.network.payee,
            _atomic_amount(grant.per_transaction_limit_usdc, field_name="per_transaction_limit_usdc"),
            _atomic_amount(grant.max_amount_usdc, field_name="max_amount_usdc"),
            int(grant.starts_at.astimezone(UTC).timestamp()),
            int(grant.expires_at.astimezone(UTC).timestamp()),
            self.execution_signer_address,
        )

    def _budget_grant(self) -> SpendGrant:
        return self._budget_grant_for_core_grant(self._require_active_grant())

    def budget_payload(self) -> dict[str, Any]:
        grant = self._budget_grant()
        digest = "0x" + hash_grant(grant, self.network.chain_id, self.network.executor).hex()
        return {
            "typed_data": grant.typed_data(self.network.chain_id, self.network.executor),
            "grant": grant.to_json(),
            "digest": digest,
        }

    def budget_bind(self, signature: str | bytes) -> dict[str, Any]:
        self._require_active_grant()
        if self.budget_binding is not None:
            canonical = _canonical_signature(signature)
            if self._metadata.get("budget_signature") != canonical:
                raise ValueError("completed budget binding rejects a different signature")
            return {
                "status": "bound",
                "binding_id": self.budget_binding.binding_id,
                "grant_id": self.budget_binding.grant_id,
                "grant_hash": self.budget_binding.grant_hash,
            }
        canonical = _canonical_signature(signature)
        payload = self.budget_payload()
        try:
            verify_grant_signature(
                payload["grant"], canonical, self.network.chain_id, self.network.executor
            )
        except Exception:
            raise ValueError("budget grant signature is invalid") from None
        self._metadata["budget_signature"] = canonical
        self._metadata["budget_grant"] = payload["grant"]
        self._persist_state()
        try:
            self.budget_binding = self.funding_service.bind_budget_grant(
                spending_grant_id=self.grant.spending_grant_id,
                wallet_identity_id=self.wallet_identity.wallet_identity_id,
                grant=payload["grant"],
                owner_signature=canonical,
                agent_id=AGENT_ID,
                network=self.network.network,
                executor_contract=self.network.executor,
                token_address=self.network.token,
                payee=self.network.payee,
                execution_signer=self.execution_signer_address,
            )
        except Exception:
            existing = self.funding_service.get_budget_binding(
                spending_grant_id=self.grant.spending_grant_id
            )
            if existing is None:
                raise
            if existing.owner_signature != canonical:
                raise CorePersistenceError("budget binding outcome requires inspection")
            self.budget_binding = existing
        self._metadata["budget_binding_id"] = self.budget_binding.binding_id
        self._persist_state()
        return {
            "status": "bound",
            "binding_id": self.budget_binding.binding_id,
            "grant_id": self.budget_binding.grant_id,
            "grant_hash": self.budget_binding.grant_hash,
        }

    def allowance_verify(self, transaction_hash: str) -> dict[str, Any]:
        canonical_tx_hash = canonicalize_transaction_hash(transaction_hash)
        if self.allowance is not None:
            if self.allowance.allowance_tx_hash != canonical_tx_hash:
                raise ValueError("completed allowance verification rejects a different transaction")
            if self._metadata.get("allowance_id") is None:
                if (
                    self.allowance.status != "active"
                    or self.allowance.observed_allowance_atomic < TOTAL_AMOUNT_ATOMIC
                ):
                    raise ValueError("current allowance does not cover the total onboarding budget")
                self._metadata["allowance_id"] = self.allowance.asset_allowance_id
                self._metadata["allowance_tx_hash"] = self.allowance.allowance_tx_hash
                self._persist_state()
            return {
                "status": "verified",
                "allowance_id": self.allowance.asset_allowance_id,
                "transaction_hash": self.allowance.allowance_tx_hash,
                "observed_allowance_atomic": str(self.allowance.observed_allowance_atomic),
            }
        self._require_active_grant()
        if self.budget_binding is None:
            raise ValueError("budget grant must be bound before allowance verification")
        candidate = self.account_service.verify_asset_allowance(
            self.wallet_identity.wallet_identity_id,
            self.network.network,
            self.network.token,
            self.network.executor,
            canonical_tx_hash,
            expected_amount_atomic=TOTAL_AMOUNT_ATOMIC,
        )
        if (
            candidate.status != "active"
            or candidate.observed_allowance_atomic < TOTAL_AMOUNT_ATOMIC
        ):
            # AccountService durably records a positive but insufficient
            # observation. Keep that proof available for a later retry, but do
            # not publish it as the completed onboarding stage in memory.
            raise ValueError("current allowance does not cover the total onboarding budget")
        self.allowance = candidate
        self._metadata["allowance_id"] = self.allowance.asset_allowance_id
        self._metadata["allowance_tx_hash"] = self.allowance.allowance_tx_hash
        self._persist_state()
        return {
            "status": "verified",
            "allowance_id": self.allowance.asset_allowance_id,
            "transaction_hash": self.allowance.allowance_tx_hash,
            "observed_allowance_atomic": str(self.allowance.observed_allowance_atomic),
        }

    def _phase(self) -> str:
        if self._blocked_reason:
            return "blocked"
        if self.wallet_identity is None:
            return "wallet"
        if self.wallet_identity.status != "active":
            return "revoked"
        if self.grant is None:
            return "core_grant"
        current_grant = self.repository.spending_grant(self.grant.spending_grant_id)
        if current_grant is None:
            return "blocked"
        self.grant = current_grant
        if self.grant.status == "revoked":
            return "revoked"
        if self.grant.status in {"expired", "exhausted"} or _now() >= self.grant.expires_at:
            return "expired"
        if self.budget_binding is None:
            return "budget_grant"
        if self.allowance is None or self.allowance.status != "active":
            return "allowance"
        return "ready"

    def onboarding_status(self) -> dict[str, Any]:
        phase = self._phase()
        grant_request = self._metadata.get("grant_request") or {}
        terms = {
            "max_amount_usdc": grant_request.get("max_amount_usdc", "1.00"),
            "per_transaction_limit_usdc": grant_request.get(
                "per_transaction_limit_usdc", "0.50"
            ),
            "hourly_limit_usdc": grant_request.get("hourly_limit_usdc", "1.00"),
            "daily_limit_usdc": grant_request.get("daily_limit_usdc", "1.00"),
            "validity_seconds": int(VALIDITY.total_seconds()),
            "token_decimals": 6,
            "notification_mode": "silent_under_limits",
            "network": self.network.network,
            "asset": self.network.token,
            "merchant": MERCHANT_ID,
            "venue": VENUE,
            "merchant_trust": TRUST_TIER,
            "resource": RESOURCE,
        }
        result: dict[str, Any] = {
            "phase": phase,
            "mode": self.network.mode,
            "chain": self.network.network,
            "chain_id": self.network.chain_id,
            "token": self.network.token,
            "token_symbol": "TestUSD",
            "token_decimals": 6,
            "executor": self.network.executor,
            "payee": self.network.payee,
            "owner": self.owner_address,
            "execution_signer": self.execution_signer_address,
            "relayer": self.relayer_address,
            "terms": terms,
            "simulation": False,
            "real_funds": False,
        }
        if self.wallet_identity is not None:
            result["wallet_identity_id"] = self.wallet_identity.wallet_identity_id
            result["wallet_address"] = self.wallet_identity.wallet_address
        if self.grant is not None:
            result["spending_grant_id"] = self.grant.spending_grant_id
            result["grant_status"] = self.grant.status
            result["grant_starts_at"] = self.grant.starts_at.astimezone(UTC).isoformat()
            result["grant_expires_at"] = self.grant.expires_at.astimezone(UTC).isoformat()
        if self.budget_binding is not None:
            result["budget_binding_id"] = self.budget_binding.binding_id
            result["chain_grant_id"] = self.budget_binding.grant_id
            result["grant_hash"] = self.budget_binding.grant_hash
        if self.allowance is not None:
            result["allowance_id"] = self.allowance.asset_allowance_id
            result["allowance_tx_hash"] = self.allowance.allowance_tx_hash
        return result

    def _require_ready(self) -> None:
        if self._phase() != "ready":
            raise ValueError("public Core onboarding is not ready for funding actions")

    def _require_onboarded(self) -> None:
        """Require durable initial authorization while permitting recovery reads.

        A grant may expire or be revoked after a payment has been prepared or
        broadcast.  Existing reservation inspection and reconciliation must
        remain available in that state; fresh reservations still use
        ``_require_ready`` and the Funding service's current-grant checks.
        """

        if (
            self.wallet_identity is None
            or self.grant is None
            or self.budget_binding is None
            or self.allowance is None
        ):
            raise ValueError("public Core onboarding is not complete")

    def funding_readiness(self) -> dict[str, Any]:
        self._require_onboarded()
        return {
            **self.funding_service.get_funding_readiness(),
            "simulation": False,
            "real_funds": False,
        }

    def reserve(self, payload: dict[str, Any]) -> dict:
        self._require_ready()
        return super().reserve(payload)

    def settle(self, reservation_id: str, payload: dict[str, Any]) -> dict:
        self._require_onboarded()
        return super().settle(reservation_id, payload)

    def reconcile(self, reservation_id: str) -> dict:
        self._require_onboarded()
        return super().reconcile(reservation_id)

    def finalize(self, reservation_id: str, payload: dict[str, Any]) -> dict:
        self._require_onboarded()
        return super().finalize(reservation_id, payload)

    def reservation(self, reservation_id: str) -> dict | None:
        self._require_onboarded()
        return super().reservation(reservation_id)

    def release(self, reservation_id: str, reason: str) -> dict:
        self._require_onboarded()
        return super().release(reservation_id, reason)

    def health(self) -> dict[str, Any]:
        phase = self._phase()
        return {
            "status": "ready" if phase == "ready" else "onboarding",
            "onboarding_phase": phase,
            "mode": self.network.mode,
            "network": self.network.network,
            "simulation": False,
            "real_funds": False,
        }

    def snapshot(self) -> dict[str, Any]:
        self._require_onboarded()
        grant = self.repository.spending_grant(self.grant.spending_grant_id)
        if grant is None or self.budget_binding is None:
            raise CorePersistenceError("ready public Core authorization is unavailable")
        return {
            "mode": self.network.mode,
            "simulation": False,
            "real_funds": False,
            "token_symbol": "TestUSD",
            "budget_usdc": format(Decimal(str(grant.max_amount_usdc)), ".2f"),
            "used_amount_usdc": format(Decimal(str(grant.used_amount_usdc)), ".2f"),
            "reserved_amount_usdc": format(Decimal(str(grant.reserved_amount_usdc)), ".2f"),
            "grant_status": grant.status,
            "grant_id": grant.spending_grant_id,
            "chain_grant_id": self.budget_binding.grant_id,
            "grant_hash": self.budget_binding.grant_hash,
            "network": self.network.network,
            "token": self.network.token,
            "pay_to": self.network.payee,
            "executor": self.network.executor,
            "wallet_address": self.owner_address,
            "execution_signer": self.execution_signer_address,
            "relayer": self.relayer_address,
            "merchant_id": MERCHANT_ID,
            "resource": RESOURCE,
            "settlement_submissions": self.backend.pending_nonce(),
            "receipt_signing_key": self.config.clink_receipt_signing_key,
        }

    def revoke(self) -> dict[str, Any]:
        if self.grant is None:
            raise ValueError("Core spending grant has not been verified")
        grant = self.account_service.revoke_spending_grant(self.grant.spending_grant_id)
        self.grant = grant
        return grant.model_dump(mode="json")

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        lock = getattr(self, "_state_lock", None)
        if lock is not None:
            lock.release()
            self._state_lock = None


__all__ = ["ExternalWalletCore", "CorePersistenceError"]
