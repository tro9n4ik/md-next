"""Panel roles and ownership of clients created by subadministrators."""

from alembic import op
import sqlalchemy as sa

revision = "0015_subadmins"
down_revision = "0014_node_short_id"
branch_labels = None
depends_on = None


def upgrade():
    # Existing panel users keep their administrator permissions.
    op.add_column(
        "users",
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "users",
        sa.Column("view_all_clients", sa.Boolean(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_clients_created_by_id_users",
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_clients_created_by_id", ["created_by_id"])


def downgrade():
    with op.batch_alter_table("clients") as batch:
        batch.drop_index("ix_clients_created_by_id")
        batch.drop_constraint("fk_clients_created_by_id_users", type_="foreignkey")
        batch.drop_column("created_by_id")
    op.drop_column("users", "is_active")
    op.drop_column("users", "view_all_clients")
    op.drop_column("users", "role")
