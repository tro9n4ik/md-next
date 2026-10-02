"""Таблица приглашений узлов

Идентификатор миграции: 0005_node_invites
Предыдущая миграция: 0004_traffic_samples
Дата создания: 2025-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_node_invites'
down_revision = '0004_traffic_samples'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('node_invites'):
        op.create_table(
            'node_invites',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('token_hash', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('revoked', sa.Boolean(), server_default='0', nullable=True),
            sa.Column('node_id', sa.Integer(), sa.ForeignKey('nodes.id'), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_node_invites_id'), 'node_invites', ['id'], unique=False)
        op.create_index(op.f('ix_node_invites_token_hash'), 'node_invites', ['token_hash'], unique=True)

def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('node_invites'):
        op.drop_index(op.f('ix_node_invites_token_hash'), table_name='node_invites')
        op.drop_index(op.f('ix_node_invites_id'), table_name='node_invites')
        op.drop_table('node_invites')
