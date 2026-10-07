"""A lost/mismatched worker reply must never be reused as a later purchase reply."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from examples.commerce import node


def child_factory(monkeypatch, code):
    original = subprocess.Popen
    def spawn(_args, **kwargs):
        return original([sys.executable, '-u', '-c', code], **kwargs)
    monkeypatch.setattr(node.subprocess, 'Popen', spawn)


class NoReply:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def register(self, *args): pass
    def select(self, **kwargs): return []


class RecordingNoReply:
    last_timeout = None

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def register(self, *args): pass
    def select(self, **kwargs):
        type(self).last_timeout = kwargs['timeout']
        return []


def test_timeout_closes_the_channel_before_another_request(tmp_path, monkeypatch):
    child_factory(monkeypatch, 'import sys,time; sys.stdin.readline(); time.sleep(30)')
    monkeypatch.setattr(node.selectors, 'DefaultSelector', NoReply)
    bridge = node.MarketplaceBridge(tmp_path)
    process = bridge.process
    try:
        with pytest.raises(TimeoutError):
            bridge.request('snapshot')
        assert process.poll() is not None
        with pytest.raises(RuntimeError, match='broken|exited'):
            bridge.request('snapshot')
    finally:
        bridge.close()


def test_wrong_response_id_breaks_channel(tmp_path, monkeypatch):
    code = ('import sys,json; sys.stdin.readline(); '
            'print(json.dumps({"id":-1,"result":{"state":"delivered"}}),flush=True); '
            'sys.stdin.read()')
    child_factory(monkeypatch, code)
    bridge = node.MarketplaceBridge(tmp_path)
    try:
        with pytest.raises(RuntimeError, match='mismatched'):
            bridge.request('execute', {'preview_id': 'preview_1'})
    finally:
        bridge.close()


@pytest.mark.parametrize(
    ('kwargs', 'expected_timeout'),
    [({}, 45), ({'timeout_seconds': 240}, 240)],
)
def test_marketplace_timeout_is_layer_configurable(
    tmp_path, monkeypatch, kwargs, expected_timeout
):
    child_factory(monkeypatch, 'import sys,time; sys.stdin.readline(); time.sleep(30)')
    RecordingNoReply.last_timeout = None
    monkeypatch.setattr(node.selectors, 'DefaultSelector', RecordingNoReply)
    monkeypatch.setattr(
        node,
        'time',
        SimpleNamespace(monotonic=lambda: 100.0),
    )
    bridge = node.MarketplaceBridge(tmp_path, **kwargs)
    try:
        with pytest.raises(TimeoutError):
            bridge.request('snapshot')
        assert bridge.timeout_seconds == expected_timeout
        assert RecordingNoReply.last_timeout == expected_timeout
    finally:
        bridge.close()


@pytest.mark.parametrize(
    'timeout_seconds',
    [True, False, 0, -1, float('nan'), float('inf'), 901],
)
def test_marketplace_timeout_rejects_invalid_values(tmp_path, monkeypatch, timeout_seconds):
    def unexpected_process(*args, **kwargs):
        raise AssertionError('invalid timeout must be rejected before starting worker')

    monkeypatch.setattr(node.subprocess, 'Popen', unexpected_process)
    with pytest.raises(ValueError, match='timeout_seconds'):
        node.MarketplaceBridge(tmp_path, timeout_seconds=timeout_seconds)
