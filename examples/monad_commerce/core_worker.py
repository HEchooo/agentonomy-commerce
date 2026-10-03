"""Standalone Core process for the explicit local Anvil rehearsal.

Copies the composition wiring of examples/commerce; no simulated RPC/provider.
Ephemeral test keys enter via the parent's pipe and are never persisted.
Public testnet uses external signers and wallet onboarding; this test launcher
rejects every public network before touching signing material.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/core'))
sys.path.insert(0,str(ROOT))
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys import keys
from eth_utils import keccak
from agentonomy_commerce.budget_network import NetworkConfig, RpcClient
from agentonomy_commerce.budget_backend import BudgetBackend
from apps.facilitator.budget_protocol import SpendGrant, sign_grant
from examples.commerce.core_persistence import StateLock, _atomic_write_json, _atomic_write_bytes
from services.account_service.repository import AccountRepository
from services.account_service.schemas import AuthorizationResolutionRequest, PublicAccountSession, SpendingGrantRequest
from services.account_service.service import AccountService
from services.action_service.schemas import CreateActionIntentRequest, UpdateActionIntentRequest
from services.action_service.service import ActionService
from services.audit_service.schemas import WriteAuditEventRequest
from services.audit_service.service import AuditService
from services.funding_service.schemas import CreateSpendingReservationRequest, FinalizeSpendingReservationRequest, ReleaseSpendingReservationRequest, SettleSpendingReservationRequest
from services.funding_service.budget_service import BudgetFundingService
from services.funding_service.budget_binding import derive_agent_scope
from services.policy_service.schemas import EvaluateActionPolicyRequest
from services.policy_service.budget_policy import BudgetPolicyService
from shared.budget_config import BudgetAppConfig
CorePersistenceError=RuntimeError
USER_ID='commerce-demo-user'
AGENT_ID='hermes'
MERCHANT_ID='commerce_analytics'
VENUE='clink_marketplace'
TRUST_TIER='clink_verified'
RESOURCE='https://merchant.agentonomy.invalid/v1/reconcile'
def _money(value): return format(Decimal(str(value)), '.2f')
def _sign(digest,key):
    sig=keys.PrivateKey(bytes(key)).sign_msg_hash(digest)
    return sig.r.to_bytes(32,'big')+sig.s.to_bytes(32,'big')+bytes([sig.v+27])

class CoreRuntime:
    def __init__(self,state_dir,bootstrap):
        fields=('mode','chain_id','rpc_urls','token','executor','payee')
        self.network=NetworkConfig(**{key:bootstrap[key] for key in fields})
        if self.network.mode != 'local_anvil':
            raise ValueError('ephemeral-key bootstrap is restricted to local Anvil')
        self.rpc=RpcClient(self.network.rpc_urls[0])
        self.rpc.check_chain(31337)
        if 'anvil' not in str(self.rpc.call('web3_clientVersion',[])).lower():
            raise ValueError('local rehearsal requires Anvil')
        self.state_dir=Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True,exist_ok=True)
        os.chmod(self.state_dir,0o700)
        self._state_lock=StateLock(self.state_dir)
        self._state_lock.acquire()
        self.persistent=True
        self.wallet=Account.from_key(bootstrap['owner_key'])
        self.wallet_address=self.wallet.address
        self.execution_signer=Account.from_key(bootstrap['execution_key'])
        self.relayer=Account.from_key(bootstrap['relayer_key'])
        self.approval_tx=bootstrap['approval_tx']
        metadata_path=self.state_dir/'budget-bootstrap.json'
        secret_path=self.state_dir/'receipt.secret'
        database_path=self.state_dir/'core.sqlite3'
        existing=metadata_path.exists()
        if existing:
            if not secret_path.exists() or not database_path.exists():
                raise RuntimeError('incomplete persistent Core state')
            metadata=json.loads(metadata_path.read_text())
            expected=dict(owner=self.wallet_address.lower(),chain=self.network.chain_id,token=self.network.token,
                          executor=self.network.executor,payee=self.network.payee,signer=self.execution_signer.address.lower())
            if metadata['deployment'] != expected: raise ValueError('persistent deployment binding changed')
            receipt_secret=secret_path.read_text()
        else:
            if database_path.exists() or secret_path.exists(): raise RuntimeError('partial Core bootstrap requires inspection')
            receipt_secret=secrets.token_hex(32)
            _atomic_write_bytes(secret_path,receipt_secret.encode())
        database_url=f'sqlite+pysqlite:///{database_path}'
        self.config=BudgetAppConfig(funding_database_url=database_url,
            budget_mode='local_anvil',budget_network=self.network.network,budget_chain_id=31337,
            budget_rpc_urls=self.network.rpc_urls,budget_token_address=self.network.token,
            budget_contract_address=self.network.executor,budget_payee_address=self.network.payee,
            budget_allowed_merchant_ids=(MERCHANT_ID,),budget_allowed_resources=(RESOURCE,),
            clink_receipt_signing_key=receipt_secret,account_allowed_products=('marketplace',))
        self.repository=AccountRepository(database_url)
        self.account_service=AccountService(self.repository,domain='account.agentonomy.local',
            rpc_transport=lambda network,method,params:self.rpc.call(method,params),
            network_configs={self.network.network:dict(chain_id=31337,required_confirmations=1,
                token_symbol='TestUSD',token_decimals=6,token_address=self.network.token)},allowed_products={'marketplace'})
        if existing:
            self.wallet_identity=self.repository.wallet_identity(metadata['identity_id'])
            self.grant=self.repository.spending_grant(metadata['grant_id'])
            self.allowance=self.repository.asset_allowance(metadata['allowance_id'])
            if not all((self.wallet_identity,self.grant,self.allowance)): raise RuntimeError('missing persistent authorization')
        else:
            self.wallet_identity,self.grant,self.allowance=self._create_signed_authorization()
        self.action_service=ActionService(config=self.config)
        self.policy_service=BudgetPolicyService(config=self.config)
        self.audit_service=AuditService(database_url=database_url,storage_file=self.state_dir/'audit.jsonl',clock=lambda:datetime.now(UTC))
        self.backend=BudgetBackend(self.network,relayer_address=self.relayer.address,
            execution_signer_address=self.execution_signer.address,
            execution_sign=lambda digest:_sign(digest,self.execution_signer.key),
            transaction_sign=lambda tx:self.relayer.sign_transaction(tx).raw_transaction)
        self.funding_service=BudgetFundingService(config=self.config,storage_file=self.state_dir/'funding.jsonl',
            rpc_transport=lambda network,method,params:self.rpc.call(method,params),
            policy_service=self.policy_service,backend=self.backend)
        if not existing:
            chain_grant=SpendGrant('0x'+keccak(text='agentonomy:grant:v1:'+self.grant.spending_grant_id).hex(),
                self.wallet_address,derive_agent_scope(AGENT_ID,self.grant.spending_grant_id),self.network.token,
                self.network.payee,500000,1000000,int(self.grant.starts_at.timestamp()),int(self.grant.expires_at.timestamp()),
                self.execution_signer.address)
            self.funding_service.bind_budget_grant(spending_grant_id=self.grant.spending_grant_id,
                wallet_identity_id=self.wallet_identity.wallet_identity_id,grant=chain_grant,
                owner_signature=sign_grant(chain_grant,self.wallet.key,31337,self.network.executor))
            _atomic_write_json(metadata_path,dict(identity_id=self.wallet_identity.wallet_identity_id,
                grant_id=self.grant.spending_grant_id,allowance_id=self.allowance.asset_allowance_id,
                deployment=dict(owner=self.wallet_address.lower(),chain=31337,token=self.network.token,
                    executor=self.network.executor,payee=self.network.payee,signer=self.execution_signer.address.lower())))

    def _create_signed_authorization(self):
        if self.wallet is None:
            raise CorePersistenceError(
                "persistent Core cannot create authorization without a wallet signer"
            )
        wallet = self.wallet
        now = datetime.now(UTC).replace(microsecond=0)
        public_session = self.repository.create_public_account_session(
            PublicAccountSession(
                public_account_session_id=f"public_{uuid4().hex}",
                token_digest=hashlib.sha256(uuid4().bytes).hexdigest(),
                browser_session_digest=hashlib.sha256(uuid4().bytes).hexdigest(),
                csrf_token_digest=hashlib.sha256(uuid4().bytes).hexdigest(),
                user_id=USER_ID,
                expires_at=now + timedelta(minutes=30),
                exchanged_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        wallet_challenge = self.account_service.create_wallet_challenge(
            USER_ID,
            wallet.address,
            created_by_public_account_session_id=public_session.public_account_session_id,
        )
        wallet_signature = Account.sign_message(
            encode_defunct(text=wallet_challenge.message_to_sign),
            wallet.key,
        ).signature.hex()
        identity = self.account_service.verify_wallet_challenge(
            wallet_challenge.session_id,
            wallet_challenge.message_to_sign,
            wallet_signature,
        )

        unsigned_grant = SpendingGrantRequest(
            user_id=USER_ID,
            wallet_identity_id=identity.wallet_identity_id,
            agent_id=AGENT_ID,
            max_amount_usdc=Decimal("1.00"),
            per_transaction_limit_usdc=Decimal("0.50"),
            hourly_limit_usdc=Decimal("1.00"),
            daily_limit_usdc=Decimal("1.00"),
            product_scopes=["marketplace"],
            venue_scopes=[VENUE],
            merchant_scopes=[MERCHANT_ID],
            merchant_trust_scopes=[TRUST_TIER],
            notification_mode="silent_under_limits",
            network_scopes=[self.network.network],
            asset_scopes=[self.network.token],
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=30 if self.persistent else 1),
        )
        grant_challenge = self.account_service.create_spending_grant_challenge(
            unsigned_grant
        )
        grant_signature = Account.sign_message(
            encode_defunct(text=grant_challenge.message_to_sign),
            wallet.key,
        ).signature.hex()
        grant = self.account_service.create_spending_grant(
            unsigned_grant.model_copy(
                update={
                    "session_id": grant_challenge.session_id,
                    "signed_message": grant_challenge.message_to_sign,
                    "signature": grant_signature,
                }
            )
        )
        allowance = self.account_service.verify_asset_allowance(
            identity.wallet_identity_id,
            self.network.network,
            self.network.token,
            self.network.executor,
            self.approval_tx,
        )
        return identity, grant, allowance


    def resolve_authorization(self, payload: dict[str, Any]) -> dict:
        request = AuthorizationResolutionRequest.model_validate(payload)
        return self.account_service.resolve_authorization(request).model_dump(mode="json")


    def create_account_session(self, user_id: str) -> dict:
        """Return the same account-console projection as Core's internal API."""

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.config.account_session_ttl_seconds)
        session_id = secrets.token_urlsafe(32)
        self.repository.create_public_account_session(
            PublicAccountSession(
                public_account_session_id=f"public_{uuid4().hex}",
                token_digest=hashlib.sha256(session_id.encode()).hexdigest(),
                user_id=user_id,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        return {
            "session_id": session_id,
            "account_url": f"/account/{session_id}",
            "expires_at": expires_at.isoformat(),
        }


    def create_action(self, payload: dict[str, Any]) -> dict:
        request = CreateActionIntentRequest.model_validate(payload)
        return self.action_service.create_intent(request).model_dump(mode="json")


    def evaluate_policy(self, payload: dict[str, Any]) -> dict:
        request = EvaluateActionPolicyRequest.model_validate(payload)
        return self.policy_service.evaluate(request).model_dump(mode="json")


    def update_action(self, action_id: str, payload: dict[str, Any]) -> dict:
        request = UpdateActionIntentRequest.model_validate(payload)
        action = self.action_service.update_intent(action_id, request)
        if action is None:
            raise ValueError("action intent not found")
        return action.model_dump(mode="json")


    def audit(self, payload: dict[str, Any]) -> dict:
        request = WriteAuditEventRequest.model_validate(payload)
        return self.audit_service.write_event(request).model_dump(mode="json")


    def reserve(self, payload: dict[str, Any]) -> dict:
        request = CreateSpendingReservationRequest.model_validate(payload)
        return self.funding_service.reserve_spending(request)


    def finalize(self, reservation_id: str, payload: dict[str, Any]) -> dict:
        request = FinalizeSpendingReservationRequest.model_validate(payload)
        return self.funding_service.finalize_reservation(reservation_id, request)


    def reservation(self, reservation_id: str) -> dict | None:
        return self.funding_service.get_reservation(reservation_id)


    def release(self, reservation_id: str, reason: str) -> dict:
        request = ReleaseSpendingReservationRequest(reason=reason)
        return self.funding_service.release_reservation(reservation_id, request)


    def revoke(self) -> dict:
        grant = self.account_service.revoke_spending_grant(self.grant.spending_grant_id)
        return grant.model_dump(mode="json")


    def close(self) -> None:
        if self._state_lock is not None:
            self._state_lock.release()
            self._state_lock = None


    def settle(self,reservation_id,payload):
        return self.funding_service.settle_reservation(reservation_id,SettleSpendingReservationRequest.model_validate(payload))
    def reconcile(self,reservation_id):
        return self.funding_service.reconcile_reservation(reservation_id)
    def funding_readiness(self): return self.funding_service.get_funding_readiness()
    def health(self): return {'status':'ready','mode':'local_anvil','real_funds':False}
    def snapshot(self):
        grant=self.repository.spending_grant(self.grant.spending_grant_id)
        binding=self.funding_service.get_budget_binding(spending_grant_id=grant.spending_grant_id)
        return dict(mode='local_anvil',simulation=False,real_funds=False,token_symbol='TestUSD',
            budget_usdc=_money(grant.max_amount_usdc),used_amount_usdc=_money(grant.used_amount_usdc),
            reserved_amount_usdc=_money(grant.reserved_amount_usdc),grant_status=grant.status,
            grant_id=grant.spending_grant_id,chain_grant_id=binding.grant_id,grant_hash=binding.grant_hash,
            network=self.network.network,token=self.network.token,pay_to=self.network.payee,
            executor=self.network.executor,wallet_address=self.wallet_address,
            merchant_id=MERCHANT_ID,resource=RESOURCE,settlement_submissions=self.backend.pending_nonce(),
            receipt_signing_key=self.config.clink_receipt_signing_key)

def _dispatch(runtime: CoreRuntime, method: str, params: dict[str, Any]) -> Any:
    if method not in METHODS:
        raise ValueError("unknown local Core operation")
    if method in {"resolve_authorization", "create_action", "evaluate_policy", "audit", "reserve"}:
        return getattr(runtime, method)(params)
    if method == "create_account_session":
        return runtime.create_account_session(params["user_id"])
    if method == "update_action":
        return runtime.update_action(params["action_id"], params.get("payload", {}))
    if method in {"settle", "finalize"}:
        return getattr(runtime, method)(params["reservation_id"], params.get("payload", {}))
    if method in {"reconcile", "reservation"}:
        return getattr(runtime, method)(params["reservation_id"])
    if method == "release":
        return runtime.release(params["reservation_id"], params.get("reason", ""))
    return getattr(runtime, method)()


METHODS={'resolve_authorization','create_account_session','create_action','evaluate_policy','update_action','audit',
 'funding_readiness','reserve','settle','reconcile','finalize','reservation','release','health','snapshot','revoke'}
def main():
    bootstrap=json.loads(sys.stdin.buffer.readline(65537))
    runtime=CoreRuntime(Path(sys.argv[1]),bootstrap)
    del bootstrap
    try:
        for line in sys.stdin:
            if len(line)>1048576: return
            request=json.loads(line)
            try: result={'id':request['id'],'ok':True,'result':_dispatch(runtime,request['method'],request.get('params',{}))}
            except Exception as exc: result={'id':request['id'],'ok':False,'error':{'type':type(exc).__name__,'message':str(exc)}}
            print(json.dumps(result,default=str),flush=True)
    finally: runtime.close()
if __name__=='__main__': main()
