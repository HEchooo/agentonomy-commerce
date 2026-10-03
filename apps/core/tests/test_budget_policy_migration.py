from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from services.action_policy_repository import ActionPolicyRepository


def payload(identifier, chain):
    return dict(policy_decision_id=identifier, action_id='act', user_id='user', agent_id='agent',
                action_type='marketplace_purchase', decision='approved', target_address='0x'+'11'*20,
                chain=chain, evaluated_at='2026-10-03T00:00:00Z')


def test_budget_policy_storage_accepts_testnets_not_arbitrary_chains(tmp_path):
    repo = ActionPolicyRepository(f'sqlite+pysqlite:///{tmp_path / "core.sqlite3"}')
    for chain in ['eip155:31337', 'eip155:10143']:
        repo.create_policy_decision(payload(chain, chain))
        assert repo.policy_decision(chain)['chain'] == chain
    with pytest.raises(ValueError):
        repo.create_policy_decision(payload('wrong', 'eip155:1'))


def test_budget_migration_preserves_old_data_and_refuses_unsafe_downgrade(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path/'migrate.sqlite3'
    cfg = Config(str(root/'alembic.ini'))
    cfg.set_main_option('script_location', str(root/'migrations'))
    cfg.set_main_option('sqlalchemy.url', f'sqlite+pysqlite:///{database}')
    command.upgrade(cfg, '20260916_0022')
    repo = ActionPolicyRepository(f'sqlite+pysqlite:///{database}')
    repo.create_policy_decision(payload('old', 'eip155:137'))
    command.upgrade(cfg, 'head')
    repo.create_policy_decision(payload('new', 'eip155:31337'))
    with pytest.raises(RuntimeError, match='testnet policy records'):
        command.downgrade(cfg, '20260916_0022')
    assert repo.policy_decision('new')['chain'] == 'eip155:31337'
    with repo.engine.begin() as connection:
        connection.execute(text("DELETE FROM policy_decisions WHERE policy_decision_id='new'"))
    command.downgrade(cfg, '20260916_0022')
    assert repo.policy_decision('old')['chain'] == 'eip155:137'
    with pytest.raises(ValueError):
        repo.create_policy_decision(payload('newer', 'eip155:31337'))
