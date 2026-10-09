"""Store per-node Reality shortId without changing existing node transports."""
from alembic import op
import sqlalchemy as sa

revision = '0014_node_short_id'
down_revision = '0013_cdn_quota'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('short_id', sa.String(16), nullable=True))


def downgrade():
    op.drop_column('nodes', 'short_id')
