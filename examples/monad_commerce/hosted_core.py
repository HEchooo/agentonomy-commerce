"""Hosted browser and OPC composition for the Monad Commerce Core.

This profile is a transport-facing adapter around :class:`ExternalWalletCore`.
It keeps the existing Core repository, wallet identity, spending grant and
budget binding as the only authority.  Browser cookies and OPC credentials are
opaque capability references; this module never accepts or stores a wallet
private key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any
from uuid import uuid4

from examples.monad_commerce.public_core import (
    AGENT_ID,
    CHALLENGE_TTL,
    MERCHANT_ID,
    TRUST_TIER,
    USER_ID,
    VALIDITY,
    VENUE,
    CorePersistenceError,
    ExternalWalletCore,
    _atomic_write_bytes,
    _canonical_signature,
    _now,
    _whole_second_now,
)
from services.account_service.opc_service import (
    OPC_INSTALLATION_PURPOSE,
    OpcAccountService,
)
from services.account_service.schemas import (
    AccountSession,
    AuthorizationResolutionRequest,
    PublicAccountSession,
    SpendingGrantRequest,
)


BROWSER_SESSION_TTL = timedelta(hours=1)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_BASE_STATE_FILES = (
    "public-onboarding.json",
    "core.sqlite3",
    "receipt.secret",
)
_OPC_TOKEN_FILENAME = "opc-token.secret"


def _path_exists_including_dangling(path: Path) -> bool:
    """Return whether a state path entry exists, including a dangling link."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CorePersistenceError("persistent hosted Core state is unreadable") from exc
    return True


