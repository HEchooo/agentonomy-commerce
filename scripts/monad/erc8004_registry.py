"""Prepare an unsigned service registration or verify its public identity.

This operator boundary has no signer, AWS profile or transaction-send option.
Registry pins must be obtained and reviewed before preparing registration.
"""
import argparse
from collections.abc import Mapping
import json
from pathlib import Path

from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig
from examples.monad_commerce.hosted_server import load_configuration
from examples.monad_commerce.public_worker import validate_bootstrap


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--network-config', type=Path, required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--operation', choices=('prepare-registration', 'verify-identity'), required=True)
    args = parser.parse_args(argv)
    try:
        network = validate_bootstrap(load_configuration(args.network_config)).network
        config = RegistryConfig.from_dict(load_configuration(args.config), network=network, origin=args.origin)
        client = ERC8004Client(network, config)
        result = {'broadcast': False, 'signed': False}
        if args.operation == 'prepare-registration':
            result.update(status='unsigned_registration', transaction=client.registration_transaction(),
                          registration=client.registration_document())
        else:
            identity = client.verify_identity()
            if identity is None:
                result.update(status='registration_pending', identity=None)
                print(json.dumps(result, sort_keys=True))
                return 2
            if not isinstance(identity, Mapping) or identity.get('verified') is not True:
                raise ValueError('verified identity required')
            result.update(status='identity_verified', identity=identity)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'blocked', 'signed': False, 'broadcast': False,
                          'error': 'Check protected configuration, registry code pins and both RPCs.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
