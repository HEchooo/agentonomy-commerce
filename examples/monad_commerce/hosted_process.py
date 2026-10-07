"""Isolated Core and Marketplace composition for wallet-scoped hosted commerce.

The parent transports only public scope, cookie digests and user signatures.
KMS credentials remain exclusively in the existing restricted signer process.
"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / 'apps/marketplace', ROOT / 'apps/node'):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
from examples.monad_commerce.public_bridge import PublicCoreBridge
from examples.monad_commerce.public_worker import validate_bootstrap


HOSTED_FIELDS = {
    'browser_challenge': {'browser_digest', 'csrf_digest'},
    'browser_verify': {'browser_digest', 'csrf_digest', 'challenge_id', 'signature'},
    'browser_authorize': {'browser_digest'},
    'browser_logout': {'browser_digest'},
    'browser_resume': {'browser_digest', 'csrf_digest'},
    'opc_pair': {'proof', 'browser_digest', 'csrf_digest'},
    'opc_approve': {'browser_digest', 'csrf_digest', 'challenge_id', 'signature'},
    'opc_token': {'proof'},
    'opc_authenticate': {'access_token'},
    'opc_status': {'proof'},
    'payment_safety': {'purchase_id'},
}
SETUP_FIELDS = {
    'onboarding_status': set(), 'wallet_challenge': set(),
    'grant_challenge': {'browser_digest', 'csrf_digest'},
    'budget_payload': set(), 'wallet_verify': {'signature'},
    'grant_verify': {'signature'}, 'budget_bind': {'signature'},
    'allowance_verify': {'transaction_hash'},
}


def validate_hosted_bootstrap(value):
    if not isinstance(value, dict) or set(value) != {'deployment', 'signer_configuration', 'public_origin'}:
        raise ValueError('explicit hosted deployment, signer scope and origin required')
    scope = validate_bootstrap({key: value[key] for key in ('deployment', 'signer_configuration')})
    from shared.opc_protocol import canonical_opc_origin
    if canonical_opc_origin(value['public_origin']) != value['public_origin']:
        raise ValueError('canonical HTTPS public origin required')
    return scope


def dispatch_hosted(runtime, method, params):
    if not isinstance(params, dict):
        raise ValueError('parameters must be an object')
    fields = HOSTED_FIELDS.get(method, SETUP_FIELDS.get(method))
    if method == 'browser_authorize' and set(params) == {'browser_digest', 'csrf_digest'}:
        fields = set(params)
    if fields is None or set(params) != fields:
        raise ValueError('operation outside hosted Core scope')
    if method == 'browser_resume':
        from datetime import UTC, datetime
        from sqlalchemy import select
        from services.account_service.repository import AccountSessionRow
        from examples.monad_commerce.hosted_core import _now
        session = runtime.repository.access_public_account_browser_session(params['browser_digest'], _now())
        if session is None:
            return None
        runtime._browser_session(params['browser_digest'], params['csrf_digest'], require_csrf=True)
        with runtime.repository.sessions() as tx:
            rows = tx.scalars(select(AccountSessionRow).where(
                AccountSessionRow.created_by_public_account_session_id == session.public_account_session_id,
                AccountSessionRow.purpose == 'clink_wallet_identity',
            )).all()
            if len(rows) != 1:
                raise ValueError('browser wallet challenge state is ambiguous')
            challenge = runtime.repository._account_session(rows[0])
        runtime._validate_wallet_challenge(session, challenge.account_session_id)
        return {'session_id': challenge.account_session_id,
                'message_to_sign': runtime.account_service._canonical_message(challenge),
                'expires_at': challenge.expires_at.astimezone(UTC).isoformat(),
                'signing_method': 'personal_sign'}
    if method == 'payment_safety':
        purchase_id = params['purchase_id']
        if type(purchase_id) is not str or re.fullmatch(r'purchase_[a-zA-Z0-9_-]{1,120}', purchase_id) is None:
            raise ValueError('invalid purchase scope')
        # Called only after the synchronous Marketplace operation has returned.
        # A durable attempt of any kind (even not yet broadcast) blocks abort.
        with runtime.funding_service.ledger.transaction() as tx:
            row = tx.by_purchase(purchase_id)
            no_broadcast = row is None or (
                row.get('state') in {'spending_reserved', 'released', 'failed'}
                and not any(row.get(key) is not None for key in (
                    'tx_hash', 'budget_attempt', 'settlement_nonce',
                    'settlement_transaction', 'budget_broadcast_claimed_at',
                    'budget_broadcasted_at', 'risk_prebroadcast_state',
                ))
            )
        return {'source': 'core_funding_ledger', 'purchase_id': purchase_id,
                'owner': runtime.owner_address, 'no_broadcast': no_broadcast}
    return getattr(runtime, method)(**params)


class HostedCoreBridge(PublicCoreBridge):
    def _start(self):
        if self._broken is not None:
            raise RuntimeError('Core channel unavailable; inspect original order')
        if self.process is not None:
            return
        validate_hosted_bootstrap(self.bootstrap)
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG', 'LC_ALL'}}
        env['PYTHONUNBUFFERED'] = '1'
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'examples.monad_commerce.hosted_process', 'core', str(self.state_dir)],
            cwd=Path(__file__).resolve().parents[2], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self.process.stdin.write(json.dumps(self.bootstrap, separators=(',', ':')) + '\n')
        self.process.stdin.flush()

    def hosted(self, method, **params):
        if method not in HOSTED_FIELDS:
            raise ValueError('unknown hosted authority operation')
        return self._call(method, params)


def _core_main(state_dir):
    signer = runtime = None
    try:
        line = sys.stdin.buffer.readline(65537)
        if len(line) > 65536 or not line.endswith(b'\n'):
            return 2
        bootstrap = json.loads(line)
        validate_hosted_bootstrap(bootstrap)
        from examples.monad_commerce.hosted_signer import HostedKmsSignerBridge
        from examples.monad_commerce.hosted_core import HostedWalletCore
        from examples.monad_commerce.core_worker import _dispatch, METHODS
        signer = HostedKmsSignerBridge(bootstrap['signer_configuration'])
        runtime = HostedWalletCore(
            state_dir, bootstrap['deployment'], signer,
            opc_origin=bootstrap['public_origin'], allow_loopback_http=False,
            mcp_url=bootstrap['public_origin'] + '/mcp',
        )
        del bootstrap
        while True:
            line = sys.stdin.buffer.readline(1048577)
            if not line:
                return 0
            if len(line) > 1048576 or not line.endswith(b'\n'):
                return 2
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) != {'id', 'method', 'params'} or type(request['id']) is not int:
                return 2
            try:
                method, params = request['method'], request['params']
                if method in HOSTED_FIELDS or method in SETUP_FIELDS:
                    result = dispatch_hosted(runtime, method, params)
                elif method in METHODS:
                    result = _dispatch(runtime, method, params)
                else:
                    raise ValueError('operation outside Core scope')
                response = {'id': request['id'], 'ok': True, 'result': result}
            except Exception:
                response = {'id': request['id'], 'ok': False,
                            'error': {'message': 'Core operation unconfirmed; inspect authorization or original order'}}
            print(json.dumps(response, default=str), flush=True)
    except Exception:
        print(json.dumps({'id': None, 'ok': False, 'error': {'message': 'Hosted Core unavailable'}}), flush=True)
        return 2
    finally:
        if runtime:
            runtime.close()
        if signer:
            signer.close()


def _market_main(state_dir):
    from examples.monad_commerce.hosted_market_runtime import HostedCommerceRuntime
    runtime = None
    try:
        while True:
            line = sys.stdin.buffer.readline(1048577)
            if not line:
                return 0
            if len(line) > 1048576 or not line.endswith(b'\n'):
                return 2
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) != {'id', 'method', 'arguments'} or type(request['id']) is not int:
                return 2
            try:
                method, args = request['method'], request['arguments']
                if method == 'initialize' and runtime is None:
                    validate_hosted_bootstrap(args)
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        port = sock.getsockname()[1]
                    runtime = HostedCommerceRuntime(state_dir, port, args)
                    runtime.__enter__()
                    result = {'status': 'ready', 'mode': 'monad_testnet'}
                elif runtime is not None and method in {
                    'search', 'details', 'preview', 'preview_state', 'execute', 'purchase',
                    'recover_purchase', 'snapshot', 'revoke', 'onboarding_status', 'hosted',
                }:
                    result = getattr(runtime, method)(**args)
                else:
                    raise ValueError('operation outside Marketplace scope')
                response = {'id': request['id'], 'result': result}
            except KeyError:
                response = {'id': request['id'], 'error': 'purchase_not_found' if method == 'purchase' else 'item_not_found'}
            except Exception:
                response = {'id': request['id'], 'error': 'Operation unconfirmed; query original order'}
            print(json.dumps(response, default=str), flush=True)
    finally:
        if runtime:
            runtime.__exit__(None, None, None)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(2)
    if sys.argv[1] == 'core':
        raise SystemExit(_core_main(Path(sys.argv[2])))
    if sys.argv[2] == 'market':
        raise SystemExit(_market_main(Path(sys.argv[1])))
    raise SystemExit(2)
