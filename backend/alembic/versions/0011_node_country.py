"""Страна фактического выхода ноды."""
from alembic import op
import sqlalchemy as sa

revision = "0011_node_country"
down_revision = "0010_client_limits"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("nodes", sa.Column("country_code", sa.String(2), nullable=True))

def downgrade():
    with op.batch_alter_table("nodes") as batch:
        batch.drop_column("country_code")
