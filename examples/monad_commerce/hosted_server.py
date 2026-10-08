"""Serve the wallet-scoped demo behind an operator-managed HTTPS reverse proxy.

The listener is always loopback. This command never installs AWS credentials,
changes cloud resources, renews grants, or deploys a contract.
"""
import argparse
import json
from pathlib import Path
import os
import stat

import uvicorn

from examples.monad_commerce.hosted_api import create_app
from examples.monad_commerce.hosted_service import HostedCommerceService


def load_configuration(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 65536):
            raise ValueError('private configuration required')
        raw = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    return json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--erc8004-config', type=Path)
    parser.add_argument('--port', type=int, default=8092)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not 1024 <= args.port <= 65535:
            raise ValueError('port out of range')
        configuration = load_configuration(args.config)
        registry = None
        if args.erc8004_config:
            from agentonomy_commerce.erc8004 import ERC8004Client, RegistryConfig
            from examples.monad_commerce.public_worker import validate_bootstrap
            network = validate_bootstrap(configuration).network
            pins = RegistryConfig.from_dict(load_configuration(args.erc8004_config),
                                            network=network, origin=args.origin)
            registry = ERC8004Client(network, pins)
        service = HostedCommerceService(args.state_dir, configuration, public_origin=args.origin,
                                        registry=registry)
        app = create_app(origin=args.origin, runtime_factory=lambda: service)
        if args.validate_only:
            print(json.dumps({'status': 'configuration_valid', 'chain_id': 10143,
                              'broadcast': False, 'credentials_loaded': False,
                              'listener': '127.0.0.1', 'origin': args.origin}))
            return 0
        uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1,
                    proxy_headers=False, access_log=False)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError):
        print(json.dumps({'status': 'blocked', 'broadcast': False,
                          'error': 'Review private configuration and deployment pins.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
