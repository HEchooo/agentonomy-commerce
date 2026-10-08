"""Bounded browser/tenant routing around the copied Core and Marketplace.

This registry stores routing and admission digests only. Wallet identity, browser
proof, OPC consent, grants and accounting remain in each canonical Core database.
All tenant payment paths use ONE shared relayer gate, outside tenant directories.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from uuid import uuid4

from agentonomy_commerce.budget_network import address
from agentonomy_commerce.relayer_gate import RelayerGate
from examples.commerce.core_persistence import StateLock
from examples.monad_commerce.public_worker import validate_bootstrap
from examples.monad_commerce.hosted_canary import HostedCanary
from examples.monad_commerce.rehearsal import public_purchase

_TOKEN = re.compile(r'^[A-Za-z0-9_-]{43}$')
_SESSION_SECONDS = 3600
_AGENT_OPERATIONS = {'search', 'details', 'preview', 'execute'}
_BROWSER_OPERATIONS = {
    'status', 'grant_challenge', 'grant_verify', 'budget_payload', 'budget_bind',
    'approval_transaction', 'verify_approval', 'claim_transaction', 'verify_claim',
    'opc_prepare', 'opc_approve', 'purchase', 'recover_purchase',
    'revoke_prepare', 'verify_revocation',
    'agent_identity', 'feedback_prepare', 'feedback_verify', 'feedback_status',
}
_MUTATIONS = {
    'grant_challenge', 'grant_verify', 'budget_payload', 'budget_bind',
    'verify_approval', 'verify_claim', 'opc_prepare', 'opc_approve',
    'preview', 'execute', 'recover_purchase', 'revoke_prepare', 'verify_revocation',
    'feedback_prepare', 'feedback_verify',
}
_MCP_AUTH_FIELDS = (
    'issuer', 'user_id', 'agent_id', 'scope', 'wallet_identity_id',
    'spending_grant_id', 'installation_id', 'credential_id', 'expires_at',
)


def _digest(value):
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise PermissionError('browser session required')
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _private_directory(path):
    if path.is_symlink():
        raise ValueError('state must not be a symlink')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise ValueError('state directory must have mode0700')


class HostedCommerceService:
    max_sessions = 128
    max_tenants = 32
    max_open_tenants = 4

    def __init__(self, state_dir, configuration, *, public_origin,
                 canary_factory=HostedCanary, clock=time.time, registry=None):
        from shared.opc_protocol import canonical_opc_origin
        if canonical_opc_origin(public_origin) != public_origin:
            raise ValueError('canonical public HTTPS origin required')
        self.scope = validate_bootstrap(configuration)
        self.configuration = deepcopy(configuration)
        # Browser wallet and business consent messages use the same public
        # authority as the HTTPS site, never the legacy localhost default.
        self.configuration['deployment']['domain'] = public_origin.removeprefix('https://')
        self.public_origin, self.clock = public_origin, clock
        if registry is not None and (registry.network != self.scope.network or registry.origin != public_origin):
            raise ValueError('registry must match fixed commerce network and origin')
        self.registry, self.feedback = registry, None
        self.state_dir = Path(state_dir)
        self.canary_factory = canary_factory
        self._cache = OrderedDict()
        self._agent_credentials = {}
        self._lock = threading.RLock()
        self._state_lock = self.db = self.gate = None

    def __enter__(self):
        try:
            _private_directory(self.state_dir)
            registry_dir = self.state_dir / 'registry'
            _private_directory(registry_dir)
            self._state_lock = StateLock(registry_dir)
            self._state_lock.acquire()
            database = self.state_dir / 'sessions.sqlite3'
            if database.exists() or database.is_symlink():
                info = database.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
                    raise ValueError('unsafe browser registry')
            self.db = sqlite3.connect(database, check_same_thread=False)
            os.chmod(database, 0o600)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    browser_digest TEXT PRIMARY KEY, csrf_digest TEXT NOT NULL,
                    expires INTEGER NOT NULL, owner TEXT, tenant_id TEXT,
                    authenticated INTEGER NOT NULL DEFAULT 0, challenge TEXT
                );
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL
                );
            """)
            self.db.commit()
            if self.registry is not None:
                from examples.monad_commerce.hosted_feedback import FeedbackStore
                self.feedback = FeedbackStore(self.db, self.registry, clock=self.clock)
            self.gate = RelayerGate(self.state_dir / 'relayer', self.scope.network,
                                    self.scope.relayer_address)
            _private_directory(self.state_dir / 'tenants')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        # Wait for the current serialized MCP call and its lock-free Core
        # admissions to finish before disposing their pipes and routing map.
        with self._lock:
            self._agent_credentials.clear()
            for canary in reversed(list(self._cache.values())):
                canary.__exit__(None, None, None)
            self._cache.clear()
            if self.db:
                self.feedback = None
                self.db.close()
                self.db = None
            if self._state_lock:
                self._state_lock.release()
                self._state_lock = None

    def _lookup(self, token, csrf=None):
        browser = _digest(token)
        row = self.db.execute('SELECT * FROM sessions WHERE browser_digest=?', (browser,)).fetchone()
        if row is None or row['expires'] <= int(self.clock()):
            raise PermissionError('browser session expired')
        if csrf is not None and not hmac.compare_digest(row['csrf_digest'], _digest(csrf)):
            raise PermissionError('CSRF does not match browser session')
        return dict(row)

    def _canary(self, row):
        tenant_id, owner = row['tenant_id'], row['owner']
        if not tenant_id or not owner:
            raise PermissionError('wallet login required')
        owner = address(owner)
        if tenant_id != 'tenant_' + hashlib.sha256(owner.encode()).hexdigest()[:32]:
            raise ValueError('persistent tenant routing mismatch')
        canary = self._cache.get(tenant_id)
        if canary is not None and getattr(canary, 'broken', False):
            # Restart only when a subsequent explicit request arrives. This
            # never replays a timed-out mutation; the global gate stays pinned.
            self._cache.pop(tenant_id)
            canary.__exit__(None, None, None)
            canary = None
        if canary is not None:
            self._cache.move_to_end(tenant_id)
            return canary
        if len(self._cache) >= self.max_open_tenants:
            _, old = self._cache.popitem(last=False)
            old.__exit__(None, None, None)
        config = deepcopy(self.configuration)
        config['deployment']['owner'] = owner
        config['signer_configuration']['scope']['owner'] = owner
        # Address selection only prepares a proof challenge. Core identity and
        # signed grant/OPC consent remain mandatory before any signing operation.
        validate_bootstrap(config)
        _private_directory(self.state_dir / 'tenants' / tenant_id)
        canary = self.canary_factory(
            self.state_dir / 'tenants' / tenant_id, config,
            public_origin=self.public_origin, tenant_id=tenant_id, gate=self.gate,
        )
        canary.__enter__()
        self._cache[tenant_id] = canary
        return canary

    def _authorize(self, token, csrf=None, *, mutation=False):
        if mutation and csrf is None:
            raise PermissionError('CSRF required')
        row = self._lookup(token, csrf)
        if not row['authenticated']:
            raise PermissionError('wallet login required')
        canary = self._canary(row)
        params = {'browser_digest': row['browser_digest']}
        if csrf is not None:
            params['csrf_digest'] = row['csrf_digest']
        try:
            principal = canary.hosted('browser_authorize', **params)
        except (ValueError, RuntimeError):
            raise PermissionError('wallet session unavailable') from None
        if principal.get('wallet_address') != row['owner']:
            raise PermissionError('wallet session mismatch')
        return row, canary

    def session(self, browser_token, csrf_token=None):
        with self._lock:
            browser = _digest(browser_token)
            row = self.db.execute('SELECT * FROM sessions WHERE browser_digest=?', (browser,)).fetchone()
            if row is None:
                if csrf_token is None:
                    raise PermissionError('browser session required')
                csrf = _digest(csrf_token)
                self.db.execute('DELETE FROM sessions WHERE expires<=?', (int(self.clock()),))
                count = self.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
                if count >= self.max_sessions:
                    raise ValueError('browser admission capacity reached')
                self.db.execute('INSERT INTO sessions(browser_digest,csrf_digest,expires) VALUES(?,?,?)',
                                (browser, csrf, int(self.clock()) + _SESSION_SECONDS))
                self.db.commit()
                return {'authenticated': False}
            row = self._lookup(browser_token, csrf_token)
            if row['authenticated']:
                self._authorize(browser_token, csrf_token)
                return {'authenticated': True, 'owner': row['owner']}
            if row['owner']:
                try:
                    params = {'browser_digest': row['browser_digest']}
                    if csrf_token is not None:
                        params['csrf_digest'] = row['csrf_digest']
                    principal = self._canary(row).hosted('browser_authorize', **params)
                except (ValueError, RuntimeError):
                    principal = None
                if principal and principal.get('wallet_address') == row['owner']:
                    # Canonical Core may have committed proof before a registry
                    # commit failed. The routing flag is only a cache of it.
                    self.db.execute('UPDATE sessions SET authenticated=1 WHERE browser_digest=?', (row['browser_digest'],))
                    self.db.commit()
                    return {'authenticated': True, 'owner': row['owner']}
            return {'authenticated': False}

    def login_challenge(self, browser_token, csrf_token, owner):
        with self._lock:
            row = self._lookup(browser_token, csrf_token)
            owner = address(owner)
            if row['owner'] and row['owner'] != owner:
                raise PermissionError('logout before changing wallets')
            if row['authenticated']:
                raise PermissionError('wallet already authenticated')
            if row['challenge']:
                challenge = json.loads(row['challenge'])
                return challenge
            tenant_id = 'tenant_' + hashlib.sha256(owner.encode()).hexdigest()[:32]
            existing = self.db.execute('SELECT owner FROM tenants WHERE tenant_id=?', (tenant_id,)).fetchone()
            if existing is None:
                count = self.db.execute('SELECT COUNT(*) FROM tenants').fetchone()[0]
                if count >= self.max_tenants:
                    raise ValueError('wallet admission capacity reached')
                self.db.execute('INSERT INTO tenants VALUES(?,?)', (tenant_id, owner))
            elif existing['owner'] != owner:
                raise ValueError('tenant routing conflict')
            self.db.execute('UPDATE sessions SET owner=?,tenant_id=? WHERE browser_digest=?',
                            (owner, tenant_id, row['browser_digest']))
            self.db.commit()
            row = self._lookup(browser_token, csrf_token)
            canary = self._canary(row)
            params = {'browser_digest': row['browser_digest'], 'csrf_digest': row['csrf_digest']}
            result = canary.hosted('browser_resume', **params)
            if result is None:
                result = canary.hosted('browser_challenge', **params)
            self.db.execute('UPDATE sessions SET challenge=? WHERE browser_digest=?',
                            (json.dumps(result), row['browser_digest']))
            self.db.commit()
            return result

    def login_verify(self, browser_token, csrf_token, challenge_id, signature):
        with self._lock:
            row = self._lookup(browser_token, csrf_token)
            if not row['challenge'] or json.loads(row['challenge'])['session_id'] != challenge_id:
                raise PermissionError('wallet proof does not match this browser')
            canary = self._canary(row)
            result = canary.hosted('browser_verify', browser_digest=row['browser_digest'],
                                  csrf_digest=row['csrf_digest'], challenge_id=challenge_id,
                                  signature=signature)
            if result.get('wallet_address') != row['owner']:
                raise PermissionError('wallet proof mismatch')
            self.db.execute('UPDATE sessions SET authenticated=1 WHERE browser_digest=?', (row['browser_digest'],))
            self.db.commit()
            return {'authenticated': True, 'owner': row['owner']}

    def logout(self, browser_token, csrf_token):
        with self._lock:
            row = self._lookup(browser_token, csrf_token)
            if row['owner']:
                self._canary(row).hosted('browser_logout', browser_digest=row['browser_digest'])
            self.db.execute('DELETE FROM sessions WHERE browser_digest=?', (row['browser_digest'],))
            self.db.commit()
            return {'authenticated': False}

    def _device(self, row):
        from clink_node.opc_client import OpcClientStateStore
        directory = self.state_dir / 'tenants' / row['tenant_id'] / 'hosted-agent'
        _private_directory(directory)
        store = OpcClientStateStore(directory / 'device.json')
        if store.path.exists() or store.path.is_symlink():
            state = store.load()
        else:
            state = store.create(origin=self.public_origin, label='Agentonomy website demo Agent')
        if state.origin != self.public_origin:
            raise ValueError('hosted device origin mismatch')
        return state

    def _proof(self, row, action):
        from shared.opc_protocol import sign_opc_proof
        state = self._device(row)
        return sign_opc_proof(state.device_key, origin=self.public_origin,
                             action=action, request_id=str(uuid4()), now=int(self.clock()),
                             label=state.label if action == 'pair' else None)

    def _opc_status(self, row, canary):
        device = self.state_dir / 'tenants' / row['tenant_id'] / 'hosted-agent' / 'device.json'
        if not device.exists() and not device.is_symlink():
            return {'status': 'unpaired'}
        value = canary.hosted('opc_status', proof=self._proof(row, 'status'))
        return {key: value[key] for key in ('status', 'installation_id') if key in value}

    def _agent_operation(self, row, canary, operation, args):
        # Short credentials never leave this hosted device/Node composition.
        credential = canary.hosted('opc_token', proof=self._proof(row, 'token'))
        principal = canary.hosted('opc_authenticate', access_token=credential['access_token'])
        scope = self._mcp_scope(principal)
        from apps.node.clink_node.mcp_proxy import McpToolProxy
        from examples.monad_commerce.hosted_mcp import HostedMcpPrincipal, call_hosted_tool
        from examples.monad_commerce.node import BudgetMarketplaceClient, TOOLS
        import asyncio
        tool = next((name for name, (method, _, _) in TOOLS.items() if method == operation), None)
        if tool is None:
            raise ValueError('operation outside Node MCP tools')
        # A new proxy per call avoids cross-tenant tool-owner caching. Each
        # runtime is already server-bound to the authenticated Core owner.
        class Runtime:
            def request(self, method, arguments):
                return canary.request(method, arguments)
            def execute(self, preview_id):
                return public_purchase(canary.request('execute', {'preview_id': preview_id}))
        proxy = McpToolProxy({'marketplace': BudgetMarketplaceClient(Runtime())})
        token = credential['access_token']
        key = self._mcp_token_digest(token)
        if key in self._agent_credentials:
            raise PermissionError('Agent credential already in use')
        identity = HostedMcpPrincipal(row['tenant_id'], row['owner'], proxy)
        self._agent_credentials[key] = (canary, scope, identity)
        try:
            result = asyncio.run(call_hosted_tool(
                origin=self.public_origin, access_service=self,
                access_token=token, name=tool, arguments=args,
            ))
        finally:
            self._agent_credentials.pop(key, None)
        if result.isError or not isinstance(result.structuredContent, dict):
            raise RuntimeError('Agent operation unconfirmed')
        return result.structuredContent

    @staticmethod
    def _mcp_token_digest(token):
        if (type(token) is not str or not 1 <= len(token) <= 2048
            or any(ord(c) <= 32 or ord(c) >= 127 for c in token)):
            raise PermissionError('Agent credential invalid')
        return hashlib.sha256(token.encode('ascii')).hexdigest()

    @staticmethod
    def _mcp_scope(principal):
        if (type(principal) is not dict or set(principal) != set(_MCP_AUTH_FIELDS)
            or principal.get('issuer') != 'opc'
            or principal.get('user_id') != 'commerce-demo-user'
            or principal.get('agent_id') != 'hermes'
            or principal.get('scope') != 'payments'
            or type(principal.get('expires_at')) is not int
            or any(type(principal.get(k)) is not str or not principal[k]
                   for k in _MCP_AUTH_FIELDS[:-1])):
            raise PermissionError('Agent scope mismatch')
        return tuple(principal[k] for k in _MCP_AUTH_FIELDS)

    def authenticate(self, access_token):
        # The gateway invokes this in a worker thread while dispatch holds the
        # outer lock. Do not acquire that lock here. Entries exist only for the
        # lifetime of one serialized call, and every admission rechecks Core.
        entry = self._agent_credentials.get(self._mcp_token_digest(access_token))
        if entry is None:
            raise PermissionError('Agent credential unavailable')
        canary, expected, identity = entry
        try:
            actual = self._mcp_scope(canary.hosted('opc_authenticate', access_token=access_token))
        except Exception:
            raise PermissionError('Agent authorization unavailable') from None
        if actual != expected:
            raise PermissionError('Agent authorization changed')
        return identity

    def registration_document(self):
        if self.registry is None:
            raise ValueError('ERC-8004 registry not configured')
        return self.registry.registration_document()

    def public_feedback(self, digest):
        with self._lock:
            return None if self.feedback is None else self.feedback.public(digest)

    def _service_identity(self):
        if self.registry is None:
            return {'status': 'not_configured', 'verified': False}
        identity = self.registry.verify_identity()
        if identity is None:
            return {'status': 'registration_pending', 'verified': False}
        return identity | {
            'reputation_registry': self.registry.config.reputation_registry,
        }

    def _identity_guard(self, canary, operation, args):
        if self.registry is None:
            return
        identity = self._service_identity()
        if (identity.get('verified') is not True
            or identity.get('agent_wallet') != self.scope.network.payee):
            raise ValueError('verified service receiver required')
        if operation in {'execute', 'recover_purchase'}:
            canary._start_market()
            if operation == 'execute':
                preview_id = args['preview_id']
            else:
                preview_id = canary.request('purchase', {'purchase_id': args['purchase_id']})['preview_id']
            preview = canary.market.request('preview_state', {'preview_id': preview_id})
            payment = preview.get('payment') or {}
            if (preview.get('preview_id') != preview_id
                or payment.get('pay_to') != identity['agent_wallet']
                or payment.get('network') != self.scope.network.network
                or payment.get('asset') != self.scope.network.token
                or str(payment.get('amount_atomic')) != '300000'):
                raise ValueError('frozen preview differs from verified service receiver')

    def _feedback_operation(self, row, canary, operation, args):
        if self.feedback is None:
            raise ValueError('ERC-8004 feedback not configured')
        purchase_id = args.get('purchase_id')
        if (type(purchase_id) is not str
            or re.fullmatch(r'purchase_[A-Za-z0-9][A-Za-z0-9_-]{0,159}', purchase_id) is None):
            raise ValueError('canonical purchase ID required')
        fields = {'purchase_id', 'score'} if operation == 'feedback_prepare' else (
                 {'purchase_id', 'transaction_hash'} if operation == 'feedback_verify' else {'purchase_id'})
        if set(args) != fields:
            raise ValueError('feedback fields are outside scope')
        scope = dict(tenant_id=row['tenant_id'], purchase_id=purchase_id, buyer=row['owner'])
        if operation == 'feedback_status':
            return self.feedback.status(**scope)
        if operation == 'feedback_verify':
            return self.feedback.verify(**scope, tx_hash=args['transaction_hash'])
        purchase = canary.request('purchase', {'purchase_id': purchase_id})
        if canary._verified_gate_proof(purchase, purchase_id) is None:
            raise ValueError('Core-verified original payment required')
        return self.feedback.prepare(**scope, score=args['score'], purchase=purchase,
                                     identity=self._service_identity())

    def dispatch(self, browser_token, csrf_token, operation, args):
        with self._lock:
            if operation not in _BROWSER_OPERATIONS | _AGENT_OPERATIONS:
                raise ValueError('operation outside website scope')
            if operation == 'status':
                admitted = self.session(browser_token, csrf_token)
                if not admitted['authenticated']:
                    return admitted
            row, canary = self._authorize(browser_token, csrf_token, mutation=operation in _MUTATIONS)
            if operation == 'agent_identity':
                if args:
                    raise ValueError('service identity is fixed by operator')
                return self._service_identity()
            if operation.startswith('feedback_'):
                return self._feedback_operation(row, canary, operation, args)
            if operation == 'status':
                return canary.status() | {'authenticated': True, 'opc': self._opc_status(row, canary)}
            if operation in _AGENT_OPERATIONS:
                if operation in {'preview', 'execute'}:
                    self._identity_guard(canary, operation, args)
                return self._agent_operation(row, canary, operation, args)
            if operation == 'opc_prepare':
                if args:
                    raise ValueError('hosted device proof cannot be supplied by browser')
                return canary.hosted('opc_pair', proof=self._proof(row, 'pair'),
                                    browser_digest=row['browser_digest'], csrf_digest=row['csrf_digest'])
            if operation == 'opc_approve':
                result = canary.hosted('opc_approve', browser_digest=row['browser_digest'],
                                      csrf_digest=row['csrf_digest'], **args)
                return {'status': 'active', 'installation_id': result['installation_id']}
            if operation == 'grant_challenge':
                if args:
                    raise ValueError('browser session cannot be supplied by browser')
                return canary.onboarding(operation, browser_digest=row['browser_digest'],
                                         csrf_digest=row['csrf_digest'])
            if operation in {'grant_verify', 'budget_payload', 'budget_bind'}:
                return canary.onboarding(operation, **args)
            if operation in {'purchase', 'recover_purchase'}:
                if operation == 'recover_purchase' and self.registry is not None:
                    existing = canary.request('purchase', {'purchase_id': args['purchase_id']})
                    if canary._verified_gate_proof(existing, args['purchase_id']) is None:
                        self._identity_guard(canary, operation, args)
                return public_purchase(canary.request(operation, args))
            return getattr(canary, operation)(**args)
