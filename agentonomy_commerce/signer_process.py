"""Dedicated local operator KMS worker, reachable only through inherited pipes.

Only worker_main reads the isolated temporary session files and constructs the
explicit AWS session. Core, Marketplace and Watcher do not load boto3 or receive
AWS credentials. Separate processes are not an OS security sandbox: a server
installation also requires distinct service users and protected short-lived
credential installation.
"""
from __future__ import annotations

import configparser
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from agentonomy_commerce.kms_scope import SigningScope, ScopedSigner
from examples.monad_commerce.core_bridge import CoreBridge

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE = 65536
MAX_CREDENTIAL_FILE_BYTES = 16384
_PROFILE_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')
_ACCESS_KEY_RE = re.compile(r'ASIA[A-Z0-9]{16}')
_ROLE_ARN_RE = re.compile(
    r'arn:aws:iam::(?P<account>[0-9]{12}):role/'
    r'(?P<role>[A-Za-z0-9+=,.@_-]+(?:/[A-Za-z0-9+=,.@_-]+)*)'
)
_ASSUMED_ROLE_ARN_RE = re.compile(
    r'arn:aws:sts::(?P<account>[0-9]{12}):assumed-role/'
    r'(?P<role>[A-Za-z0-9+=,.@_-]+(?:/[A-Za-z0-9+=,.@_-]+)*)/'
    r'(?P<session>[A-Za-z0-9+=,.@_-]+)'
)
_PRIVATE_DIRECTORY_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
_PRIVATE_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR


def validate_configuration(value):
    fields = {
        'profile', 'region', 'account_id', 'expected_role_arn', 'credential_directory',
        'execution_key_arn', 'gas_key_arn', 'scope',
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('explicit non-secret KMS configuration required')
    if not isinstance(value['profile'], str) or _PROFILE_RE.fullmatch(value['profile']) is None:
        raise ValueError('invalid AWS profile name')
    if value['profile'] == 'agentonomy-dev':
        raise ValueError('invalid AWS profile name')
    if value['region'] != 'ap-southeast-1' or not re.fullmatch(r'[0-9]{12}', str(value['account_id'])):
        raise ValueError('unexpected AWS account or region')
    if not isinstance(value['credential_directory'], str):
        raise ValueError('isolated credential directory required')
    try:
        credential_directory = Path(value['credential_directory'])
    except (TypeError, ValueError):
        raise ValueError('isolated credential directory required') from None
    if not credential_directory.is_absolute() or '..' in credential_directory.parts:
        raise ValueError('isolated credential directory must be absolute')
    role = _ROLE_ARN_RE.fullmatch(str(value['expected_role_arn']))
    if role is None or role.group('account') != str(value['account_id']):
        raise ValueError('expected assumed role must match AWS account')
    prefix = f"arn:aws:kms:{value['region']}:{value['account_id']}:key/"
    pattern = re.escape(prefix) + r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
    for field in ('execution_key_arn', 'gas_key_arn'):
        if not isinstance(value[field], str) or not re.fullmatch(pattern, value[field]):
            raise ValueError('exact account-bound KMS key ARN required')
    if value['execution_key_arn'] == value['gas_key_arn']:
        raise ValueError('execution and gas keys must differ')
    scope = SigningScope.from_json(value['scope'])
    if scope.network.mode != 'monad_testnet':
        raise ValueError('this KMS worker only supports Monad testnet')
    return scope


def signer_environment(configuration=None):
    allowed = {'PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG', 'LC_ALL'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    if configuration is None:
        isolated_home = Path(os.devnull).parent
        credentials_file = Path(os.devnull)
        config_file = Path(os.devnull)
    else:
        isolated_home = Path(configuration['credential_directory'])
        credentials_file = isolated_home / 'credentials'
        config_file = isolated_home / 'config'
    env.update(
        HOME=str(isolated_home),
        PYTHONUNBUFFERED='1',
        AWS_EC2_METADATA_DISABLED='true',
        AWS_SHARED_CREDENTIALS_FILE=str(credentials_file),
        AWS_CONFIG_FILE=str(config_file),
    )
    return env


class KmsSignerBridge(CoreBridge):
    def __init__(self, configuration):
        validate_configuration(configuration)
        # Parent owns only public scope/profile references, never credentials.
        super().__init__(ROOT, configuration)

    def _start(self):
        if self._broken is not None:
            raise RuntimeError('signer channel unavailable; inspect existing payment')
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'agentonomy_commerce.signer_process'], cwd=ROOT,
            env=signer_environment(self.bootstrap), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.process.stdin.write(json.dumps(self.bootstrap, separators=(',', ':')) + '\n')
        self.process.stdin.flush()

    def sign_execution(self, grant, owner_signature, execution):
        signature = owner_signature if isinstance(owner_signature, str) else '0x' + bytes(owner_signature).hex()
        result = self._call('execution', {'grant': grant.to_json(), 'owner_signature': signature,
                                          'execution': execution.to_json()})
        return bytes.fromhex(result['signature'][2:])

    def sign_transaction(self, transaction):
        result = self._call('transaction', {'transaction': transaction})
        return bytes.fromhex(result['raw_transaction'][2:])


def _validate_private_stat(metadata, path, expected_mode, kind):
    if kind == 'directory':
        valid_type = stat.S_ISDIR(metadata.st_mode)
    else:
        valid_type = stat.S_ISREG(metadata.st_mode)
    if not valid_type or stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise ValueError(f'unsafe isolated credential {kind}')
    getuid = getattr(os, 'getuid', None)
    if getuid is not None and metadata.st_uid != getuid():
        raise ValueError(f'isolated credential {kind} is not owner-controlled')


def _validate_no_symlink_components(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                raise ValueError('unsafe isolated credential path')
        except OSError:
            raise ValueError('unsafe isolated credential path') from None


def _read_private_file(path, label):
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_NONBLOCK', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_CLOEXEC', 0)
    )
    descriptor = None
    try:
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        _validate_private_stat(metadata, path, _PRIVATE_FILE_MODE, 'file')
        with os.fdopen(descriptor, 'r', encoding='utf-8', newline='') as stream:
            descriptor = None
            contents = stream.read(MAX_CREDENTIAL_FILE_BYTES + 1)
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f'unsafe isolated {label} file') from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(contents) > MAX_CREDENTIAL_FILE_BYTES:
        raise ValueError(f'isolated {label} file is too large')
    return contents


