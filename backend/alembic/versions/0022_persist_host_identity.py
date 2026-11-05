"""Постоянная идентичность установки в сохраняемом томе."""
from app.services.crypto import machine_id

revision = "0022_persist_host_identity"
down_revision = "0021_bind_node_secrets"
branch_labels = None
depends_on = None


def upgrade():
    # The first persisted machine-id is byte-for-byte compatible with v2.
    machine_id()


def downgrade():
    # Removing the identity would make existing node records unreadable.
    pass
