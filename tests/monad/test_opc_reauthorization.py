from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import time

import pytest
from sqlalchemy import select

from apps.facilitator.budget_protocol import sign_grant
from shared.hosted_facilitator_protocol import DeviceSigningKey
from shared.opc_protocol import sign_opc_proof

pytest_plugins = ["test_hosted_core"]

from services.account_service.opc_service import (
    OpcAccessCredentialRow,
    OpcInstallationRow,
)
from test_hosted_core import (  # noqa: E402
    APPROVAL_TX,
    EXECUTOR,
    OPC_ORIGIN,
    OWNER,
    _onboard,
    _sign_personal,
)
from test_grant_renewal import _expire_grant, _renew  # noqa: E402


def test_expired_device_consent_can_be_reauthorized_without_reinstalling_the_device(
    core_factory, tmp_path
):
    """An expired device consent must yield a fresh owner challenge.

    The durable installation and grant remain the same authority. The old
    credential is rejected, and no replacement credential is issued before the
    wallet signs the new installation challenge.
    """

    core = core_factory(tmp_path)
    try:
        browser, csrf, _wallet = _onboard(core, "opc-reauthorization")
        device = DeviceSigningKey.generate()

        def proof(action: str, request_id: str) -> str:
            return sign_opc_proof(
                device,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action=action,
                request_id=request_id,
                now=int(time.time()),
            )

        first_pairing = core.opc_pair(
            proof("pair", "reauthorization-initial-pair"), browser, csrf
        )
        first_installation = core.opc_approve(
            browser,
            csrf,
            first_pairing["session_id"],
            _sign_personal(first_pairing["message_to_sign"], OWNER),
        )
        assert first_installation["status"] == "active"

        old_token = core.opc_token(proof("token", "reauthorization-old-token"))
        assert core.opc_authenticate(old_token["access_token"])["installation_id"] == (
            first_pairing["installation_id"]
        )

        with core.repository._write_session() as session:
            installation = session.get(
                OpcInstallationRow, first_pairing["installation_id"]
            )
            assert installation is not None
            installation.consent_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        with pytest.raises(ValueError, match="consent|authority|unavailable|not active"):
            core.opc_authenticate(old_token["access_token"])
        assert core.opc_status(proof("status", "reauthorization-expired-status"))["status"] == (
            "consent_required"
        )

        fresh_pairing = core.opc_pair(
            proof("pair", "reauthorization-fresh-pair"), browser, csrf
        )
        assert fresh_pairing["installation_id"] == first_pairing["installation_id"]
        assert fresh_pairing["session_id"]
        assert fresh_pairing["message_to_sign"]

        with pytest.raises(ValueError, match="consent|authority|unavailable|not active"):
            core.opc_token(proof("token", "reauthorization-before-approval"))

        renewed_installation = core.opc_approve(
            browser,
            csrf,
            fresh_pairing["session_id"],
            _sign_personal(fresh_pairing["message_to_sign"], OWNER),
        )
        assert renewed_installation["status"] == "active"
        with core.repository.sessions() as session:
            old_credential = session.scalar(
                select(OpcAccessCredentialRow).where(
                    OpcAccessCredentialRow.token_digest
                    == hashlib.sha256(old_token["access_token"].encode()).hexdigest()
                )
            )
            assert old_credential is not None
            assert old_credential.status == "revoked"
        new_token = core.opc_token(proof("token", "reauthorization-new-token"))
        assert core.opc_authenticate(new_token["access_token"])["installation_id"] == (
            first_pairing["installation_id"]
        )
        with pytest.raises(ValueError, match="revoked|consent|authority|unavailable"):
            core.opc_authenticate(old_token["access_token"])
    finally:
        core.close()


