import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from examples.monad_commerce.rehearsal import CSV


def test_actual_unified_mcp_purchases_on_local_chain_without_identity_override():
    async def run():
        env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR'}}
        env['PYTHONPATH']='.:apps/node:apps/core'
        params=StdioServerParameters(command=sys.executable,args=['-m','examples.monad_commerce.node'],env=env,cwd=str(Path(__file__).resolve().parents[2]))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                info=await session.initialize()
                assert info.serverInfo.name=='Clink Node'
                async def call(name,args={}):
                    response=await session.call_tool(name,args)
                    assert not response.isError,response
                    return response.structuredContent or json.loads(response.content[0].text)
                bad=await session.call_tool('create_clink_purchase_preview',dict(offering_id='csv-reconciliation-v1',csv_text=CSV,idempotency_key='bad',user_id='attacker'))
                assert bad.isError
                preview=await call('create_clink_purchase_preview',dict(offering_id='csv-reconciliation-v1',csv_text=CSV,idempotency_key='mcp-1'))
                paid=await call('execute_clink_purchase',dict(preview_id=preview['preview_id']))
                assert paid['state']=='delivered'
                assert paid['settlement']['verified'] is True
                replay=await call('execute_clink_purchase',dict(preview_id=preview['preview_id']))
                assert replay['purchase_id']==paid['purchase_id']
                status=await call('clink_node_status')
                assert status['used_amount_usdc']=='0.30'
                assert status['settlement_submissions']==1
    asyncio.run(run())
