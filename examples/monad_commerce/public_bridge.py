"""Core isolation for an externally authorized public testnet buyer."""
import json
import os
import subprocess
import sys

from examples.monad_commerce.core_bridge import CoreBridge, ROOT


class PublicCoreBridge(CoreBridge):
    def _start(self):
        if self._broken is not None:
            raise RuntimeError('Core channel unavailable; inspect original order')
        if self.process is not None:
            return
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG', 'LC_ALL'}}
        env['PYTHONUNBUFFERED'] = '1'
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'examples.monad_commerce.public_worker', str(self.state_dir)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.process.stdin.write(json.dumps(self.bootstrap, separators=(',', ':')) + '\n')
        self.process.stdin.flush()

    def onboarding(self, method, **params):
        if method not in {'onboarding_status', 'wallet_challenge', 'wallet_verify',
                          'grant_challenge', 'grant_verify', 'budget_payload',
                          'budget_bind', 'allowance_verify'}:
            raise ValueError('unknown wallet setup operation')
        return self._call(method, params)
