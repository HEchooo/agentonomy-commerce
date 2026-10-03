"""Independently read and verify a declared purchase; never signs or broadcasts.

ExpectedPayment must come from the immutable Core purchase record, not a merchant
assertion. RPC observations are fetched afresh; an exported success boolean is
never accepted as chain evidence.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from agentonomy_commerce.budget_network import NetworkConfig, quantity
from agentonomy_commerce.budget_observations import fetch_observations
from apps.facilitator.budget_watcher import ExpectedPayment, verify_payment, verify_reverted_payment


def verify(config_value, expected_value):
    config = NetworkConfig(**{key: config_value[key] for key in NetworkConfig.__dataclass_fields__ if key in config_value})
    expected = ExpectedPayment(**expected_value)
    if (expected.chain_id, expected.executor, expected.token, expected.payee) != (
        config.chain_id, config.executor, config.token, config.payee
    ):
        raise ValueError('expected purchase differs from trusted deployment')
    observations = fetch_observations(config, expected.tx_hash)
    if observations is None:
        return dict(status='pending', verified=False, broadcast=False)
    reverted = quantity(observations[0]['receipt']['status']) == 0
    result = (verify_reverted_payment if reverted else verify_payment)(expected, *observations)
    return asdict(result) | dict(status='reverted' if reverted else 'verified',
        broadcast=False, mode=config.mode, public_testnet_acceptance=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('expected',type=Path,help='ExpectedPayment JSON exported from the trusted Core record')
    args=parser.parse_args()
    try:
        result=verify(json.loads(args.config.read_text()),json.loads(args.expected.read_text()))
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print(json.dumps(dict(status='unverified',verified=False,broadcast=False,reason=str(exc))))
        return 2
    print(json.dumps(result,indent=2))
    return 0 if result['status'] in {'verified','reverted'} else 2

if __name__=='__main__': raise SystemExit(main())
