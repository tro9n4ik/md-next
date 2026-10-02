"""0002_client_fields

Revision ID: 0002_client_fields
Revises: 0001_baseline
Create Date: 2026-09-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0002_client_fields'
down_revision: Union[str, None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('clients'):
        columns = [c['name'] for c in inspector.get_columns('clients')]
        with op.batch_alter_table('clients', schema=None) as batch_op:
            if 'phone' not in columns:
                batch_op.add_column(sa.Column('phone', sa.String(), nullable=False, server_default=''))
            if 'email' not in columns:
                batch_op.add_column(sa.Column('email', sa.String(), nullable=False, server_default=''))
            if 'private_key_hash' not in columns:
                batch_op.add_column(sa.Column('private_key_hash', sa.String(), nullable=True))
            if 'private_key' in columns:
                batch_op.drop_column('private_key')

def downgrade() -> None:
    pass
