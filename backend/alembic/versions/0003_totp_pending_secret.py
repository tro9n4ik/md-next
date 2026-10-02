"""0003_totp_pending_secret

Идентификатор миграции: 0003_totp_pending_secret
Предыдущая миграция: 0002_client_fields
Дата создания: 2026-09-30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0003_totp_pending_secret'
down_revision: Union[str, None] = '0002_client_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('users'):
        columns = [c['name'] for c in inspector.get_columns('users')]
        if 'totp_pending_secret' not in columns:
            with op.batch_alter_table('users', schema=None) as batch_op:
                batch_op.add_column(sa.Column('totp_pending_secret', sa.String(), nullable=True))

def downgrade() -> None:
    pass
