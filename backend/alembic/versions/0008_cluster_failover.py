"""Add node priorities for cluster failover."""
from alembic import op
import sqlalchemy as sa

revision = "0008_cluster_failover"
down_revision = "0007_client_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "priority" not in {column["name"] for column in inspector.get_columns("nodes")}:
        op.add_column("nodes", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    conn = op.get_bind()
    node_ids = conn.execute(sa.text("SELECT id FROM nodes ORDER BY id")).scalars().all()
    for priority, node_id in enumerate(node_ids):
        conn.execute(sa.text("UPDATE nodes SET priority = :priority WHERE id = :id"), {"priority": priority, "id": node_id})
    if not any(index["name"] == "ix_nodes_priority" for index in inspector.get_indexes("nodes")):
        op.create_index("ix_nodes_priority", "nodes", ["priority"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_nodes_priority", table_name="nodes")
    op.drop_column("nodes", "priority")
