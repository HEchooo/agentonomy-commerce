import json
import httpx
import pytest
from examples.monad_commerce.public_node import ApiCanaryClient


def test_node_uses_only_local_api_and_retains_order_identity():
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        assert request.headers['origin'] == 'http://127.0.0.1:8091'
        if request.url.path == '/api/session':
            return httpx.Response(200, json={'status':'ready'}, headers={'set-cookie':'agentonomy_monad_operator=test; HttpOnly; Path=/api'})
        assert 'agentonomy_monad_operator=test' in request.headers.get('cookie', '')
        return httpx.Response(200, json={'purchase_id':'order-1','state':'payment_submitted'})
    with ApiCanaryClient(transport=httpx.MockTransport(handle)) as client:
        assert client.execute('preview-1')['purchase_id'] == 'order-1'
        client.request('purchase', {'purchase_id':'order-1'})
    assert calls == [('POST','/api/session',{}), ('POST','/api/execute',{'preview_id':'preview-1'}),
                     ('GET','/api/purchases/order-1',None)]


def test_node_cannot_choose_external_api_or_signing_method():
    for url in ['https://example.com', 'http://127.0.0.1:8091/path', 'http://x:y@127.0.0.1:8091']:
        with pytest.raises(ValueError): ApiCanaryClient(url)
    with pytest.raises(ValueError): ApiCanaryClient().request('sign_digest', {'digest':'x'})
