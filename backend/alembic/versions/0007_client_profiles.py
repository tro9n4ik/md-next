"""Добавляет профили протоколов, токены подписок и счётчики трафика профилей.

Идентификатор миграции: 0007_client_profiles
Предыдущая миграция: 0006_audit_fixes
"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = "0007_client_profiles"
down_revision = "0006_audit_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    client_cols = {column["name"] for column in inspector.get_columns("clients")}

    if "traffic_total" not in client_cols:
        op.add_column("clients", sa.Column("traffic_total", sa.BigInteger(), nullable=False, server_default="0"))
        if "traffic_used" in client_cols:
            conn.execute(sa.text("UPDATE clients SET traffic_total = COALESCE(traffic_used, 0)"))
    if "sub_token" not in client_cols:
        op.add_column("clients", sa.Column("sub_token", sa.String(length=64), nullable=True))
    if "protocol" in client_cols:
        with op.batch_alter_table("clients") as batch:
            batch.alter_column("protocol", existing_type=sa.String(), nullable=True)

    clients = conn.execute(sa.text("SELECT id, protocol, uuid, public_key, ip_address FROM clients")).mappings().all()
    for row in clients:
        token = secrets.token_urlsafe(32)
        conn.execute(sa.text("UPDATE clients SET sub_token = :token WHERE id = :id"), {"token": token, "id": row["id"]})
    with op.batch_alter_table("clients") as batch:
        batch.alter_column("sub_token", existing_type=sa.String(length=64), nullable=False)

    if "client_profiles" not in inspector.get_table_names():
        op.create_table(
            "client_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("uuid", sa.String(length=64), nullable=True),
            sa.Column("auth", sa.String(length=255), nullable=True),
            sa.Column("public_key", sa.String(length=128), nullable=True),
            sa.Column("private_key_enc", sa.String(length=512), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("traffic_up", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("traffic_down", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("uuid", name="uq_client_profiles_uuid"),
            sa.UniqueConstraint("ip_address", name="uq_client_profiles_ip_address"),
        )
        op.create_index("ix_client_profiles_client_id", "client_profiles", ["client_id"])
        op.create_index("ix_client_profiles_kind", "client_profiles", ["kind"])

    for row in clients:
        kind = "vless_reality_tcp" if row["protocol"] == "vless" else "awg" if row["protocol"] == "awg" else None
        if not kind:
            continue
        conn.execute(
            sa.text("""INSERT INTO client_profiles
                (client_id, kind, uuid, public_key, ip_address, is_enabled, traffic_up, traffic_down)
                VALUES (:client_id, :kind, :uuid, :public_key, :ip_address, 1, 0, 0)"""),
            {
                "client_id": row["id"], "kind": kind,
                "uuid": row["uuid"] if kind == "vless_reality_tcp" else None,
                "public_key": row["public_key"] if kind == "awg" else None,
                "ip_address": row["ip_address"] if kind == "awg" else None,
            },
        )

    op.create_index("ix_clients_sub_token", "clients", ["sub_token"], unique=True)
    for kind in ("vless_reality_tcp", "awg", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2"):
        key = f"profiles.enabled.{kind}"
        existing = conn.execute(sa.text("SELECT key FROM settings WHERE key = :key"), {"key": key}).first()
        if not existing:
            enabled = "true" if kind in ("vless_reality_tcp", "awg") else "false"
            conn.execute(sa.text("INSERT INTO settings (key, value) VALUES (:key, :value)"), {"key": key, "value": enabled})


def downgrade() -> None:
    op.drop_index("ix_clients_sub_token", table_name="clients")
    op.drop_index("ix_client_profiles_kind", table_name="client_profiles")
    op.drop_index("ix_client_profiles_client_id", table_name="client_profiles")
    op.drop_table("client_profiles")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("sub_token")
        batch.drop_column("traffic_total")
        batch.alter_column("protocol", existing_type=sa.String(), nullable=False)
