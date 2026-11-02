"""Индивидуальный выход клиента без изменения его подписки и ключей."""
from alembic import op
import sqlalchemy as sa

revision = "0019_client_exit_node"
down_revision = "0018_client_groups"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("exit_node_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_clients_exit_node_id", "nodes", ["exit_node_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_clients_exit_node_id", ["exit_node_id"])


def downgrade():
    with op.batch_alter_table("clients") as batch:
        batch.drop_index("ix_clients_exit_node_id")
        batch.drop_constraint("fk_clients_exit_node_id", type_="foreignkey")
        batch.drop_column("exit_node_id")