def _parse_private_ini(contents, section, required, label):
    parser = configparser.RawConfigParser(
        interpolation=None,
        strict=True,
        allow_no_value=False,
    )
    parser.optionxform = str
    try:
        parser.read_string(contents)
        sections = parser.sections()
        options = dict(parser.items(section, raw=True)) if sections == [section] else {}
    except (configparser.Error, KeyError, TypeError, ValueError):
        raise ValueError(f'invalid isolated {label} file') from None
    if parser.defaults() or sections != [section] or set(options) != set(required):
        raise ValueError(f'invalid isolated {label} file')
    return options


def _read_isolated_credentials(configuration):
    directory = Path(configuration['credential_directory'])
    try:
        _validate_no_symlink_components(directory)
        directory_metadata = os.lstat(directory)
        _validate_private_stat(
            directory_metadata, directory, _PRIVATE_DIRECTORY_MODE, 'directory'
        )
    except (OSError, ValueError):
        raise ValueError('unsafe isolated credential directory') from None

    credentials_path = directory / 'credentials'
    config_path = directory / 'config'
    credentials_text = _read_private_file(credentials_path, 'credential')
    config_text = _read_private_file(config_path, 'AWS config')
    profile = configuration['profile']
    credentials = _parse_private_ini(
        credentials_text,
        profile,
        {'aws_access_key_id', 'aws_secret_access_key', 'aws_session_token'},
        'credential',
    )
    config = _parse_private_ini(config_text, f'profile {profile}', {'region'}, 'AWS config')
    access_key = credentials['aws_access_key_id']
    secret_key = credentials['aws_secret_access_key']
    session_token = credentials['aws_session_token']
    if _ACCESS_KEY_RE.fullmatch(access_key) is None:
        raise ValueError('isolated credentials require a temporary access key')
    if not secret_key.strip() or not session_token.strip():
        raise ValueError('isolated credentials require a secret and session token')
    if config['region'] != configuration['region']:
        raise ValueError('isolated AWS config region mismatch')
    return {
        'aws_access_key_id': access_key,
        'aws_secret_access_key': secret_key,
        'aws_session_token': session_token,
    }


def _validate_assumed_role_identity(identity, configuration):
    expected = _ROLE_ARN_RE.fullmatch(str(configuration['expected_role_arn']))
    if expected is None or not isinstance(identity, Mapping):
        raise ValueError('AWS assumed role identity required')
    if identity.get('Account') != configuration['account_id']:
        raise ValueError('AWS account mismatch')
    caller_arn = identity.get('Arn')
    assumed = _ASSUMED_ROLE_ARN_RE.fullmatch(caller_arn) if isinstance(caller_arn, str) else None
    if (
        assumed is None
        or assumed.group('account') != configuration['account_id']
        or assumed.group('role') != expected.group('role')
        or not assumed.group('session')
    ):
        raise ValueError('AWS assumed role identity required')


