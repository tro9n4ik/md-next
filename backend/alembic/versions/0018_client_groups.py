"""Группы клиентов и сохранение последней активности."""
from alembic import op
import sqlalchemy as sa

revision = "0018_client_groups"
down_revision = "0017_pause_permission"
branch_labels = None
depends_on = None


def upgrade():
    groups = op.create_table(
        "client_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("name_key", sa.String(48), nullable=False, unique=True),
        sa.Column("color", sa.String(16), nullable=False, server_default="emerald"),
    )
    op.bulk_insert(groups, [
        {"name": "Друзья", "name_key": "друзья", "color": "emerald"},
        {"name": "Коллеги", "name_key": "коллеги", "color": "blue"},
        {"name": "Клиенты", "name_key": "клиенты", "color": "violet"},
    ])
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("group_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_clients_group_id", "client_groups", ["group_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_clients_group_id", ["group_id"])


def downgrade():
    with op.batch_alter_table("clients") as batch:
        batch.drop_index("ix_clients_group_id")
        batch.drop_constraint("fk_clients_group_id", type_="foreignkey")
        batch.drop_column("last_seen_at")
        batch.drop_column("group_id")
    op.drop_table("client_groups")
