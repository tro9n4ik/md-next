import pytest

from app.services import crypto


def test_host_bound_secret_rejects_other_machine(monkeypatch):
    monkeypatch.setattr(crypto, "machine_id", lambda: b"1" * 32)
    stored = crypto.encrypt_node_secret("synthetic")
    assert stored.startswith(crypto.HOST_SECRET_PREFIX)
    portable = crypto.portable_node_secret(stored)
    monkeypatch.setattr(crypto, "machine_id", lambda: b"2" * 32)
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_node_secret(stored)
    assert crypto.decrypt_node_secret(portable) == "synthetic"
    rebound = crypto.encrypt_node_secret(crypto.decrypt_node_secret(portable))
    assert crypto.decrypt_node_secret(rebound) == "synthetic"


def test_legacy_secret_remains_readable():
    stored = crypto.NODE_SECRET_PREFIX + crypto.encrypt_secret("legacy")
    assert crypto.decrypt_node_secret(stored) == "legacy"


def test_root_capture_uses_explicit_installation_secret(monkeypatch, tmp_path):
    import sqlite3
    from app.services import migration_bundle

    monkeypatch.setenv("JWT_SECRET_KEY", "synthetic-source-key")
    stored = crypto.encrypt_node_secret("node-token")
    database = tmp_path / "snapshot.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, secret TEXT)")
        db.execute("INSERT INTO nodes VALUES (1, ?)", (stored,))
    monkeypatch.setenv("JWT_SECRET_KEY", "unrelated-root-environment")
    migration_bundle.portable_secrets(database, 'JWT_SECRET_KEY="synthetic-source-key"\n')
    with sqlite3.connect(database) as db:
        portable = db.execute("SELECT secret FROM nodes").fetchone()[0]
    assert crypto.decrypt_node_secret(portable, installation_secret="synthetic-source-key") == "node-token"
    assert __import__("os").environ["JWT_SECRET_KEY"] == "unrelated-root-environment"