def test_valid_active_pairing_does_not_downgrade_installation(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _wallet = _onboard(core, "opc-active-repair")
        device = DeviceSigningKey.generate()

        def proof(action: str, request_id: str) -> str:
            return sign_opc_proof(
                device,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action=action,
                request_id=request_id,
                now=int(time.time()),
            )

        initial = core.opc_pair(proof("pair", "active-initial-pair"), browser, csrf)
        core.opc_approve(
            browser,
            csrf,
            initial["session_id"],
            _sign_personal(initial["message_to_sign"], OWNER),
        )
        before = core.opc_service.installation(initial["installation_id"])
        refreshed = core.opc_service.create_pairing(
            proof("pair", "active-refresh-pair")
        )
        after = core.opc_service.installation(initial["installation_id"])

        assert refreshed["status"] == "active"
        assert after["status"] == "active"
        assert after["consent_expires_at"] == before["consent_expires_at"]
        assert core.opc_status(proof("status", "active-status"))["status"] == "active"
    finally:
        core.close()


def test_revoked_installation_cannot_be_revived_by_a_new_pairing(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _wallet = _onboard(core, "opc-revoked-repair")
        device = DeviceSigningKey.generate()

        def proof(action: str, request_id: str) -> str:
            return sign_opc_proof(
                device,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action=action,
                request_id=request_id,
                now=int(time.time()),
            )

        initial = core.opc_pair(proof("pair", "revoked-initial-pair"), browser, csrf)
        core.opc_approve(
            browser,
            csrf,
            initial["session_id"],
            _sign_personal(initial["message_to_sign"], OWNER),
        )
        core.opc_service.revoke(proof("revoke", "revoked-installation"))

        with pytest.raises(ValueError, match="revoked"):
            core.opc_service.create_pairing(proof("pair", "revoked-new-pair"))
        assert core.opc_service.installation(initial["installation_id"])["status"] == (
            "revoked"
        )
    finally:
        core.close()


def test_wrong_device_key_cannot_downgrade_existing_installation(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _wallet = _onboard(core, "opc-wrong-device")
        device = DeviceSigningKey.generate()

        def proof(key: DeviceSigningKey, action: str, request_id: str) -> str:
            return sign_opc_proof(
                key,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action=action,
                request_id=request_id,
                now=int(time.time()),
            )

        initial = core.opc_pair(
            proof(device, "pair", "wrong-device-initial-pair"), browser, csrf
        )
        core.opc_approve(
            browser,
            csrf,
            initial["session_id"],
            _sign_personal(initial["message_to_sign"], OWNER),
        )
        wrong_device = DeviceSigningKey.generate()
        wrong_pairing = core.opc_service.create_pairing(
            proof(wrong_device, "pair", "wrong-device-pair")
        )

        assert wrong_pairing["installation_id"] != initial["installation_id"]
        assert core.opc_service.installation(initial["installation_id"])["status"] == (
            "active"
        )
    finally:
        core.close()


def test_expired_grant_requires_fresh_consent_and_rebind_for_existing_device(
    core_factory, tmp_path
):
    core = core_factory(tmp_path)
    try:
        browser, csrf, _wallet = _onboard(core, "opc-grant-reauthorization")
        device = DeviceSigningKey.generate()

        def proof(action: str, request_id: str) -> str:
            return sign_opc_proof(
                device,
                origin=OPC_ORIGIN,
                allow_loopback_http=True,
                action=action,
                request_id=request_id,
                now=int(time.time()),
            )

        initial = core.opc_pair(
            proof("pair", "grant-reauthorization-initial-pair"), browser, csrf
        )
        core.opc_approve(
            browser,
            csrf,
            initial["session_id"],
            _sign_personal(initial["message_to_sign"], OWNER),
        )
        old_token = core.opc_token(
            proof("token", "grant-reauthorization-old-token")
        )["access_token"]
        old_grant_id = core.grant.spending_grant_id

        _expire_grant(core)
        renewed = _renew(core, browser, csrf)
        new_grant_id = renewed["result"]["spending_grant_id"]
        assert new_grant_id != old_grant_id
        payload = core.budget_payload()
        core.budget_bind(
            "0x"
            + sign_grant(payload["grant"], OWNER.key, 31337, EXECUTOR).hex()
        )
        core.allowance_verify(APPROVAL_TX)
        assert core._phase() == "ready"
        with pytest.raises(ValueError, match="authorization|grant|authority|active|revoked"):
            core.opc_authenticate(old_token)
        assert core.opc_status(proof("status", "grant-reauthorization-status"))["status"] == (
            "consent_required"
        )

        fresh = core.opc_pair(
            proof("pair", "grant-reauthorization-fresh-pair"), browser, csrf
        )
        approved = core.opc_approve(
            browser,
            csrf,
            fresh["session_id"],
            _sign_personal(fresh["message_to_sign"], OWNER),
        )
        assert approved["status"] == "active"
        assert approved["spending_grant_id"] == new_grant_id
        new_token = core.opc_token(
            proof("token", "grant-reauthorization-new-token")
        )["access_token"]
        assert core.opc_authenticate(new_token)["spending_grant_id"] == new_grant_id
        with pytest.raises(ValueError, match="authorization|grant|authority|active|revoked"):
            core.opc_authenticate(old_token)
    finally:
        core.close()
