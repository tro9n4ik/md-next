"""Keep the chosen node transport without changing existing installations."""
from alembic import op
import sqlalchemy as sa

revision = "0023_node_transport"
down_revision = "0022_persist_host_identity"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("nodes", "node_invites"):
        op.add_column(table, sa.Column("transport", sa.String(16), nullable=False, server_default="xray"))
    for name, kind in (
        ("control_token_hash", sa.String(64)), ("control_seen_at", sa.DateTime(timezone=True)),
        ("control_agent_version", sa.String(32)), ("control_uptime_ms", sa.BigInteger()),
        ("control_command_id", sa.String(32)), ("control_command_deadline", sa.DateTime(timezone=True)),
        ("control_command_result", sa.Text()), ("control_command_finished_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("nodes", sa.Column(name, kind, nullable=True))


def downgrade():
    # Older code cannot safely manage experimental nodes. Require their removal
    # before dropping the information that distinguishes them.
    connection = op.get_bind()
    for table in ("nodes", "node_invites"):
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table} WHERE transport != 'xray'")).scalar()
        if count:
            raise RuntimeError("Удалите экспериментальные ноды и приглашения перед откатом миграции транспорта")
    for table in ("node_invites", "nodes"):
        op.drop_column(table, "transport")
    for name in ("control_token_hash", "control_seen_at", "control_agent_version", "control_uptime_ms",
                 "control_command_id", "control_command_deadline", "control_command_result", "control_command_finished_at"):
        op.drop_column("nodes", name)