def _validate_kms_signer(client, signer, key_arn):
    try:
        response = client.describe_key(KeyId=key_arn)
        metadata = response.get('KeyMetadata') if isinstance(response, Mapping) else None
    except Exception:
        raise ValueError('KMS key metadata validation failed') from None
    if (
        not isinstance(metadata, Mapping)
        or metadata.get('Arn') != key_arn
        or metadata.get('KeyState') != 'Enabled'
        or metadata.get('KeyUsage') != 'SIGN_VERIFY'
        or metadata.get('KeySpec') != 'ECC_SECG_P256K1'
    ):
        raise ValueError('KMS key metadata validation failed')

    signer.validate()
    try:
        response = client.sign(
            KeyId=key_arn,
            Message=bytes(32),
            MessageType='DIGEST',
            SigningAlgorithm='ECDSA_SHA_256',
            DryRun=True,
        )
    except Exception as error:
        response = getattr(error, 'response', None)
        details = response.get('Error') if isinstance(response, Mapping) else None
        if isinstance(details, Mapping) and details.get('Code') == 'DryRunOperationException':
            return
        raise ValueError('KMS dry-run validation failed') from None
    if response is not None:
        raise ValueError('KMS dry-run unexpectedly returned a signature')
    raise ValueError('KMS dry-run unexpectedly succeeded')


def _load_signer(configuration, scope):
    # Imports and credential resolution are confined to this worker entrypoint.
    import boto3
    from botocore.config import Config
    from agentonomy_commerce.kms_adapter import KmsDigestSigner

    credentials = _read_isolated_credentials(configuration)
    session = boto3.Session(
        aws_access_key_id=credentials['aws_access_key_id'],
        aws_secret_access_key=credentials['aws_secret_access_key'],
        aws_session_token=credentials['aws_session_token'],
        region_name=configuration['region'],
    )
    options = Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 0})
    identity = session.client('sts', config=options).get_caller_identity()
    _validate_assumed_role_identity(identity, configuration)
    client = session.client('kms', config=options)
    execution = KmsDigestSigner(client, configuration['execution_key_arn'], scope.execution_address)
    gas = KmsDigestSigner(client, configuration['gas_key_arn'], scope.relayer_address)
    _validate_kms_signer(client, execution, configuration['execution_key_arn'])
    _validate_kms_signer(client, gas, configuration['gas_key_arn'])
    return ScopedSigner(scope, execution, gas)


def _read_line(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line:
        return None
    if len(line) > MAX_LINE or not line.endswith('\n'):
        raise ValueError('invalid signer request framing')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError('signer request object required')
    return value


def worker_main():
    try:
        configuration = _read_line(sys.stdin)
        scope = validate_configuration(configuration)
        signer = _load_signer(configuration, scope)
        del configuration
    except Exception:
        print(json.dumps({'id': None, 'ok': False, 'error': {'message': 'KMS signer unavailable; verify account, permission and key pins'}}), flush=True)
        return 2
    while True:
        try:
            request = _read_line(sys.stdin)
        except Exception:
            # A malformed frame has no trustworthy request ID. End the pipe;
            # the caller marks it broken and cannot accidentally restart it.
            return 2
        try:
            if request is None:
                return 0
            if set(request) != {'id', 'method', 'params'} or type(request['id']) is not int:
                return 2
            params = request['params']
            if request['method'] == 'health' and params == {}:
                result = {'status': 'ready', 'mode': 'monad_testnet', 'credentials_exposed': False}
            elif request['method'] == 'execution' and isinstance(params, dict) and set(params) == {'grant', 'owner_signature', 'execution'}:
                result = {'signature': '0x' + signer.sign_execution(**params).hex()}
            elif request['method'] == 'transaction' and isinstance(params, dict) and set(params) == {'transaction'}:
                result = {'raw_transaction': '0x' + signer.sign_transaction(params['transaction']).hex()}
            else:
                raise ValueError('operation outside signer scope')
            response = {'id': request['id'], 'ok': True, 'result': result}
        except Exception:
            response = {'id': request.get('id') if isinstance(locals().get('request'), dict) else None,
                        'ok': False, 'error': {'message': 'KMS signing rejected or unavailable'}}
        print(json.dumps(response, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    raise SystemExit(worker_main())
