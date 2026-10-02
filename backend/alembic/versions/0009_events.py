"""Create event journal."""
from alembic import op
import sqlalchemy as sa

revision = "0009_events"
down_revision = "0008_cluster_failover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=512), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
    )
    op.create_index("ix_events_ts", "events", ["ts"])
    op.create_index("ix_events_level_ts", "events", ["level", "ts"])
    op.create_index("ix_events_category_ts", "events", ["category", "ts"])


def downgrade() -> None:
    op.drop_index("ix_events_category_ts", table_name="events")
    op.drop_index("ix_events_level_ts", table_name="events")
    op.drop_index("ix_events_ts", table_name="events")
    op.drop_table("events")
