"""Marketplace worker for the wallet-authorized Monad canary."""
from pathlib import Path
import json
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/marketplace'))
from examples.monad_commerce.public_runtime import PublicCommerceRuntime


def main():
    runtime = None
    try:
        while True:
            line = sys.stdin.buffer.readline(1048577)
            if not line:
                return 0
            if len(line) > 1048576 or not line.endswith(b'\n'):
                return 2
            request = json.loads(line)
            try:
                method, args = request['method'], request.get('arguments', {})
                if method == 'initialize' and runtime is None:
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        port = sock.getsockname()[1]
                    runtime = PublicCommerceRuntime(Path(sys.argv[1]), port, args)
                    runtime.__enter__()
                    result = {'status': 'ready', 'mode': 'monad_testnet'}
                elif runtime is not None and method in {'search', 'details', 'preview', 'execute', 'purchase', 'recover_purchase', 'snapshot', 'revoke', 'onboarding_status'}:
                    result = getattr(runtime, method)(**args)
                else:
                    raise ValueError('operation not allowed')
                response = {'id': request['id'], 'result': result}
            except Exception:
                response = {'id': request.get('id'), 'error': 'Operation unconfirmed; query the existing order before retrying'}
            print(json.dumps(response, default=str), flush=True)
    finally:
        if runtime is not None:
            runtime.__exit__(None, None, None)


if __name__ == '__main__':
    raise SystemExit(main())
