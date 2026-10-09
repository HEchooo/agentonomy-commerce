"""External device admission around the existing wallet-isolated Core.

An unbound routing intent grants no authority. After wallet setup the device
must present a fresh pair proof to the tenant Core, which creates and verifies
the real installation consent. Only credential digests are kept for routing;
every MCP request and business operation re-authenticates with that Core.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from examples.monad_commerce.hosted_service import HostedCommerceService, _digest


_BINDING_OPERATIONS = {
    'status', 'grant_challenge', 'grant_verify', 'budget_payload', 'budget_bind',
    'approval_transaction', 'verify_approval', 'claim_transaction', 'verify_claim',
    'revoke_prepare', 'verify_revocation',
}


def public_device_status(value):
    return {key: value[key] for key in (
        'installation_id', 'status', 'label', 'scope', 'consent_expires_at',
        'created_at', 'updated_at',
    ) if key in value}


def _expiry(value):
    if type(value) is int:
        return value
    if type(value) is str:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
    raise ValueError('expiry unavailable')


class ExternalHostedCommerceService(HostedCommerceService):
    """Browser authorization plus device-authenticated remote MCP routing."""

    max_devices = 128
    max_links = 512

    def __enter__(self):
        super().__enter__()
        try:
            path = self.state_dir / 'opc-routing.secret'
            if not path.exists() and not path.is_symlink():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
                    raise ValueError('protected routing secret required')
                self._routing_secret = os.read(fd, 33)
                if len(self._routing_secret) != 32:
                    raise ValueError('routing secret invalid')
            finally:
                os.close(fd)
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS external_devices (
                    installation_id TEXT PRIMARY KEY, public_jwk TEXT NOT NULL,
                    label TEXT NOT NULL, status TEXT NOT NULL,
                    owner TEXT, tenant_id TEXT, browser_digest TEXT, csrf_digest TEXT,
                    challenge TEXT
                );
                CREATE TABLE IF NOT EXISTS external_links (
                    token_digest TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, expires INTEGER NOT NULL, browser_digest TEXT,
                    UNIQUE(installation_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS external_credentials (
                    token_digest TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
                    owner TEXT NOT NULL, tenant_id TEXT NOT NULL,
                    scope TEXT NOT NULL, expires INTEGER NOT NULL
                );
            ''')
            self.db.commit()
            return self
        except BaseException:
            super().__exit__(None, None, None)
            raise

    def wallet_configuration(self):
        # Presentation derives from the canonical deployed scope and Core terms.
        # Operator AWS paths, RPC URLs and private signing scope never leave here.
        from examples.monad_commerce.public_core import (
            TOTAL_AMOUNT_ATOMIC, PER_PAYMENT_AMOUNT_ATOMIC, VALIDITY,
        )
        from apps.facilitator.budget_protocol import EIP712_DOMAIN_NAME, EIP712_DOMAIN_VERSION
        network = self.scope.network
        return {'networks': [{
            'chain_id': network.chain_id, 'network': network.network,
            'token': network.token, 'executor': network.executor, 'payee': network.payee,
            'token_symbol': 'TestUSD', 'token_decimals': network.token_decimals,
            'test_asset': True,
            'terms': {'claim_atomic': str(TOTAL_AMOUNT_ATOMIC),
                      'total_atomic': str(TOTAL_AMOUNT_ATOMIC),
                      'per_payment_atomic': str(PER_PAYMENT_AMOUNT_ATOMIC),
                      'validity_seconds': int(VALIDITY.total_seconds())},
            'wallet_transaction': {'max_gas': '100000',
                                   'max_gas_price_wei': str(network.max_gas_price_wei)},
            'budget_domain': {'name': EIP712_DOMAIN_NAME, 'version': EIP712_DOMAIN_VERSION},
        }]}

    def _device(self, installation):
        row = self.db.execute('SELECT * FROM external_devices WHERE installation_id=?',
                              (installation,)).fetchone()
        return dict(row) if row else None

    def _verified_device(self, action, proof):
        from shared.opc_protocol import verify_opc_proof
        claims = verify_opc_proof(proof, origin=self.public_origin,
                                 action=action, now=int(self.clock()))
        row = self._device(claims['installation_id'])
        if row is not None:
            if json.loads(row['public_jwk']) != claims['public_jwk']:
                raise PermissionError('device identity mismatch')
            if action == 'pair' and row['label'] != claims.get('label', 'OPC installation'):
                raise PermissionError('device label mismatch')
        return claims, row

    def _link_token(self, installation, request_id):
        import base64
        raw = hmac.new(self._routing_secret, (installation + '\0' + request_id).encode(),
                       hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')

    def _link(self, claims):
        now = int(self.clock())
        self.db.execute('DELETE FROM external_links WHERE expires<=?', (now,))
        token = self._link_token(claims['installation_id'], claims['request_id'])
        digest = _digest(token)
        existing = self.db.execute('SELECT * FROM external_links WHERE token_digest=?',
                                   (digest,)).fetchone()
        if existing is None:
            if self.db.execute('SELECT COUNT(*) FROM external_links').fetchone()[0] >= self.max_links:
                raise ValueError('pairing admission full')
            self.db.execute('INSERT INTO external_links(token_digest,installation_id,request_id,expires) VALUES(?,?,?,?)',
                            (digest, claims['installation_id'], claims['request_id'], now + 600))
            self.db.commit()
            expires = now + 600
        else:
            expires = existing['expires']
        return token, expires

    def _bound_canary(self, row, *, browser_required=False):
        if not row or not row['tenant_id'] or not row['owner']:
            raise PermissionError('wallet binding required')
        if browser_required:
            session = self.db.execute('SELECT * FROM sessions WHERE browser_digest=?',
                                      (row['browser_digest'],)).fetchone()
            if (session is None or not session['authenticated']
                or session['expires'] <= int(self.clock())
                or session['owner'] != row['owner'] or session['tenant_id'] != row['tenant_id']
                or session['csrf_digest'] != row['csrf_digest']):
                raise PermissionError('binding browser unavailable')
        canary = self._canary(row)
        if browser_required:
            identity = canary.hosted('browser_authorize', browser_digest=row['browser_digest'],
                                     csrf_digest=row['csrf_digest'])
            if identity.get('wallet_address') != row['owner']:
                raise PermissionError('binding wallet mismatch')
        return canary

    def device_request(self, action, proof):
        with self._lock:
            if action not in {'pair', 'status', 'token', 'revoke'}:
                raise ValueError('device action unavailable')
            self.db.execute('DELETE FROM external_links WHERE expires<=?', (int(self.clock()),))
            self.db.execute('''DELETE FROM external_devices WHERE owner IS NULL AND status='pending'
                               AND NOT EXISTS (SELECT 1 FROM external_links WHERE
                               external_links.installation_id=external_devices.installation_id)''')
            self.db.commit()
            claims, row = self._verified_device(action, proof)
            installation = claims['installation_id']
            if action == 'pair':
                if row and row['status'] == 'revoked':
                    raise PermissionError('device revoked')
                if row is None:
                    if self.db.execute('SELECT COUNT(*) FROM external_devices').fetchone()[0] >= self.max_devices:
                        raise ValueError('device admission full')
                    self.db.execute('INSERT INTO external_devices(installation_id,public_jwk,label,status) VALUES(?,?,?,?)',
                                    (installation, json.dumps(claims['public_jwk'], sort_keys=True),
                                     claims.get('label', 'OPC installation'), 'pending'))
                    self.db.commit()
                    row = self._device(installation)
                if row['status'] == 'claimed':
                    # This exact fresh pair proof, never the original intent
                    # proof, enters Core after an authenticated browser claim.
                    try:
                        canary = self._bound_canary(row, browser_required=True)
                    except PermissionError:
                        # A new management link lets the same wallet log in
                        # again. This fallback never enters Core or grants
                        # device authority using the expired browser session.
                        canary = None
                    challenge = canary.hosted('opc_pair', proof=proof,
                                             browser_digest=row['browser_digest'],
                                             csrf_digest=row['csrf_digest']) if canary is not None else None
                    if challenge is not None:
                        if challenge.get('installation_id') != installation:
                            raise PermissionError('Core device mismatch')
                        self.db.execute('UPDATE external_devices SET status=?,challenge=? WHERE installation_id=?',
                                        ('challenge', json.dumps(challenge), installation))
                        self.db.commit()
                        row = self._device(installation)
                token, expires = self._link(claims)
                challenge = json.loads(row['challenge']) if row['challenge'] else {}
                return {'installation_id': installation,
                        'pairing_id': challenge.get('pairing_id', 'intent_' + hashlib.sha256(token.encode()).hexdigest()[:40]),
                        'verification_uri': self.public_origin + '/account/' + token,
                        'expires_at': expires,
                        'status': 'active' if row['status'] == 'active' else 'pending'}
            if action == 'status':
                if row is None:
                    return {'installation_id': installation, 'status': 'unpaired'}
                if row['status'] in {'pending', 'claimed'}:
                    result = {'installation_id': installation, 'status': 'pending'}
                    if row['status'] == 'claimed':
                        result['pairing_required'] = True
                    return result
                if row['status'] == 'revoked':
                    return {'installation_id': installation, 'status': 'revoked'}
                result = self._bound_canary(row).hosted('opc_status', proof=proof)
                if result.get('installation_id') != installation:
                    raise PermissionError('Core device mismatch')
                if result.get('status') in {'active', 'revoked', 'consent_required'}:
                    # Reconcile a Core commit whose response was lost. Reading
                    # a signed device status never repeats wallet approval.
                    self.db.execute('UPDATE external_devices SET status=? WHERE installation_id=?',
                                    (result['status'], installation))
                    self.db.commit()
                return public_device_status(result)
            if action == 'revoke':
                if row is None:
                    raise PermissionError('no device binding to revoke')
                if row['tenant_id']:
                    self._bound_canary(row).hosted('opc_revoke', proof=proof)
                    self.db.execute('UPDATE external_devices SET status=? WHERE installation_id=?', ('revoked', installation))
                else:
                    # An unclaimed intent has no Core authority to revoke.
                    # Do not retain permanent anonymous capacity tombstones.
                    self.db.execute('DELETE FROM external_links WHERE installation_id=?', (installation,))
                    self.db.execute('DELETE FROM external_devices WHERE installation_id=?', (installation,))
                self.db.execute('DELETE FROM external_credentials WHERE installation_id=?', (installation,))
                self.db.commit()
                return {'installation_id': installation, 'status': 'revoked'}
            if row is None or row['status'] != 'active':
                raise PermissionError('device consent required')
            canary = self._bound_canary(row)
            credential = canary.hosted('opc_token', proof=proof)
            token = credential['access_token']
            principal = canary.hosted('opc_authenticate', access_token=token)
            scope = self._mcp_scope(principal)
            if (credential.get('installation_id') != installation
                or principal['installation_id'] != installation
                or credential.get('mcp_url') != self.public_origin + '/mcp'
                or credential.get('token_type') != 'Bearer'
                or not int(self.clock()) < credential.get('expires_at', 0) <= int(self.clock()) + 330):
                raise PermissionError('Core credential scope mismatch')
            self.db.execute('DELETE FROM external_credentials WHERE expires<=?', (int(self.clock()),))
            self.db.execute('DELETE FROM external_credentials WHERE installation_id=?', (installation,))
            self.db.execute('INSERT OR REPLACE INTO external_credentials VALUES(?,?,?,?,?,?)',
                            (self._mcp_token_digest(token), installation, row['owner'], row['tenant_id'],
                             json.dumps(scope), credential['expires_at']))
            self.db.commit()
            return credential

    def _browser_link(self, browser_token, request, csrf=None, *, mutation=False):
        row, canary = self._authorize(browser_token, csrf, mutation=mutation)
        link = self.db.execute('SELECT * FROM external_links WHERE token_digest=?', (_digest(request),)).fetchone()
        if link is None or link['expires'] <= int(self.clock()):
            raise PermissionError('pairing link expired')
        device = self._device(link['installation_id'])
        if (device is None or device['status'] == 'revoked'
            or (device['owner'] is not None and device['owner'] != row['owner'])
            or (link['browser_digest'] is not None and link['browser_digest'] != row['browser_digest'])):
            raise PermissionError('pairing unavailable for this browser')
        return row, canary, device

    def claim_device(self, browser_token, csrf, request):
        with self._lock:
            row, canary, device = self._browser_link(browser_token, request, csrf, mutation=True)
            status = canary.status()['onboarding']
            if not all(status.get(key) for key in ('wallet_identity_id', 'spending_grant_id', 'budget_binding_id', 'allowance_id')):
                raise PermissionError('wallet budget setup required')
            self.db.execute('UPDATE external_links SET browser_digest=? WHERE token_digest=?',
                            (row['browser_digest'], _digest(request)))
            expired = (device['status'] == 'challenge' and device['challenge']
                       and _expiry(json.loads(device['challenge'])['expires_at']) <= int(self.clock()))
            new_browser_challenge = (device['status'] == 'challenge'
                                     and device['browser_digest'] != row['browser_digest'])
            if device['status'] in {'pending', 'claimed', 'consent_required'} or expired or new_browser_challenge:
                self.db.execute('UPDATE external_devices SET status=?,owner=?,tenant_id=?,browser_digest=?,csrf_digest=? WHERE installation_id=?',
                                ('claimed', row['owner'], row['tenant_id'], row['browser_digest'], row['csrf_digest'], device['installation_id']))
            self.db.commit()
            return self.binding_status(browser_token, request)

    def binding_status(self, browser_token, request):
        with self._lock:
            _, _, device = self._browser_link(browser_token, request)
            result = {key: device[key] for key in ('status', 'installation_id', 'label')}
            if device['status'] == 'challenge':
                challenge = json.loads(device['challenge'])
                if _expiry(challenge['expires_at']) <= int(self.clock()):
                    raise PermissionError('device consent challenge expired')
                result['challenge'] = {key: challenge[key] for key in ('session_id', 'message_to_sign', 'expires_at')}
            return result

    def approve_device(self, browser_token, csrf, request, challenge_id, signature):
        with self._lock:
            row, canary, device = self._browser_link(browser_token, request, csrf, mutation=True)
            challenge = json.loads(device['challenge']) if device['challenge'] else {}
            if (device['status'] != 'challenge' or challenge.get('session_id') != challenge_id
                or _expiry(challenge['expires_at']) <= int(self.clock())):
                raise PermissionError('device consent challenge unavailable')
            approved = canary.hosted('opc_approve', browser_digest=row['browser_digest'],
                                     csrf_digest=row['csrf_digest'], challenge_id=challenge_id, signature=signature)
            if approved.get('installation_id') != device['installation_id']:
                raise PermissionError('Core device mismatch')
            self.db.execute('UPDATE external_devices SET status=? WHERE installation_id=?', ('active', device['installation_id']))
            self.db.commit()
            return {'status': 'active', 'installation_id': device['installation_id']}

    def dispatch(self, browser_token, csrf_token, operation, args):
        if operation not in _BINDING_OPERATIONS:
            raise ValueError('business operations are available only through Agent MCP')
        if operation != 'status':
            return super().dispatch(browser_token, csrf_token, operation, args)
        with self._lock:
            admitted = self.session(browser_token, csrf_token)
            if not admitted['authenticated']:
                return admitted
            row, canary = self._authorize(browser_token, csrf_token)
            value = canary.status()
            value.pop('commerce', None)
            devices = self.db.execute('SELECT installation_id,label,status FROM external_devices WHERE tenant_id=?',
                                      (row['tenant_id'],)).fetchall()
            return value | {'authenticated': True, 'devices': [dict(item) for item in devices]}

    def _credential(self, access_token):
        route = self.db.execute('SELECT * FROM external_credentials WHERE token_digest=?',
                                (self._mcp_token_digest(access_token),)).fetchone()
        if route is None or route['expires'] <= int(self.clock()):
            raise PermissionError('device credential unavailable')
        row = dict(route)
        device = self._device(row['installation_id'])
        if (device is None or device['status'] != 'active' or device['owner'] != row['owner']
            or device['tenant_id'] != row['tenant_id']):
            raise PermissionError('device credential unavailable')
        canary = self._bound_canary(row)
        try:
            actual = self._mcp_scope(canary.hosted('opc_authenticate', access_token=access_token))
        except Exception:
            raise PermissionError('Core device authorization unavailable') from None
        if actual != tuple(json.loads(row['scope'])):
            raise PermissionError('Core device authority changed')
        return row, canary

    def authenticate(self, access_token):
        from examples.monad_commerce.hosted_mcp import HostedMcpPrincipal
        from examples.monad_commerce.external_tools import device_proxy
        with self._lock:
            row, _ = self._credential(access_token)
            return HostedMcpPrincipal(row['tenant_id'], row['owner'], device_proxy(self, access_token))

    def device_operation(self, access_token, operation, args):
        from examples.commerce.node import public_result
        from examples.monad_commerce.rehearsal import public_purchase
        with self._lock:
            row, canary = self._credential(access_token)
            if operation == 'status':
                return canary.status() | {'agent_identity': self._service_identity()}
            if operation in {'preview', 'execute'}:
                self._identity_guard(canary, operation, args)
            if operation in {'preview', 'execute', 'purchase', 'recover_purchase'}:
                args = args | {'opc_installation_id': row['installation_id']}
            if operation == 'execute':
                return public_purchase(canary.request('execute', args))
            if operation == 'recover_purchase':
                existing = canary.request('purchase', {'purchase_id': args['purchase_id']})
                if canary._verified_gate_proof(existing, args['purchase_id']) is None:
                    self._identity_guard(canary, operation, args)
                return public_purchase(canary.request(operation, args))
            if operation not in {'search', 'details', 'preview', 'purchase'}:
                raise ValueError('operation outside Agent MCP scope')
            value = canary.request(operation, args)
            if operation == 'purchase':
                return public_purchase(value)
            return public_result('create_clink_purchase_preview', value) if operation == 'preview' else value
