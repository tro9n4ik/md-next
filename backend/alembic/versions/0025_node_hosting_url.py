"""Optional admin-only hosting links for nodes and pending invitations."""
from alembic import op
import sqlalchemy as sa

revision = '0025_node_hosting_url'
down_revision = '0024_node_reality_domain'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('hosting_url', sa.String(2048), nullable=True))
    op.add_column('node_invites', sa.Column('hosting_url', sa.String(2048), nullable=True))


def downgrade():
    op.drop_column('node_invites', 'hosting_url')
    op.drop_column('nodes', 'hosting_url')
