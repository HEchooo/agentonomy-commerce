"""Trusted Core process with external owner signatures and a private KMS pipe."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

from agentonomy_commerce.budget_network import NetworkConfig, address
from agentonomy_commerce.signer_process import KmsSignerBridge, validate_configuration


def validate_bootstrap(bootstrap):
    if not isinstance(bootstrap, dict) or set(bootstrap) != {'deployment', 'signer_configuration'}:
        raise ValueError('explicit deployment and signer scope required')
    deployment = bootstrap['deployment']
    scope = validate_configuration(bootstrap['signer_configuration'])
    network = NetworkConfig(**{key: deployment[key] for key in NetworkConfig.__dataclass_fields__ if key in deployment})
    if network != scope.network or (
        address(deployment['owner']), address(deployment['execution_signer']), address(deployment['relayer'])
    ) != (scope.owner, scope.execution_address, scope.relayer_address):
        raise ValueError('Core and signing worker deployment pins differ')
    return scope


def main():
    signer = runtime = None
    try:
        line = sys.stdin.buffer.readline(65537)
        if len(line) > 65536 or not line.endswith(b'\n'):
            return 2
        bootstrap = json.loads(line)
        validate_bootstrap(bootstrap)
        from examples.monad_commerce.public_core import ExternalWalletCore
        from examples.monad_commerce.core_worker import _dispatch, METHODS
        signer = KmsSignerBridge(bootstrap['signer_configuration'])
        runtime = ExternalWalletCore(Path(sys.argv[1]), bootstrap['deployment'], signer)
        del bootstrap
    except Exception:
        print(json.dumps({'id': None, 'ok': False, 'error': {'message': 'Public Core startup failed; inspect deployment and state'}}), flush=True)
        if signer:
            signer.close()
        return 2
    try:
        while True:
            line = sys.stdin.buffer.readline(1048577)
            if not line:
                return 0
            if len(line) > 1048576 or not line.endswith(b'\n'):
                return 2
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) != {'id', 'method', 'params'} or type(request['id']) is not int:
                return 2
            method, params = request['method'], request['params']
            try:
                no_params = {'onboarding_status', 'wallet_challenge', 'grant_challenge', 'budget_payload'}
                signatures = {'wallet_verify', 'grant_verify', 'budget_bind'}
                if method in no_params and params == {}:
                    result = getattr(runtime, method)()
                elif method in signatures and isinstance(params, dict) and set(params) == {'signature'}:
                    result = getattr(runtime, method)(params['signature'])
                elif method == 'allowance_verify' and isinstance(params, dict) and set(params) == {'transaction_hash'}:
                    result = runtime.allowance_verify(params['transaction_hash'])
                elif method in METHODS:
                    # Funding owns fresh-authorization checks. Read/reconcile
                    # must remain possible for a payment broadcast before revoke.
                    result = _dispatch(runtime, method, params)
                else:
                    raise ValueError('operation outside Core scope')
                response = {'id': request['id'], 'ok': True, 'result': result}
            except Exception:
                response = {'id': request['id'], 'ok': False,
                            'error': {'message': 'Core operation not completed; inspect current wallet or original order status'}}
            print(json.dumps(response, default=str), flush=True)
    finally:
        runtime.close()
        signer.close()


if __name__ == '__main__':
    raise SystemExit(main())
