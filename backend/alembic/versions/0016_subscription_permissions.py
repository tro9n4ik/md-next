"""Права субадминистраторов на создание, выдачу и удаление подписок."""

from alembic import op
import sqlalchemy as sa

revision = "0016_subscription_permissions"
down_revision = "0015_subadmins"
branch_labels = None
depends_on = None


def upgrade():
    # Сохраняем прежнюю возможность выдачи; удаление требует явного разрешения.
    op.add_column("users", sa.Column("can_issue_subscriptions", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("users", sa.Column("can_delete_subscriptions", sa.Boolean(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("users", "can_delete_subscriptions")
    op.drop_column("users", "can_issue_subscriptions")