class HostedWalletCore(ExternalWalletCore):
    """External-wallet Core with a durable browser session and OPC adapter.

    ``USER_ID`` and ``AGENT_ID`` deliberately stay fixed for this composition.
    Tenant selection belongs to the caller's outer namespace; this class does
    not create a second identity or budget authority inside the Core database.
    """

    USER_ID = USER_ID
    AGENT_ID = AGENT_ID
    BROWSER_SESSION_TTL = BROWSER_SESSION_TTL
    BROWSER_CHALLENGE_TTL = CHALLENGE_TTL

    def __init__(
        self,
        state_dir: str | os.PathLike[str],
        bootstrap: dict[str, Any],
        signer: Any,
        *,
        allow_local: bool = False,
        opc_origin: str = "http://127.0.0.1:8091",
        allow_loopback_http: bool = True,
        account_origin: str | None = None,
        mcp_url: str | None = None,
    ) -> None:
        resolved_state_dir = Path(state_dir).expanduser().resolve()
        base_state_present = [
            _path_exists_including_dangling(resolved_state_dir / name)
            for name in _BASE_STATE_FILES
        ]
        opc_secret_present = _path_exists_including_dangling(
            resolved_state_dir / _OPC_TOKEN_FILENAME
        )
        if any(base_state_present) or opc_secret_present:
            if not (all(base_state_present) and opc_secret_present):
                raise CorePersistenceError("persistent hosted Core state is incomplete")
        self._hosted_state_existed = all(base_state_present)
        super().__init__(state_dir, bootstrap, signer, allow_local=allow_local)
        self.user_id = USER_ID
        self.agent_id = AGENT_ID
        try:
            token_signing_key = self._load_opc_token_key()
            self.opc_service = OpcAccountService(
                self.account_service,
                origin=opc_origin,
                token_signing_key=token_signing_key,
                clock=_now,
                allow_loopback_http=allow_loopback_http,
                account_origin=account_origin or opc_origin,
                mcp_url=mcp_url,
            )
        except Exception:
            self.close()
            raise

    def _load_opc_token_key(self) -> bytes:
        """Load or create the OPC key with permissions independent of receipts."""

        path = self.state_dir / _OPC_TOKEN_FILENAME
        try:
            path.lstat()
        except FileNotFoundError:
            if self._hosted_state_existed:
                raise CorePersistenceError(
                    "persistent OPC token secret is missing"
                ) from None
        except OSError as exc:
            raise CorePersistenceError(
                "persistent OPC token secret is unreadable"
            ) from exc
        else:
            try:
                link_status = path.lstat()
            except OSError as exc:
                raise CorePersistenceError(
                    "persistent OPC token secret is unreadable"
                ) from exc
            if stat.S_ISLNK(link_status.st_mode):
                raise CorePersistenceError(
                    "persistent OPC token secret must not be a symlink"
                )
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise CorePersistenceError(
                    "persistent OPC token secret is unreadable"
                ) from exc
            try:
                file_status = os.fstat(descriptor)
                if not stat.S_ISREG(file_status.st_mode):
                    raise CorePersistenceError(
                        "persistent OPC token secret must be a regular file"
                    )
                if file_status.st_nlink != 1:
                    raise CorePersistenceError(
                        "persistent OPC token secret must have one directory link"
                    )
                if stat.S_IMODE(file_status.st_mode) != 0o600:
                    raise CorePersistenceError(
                        "persistent OPC token secret permissions are too broad"
                    )
                if file_status.st_uid != os.getuid():
                    raise CorePersistenceError(
                        "persistent OPC token secret owner is invalid"
                    )
                if file_status.st_size != 32:
                    raise CorePersistenceError(
                        "persistent OPC token secret must contain exactly 32 bytes"
                    )
                value = os.read(descriptor, 33)
            except CorePersistenceError:
                raise
            except OSError as exc:
                raise CorePersistenceError(
                    "persistent OPC token secret is unreadable"
                ) from exc
            finally:
                os.close(descriptor)
            if len(value) != 32:
                raise CorePersistenceError(
                    "persistent OPC token secret must contain exactly 32 bytes"
                )
            return value

        value = secrets.token_bytes(32)
        try:
            _atomic_write_bytes(path, value, mode=0o600)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise CorePersistenceError(
                "persistent OPC token secret could not be created"
            ) from exc
        return value

    @staticmethod
    def _validate_digest(value: object, *, field_name: str) -> str:
        if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
            raise ValueError(
                f"{field_name} must be a lowercase 64-character hex digest"
            )
        return value

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if not isinstance(value, datetime):
            raise ValueError("timestamp is invalid")
        return value.astimezone(UTC).isoformat()

    def _browser_session(
        self,
        browser_digest: object,
        csrf_digest: object | None = None,
        *,
        require_csrf: bool = False,
    ) -> PublicAccountSession:
        browser = self._validate_digest(browser_digest, field_name="browser_digest")
        csrf = (
            self._validate_digest(csrf_digest, field_name="csrf_digest")
            if csrf_digest is not None
            else None
        )
        session = self.repository.access_public_account_browser_session(browser, _now())
        if session is None or session.status != "active" or session.user_id != USER_ID:
            raise ValueError("browser session is unavailable")
        if require_csrf and csrf is None:
            raise ValueError("CSRF token is required")
        if csrf is not None and (
            session.csrf_token_digest is None
            or not hmac.compare_digest(session.csrf_token_digest, csrf)
        ):
            raise ValueError("CSRF token does not match browser session")
        return session

    def _authorized_browser_session(
        self, browser_digest: object, csrf_digest: object | None = None
    ) -> PublicAccountSession:
        session = self._browser_session(
            browser_digest, csrf_digest, require_csrf=csrf_digest is not None
        )
        identity_id = session.authenticated_wallet_identity_id
        if identity_id is None:
            raise ValueError("browser session is not authenticated")
        identity = self.repository.wallet_identity(identity_id)
        if (
            identity is None
            or identity.status != "active"
            or identity.user_id != USER_ID
            or identity.wallet_address != self.owner_address
        ):
            raise ValueError("browser session wallet authorization is unavailable")
        if (
            self.wallet_identity is None
            or self.wallet_identity.wallet_identity_id != identity.wallet_identity_id
        ):
            raise ValueError("browser session wallet authorization is unavailable")
        return session

    def _grant_challenge_public_session_id(self) -> str | None:
        challenge = self._metadata.get("grant_challenge")
        if challenge is None:
            return None
        account_session = self.repository.account_session(challenge.get("session_id"))
        if account_session is None:
            raise CorePersistenceError(
                "persistent public Core grant challenge is missing"
            )
        public_session_id = account_session.created_by_public_account_session_id
        if not isinstance(public_session_id, str) or not public_session_id:
            raise CorePersistenceError(
                "persistent public Core grant challenge browser binding is missing"
            )
        return public_session_id

    def _grant_request(self) -> SpendingGrantRequest:
        raw = self._metadata.get("grant_request")
        if not isinstance(raw, dict):
            raise ValueError("Core grant challenge has not been created")
        public_session_id = self._grant_challenge_public_session_id()
        return SpendingGrantRequest.model_validate(
            raw
            | {
                "created_by_public_account_session_id": public_session_id,
                "session_id": None,
                "signed_message": None,
                "signature": None,
            }
        )

    def _grant_request_has_fixed_terms(self, request: SpendingGrantRequest) -> bool:
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
            and request.created_by_public_account_session_id
            == self._grant_challenge_public_session_id()
            and request.starts_at.microsecond == 0
            and request.expires_at.microsecond == 0
            and request.expires_at - request.starts_at
            == VALIDITY + timedelta(seconds=60)
        )

    def grant_challenge(
        self, browser_digest: object, csrf_digest: object
    ) -> dict[str, Any]:
        """Create a grant challenge bound to the current browser session.

        The inherited ExternalWalletCore flow binds this request to the
        original wallet challenge. Hosted login sessions are rotatable, so an
        expired or logged-out browser session must never be reused for a new
        spending grant. Existing signed grants and budget state are returned
        as-is and are never reconstructed here.
        """
        session = self._authorized_browser_session(browser_digest, csrf_digest)
        if self.grant is not None:
            phase = self._phase()
            if phase in {"expired", "revoked", "blocked"}:
                raise ValueError("Core spending grant is unavailable")
            return {
                "status": "verified",
                "spending_grant_id": self.grant.spending_grant_id,
                "expires_at": self.grant.expires_at.astimezone(UTC).isoformat(),
            }

        existing = self._metadata.get("grant_challenge")
        if existing is not None:
            try:
                expires_at = datetime.fromisoformat(existing["expires_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CorePersistenceError(
                    "persistent public Core grant challenge expiry is invalid"
                ) from exc
            existing_public_session_id = self._grant_challenge_public_session_id()
            if (
                existing_public_session_id == session.public_account_session_id
                and _now() < expires_at.astimezone(UTC)
            ):
                request = self._metadata.get("grant_request")
                if not isinstance(request, dict):
                    raise CorePersistenceError(
                        "persistent public Core grant request is missing"
                    )
                return {
                    **existing,
                    "signing_method": "personal_sign",
                    "grant_request": request,
                }
            if self._metadata.get("grant_signature") is not None:
                raise CorePersistenceError(
                    "persistent public Core grant challenge outcome requires inspection"
                )

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
            created_by_public_account_session_id=session.public_account_session_id,
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

    def _browser_authorization(self, session: PublicAccountSession) -> dict[str, Any]:
        identity_id = session.authenticated_wallet_identity_id
        if identity_id is None:
            raise ValueError("browser session is not authenticated")
        identity = self.repository.wallet_identity(identity_id)
        if (
            identity is None
            or identity.status != "active"
            or identity.user_id != USER_ID
            or identity.wallet_address != self.owner_address
        ):
            raise ValueError("browser session wallet authorization is unavailable")
        if (
            self.wallet_identity is None
            or self.wallet_identity.wallet_identity_id != identity.wallet_identity_id
        ):
            raise ValueError("browser session wallet authorization is unavailable")
        return {
            "status": "authorized",
            "public_account_session_id": session.public_account_session_id,
            "user_id": USER_ID,
            "wallet_identity_id": identity.wallet_identity_id,
            "wallet_address": identity.wallet_address,
            "expires_at": self._timestamp(session.expires_at),
        }

    def browser_challenge(
        self, browser_digest: object, csrf_digest: object
    ) -> dict[str, Any]:
        browser = self._validate_digest(browser_digest, field_name="browser_digest")
        csrf = self._validate_digest(csrf_digest, field_name="csrf_digest")
        if (
            self.repository.access_public_account_browser_session(browser, _now())
            is not None
        ):
            raise ValueError("browser session digest is already in use")

        now = _now()
        public_session = PublicAccountSession(
            public_account_session_id=f"public_hosted_{uuid4().hex}",
            token_digest=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            browser_session_digest=browser,
            csrf_token_digest=csrf,
            user_id=USER_ID,
            expires_at=now + BROWSER_SESSION_TTL,
            exchanged_at=now,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_public_account_session(public_session)
        challenge = self.account_service.create_wallet_challenge(
            USER_ID,
            self.owner_address,
            created_by_public_account_session_id=public_session.public_account_session_id,
        )
        return {
            "public_account_session_id": public_session.public_account_session_id,
            "session_id": challenge.session_id,
            "message_to_sign": challenge.message_to_sign,
            "expires_at": self._timestamp(challenge.expires_at),
            "browser_expires_at": self._timestamp(public_session.expires_at),
            "signing_method": "personal_sign",
        }

    def _validate_wallet_challenge(
        self, session: PublicAccountSession, challenge_id: object
    ) -> AccountSession:
        if not isinstance(challenge_id, str) or not challenge_id:
            raise ValueError("wallet challenge is invalid")
        challenge = self.repository.account_session(challenge_id)
        now = _now()
        if (
            challenge is None
            or challenge.purpose != "clink_wallet_identity"
            or challenge.domain != self.domain
            or challenge.user_id != USER_ID
            or challenge.wallet_address != self.owner_address
            or challenge.created_by_public_account_session_id
            != session.public_account_session_id
        ):
            raise ValueError("wallet challenge is not bound to browser session")
        if challenge.consumed_at is not None:
            raise ValueError("wallet challenge already consumed")
        if challenge.expires_at.astimezone(UTC) <= now:
            raise ValueError("wallet challenge expired")
        return challenge

    def browser_verify(
        self,
        browser_digest: object,
        csrf_digest: object,
        challenge_id: object,
        signature: str | bytes,
    ) -> dict[str, Any]:
        session = self._browser_session(browser_digest, csrf_digest, require_csrf=True)
        challenge = self._validate_wallet_challenge(session, challenge_id)
        canonical = _canonical_signature(signature)

        if self.wallet_identity is None:
            existing_signature = self._metadata.get("wallet_signature")
            if existing_signature is not None:
                raise CorePersistenceError(
                    "persistent wallet verification outcome requires inspection"
                )
            self._metadata["wallet_challenge"] = {
                "public_account_session_id": session.public_account_session_id,
                "session_id": challenge.account_session_id,
                "message_to_sign": self.account_service._canonical_message(challenge),
                "expires_at": self._timestamp(challenge.expires_at),
            }
            self._persist_state()
            self.wallet_verify(canonical)
        else:
            self.wallet_identity = self.account_service.verify_wallet_challenge(
                challenge.account_session_id,
                self.account_service._canonical_message(challenge),
                canonical,
            )

        authenticated = self.repository.authenticate_public_account_browser_session(
            session.public_account_session_id,
            self._validate_digest(browser_digest, field_name="browser_digest"),
            self.wallet_identity.wallet_identity_id,
            _now(),
        )
        return self._browser_authorization(authenticated)

    def browser_authorize(
        self, browser_digest: object, csrf_digest: object | None = None
    ) -> dict[str, Any]:
        return self._browser_authorization(
            self._authorized_browser_session(browser_digest, csrf_digest)
        )

    def browser_logout(self, browser_digest: object) -> dict[str, Any]:
        session = self._browser_session(browser_digest)
        revoked = self.repository.revoke_public_account_session(
            session.token_digest, _now()
        )
        if revoked is None:
            raise ValueError("browser session is unavailable")
        return {
            "status": "logged_out",
            "public_account_session_id": revoked.public_account_session_id,
        }

    def _require_hosted_authority(self) -> None:
        self._require_ready()
        if (
            self.wallet_identity is None
            or self.grant is None
            or self.budget_binding is None
        ):
            raise ValueError("hosted Core authorization is unavailable")
        identity = self.repository.wallet_identity(
            self.wallet_identity.wallet_identity_id
        )
        grant = self.repository.spending_grant(self.grant.spending_grant_id)
        if (
            identity is None
            or identity.status != "active"
            or identity.user_id != USER_ID
            or identity.wallet_address != self.owner_address
            or grant is None
            or grant.status != "active"
            or grant.user_id != USER_ID
            or grant.wallet_identity_id != identity.wallet_identity_id
            or grant.agent_id != AGENT_ID
            or self.budget_binding.user_id != USER_ID
            or self.budget_binding.agent_id != AGENT_ID
            or self.budget_binding.status != "active"
            or self.budget_binding.spending_grant_id != grant.spending_grant_id
            or self.budget_binding.wallet_identity_id != identity.wallet_identity_id
        ):
            raise ValueError("hosted Core authorization is unavailable")
        self.wallet_identity = identity
        self.grant = grant

    def resolve_authorization(self, payload: dict[str, Any]) -> dict:
        request = AuthorizationResolutionRequest.model_validate(payload)
        if request.opc_installation_id is None:
            return super().resolve_authorization(payload)
        authority = None
        try:
            authority = self.opc_service.authorization_scope(
                request.opc_installation_id, user_id=request.user_id, agent_id=request.agent_id,
            )
        except ValueError:
            pass
        # Same canonical resolution used by the shared Core HTTP adapter.
        # Funding independently checks this installation again in its ledger.
        return self.account_service.resolve_authorization(
            request, opc_authorization=authority,
        ).model_dump(mode='json')

    def opc_pair(
        self, proof: str, browser_digest: object, csrf_digest: object
    ) -> dict[str, Any]:
        self._require_hosted_authority()
        session = self._authorized_browser_session(browser_digest, csrf_digest)
        pairing = self.opc_service.create_pairing(proof)
        self.opc_service.claim_pairing(
            pairing["pairing_id"],
            user_id=USER_ID,
            public_account_session_id=session.public_account_session_id,
        )
        challenge = self.opc_service.create_installation_challenge(
            pairing["pairing_id"],
            user_id=USER_ID,
            public_account_session_id=session.public_account_session_id,
            spending_grant_id=self.grant.spending_grant_id,
        )
        return {
            "pairing_id": pairing["pairing_id"],
            "installation_id": pairing["installation_id"],
            "public_account_session_id": session.public_account_session_id,
            "session_id": challenge["session_id"],
            "message_to_sign": challenge["message_to_sign"],
            "expires_at": self._timestamp(challenge["expires_at"]),
            "agent_id": AGENT_ID,
            "spending_grant_id": self.grant.spending_grant_id,
        }

    def _validate_opc_challenge(
        self, session: PublicAccountSession, challenge_id: object
    ) -> AccountSession:
        if not isinstance(challenge_id, str) or not challenge_id:
            raise ValueError("OPC installation challenge is invalid")
        challenge = self.repository.account_session(challenge_id)
        now = _now()
        if (
            challenge is None
            or challenge.purpose != OPC_INSTALLATION_PURPOSE
            or challenge.domain != self.domain
            or challenge.user_id != USER_ID
            or challenge.wallet_address != self.owner_address
            or challenge.wallet_identity_id != session.authenticated_wallet_identity_id
            or challenge.created_by_public_account_session_id
            != session.public_account_session_id
        ):
            raise ValueError(
                "OPC installation challenge is not bound to browser session"
            )
        if challenge.consumed_at is not None:
            raise ValueError("OPC installation challenge already consumed")
        if challenge.expires_at.astimezone(UTC) <= now:
            raise ValueError("OPC installation challenge expired")
        return challenge

    def opc_approve(
        self,
        browser_digest: object,
        csrf_digest: object,
        challenge_id: object,
        signature: str | bytes,
    ) -> dict[str, Any]:
        self._require_hosted_authority()
        session = self._authorized_browser_session(browser_digest, csrf_digest)
        challenge = self._validate_opc_challenge(session, challenge_id)
        canonical = _canonical_signature(signature)
        signed_message = self.opc_service._canonical_installation_message(challenge)
        return self.opc_service.approve_installation(
            challenge.account_session_id, signed_message, canonical
        )

    def opc_token(self, proof: str) -> dict[str, Any]:
        self._require_hosted_authority()
        return self.opc_service.issue_token(proof)

    def opc_status(self, proof: str) -> dict[str, Any]:
        result = self.opc_service.status(proof)
        if result.get('status') == 'active':
            try:
                self.opc_service.authorization_scope(
                    result['installation_id'], user_id=USER_ID, agent_id=AGENT_ID,
                )
            except ValueError:
                # Status must reflect the effective consent/grant lifetime,
                # rather than a durable row that has not yet been amended.
                result = result | {'status': 'consent_required'}
        return result

    def opc_authenticate(self, access_token: str) -> dict[str, Any]:
        principal = self.opc_service.authenticate_access_token(access_token)
        if (
            principal.get("user_id") != USER_ID
            or principal.get("agent_id") != AGENT_ID
            or self.wallet_identity is None
            or self.grant is None
            or self.budget_binding is None
            or principal.get("wallet_identity_id")
            != self.wallet_identity.wallet_identity_id
            or principal.get("spending_grant_id") != self.grant.spending_grant_id
        ):
            raise ValueError("OPC access token is outside hosted Core authorization")
        identity = self.repository.wallet_identity(principal["wallet_identity_id"])
        grant = self.repository.spending_grant(principal["spending_grant_id"])
        if (
            identity is None
            or identity.status != "active"
            or identity.user_id != USER_ID
            or identity.wallet_address != self.owner_address
            or grant is None
            or grant.status != "active"
            or grant.user_id != USER_ID
            or grant.agent_id != AGENT_ID
            or grant.wallet_identity_id != identity.wallet_identity_id
            or self.budget_binding.user_id != USER_ID
            or self.budget_binding.agent_id != AGENT_ID
            or self.budget_binding.status != "active"
            or self.budget_binding.spending_grant_id != grant.spending_grant_id
            or self.budget_binding.wallet_identity_id != identity.wallet_identity_id
        ):
            raise ValueError("OPC access token is outside hosted Core authorization")
        return principal


__all__ = ["HostedWalletCore"]
