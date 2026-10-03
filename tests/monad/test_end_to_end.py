from pathlib import Path
from examples.monad_commerce.local_chain import LocalChain
from examples.commerce.node import MarketplaceBridge

CSV='transaction_id,date,description,amount,currency,category\n1,2026-10-03,Sale,10.00,USD,sales\n2,2026-10-03,Fee,-2.00,USD,fees\n'

def bootstrap(chain):
    return dict(mode='local_anvil',chain_id=31337,rpc_urls=[chain.url,chain.url],
        token=chain.token,executor=chain.executor,payee=chain.payee,
        owner_key='0x'+chain.owner.key.hex(),execution_key='0x'+chain.execution_signer.key.hex(),
        relayer_key='0x'+chain.relayer.key.hex(),approval_tx=chain.approval_tx)

def bridge_for(tmp_path,chain):
    bridge=MarketplaceBridge(tmp_path,worker_module='examples.monad_commerce.market_worker')
    bridge.request('initialize',bootstrap(chain))
    return bridge

def test_core_marketplace_contract_watcher_http_delivery_and_restart(tmp_path):
    with LocalChain() as chain:
        chain.deploy()
        chain.approval_tx=chain.approve(1_000_000)['transactionHash']
        chain.mine(5)
        bridge=bridge_for(tmp_path,chain)
        try:
            preview=bridge.request('preview',dict(offering_id='csv-reconciliation-v1',csv_text=CSV,idempotency_key='local-purchase-1'))
            pending=bridge.request('execute',dict(preview_id=preview['preview_id']))
            chain.mine(5)
            paid=bridge.request('execute',dict(preview_id=preview['preview_id']))
            assert paid['state']=='delivered',paid
            before=bridge.request('snapshot')
            assert before['used_amount_usdc']=='0.30'
            purchase_id=paid['purchase_id']
        finally:
            bridge.close()
        restored=bridge_for(tmp_path,chain)
        try:
            replay=restored.request('execute',dict(preview_id=preview['preview_id']))
            assert replay['purchase_id']==purchase_id
            assert restored.request('snapshot')['used_amount_usdc']=='0.30'
            assert restored.request('purchase',{'purchase_id':purchase_id})['service_result']
        finally:
            restored.close()

def test_delivered_report_and_receipt_identify_the_real_local_rail(tmp_path):
    from examples.monad_commerce.rehearsal import LocalRehearsal
    with LocalRehearsal() as runtime:
        preview=runtime.request('preview',dict(offering_id='csv-reconciliation-v1',csv_text=CSV,idempotency_key='label-check'))
        paid=runtime.execute(preview['preview_id'])
        assert paid['service_result']['settlement_mode']=='local_anvil'
        assert paid['settlement']['verified'] is True
        assert paid['settlement']['chain_id']==31337
        assert len(paid['settlement']['transaction_hash'])==66
