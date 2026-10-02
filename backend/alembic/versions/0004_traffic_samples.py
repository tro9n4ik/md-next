"""traffic_samples and node fields

Revision ID: 0004_traffic_samples
Revises: 0003_totp_pending_secret
Create Date: 2025-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_traffic_samples'
down_revision = '0003_totp_pending_secret'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('traffic_samples'):
        op.create_table(
            'traffic_samples',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
            sa.Column('rx_bytes', sa.BigInteger(), server_default='0', nullable=True),
            sa.Column('tx_bytes', sa.BigInteger(), server_default='0', nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_traffic_samples_id'), 'traffic_samples', ['id'], unique=False)
        op.create_index(op.f('ix_traffic_samples_timestamp'), 'traffic_samples', ['timestamp'], unique=False)

    if inspector.has_table('nodes'):
        columns = [c['name'] for c in inspector.get_columns('nodes')]
        with op.batch_alter_table('nodes') as batch_op:
            if 'secret' not in columns:
                batch_op.add_column(sa.Column('secret', sa.String(), nullable=True))
            if 'last_seen' not in columns:
                batch_op.add_column(sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True))
            if 'created_at' not in columns:
                batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True))

def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('nodes'):
        columns = [c['name'] for c in inspector.get_columns('nodes')]
        with op.batch_alter_table('nodes') as batch_op:
            if 'created_at' in columns:
                batch_op.drop_column('created_at')
            if 'last_seen' in columns:
                batch_op.drop_column('last_seen')
            if 'secret' in columns:
                batch_op.drop_column('secret')

    if inspector.has_table('traffic_samples'):
        op.drop_index(op.f('ix_traffic_samples_timestamp'), table_name='traffic_samples')
        op.drop_index(op.f('ix_traffic_samples_id'), table_name='traffic_samples')
        op.drop_table('traffic_samples')
