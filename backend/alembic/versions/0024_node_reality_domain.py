"""Persist the owned TLS domain without changing legacy node connections."""
from alembic import op
import sqlalchemy as sa

revision = "0024_node_reality_domain"
down_revision = "0023_node_transport"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("reality_server_name", sa.String(253), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM nodes WHERE reality_server_name IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Перед откатом удалите ноды с собственным доменом: старый код не поддерживает их подключение")
    op.drop_column("nodes", "reality_server_name")
