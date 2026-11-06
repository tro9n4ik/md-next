import asyncio
import base64
import os
import sqlite3
from contextlib import closing
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api import operations
from app.db.database import configure_sqlite
from app.models.node import Node
from app.services import backups
from app.services.crypto import decrypt_node_secret
from app.services.node_transport import node_outbound
from conftest import TestingSessionLocal


def test_pre_agent_backup_migration_and_no_revived_agent_credentials(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend / "alembic"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path/"backups")
    command.upgrade(config, "0022_persist_host_identity")
    with sqlite3.connect("md_next.db") as db:
        db.execute("INSERT INTO nodes (id,name,host,port,protocol) VALUES (1,'old','192.0.2.1',443,'trojan')")
    password = "synthetic-backup-password"
    meta = backups.create_backup(password)
    old_blob = (backups.BACKUP_DIR/meta["name"]).read_bytes()
    command.upgrade(config, "head")
    old = backups.read_backup(old_blob,password)["tables"]["nodes"][0]
    assert old["transport"] == "xray"
    assert old["control_token_hash"] is None
    with sqlite3.connect("md_next.db") as db:
        db.execute("UPDATE nodes SET transport='mdnt', control_token_hash=?, control_agent_version='0.1.0', control_command_id=?",
                   (hashlib.sha256(b"synthetic-key").hexdigest(), "a"*32))
    meta = backups.create_backup(password)
    blob = (backups.BACKUP_DIR/meta["name"]).read_bytes()
    restored = backups.read_backup(blob,password)["tables"]["nodes"][0]
    assert restored["transport"] == "mdnt"
    assert all(restored[key] is None for key in backups.NODE_CONTROL_DEFAULTS if key != "transport")


@pytest.mark.asyncio
async def test_node_secret_is_encrypted_in_sqlite_and_plain_in_application(monkeypatch):
    async with TestingSessionLocal() as db:
        node = Node(name="test", host="192.0.2.1", port=443, protocol="trojan", secret="synthetic-secret")
        db.add(node)
        await db.commit()
        stored = await db.scalar(text("SELECT secret FROM nodes WHERE id=:id"), {"id": node.id})
        assert stored != "synthetic-secret"
        with monkeypatch.context() as env:
            env.setenv("JWT_SECRET_KEY", "another-synthetic-installation")
            with pytest.raises(ValueError, match="расшифровать"):
                decrypt_node_secret(stored)
        await db.refresh(node)
        assert node.secret == "synthetic-secret"
        outbound = node_outbound({
            "id": node.id, "host": node.host, "port": node.port, "protocol": node.protocol, "secret": node.secret,
        }, uplink_port=None)
        assert outbound["settings"]["servers"][0]["password"] == "synthetic-secret"
        await db.execute(text("UPDATE nodes SET secret='legacy-secret' WHERE id=:id"), {"id": node.id})
        await db.refresh(node)
        assert node.secret == "legacy-secret"


def test_migrates_existing_secrets_and_reads_old_backup(tmp_path, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend / "alembic"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")
    command.upgrade(config, "0019_client_exit_node")
    with closing(sqlite3.connect("md_next.db")) as db, db:
        db.execute(
            "INSERT INTO nodes (name, host, port, protocol, secret) VALUES (?, ?, ?, ?, ?)",
            ("test", "192.0.2.1", 443, "trojan", "legacy-secret"),
        )
    password = "synthetic-backup-password"
    meta = backups.create_backup(password)
    blob = (backups.BACKUP_DIR / meta["name"]).read_bytes()

    (tmp_path / ".env").write_text("JWT_SECRET_KEY=" + os.environ["JWT_SECRET_KEY"] + "\n", encoding="utf-8")
    with monkeypatch.context() as env:
        env.delenv("JWT_SECRET_KEY")
        command.upgrade(config, "head")
    with closing(sqlite3.connect("md_next.db")) as db:
        migrated = db.execute("SELECT secret FROM nodes").fetchone()[0]
    assert migrated != "legacy-secret"
    restored = backups.read_backup(blob, password)["tables"]["nodes"][0]["secret"]
    assert restored != "legacy-secret"
    assert restored.startswith("fernet:host:v2:")

    with closing(sqlite3.connect("md_next.db")) as db, db:
        db.execute(
            "INSERT INTO nodes (id, name, host, port, protocol) VALUES (?, ?, ?, ?, ?)",
            (2, "removed by restore", "192.0.2.2", 443, "trojan"),
        )
        db.executemany(
            "INSERT INTO node_invites (name, token_hash, expires_at, node_id) VALUES (?, ?, ?, ?)",
            (
                ("linked", "synthetic-invite-hash", "2000-01-01", 1),
                ("removed", "another-synthetic-hash", "2000-01-01", 2),
            ),
        )

    monkeypatch.setattr("app.api.clients._sync_protocols", AsyncMock())
    monkeypatch.setattr("app.bot.bot.bot_manager.start", AsyncMock())
    monkeypatch.setattr("app.services.telegram_settings.get_telegram_settings_from_db", AsyncMock(return_value={"token": ""}))
    monkeypatch.setattr("app.services.telegram_settings.resolve_telegram_proxy", AsyncMock(return_value=""))

    async def restore_backup():
        engine = create_async_engine("sqlite+aiosqlite:///./md_next.db")
        event.listen(engine.sync_engine, "connect", configure_sqlite)
        user = SimpleNamespace(id=1)
        try:
            async with AsyncSession(engine) as db:
                preview = await operations.restore_preview(
                    operations.PreviewRequest(data=base64.b64encode(blob).decode(), password=password), db, user,
                )
                assert next(row for row in preview["tables"] if row["table"] == "nodes")["changed"] == 0
            async with AsyncSession(engine) as db:
                result = await operations.restore(
                    operations.RestoreRequest(token=preview["token"], password=password, confirm=True), db, user,
                )
                assert result["ok"]
                rollback_blob = (backups.BACKUP_DIR / result["rollback_copy"]).read_bytes()
                rollback_secret = backups.read_backup(rollback_blob, password)["tables"]["nodes"][0]["secret"]
                assert decrypt_node_secret(rollback_secret) == decrypt_node_secret(migrated)
            async with AsyncSession(engine) as db:
                assert (await db.get(Node, 1)).secret == "legacy-secret"
                assert await db.scalar(text("SELECT secret FROM nodes")) != "legacy-secret"
                assert (await db.scalars(text("SELECT node_id FROM node_invites ORDER BY id"))).all() == [1, None]
        finally:
            await engine.dispose()

    asyncio.run(restore_backup())

    command.downgrade(config, "0019_client_exit_node")
    with closing(sqlite3.connect("md_next.db")) as db:
        assert db.execute("SELECT secret FROM nodes").fetchone()[0] == "legacy-secret"
