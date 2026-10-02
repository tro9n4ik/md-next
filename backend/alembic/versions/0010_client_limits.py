"""Сроки подписок и месячные лимиты трафика клиентов."""

from alembic import op
import sqlalchemy as sa

revision = "0010_client_limits"
down_revision = "0009_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("monthly_traffic_limit", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("clients", sa.Column("monthly_traffic_up", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("clients", sa.Column("monthly_traffic_down", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("clients", sa.Column("traffic_period_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("access_blocked", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        for name in ("access_blocked", "traffic_period_start", "monthly_traffic_down", "monthly_traffic_up", "monthly_traffic_limit", "expires_at"):
            batch.drop_column(name)
