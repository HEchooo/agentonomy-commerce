import copy
import os
from pathlib import Path
import io
import json
import subprocess
import sys
import stat

import pytest
from botocore.exceptions import ClientError

from agentonomy_commerce.signer_process import validate_configuration, signer_environment
from agentonomy_commerce import signer_process
from agentonomy_commerce.kms_scope import SigningScope


ROLE_ARN = 'arn:aws:iam::123456789012:role/agentonomy-dev-kms-runtime'


def role_config(tmp_path):
    value = copy.deepcopy(config())
    value.update(
        profile='temporary-runtime',
        expected_role_arn=ROLE_ARN,
        credential_directory=str(tmp_path),
    )
    return value


def write_credentials(tmp_path, profile='temporary-runtime'):
    credentials = tmp_path / 'credentials'
    config_file = tmp_path / 'config'
    credentials.write_text(
        f'[{profile}]\n'
        'aws_access_key_id = ASIAABCDEFGHIJKLMNOP\n'
        'aws_secret_access_key = synthetic-secret\n'
        'aws_session_token = synthetic-session-token\n',
    )
    config_file.write_text(f'[profile {profile}]\nregion = ap-southeast-1\n')
    credentials.chmod(stat.S_IRUSR | stat.S_IWUSR)
    config_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    tmp_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return credentials, config_file


def config():
    return {
        'profile': 'temporary-runtime', 'region': 'ap-southeast-1', 'account_id': '123456789012',
        'expected_role_arn': ROLE_ARN,
        'credential_directory': '/tmp/clink-kms-runtime',
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


def test_configuration_accepts_only_an_isolated_temporary_role_session(tmp_path):
    value = role_config(tmp_path)
    assert validate_configuration(value).network.mode == 'monad_testnet'

    for mutation in (
        {'expected_role_arn': 'arn:aws:iam::999999999999:role/agentonomy-dev-kms-runtime'},
        {'expected_role_arn': 'arn:aws:iam::123456789012:user/operator'},
        {'profile': 'agentonomy-dev'},
        {'credential_directory': 'relative/runtime-credentials'},
    ):
        with pytest.raises(ValueError):
            validate_configuration(value | mutation)


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'mode'], ids=['missing', 'symlink', 'mode'])
def test_worker_rejects_unsafe_isolated_credential_files(tmp_path, kind):
    value = role_config(tmp_path)
    credentials, config_file = write_credentials(tmp_path)
    if kind == 'missing':
        credentials.unlink()
    elif kind == 'symlink':
        target = tmp_path / 'credential-target'
        target.write_text(credentials.read_text())
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
        credentials.unlink()
        credentials.symlink_to(target)
    else:
        credentials.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)

    scope = SigningScope.from_json(value['scope'])
    with pytest.raises(ValueError, match='credential'):
        signer_process._load_signer(value, scope)


def test_identity_validation_precedes_any_kms_call(tmp_path, monkeypatch):
    value = role_config(tmp_path)
    write_credentials(tmp_path)
    scope = SigningScope.from_json(value['scope'])
    import boto3

    calls = []

    class FakeSts:
        def get_caller_identity(self):
            return {
                'Account': value['account_id'],
                'Arn': 'arn:aws:iam::123456789012:user/operator',
            }

    class FakeKms:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError('KMS must not be touched before role validation')

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def client(self, name, **kwargs):
            calls.append(name)
            return FakeSts() if name == 'sts' else FakeKms()

    monkeypatch.setattr(boto3, 'Session', FakeSession)
    with pytest.raises(ValueError, match='assumed role'):
        signer_process._load_signer(value, scope)
    assert calls == ['sts']


def test_startup_describes_each_key_and_requires_expected_dry_run(tmp_path, monkeypatch):
    value = role_config(tmp_path)
    write_credentials(tmp_path)
    scope = SigningScope.from_json(value['scope'])
    import boto3
    from agentonomy_commerce import kms_adapter

    describe_calls = []
    sign_calls = []
    session_kwargs = {}

    class FakeSts:
        def get_caller_identity(self):
            return {
                'Account': value['account_id'],
                'Arn': 'arn:aws:sts::123456789012:assumed-role/'
                       'agentonomy-dev-kms-runtime/synthetic-session',
            }

    class FakeKms:
        def describe_key(self, **kwargs):
            describe_calls.append(kwargs)
            return {
                'KeyMetadata': {
                    'Arn': kwargs['KeyId'],
                    'KeyState': 'Enabled',
                    'KeyUsage': 'SIGN_VERIFY',
                    'KeySpec': 'ECC_SECG_P256K1',
                },
            }

        def sign(self, **kwargs):
            sign_calls.append(kwargs)
            raise ClientError(
                {'Error': {'Code': 'DryRunOperationException', 'Message': 'synthetic'}},
                'Sign',
            )

    class FakeSession:
        def __init__(self, **kwargs):
            session_kwargs.update(kwargs)
            self.kwargs = kwargs

        def client(self, name, **kwargs):
            return FakeSts() if name == 'sts' else FakeKms()

    class FakeSigner:
        def __init__(self, client, key_arn, expected_address):
            self.key_arn = key_arn

        def validate(self):
            return '0x' + '11' * 20

    monkeypatch.setattr(boto3, 'Session', FakeSession)
    monkeypatch.setattr(kms_adapter, 'KmsDigestSigner', FakeSigner)
    signer_process._load_signer(value, scope)

    assert [item['KeyId'] for item in describe_calls] == [
        value['execution_key_arn'], value['gas_key_arn'],
    ]
    assert len(sign_calls) == 2
    assert all(item['DryRun'] is True for item in sign_calls)
    assert all(item['Message'] == bytes(32) for item in sign_calls)
    assert all(item['MessageType'] == 'DIGEST' for item in sign_calls)
    assert all(item['SigningAlgorithm'] == 'ECDSA_SHA_256' for item in sign_calls)
    assert session_kwargs == {
        'aws_access_key_id': 'ASIAABCDEFGHIJKLMNOP',
        'aws_secret_access_key': 'synthetic-secret',
        'aws_session_token': 'synthetic-session-token',
        'region_name': 'ap-southeast-1',
    }


