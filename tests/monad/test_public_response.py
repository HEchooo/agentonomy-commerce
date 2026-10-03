from examples.monad_commerce.rehearsal import public_purchase

def test_public_purchase_is_whitelist_and_contains_no_control_fields():
    raw=dict(purchase_id='p',state='delivered',service_result={'count':1},
             wallet_identity_id='secret-id',receipt_signing_key='secret',
             budget_attempt={'raw_transaction':'secret'},payment_authorization={'grant':'secret'})
    public=public_purchase(raw)
    assert public=={'purchase_id':'p','state':'delivered','service_result':{'count':1}}
