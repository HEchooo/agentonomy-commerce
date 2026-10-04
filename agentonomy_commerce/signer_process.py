"""Dedicated local operator KMS worker, reachable only through inherited pipes.

Only worker_main loads the named AWS profile. Core, Marketplace and Watcher do
not load boto3 or receive AWS credentials. Separate processes are not an OS
security sandbox: a server installation also requires distinct service users
and protected short-lived credential installation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from agentonomy_commerce.kms_scope import SigningScope, ScopedSigner
from examples.monad_commerce.core_bridge import CoreBridge

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE = 65536


def validate_configuration(value):
    fields = {'profile', 'region', 'account_id', 'execution_key_arn', 'gas_key_arn', 'scope'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('explicit non-secret KMS configuration required')
    if not isinstance(value['profile'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['profile']):
        raise ValueError('invalid AWS profile name')
    if value['region'] != 'ap-southeast-1' or not re.fullmatch(r'[0-9]{12}', str(value['account_id'])):
        raise ValueError('unexpected AWS account or region')
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


def signer_environment():
    allowed = {'PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG', 'LC_ALL'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(HOME=str(Path.home()), PYTHONUNBUFFERED='1', AWS_EC2_METADATA_DISABLED='true')
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
            env=signer_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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


def _load_signer(configuration, scope):
    # Imports and credential resolution are confined to this worker entrypoint.
    import boto3
    from botocore.config import Config
    from agentonomy_commerce.kms_adapter import KmsDigestSigner
    session = boto3.Session(profile_name=configuration['profile'], region_name=configuration['region'])
    options = Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 0})
    identity = session.client('sts', config=options).get_caller_identity()
    if identity.get('Account') != configuration['account_id']:
        raise ValueError('AWS account mismatch')
    client = session.client('kms', config=options)
    execution = KmsDigestSigner(client, configuration['execution_key_arn'], scope.execution_address)
    gas = KmsDigestSigner(client, configuration['gas_key_arn'], scope.relayer_address)
    execution.validate()
    gas.validate()
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
