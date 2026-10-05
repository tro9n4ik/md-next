"""Отдельный месячный лимит и счётчики трафика обхода БС."""
from alembic import op
import sqlalchemy as sa

revision = '0013_cdn_quota'
down_revision = '0012_operations'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('cdn_monthly_traffic_limit', 'cdn_monthly_traffic_up', 'cdn_monthly_traffic_down', 'cdn_traffic_up', 'cdn_traffic_down'):
        op.add_column('clients', sa.Column(name, sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('clients', sa.Column('cdn_access_blocked', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('clients') as batch:
        for name in ('cdn_access_blocked', 'cdn_traffic_down', 'cdn_traffic_up', 'cdn_monthly_traffic_down', 'cdn_monthly_traffic_up', 'cdn_monthly_traffic_limit'):
            batch.drop_column(name)
