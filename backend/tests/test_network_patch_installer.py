"""Patch safety: mismatch is read-only; apply and automatic rollback use mocks."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts/apply-network-fix.py"
    spec = importlib.util.spec_from_file_location("network_patch_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def patch_environment(tmp_path, monkeypatch):
    module = _load()
    source, installed, backup_root = [
        tmp_path / value for value in ("source", "installed", "backups")
    ]
    relative = "backend/app/example.py"
    for root in (source, installed):
        (root / relative).parent.mkdir(parents=True)
    old, new = b"VALUE = 1\n", b"VALUE = 2\n"
    (source / relative).write_bytes(new)
    (installed / relative).write_bytes(old)
    cfg = tmp_path / "config.json"
    old_config = json.dumps(
        {
            "outbounds": [{"tag": "direct"}],
            "burstObservatory": {"subjectSelector": ["node-"]},
        }
    )
    cfg.write_text(old_config)
    stream, site, awg = [
        tmp_path / name for name in ("stream.conf", "site.conf", "awg.conf")
    ]
    for path in (stream, site, awg):
        path.write_text("old configuration")
    env = "\n".join(
        (
            f"XRAY_CONFIG_PATH={cfg}",
            f"NGINX_STREAM_CONFIG={stream}",
            f"NGINX_PANEL_CONFIG={site}",
            f"AWG_CONFIG_PATH={awg}",
            "JWT_SECRET_KEY=must-stay-the-same",
        )
    )
    (installed / "backend/.env").write_text(env)
    with sqlite3.connect(installed / "backend/md_next.db") as db:
        db.execute("create table settings (key text,value text)")
        db.execute("create table nodes (id integer,is_enabled integer,secret text)")
        db.execute("insert into settings values ('active_node_id','direct:manual')")
    (source / "NETWORK_FIX_MANIFEST.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": relative,
                        "before_sha256": hashlib.sha256(old).hexdigest(),
                        "after_sha256": hashlib.sha256(new).hexdigest(),
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module, "wait_for_panel", lambda: None)
    return module, installed, backup_root, relative, old, new, cfg, old_config, env


def test_mismatched_server_is_rejected_before_any_service_operation(
    patch_environment, monkeypatch
):
    module, installed, backups, relative, *_ = patch_environment
    (installed / relative).write_text("OTHER_VERSION = True\n")
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch",
            "--app-dir",
            str(installed),
            "--apply",
            "--backup-root",
            str(backups),
        ],
    )
    monkeypatch.setattr(
        module,
        "command",
        lambda *args: pytest.fail("Must not touch services on mismatch"),
    )
    with pytest.raises(RuntimeError, match="Версия файла отличается"):
        module.main()
    assert (installed / relative).read_text() == "OTHER_VERSION = True\n"
    assert not backups.exists()


def test_check_does_not_modify_installation(patch_environment, monkeypatch):
    module, installed, backups, relative, old, *_ = patch_environment
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch",
            "--app-dir",
            str(installed),
            "--check",
            "--backup-root",
            str(backups),
        ],
    )
    monkeypatch.setattr(
        module, "command", lambda *args: pytest.fail("Check must be read-only")
    )
    module.main()
    assert (installed / relative).read_bytes() == old
    assert not backups.exists()


def test_apply_preserves_env_and_database_and_keeps_backup(
    patch_environment, monkeypatch
):
    module, installed, backups, relative, old, new, cfg, old_config, env = (
        patch_environment
    )
    operations = []

    def command(*args):
        operations.append(args)
        if args == ("systemctl", "start", "md-next-backend"):
            cfg.write_text(
                json.dumps({"outbounds": [{"tag": "direct"}], "routing": {"rules": []}})
            )
        return "active" if args[:2] == ("systemctl", "is-active") else ""

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch",
            "--app-dir",
            str(installed),
            "--apply",
            "--backup-root",
            str(backups),
        ],
    )
    module.main()
    assert (installed / relative).read_bytes() == new
    assert (installed / "backend/.env").read_text() == env
    backup = next(backups.iterdir())
    assert (backup / "files/0").read_bytes() == old
    assert (backup / "database.sqlite").is_file()
    assert ("systemctl", "stop", "md-next-backend") in operations


def test_failed_start_restores_source_and_config_without_replacing_database(
    patch_environment, monkeypatch
):
    module, installed, backups, relative, old, new, cfg, old_config, env = (
        patch_environment
    )
    failed = False

    def command(*args):
        nonlocal failed
        if args == ("systemctl", "start", "md-next-backend") and not failed:
            failed = True
            cfg.write_text("broken new configuration")
            raise RuntimeError("simulated start failure")
        return ""

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch",
            "--app-dir",
            str(installed),
            "--apply",
            "--backup-root",
            str(backups),
        ],
    )
    with pytest.raises(RuntimeError, match="simulated"):
        module.main()
    assert (installed / relative).read_bytes() == old
    assert cfg.read_text() == old_config
    assert (installed / "backend/.env").read_text() == env
    with sqlite3.connect(installed / "backend/md_next.db") as db:
        assert (
            db.execute(
                'select value from settings where key="active_node_id"'
            ).fetchone()[0]
            == "direct:manual"
        )
