"""Read-only two-RPC observation acquisition shared by settlement and CLI."""
from agentonomy_commerce.budget_network import RpcClient, quantity, verified_boundary


def fetch_observations(config, tx_hash):
    clients = [RpcClient(url) for url in config.rpc_urls]
    boundaries = []
    for client in clients:
        client.check_chain(config.chain_id)
        block = client.call('eth_getBlockByNumber', ['finalized', False])
        boundaries.append(verified_boundary(config, quantity(block['number'])))
    # Both RPCs must prove the SAME boundary; one may be ahead of the other.
    common = min(boundaries)
    observations = []
    for client in clients:
        receipt = client.call('eth_getTransactionReceipt', [tx_hash])
        if receipt is None:
            return None
        height = quantity(receipt['blockNumber'])
        if height > common:
            return None
        boundary = client.call('eth_getBlockByNumber', [hex(common), False])
        canonical = client.call('eth_getBlockByNumber', [hex(height), False])
        transaction = client.call('eth_getTransactionByHash', [tx_hash])
        if not all((boundary, canonical, transaction)):
            return None
        if quantity(boundary['number']) != common or quantity(canonical['number']) != height:
            raise ValueError('RPC returned the wrong canonical block')
        observations.append(dict(chain_id=hex(config.chain_id), transaction=transaction,
            receipt=receipt, canonical_block=canonical,
            finality=dict(kind='local' if config.mode=='local_anvil' else 'monad_verified',
                verified=True, canonical_block=boundary)))
    return observations
