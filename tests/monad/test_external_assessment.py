"""Agent-generated service assessments stay off-chain and device-scoped."""
import json

import pytest

from shared.hosted_facilitator_protocol import DeviceSigningKey
from test_external_feedback import prepared
from test_external_opc import NOW, active, proof
from test_hosted_service import OWNER_A, OWNER_B


def assessment(**updates):
    value = {
        'rubric_version': 'agent-service-v1',
        'task_fulfillment': 90,
        'result_quality': 80,
        'interaction_quality': 70,
        'confidence': 'high',
        'basis_codes': [
            'requirements_checked', 'arithmetic_checked', 'format_checked',
            'output_hash_checked', 'recovery_observed', 'single_charge_observed',
            'interaction_reviewed', 'limitations_noted',
        ],
    }
    value.update(updates)
    return value


def record(service, issued, value=None):
    return service.device_operation(
        issued['access_token'], 'assessment_record',
        {'purchase_id': 'purchase_a', 'assessment': value or assessment()},
    )


def test_agent_assessment_records_without_signing_or_payment(prepared):
    service, _, issued, _, _ = prepared
    _, canary = service._credential(issued['access_token'])
    before_budget = canary.status()['onboarding'].copy()

    result = record(service, issued)

    assert result['score'] == 82
    assert 'purchase_id' not in result
    assert canary.status()['onboarding'] == before_budget
    assert all(method != 'execute' for method, _ in canary.calls)
    assert all(method not in {'opc_approve', 'opc_revoke'} for method, _ in canary.calls)


def test_agent_assessment_is_idempotent_and_freezes_payload(prepared):
    service, _, issued, _, _ = prepared
    first = record(service, issued)
    assert record(service, issued) == first
    with pytest.raises(ValueError):
        record(service, issued, assessment(task_fulfillment=91))


def test_agent_assessment_requires_strict_evidence_shape(prepared):
    service, _, issued, _, _ = prepared
    with pytest.raises(ValueError):
        record(service, issued, assessment(extra='private text'))
    with pytest.raises(ValueError):
        record(service, issued, assessment(basis_codes=[]))


def test_agent_assessment_is_device_and_owner_scoped(prepared):
    service, _, issued, _, _ = prepared
    record(service, issued)

    other_key = DeviceSigningKey.generate()
    active(service, other_key, OWNER_B)
    other_token = service.device_request('token', proof(other_key, 'token', 'other-token'))['access_token']
    with pytest.raises(PermissionError):
        service.device_operation(other_token, 'assessment_status', {'purchase_id': 'purchase_a'})
    with pytest.raises((KeyError, PermissionError, ValueError)):
        service.device_operation(other_token, 'assessment_record', {
            'purchase_id': 'purchase_a', 'assessment': assessment(),
        })


@pytest.mark.parametrize('termination', ['expired', 'revoked'])
def test_agent_assessment_rejects_expired_or_revoked_credentials(prepared, termination):
    service, _, issued, _, _ = prepared
    record(service, issued)
    if termination == 'expired':
        NOW[0] += 301
    else:
        service.device_request('revoke', proof(issued['test_device_key'], 'revoke', 'assessment-revoke'))
    with pytest.raises(PermissionError):
        service.device_operation(issued['access_token'], 'assessment_status', {'purchase_id': 'purchase_a'})
    with pytest.raises(PermissionError):
        service.device_operation(issued['access_token'], 'assessment_record', {
            'purchase_id': 'purchase_a', 'assessment': assessment(),
        })


def test_agent_assessment_rejects_same_wallet_order_bound_to_other_device(prepared):
    service, _, issued, _, _ = prepared
    _, canary = service._credential(issued['access_token'])
    canary.orders['purchase_a']['device'] = 'another-installation'

    with pytest.raises(PermissionError):
        service.device_operation(issued['access_token'], 'assessment_status', {
            'purchase_id': 'purchase_a',
        })
    with pytest.raises(PermissionError):
        service.device_operation(issued['access_token'], 'assessment_record', {
            'purchase_id': 'purchase_a', 'assessment': assessment(),
        })


@pytest.mark.parametrize('failure', ['pending', 'unverified', 'history'])
def test_agent_assessment_failure_does_not_create_history_sample(prepared, monkeypatch, failure):
    service, _, issued, _, _ = prepared
    _, canary = service._credential(issued['access_token'])
    if failure == 'pending':
        canary.orders['purchase_a']['purchase']['state'] = 'paid'
    elif failure == 'unverified':
        canary.orders['purchase_a']['purchase']['settlement']['verified'] = False
    else:
        def fail_history(**_):
            raise ValueError('history unavailable')
        monkeypatch.setattr(service.history, 'record_purchase', fail_history)

    with pytest.raises(ValueError):
        record(service, issued)
    history = service.device_operation(
        issued['access_token'], 'service_history', {'offering_id': 'csv-reconciliation-v1'}
    )
    assert history['agent_assessments']['sample_size'] == 0


def test_agent_assessment_does_not_change_optional_buyer_feedback(prepared):
    service, _, issued, _, _ = prepared
    record(service, issued)
    feedback = service.device_operation(
        issued['access_token'], 'feedback_prepare', {'purchase_id': 'purchase_a', 'score': 87}
    )
    assert feedback['status'] == 'prepared'


def test_agent_assessments_are_separate_from_public_history_and_private_orders(prepared):
    service, _, issued, _, _ = prepared
    record(service, issued)
    history = service.device_operation(
        issued['access_token'], 'service_history', {'offering_id': 'csv-reconciliation-v1'}
    )
    assert history['agent_assessments']['sample_size'] == 1
    text = json.dumps(history)
    for private in ('NEVER-PUBLISH-CSV', 'PRIVATE-INPUT-HASH', 'purchase_a',
                    'preview_a', issued['installation_id'], OWNER_A):
        assert private not in text


def test_agent_assessment_status_is_read_only_and_returns_original_payload(prepared):
    service, _, issued, _, _ = prepared
    first = record(service, issued)
    before_calls = len(service._credential(issued['access_token'])[1].calls)
    current = service.device_operation(
        issued['access_token'], 'assessment_status', {'purchase_id': 'purchase_a'}
    )
    after_calls = len(service._credential(issued['access_token'])[1].calls)
    assert current == first
    assert after_calls > before_calls
    assert all(method != 'execute' for method, _ in
               service._credential(issued['access_token'])[1].calls)