@pytest.mark.parametrize(
    'contents, target',
    [
        ('[temporary-runtime]\naws_access_key_id = ASIAABCDEFGHIJKLMNOP\n'
         'aws_secret_access_key = secret\naws_session_token = token\n'
         '[other]\naws_access_key_id = ASIAQRSTUVWXYZabcdef\n', 'credentials'),
        ('[temporary-runtime]\naws_access_key_id = ASIAABCDEFGHIJKLMNOP\n'
         'aws_secret_access_key = secret\naws_session_token = token\nsource_profile = operator\n',
         'credentials'),
        ('[profile temporary-runtime]\nregion = ap-southeast-1\n'
         'endpoint_url = https://operator.example\n', 'config'),
        ('[default]\nregion = ap-southeast-1\n', 'config'),
    ],
    ids=['extra-profile', 'extra-credential-option', 'custom-endpoint', 'default-profile'],
)
def test_worker_parses_only_the_selected_profile_and_options(tmp_path, contents, target):
    value = role_config(tmp_path)
    credentials, config_file = write_credentials(tmp_path)
    selected = credentials if target == 'credentials' else config_file
    selected.write_text(contents)
    selected.chmod(stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(ValueError, match='isolated'):
        signer_process._read_isolated_credentials(value)


def test_worker_rejects_unprivate_credential_directory(tmp_path):
    value = role_config(tmp_path)
    write_credentials(tmp_path)
    tmp_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP)
    with pytest.raises(ValueError, match='credential directory'):
        signer_process._read_isolated_credentials(value)


def test_worker_rejects_fifo_credential_file_without_blocking(tmp_path):
    fifo = tmp_path / 'credentials'
    os.mkfifo(fifo, stat.S_IRUSR | stat.S_IWUSR)
    root = Path(__file__).resolve().parents[2]
    source = (
        'import sys\n'
        'from agentonomy_commerce.signer_process import _read_private_file\n'
        'try:\n'
        "    _read_private_file(sys.argv[1], 'credential')\n"
        'except ValueError:\n'
        '    raise SystemExit(0)\n'
        'raise SystemExit(1)\n'
    )
    child = subprocess.Popen([sys.executable, '-c', source, str(fifo)], cwd=root)
    try:
        return_code = child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=1)
        pytest.fail('FIFO credential file blocked before regular-file validation')
    assert return_code == 0


def test_startup_rejects_a_real_signature_from_dry_run_probe(tmp_path, monkeypatch):
    value = role_config(tmp_path)
    write_credentials(tmp_path)
    scope = SigningScope.from_json(value['scope'])
    import boto3
    from agentonomy_commerce import kms_adapter

    class FakeSts:
        def get_caller_identity(self):
            return {
                'Account': value['account_id'],
                'Arn': 'arn:aws:sts::123456789012:assumed-role/'
                       'agentonomy-dev-kms-runtime/synthetic-session',
            }

    class FakeKms:
        def describe_key(self, **kwargs):
            return {
                'KeyMetadata': {
                    'Arn': kwargs['KeyId'],
                    'KeyState': 'Enabled',
                    'KeyUsage': 'SIGN_VERIFY',
                    'KeySpec': 'ECC_SECG_P256K1',
                },
            }

        def sign(self, **kwargs):
            return {'Signature': b'actual-signature'}

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        def client(self, name, **kwargs):
            return FakeSts() if name == 'sts' else FakeKms()

    class FakeSigner:
        def __init__(self, client, key_arn, expected_address):
            pass

        def validate(self):
            return '0x' + '11' * 20

    monkeypatch.setattr(boto3, 'Session', FakeSession)
    monkeypatch.setattr(kms_adapter, 'KmsDigestSigner', FakeSigner)
    with pytest.raises(ValueError, match='dry-run'):
        signer_process._load_signer(value, scope)


def test_worker_environment_does_not_forward_credentials_or_endpoints(monkeypatch):
    for field in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                  'AWS_ENDPOINT_URL', 'AWS_PROFILE', 'PYTHONPATH', 'HTTPS_PROXY'):
        monkeypatch.setenv(field, 'must-not-inherit')
    env = signer_environment(config())
    assert not any(value == 'must-not-inherit' for value in env.values())
    assert env['AWS_EC2_METADATA_DISABLED'] == 'true'
    assert env['HOME'] == config()['credential_directory']
    assert env['AWS_SHARED_CREDENTIALS_FILE'] == '/tmp/clink-kms-runtime/credentials'
    assert env['AWS_CONFIG_FILE'] == '/tmp/clink-kms-runtime/config'
    assert 'AWS_PROFILE' not in env
    assert 'AWS_WEB_IDENTITY_TOKEN_FILE' not in env
    assert 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI' not in env


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
