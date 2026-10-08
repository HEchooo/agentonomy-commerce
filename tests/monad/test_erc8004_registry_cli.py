import json
from pathlib import Path


def test_registry_cli_is_an_unsigned_readonly_operator_boundary(tmp_path, monkeypatch, capsys):
    from scripts.monad import erc8004_registry as cli
    from test_hosted_service import configuration, ORIGIN
    config_path = tmp_path/'commerce.json'
    config_path.write_text(json.dumps(configuration()))
    config_path.chmod(0o600)
    registry_path = tmp_path/'registry.json'
    registry_path.write_text('{}')
    registry_path.chmod(0o600)
    calls = []
    class Config:
        @classmethod
        def from_dict(cls, value, *, network, origin):
            calls.append((value, network.chain_id, origin))
            return object()
    class Client:
        def __init__(self, network, config): pass
        def registration_transaction(self): return {'from': 'service-owner', 'value': '0x0', 'data': 'unsigned'}
        def registration_document(self): return {'active': False, 'registrations': []}
        def verify_identity(self): return {'verified': True, 'agent_id': 0}
    monkeypatch.setattr(cli, 'RegistryConfig', Config)
    monkeypatch.setattr(cli, 'ERC8004Client', Client)
    argv = ['--config', str(registry_path), '--network-config', str(config_path), '--origin', ORIGIN]
    assert cli.main(argv+['--operation', 'prepare-registration']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['broadcast'] is False and result['signed'] is False
    assert result['transaction']['value'] == '0x0'
    assert cli.main(argv+['--operation', 'verify-identity']) == 0
    assert json.loads(capsys.readouterr().out)['identity']['agent_id'] == 0
    assert calls[0][1:] == (10143, ORIGIN)


def test_hosted_server_accepts_explicit_registry_configuration(tmp_path, monkeypatch, capsys):
    from examples.monad_commerce import hosted_server as server
    from test_hosted_service import configuration, ORIGIN
    commerce, registry = tmp_path/'commerce.json', tmp_path/'registry.json'
    commerce.write_text(json.dumps(configuration()))
    registry.write_text('{}')
    commerce.chmod(0o600)
    registry.chmod(0o600)
    # The parser must accept the explicit registry flag even when config is
    # incomplete; a sanitized blocked response proves validation, not launch.
    assert server.main(['--config', str(commerce), '--state-dir', str(tmp_path/'state'),
                        '--origin', ORIGIN, '--erc8004-config', str(registry), '--validate-only']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'
