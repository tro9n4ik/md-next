import os
import sqlite3
import pytest
from alembic.config import Config
from alembic import command
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import create_engine
import app.models  # Импортируем все модели для регистрации в Base.metadata
from app.db.database import Base

@pytest.fixture
def alembic_config(tmp_path):
    db_file = tmp_path / "test_migration.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    cfg = Config()
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg, db_file

def test_fresh_db_migration(alembic_config):
    cfg, db_file = alembic_config
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    assert "clients" in tables
    assert "nodes" in tables
    assert "users" in tables
    assert "routing_rules" in tables
    assert "traffic_samples" in tables
    assert "node_invites" in tables

    cursor.execute("PRAGMA table_info(clients);")
    cols = [row[1] for row in cursor.fetchall()]
    assert "phone" in cols
    assert "email" in cols
    assert "private_key_hash" in cols

    cursor.execute("PRAGMA table_info(nodes);")
    node_cols = [row[1] for row in cursor.fetchall()]
    assert "secret" in node_cols
    assert "last_seen" in node_cols
    assert "created_at" in node_cols

    conn.close()


def test_client_limits_migration_preserves_existing_client(alembic_config):
    cfg, db_file = alembic_config
    command.upgrade(cfg, "0009_events")
    with sqlite3.connect(db_file) as db:
        db.execute("insert into clients (id,name,phone,email,is_active,traffic_total,traffic_limit,sub_token) values (1,?,?,?,?,?,?,?)",
                   ("Существующий клиент", "", "", 1, 12345, 999999, "migration-test"))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db_file) as db:
        row = db.execute("select name,is_active,traffic_total,traffic_limit,expires_at,monthly_traffic_limit,monthly_traffic_up,monthly_traffic_down,access_blocked from clients where id=1").fetchone()
        assert row == ("Существующий клиент", 1, 12345, 999999, None, 0, 0, 0, 0)

def test_legacy_db_migration_0004_0005(alembic_config):
    cfg, db_file = alembic_config

    # Создаем старую схему БД (версии 0003) без secret/last_seen/created_at в nodes и без traffic_samples/node_invites
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            protocol VARCHAR NOT NULL,
            uuid VARCHAR,
            public_key VARCHAR,
            private_key VARCHAR,
            traffic_used INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME
        );
    """)
    cursor.execute("""
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            host VARCHAR NOT NULL,
            port INTEGER NOT NULL,
            protocol VARCHAR NOT NULL,
            public_key VARCHAR,
            is_active BOOLEAN DEFAULT 1,
            ping_ms INTEGER DEFAULT 0
        );
    """)
    cursor.execute("INSERT INTO clients (name, protocol, private_key) VALUES ('OldClient', 'vless', 'secret_priv_key');")
    cursor.execute("INSERT INTO clients (name, protocol, public_key) VALUES ('OldAWG', 'awg', 'legacy-awg-public-key');")
    cursor.execute("INSERT INTO nodes (name, host, port, protocol) VALUES ('OldNode', '1.1.1.1', 443, 'trojan');")
    conn.commit()
    conn.close()

    # Применяем миграции до head (включая 0004 и 0005)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    assert "traffic_samples" in tables
    assert "node_invites" in tables

    cursor.execute("PRAGMA table_info(nodes);")
    node_cols = [row[1] for row in cursor.fetchall()]
    assert "secret" in node_cols
    assert "last_seen" in node_cols
    assert "created_at" in node_cols

    cursor.execute("SELECT name, host, secret FROM nodes;")
    nodes_rows = cursor.fetchall()
    assert len(nodes_rows) == 1
    assert nodes_rows[0][0] == "OldNode"

    cursor.execute("SELECT sub_token FROM clients;")
    assert all(token and len(token) >= 32 for (token,) in cursor.fetchall())
    cursor.execute("SELECT kind, uuid, public_key, private_key_enc FROM client_profiles ORDER BY client_id;")
    profiles = cursor.fetchall()
    assert profiles[0][0] == "vless_reality_tcp" and profiles[0][2] is None
    assert profiles[1] == ("awg", None, "legacy-awg-public-key", None)

    conn.close()

def test_repeat_migration_idempotent(alembic_config):
    cfg, _ = alembic_config
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")

def test_downgrade_base_upgrade_head(alembic_config):
    cfg, db_file = alembic_config
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall() if row[0] != 'alembic_version']
    assert len(tables) == 0
    conn.close()

    command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall() if row[0] != 'alembic_version']
    assert "users" in tables
    assert "clients" in tables
    assert "nodes" in tables
    conn.close()

def test_migration_schema_matches_models(alembic_config):
    cfg, db_file = alembic_config
    command.upgrade(cfg, "head")

    sync_url = f"sqlite:///{db_file}"
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        mc = MigrationContext.configure(connection)
        diff = compare_metadata(mc, Base.metadata)
        assert diff == [], f"Расхождения между миграциями и моделями SQLAlchemy: {diff}"
