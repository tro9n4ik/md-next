"""Шифрование секретов нод с сохранением существующей схемы."""
from pathlib import Path

from alembic import op
from dotenv import load_dotenv
import sqlalchemy as sa

from app.services.crypto import NODE_SECRET_PREFIX, decrypt_node_secret, encrypt_node_secret

revision = "0020_encrypt_node_secrets"
down_revision = "0019_client_exit_node"
branch_labels = None
depends_on = None

nodes = sa.table("nodes", sa.column("id", sa.Integer), sa.column("secret", sa.String))


def rewrite_secrets(transform):
    load_dotenv(Path.cwd() / ".env")
    connection = op.get_bind()
    connection.exec_driver_sql("PRAGMA secure_delete=ON")
    rows = connection.execute(sa.select(nodes.c.id, nodes.c.secret).where(nodes.c.secret != "")).all()
    for node_id, stored in rows:
        value = transform(stored)
        if value != stored:
            connection.execute(nodes.update().where(nodes.c.id == node_id).values(secret=value))


def upgrade():
    def encrypt_existing(stored):
        if stored.startswith(NODE_SECRET_PREFIX):
            decrypt_node_secret(stored)
            return stored
        return encrypt_node_secret(stored)

    rewrite_secrets(encrypt_existing)


def downgrade():
    rewrite_secrets(decrypt_node_secret)
