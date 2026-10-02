"""audit_fixes

Revision ID: 0006_audit_fixes
Revises: 0005_node_invites
Create Date: 2025-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_audit_fixes'
down_revision = '0005_node_invites'
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. users.token_version
    user_cols = [c['name'] for c in inspector.get_columns('users')]
    if 'token_version' not in user_cols:
        op.add_column('users', sa.Column('token_version', sa.Integer(), server_default='1', nullable=False))

    # 2. clients.ip_address
    client_cols = [c['name'] for c in inspector.get_columns('clients')]
    if 'ip_address' not in client_cols:
        op.add_column('clients', sa.Column('ip_address', sa.String(), nullable=True))
        op.create_index(op.f('ix_clients_ip_address'), 'clients', ['ip_address'], unique=True)

    # 3. nodes.is_enabled & nodes.status
    node_cols = [c['name'] for c in inspector.get_columns('nodes')]
    if 'is_enabled' not in node_cols:
        op.add_column('nodes', sa.Column('is_enabled', sa.Boolean(), server_default='1', nullable=True))
    if 'status' not in node_cols:
        op.add_column('nodes', sa.Column('status', sa.String(), server_default='healthy', nullable=True))

def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('users'):
        user_cols = [c['name'] for c in inspector.get_columns('users')]
        if 'token_version' in user_cols:
            op.drop_column('users', 'token_version')

    if inspector.has_table('clients'):
        client_cols = [c['name'] for c in inspector.get_columns('clients')]
        if 'ip_address' in client_cols:
            op.drop_index(op.f('ix_clients_ip_address'), table_name='clients')
            op.drop_column('clients', 'ip_address')

    if inspector.has_table('nodes'):
        node_cols = [c['name'] for c in inspector.get_columns('nodes')]
        if 'status' in node_cols:
            op.drop_column('nodes', 'status')
        if 'is_enabled' in node_cols:
            op.drop_column('nodes', 'is_enabled')
