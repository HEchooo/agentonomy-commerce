import copy
import os
from pathlib import Path
import io
import json
import subprocess

import pytest

from agentonomy_commerce.signer_process import validate_configuration, signer_environment
from agentonomy_commerce import signer_process


def config():
    return {
        'profile': 'agentonomy-dev', 'region': 'ap-southeast-1', 'account_id': '123456789012',
        'execution_key_arn': 'arn:aws:kms:ap-southeast-1:123456789012:key/11111111-1111-4111-8111-111111111111',
        'gas_key_arn': 'arn:aws:kms:ap-southeast-1:123456789012:key/22222222-2222-4222-8222-222222222222',
        'scope': {
            'network': {'mode': 'monad_testnet', 'chain_id': 10143,
                'rpc_urls': ['https://rpc.example.org', 'https://rpc.example.net'],
                'token': '0x' + '11' * 20, 'executor': '0x' + '22' * 20, 'payee': '0x' + '33' * 20},
            'owner': '0x' + '44' * 20, 'execution_address': '0x' + '55' * 20,
            'relayer_address': '0x' + '66' * 20, 'nonce_min': 2, 'nonce_max': 5,
        },
    }


def test_config_pins_two_distinct_keys_to_account_and_region():
    scope = validate_configuration(config())
    assert scope.network.chain_id == 10143
    for mutation in (
        {'gas_key_arn': config()['execution_key_arn']},
        {'account_id': '999999999999'}, {'region': 'us-east-1'},
        {'execution_key_arn': 'alias/my-key'}, {'AWS_SECRET_ACCESS_KEY': 'secret'},
        {'profile': '../credentials'},
    ):
        with pytest.raises(ValueError):
            validate_configuration(config() | mutation)


def test_worker_cannot_be_started_for_local_anvil_or_mainnet():
    for mode, chain in [('local_anvil', 31337), ('monad_testnet', 143)]:
        value = copy.deepcopy(config())
        value['scope']['network'].update(mode=mode, chain_id=chain)
        with pytest.raises(ValueError):
            validate_configuration(value)


def test_worker_environment_does_not_forward_credentials_or_endpoints(monkeypatch):
    for field in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                  'AWS_ENDPOINT_URL', 'AWS_PROFILE', 'PYTHONPATH', 'HTTPS_PROXY'):
        monkeypatch.setenv(field, 'must-not-inherit')
    env = signer_environment()
    assert not any(value == 'must-not-inherit' for value in env.values())
    assert env['AWS_EC2_METADATA_DISABLED'] == 'true'
    assert Path(env['HOME']).is_absolute()


def run_worker(monkeypatch, lines, loader):
    stream = io.StringIO('\n'.join([json.dumps(config()), *lines]) + '\n')
    output = io.StringIO()
    monkeypatch.setattr(signer_process.sys, 'stdin', stream)
    monkeypatch.setattr(signer_process.sys, 'stdout', output)
    monkeypatch.setattr(signer_process, '_load_signer', loader)
    code = signer_process.worker_main()
    return code, [json.loads(line) for line in output.getvalue().splitlines()]


@pytest.mark.parametrize('bad_line', ['{broken json', 'x' * 65537, '[]'], ids=['malformed', 'oversized', 'array'])
def test_malformed_framing_terminates_without_reusing_prior_id(monkeypatch, bad_line):
    code, rows = run_worker(monkeypatch,
        [json.dumps({'id': 1, 'method': 'health', 'params': {}}), bad_line], lambda *_: object())
    assert code == 2
    assert [row['id'] for row in rows] == [1]


def test_startup_denial_is_redacted(monkeypatch):
    def denied(*args):
        raise RuntimeError('AWS_SECRET_ACCESS_KEY=hidden must never escape')
    code, rows = run_worker(monkeypatch, [], denied)
    assert code == 2 and rows[0]['id'] is None and rows[0]['ok'] is False
    assert 'hidden' not in json.dumps(rows)


def test_generic_digest_operation_is_rejected(monkeypatch):
    code, rows = run_worker(monkeypatch,
        [json.dumps({'id': 1, 'method': 'sign_digest', 'params': {'digest': '00' * 32}})], lambda *_: object())
    assert code == 0 and rows[0]['ok'] is False


def test_bridge_rejects_wrong_response_id_and_never_restarts(monkeypatch):
    real_popen = subprocess.Popen
    calls = []
    source = "import sys,json;sys.stdin.readline();r=json.loads(sys.stdin.readline());print(json.dumps({'id':r['id']+1,'ok':True,'result':{}}),flush=True)"
    def fake_worker(args, **kwargs):
        calls.append(args)
        return real_popen([args[0], '-c', source], **kwargs)
    monkeypatch.setattr(signer_process.subprocess, 'Popen', fake_worker)
    bridge = signer_process.KmsSignerBridge(config())
    try:
        with pytest.raises(RuntimeError):
            bridge.health()
        with pytest.raises(RuntimeError):
            bridge.health()
        assert len(calls) == 1 and bridge.process is None
    finally:
        bridge.close()
