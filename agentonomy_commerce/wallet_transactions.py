"""Independent dual-RPC verification for finite approve and owner revocation."""
import re
from agentonomy_commerce.budget_network import address, quantity
from agentonomy_commerce.budget_observations import fetch_observations


class InvalidWalletTransaction(ValueError):
    """Two canonical final observations prove this hash cannot satisfy the operation."""


def transaction_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', value) or int(value, 16) == 0:
        raise ValueError('transaction hash required')
    return value.lower()


def _calldata(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', value):
        raise ValueError('complete RPC transaction calldata required')
    return value.lower()


def verify_wallet_transaction(config, *, owner, tx_hash, to, data):
    owner, to, tx_hash = address(owner), address(to), transaction_hash(tx_hash)
    data = _calldata(data)
    observations = fetch_observations(config, tx_hash)
    if observations is None:
        return None
    if len(observations) != 2:
        raise ValueError('two RPC observations required')
    proofs = []
    for row in observations:
        tx, receipt, canonical = row['transaction'], row['receipt'], row['canonical_block']
        boundary, finality = row['finality']['canonical_block'], row['finality']
        block, block_hash = quantity(receipt['blockNumber']), transaction_hash(receipt['blockHash'])
        observed_data = _calldata(tx.get('input', tx.get('data')))
        receipt_status = quantity(receipt['status'])
        if receipt_status not in (0, 1):
            raise ValueError('invalid receipt status')
        if 'input' in tx and 'data' in tx and _calldata(tx['data']) != observed_data:
            raise ValueError('RPC transaction calldata fields disagree')
        if (quantity(row['chain_id']) != config.chain_id
            or quantity(tx.get('chainId', hex(config.chain_id))) != config.chain_id
            or transaction_hash(tx['hash']) != tx_hash or transaction_hash(receipt['transactionHash']) != tx_hash
            or address(receipt['from']) != address(tx['from'])
            or address(receipt['to']) != address(tx['to'])
            or quantity(tx['blockNumber']) != block or transaction_hash(tx['blockHash']) != block_hash
            or quantity(canonical['number']) != block or transaction_hash(canonical['hash']) != block_hash
            or finality['verified'] is not True
            or finality['kind'] != ('monad_verified' if config.mode == 'monad_testnet' else 'local')
            or quantity(boundary['number']) < block):
            raise ValueError('wallet transaction is not independently verified')
        boundary_hash = transaction_hash(boundary['hash'])
        if quantity(boundary['number']) == block and boundary_hash != block_hash:
            raise ValueError('wallet transaction boundary mismatch')
        proofs.append({'transaction_hash': tx_hash, 'owner': address(tx['from']), 'to': address(tx['to']),
            'value': quantity(tx['value']), 'data': observed_data,
            'receipt_status': receipt_status,
            'chain_id': config.chain_id, 'block_number': block, 'block_hash': block_hash,
            'nonce': quantity(tx['nonce']), 'finality_block_number': quantity(boundary['number']),
            'finality_block_hash': boundary_hash, 'finality_kind': finality['kind'],
            'two_rpc_verified': True, 'verified': True})
    if proofs[0] != proofs[1]:
        raise ValueError('wallet transaction RPC observations disagree')
    proof = proofs[0]
    if (proof['owner'] != owner or proof['to'] != to or proof['value'] != 0
        or proof['data'] != data or proof['receipt_status'] != 1):
        raise InvalidWalletTransaction('canonical transaction cannot satisfy this wallet operation')
    return {key: value for key, value in proof.items() if key not in {'value', 'data', 'receipt_status'}}
