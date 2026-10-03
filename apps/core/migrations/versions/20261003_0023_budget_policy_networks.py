"""Store policy provenance for explicit budget testnet profiles.

This storage constraint does not authorize a network. AppConfig production
allowlists remain unchanged; BudgetAppConfig selects one test network.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261003_0023"
down_revision = "20260916_0022"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_policy_decision_chain_canonical"
OLD = "chain IS NULL OR chain IN ('eip155:137', 'eip155:8453', 'eip155:80002')"
NEW = "chain IS NULL OR chain IN ('eip155:137', 'eip155:8453', 'eip155:80002', 'eip155:10143', 'eip155:31337')"


def _replace(expression):
    with op.batch_alter_table("policy_decisions") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, expression)


def upgrade():
    _replace(NEW)


def downgrade():
    count = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM policy_decisions WHERE chain IN ('eip155:10143', 'eip155:31337')"
    )).scalar_one()
    if count:
        raise RuntimeError("archive testnet policy records before downgrade; no records were deleted")
    _replace(OLD)
