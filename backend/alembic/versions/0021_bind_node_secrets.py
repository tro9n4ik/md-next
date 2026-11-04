"""Привязка секретов нод к идентификатору ОС."""
from pathlib import Path

from alembic import op
from dotenv import load_dotenv
import sqlalchemy as sa

from app.services.crypto import decrypt_node_secret, encrypt_node_secret, portable_node_secret

revision = "0021_bind_node_secrets"
down_revision = "0020_encrypt_node_secrets"
branch_labels = None
depends_on = None


def rewrite(transform):
    load_dotenv(Path.cwd() / ".env")
    db = op.get_bind()
    db.exec_driver_sql("PRAGMA secure_delete=ON")
    rows = db.execute(sa.text("SELECT id, secret FROM nodes WHERE secret IS NOT NULL AND secret != ''")).all()
    for identifier, secret in rows:
        db.execute(sa.text("UPDATE nodes SET secret=:secret WHERE id=:id"),
                   {"id": identifier, "secret": transform(secret)})


def upgrade():
    rewrite(lambda stored: encrypt_node_secret(decrypt_node_secret(stored)))


def downgrade():
    rewrite(portable_node_secret)
