"""0001_baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('clients'):
        op.create_table(
            'clients',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('phone', sa.String(), nullable=False, server_default=''),
            sa.Column('email', sa.String(), nullable=False, server_default=''),
            sa.Column('protocol', sa.String(), nullable=False),
            sa.Column('uuid', sa.String(), nullable=True),
            sa.Column('public_key', sa.String(), nullable=True),
            sa.Column('private_key_hash', sa.String(), nullable=True),
            sa.Column('traffic_used', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('traffic_limit', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('ix_clients_id', 'clients', ['id'])
        op.create_index('ix_clients_name', 'clients', ['name'])
        op.create_index('ix_clients_uuid', 'clients', ['uuid'], unique=True)

    if not inspector.has_table('nodes'):
        op.create_table(
            'nodes',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('host', sa.String(), nullable=False),
            sa.Column('port', sa.Integer(), nullable=False),
            sa.Column('protocol', sa.String(), nullable=False),
            sa.Column('public_key', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
            sa.Column('ping_ms', sa.Integer(), nullable=True, server_default='0'),
        )
        op.create_index('ix_nodes_id', 'nodes', ['id'])
        op.create_index('ix_nodes_name', 'nodes', ['name'])

    if not inspector.has_table('users'):
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('hashed_password', sa.String(), nullable=False),
            sa.Column('totp_secret', sa.String(), nullable=True),
            sa.Column('totp_enabled', sa.Boolean(), nullable=True, server_default='0'),
        )
        op.create_index('ix_users_id', 'users', ['id'])
        op.create_index('ix_users_username', 'users', ['username'], unique=True)

    if not inspector.has_table('routing_rules'):
        op.create_table(
            'routing_rules',
            sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
            sa.Column('domain_or_ip', sa.String(), nullable=False),
            sa.Column('target_node_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(), nullable=True, server_default='proxy'),
            sa.Column('description', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('ix_routing_rules_id', 'routing_rules', ['id'])
        op.create_index('ix_routing_rules_domain_or_ip', 'routing_rules', ['domain_or_ip'])

    if not inspector.has_table('settings'):
        op.create_table(
            'settings',
            sa.Column('key', sa.String(), nullable=False, primary_key=True),
            sa.Column('value', sa.String(), nullable=False),
        )
        op.create_index('ix_settings_key', 'settings', ['key'])

def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('settings'):
        op.drop_index('ix_settings_key', table_name='settings')
        op.drop_table('settings')

    if inspector.has_table('routing_rules'):
        op.drop_index('ix_routing_rules_domain_or_ip', table_name='routing_rules')
        op.drop_index('ix_routing_rules_id', table_name='routing_rules')
        op.drop_table('routing_rules')

    if inspector.has_table('users'):
        op.drop_index('ix_users_username', table_name='users')
        op.drop_index('ix_users_id', table_name='users')
        op.drop_table('users')

    if inspector.has_table('nodes'):
        op.drop_index('ix_nodes_name', table_name='nodes')
        op.drop_index('ix_nodes_id', table_name='nodes')
        op.drop_table('nodes')

    if inspector.has_table('clients'):
        op.drop_index('ix_clients_uuid', table_name='clients')
        op.drop_index('ix_clients_name', table_name='clients')
        op.drop_index('ix_clients_id', table_name='clients')
        op.drop_table('clients')
