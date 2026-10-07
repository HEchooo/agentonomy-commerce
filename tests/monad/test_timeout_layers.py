import threading

import pytest

from examples.monad_commerce import core_bridge
from examples.monad_commerce.public_bridge import PublicCoreBridge


class _Pipe:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Process:
    def __init__(self):
        self.stdin = _Pipe()
        self.stdout = _Pipe()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class _NoReplySelector:
    last_timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def register(self, *args):
        pass

    def select(self, *, timeout):
        type(self).last_timeout = timeout
        return []


def _bridge(bridge_type):
    bridge = bridge_type.__new__(bridge_type)
    bridge.bootstrap = {}
    bridge.state_dir = None
    bridge.persistent = True
    bridge.process = _Process()
    bridge._lock = threading.Lock()
    bridge._next_id = 0
    bridge._broken = None
    return bridge


@pytest.mark.parametrize(
    ('bridge_type', 'expected_timeout'),
    [(core_bridge.CoreBridge, 45), (PublicCoreBridge, 180)],
)
def test_core_timeout_uses_layer_default_and_breaks_without_retry(
    monkeypatch, bridge_type, expected_timeout
):
    _NoReplySelector.last_timeout = None
    monkeypatch.setattr(
        core_bridge.selectors, 'DefaultSelector', _NoReplySelector
    )
    bridge = _bridge(bridge_type)

    with pytest.raises(TimeoutError, match='outcome is unknown'):
        bridge._call('settle')

    assert _NoReplySelector.last_timeout == expected_timeout
    assert bridge._broken
    assert bridge.process is None

    with pytest.raises(RuntimeError, match='broken|unavailable'):
        bridge._call('settle')
