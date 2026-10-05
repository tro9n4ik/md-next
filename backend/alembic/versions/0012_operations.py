"""Node history and one-time Telegram account binding."""
from alembic import op
import sqlalchemy as sa

revision = '0012_operations'
down_revision = '0011_node_country'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('node_samples', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('node_id', sa.Integer(), nullable=False), sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('healthy', sa.Boolean(), nullable=False), sa.Column('ping_ms', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(64), nullable=False))
    op.create_index('ix_node_samples_node_ts', 'node_samples', ['node_id', 'ts'])
    op.create_index('ix_node_samples_ts', 'node_samples', ['ts'])
    op.create_table('telegram_links', sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('telegram_id', sa.String(32), unique=True), sa.Column('code_hash', sa.String(64), unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True)))


def downgrade():
    op.drop_table('telegram_links')
    op.drop_index('ix_node_samples_node_ts', table_name='node_samples')
    op.drop_index('ix_node_samples_ts', table_name='node_samples')
    op.drop_table('node_samples')
