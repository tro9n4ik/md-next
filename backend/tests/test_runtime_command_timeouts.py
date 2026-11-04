import importlib.util
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(filename):
    spec = importlib.util.spec_from_file_location(filename, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("binary,deadline", [("nginx", 10), ("systemctl", 20)])
def test_panel_ban_timeout_restores_map_and_preserves_state(tmp_path, monkeypatch, binary, deadline):
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(flock=lambda *_: None, LOCK_EX=1))
    ban = load_script("panel-ban.py")
    monkeypatch.setattr(ban, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(ban, "MAP", tmp_path / "bans.map")
    monkeypatch.setattr(sys, "argv", ["panel-ban.py", "ban", "192.0.2.2"])
    ban.STATE.write_text('["192.0.2.1"]')
    ban.MAP.write_text("192.0.2.1 1;\n")

    calls = []

    def run(args, **options):
        calls.append(args[0])
        if args[0] == binary and calls.count(binary) == 1:
            assert options["timeout"] == deadline
            raise subprocess.TimeoutExpired(args, deadline)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Время ожидания"):
        ban.main()
    assert ban.MAP.read_text() == "192.0.2.1 1;\n"
    assert json.loads(ban.STATE.read_text()) == ["192.0.2.1"]
    assert calls == (["nginx"] if binary == "nginx" else ["nginx", "systemctl", "systemctl"])


def test_observer_stop_timeout_is_reported_without_success(monkeypatch, capsys):
    monkeypatch.setattr(Path, "rglob", lambda *_: ())
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=0))

    def run(args, **options):
        assert options["timeout"] == 20
        raise subprocess.TimeoutExpired(args, 20)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPTS / "krawl-resource-guard.py"), run_name="__main__")
    assert result.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Время ожидания" in captured.err


@pytest.mark.parametrize("operation", ["rev-parse", "ls-files"])
def test_update_git_timeout_exits_before_stopping_panel(tmp_path, monkeypatch, operation):
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace())
    module = load_script("update-panel.py")
    updater = module.Updater(tmp_path, tmp_path, {"commit": "a" * 40})
    monkeypatch.setattr(updater, "verify_revision", Mock())
    run = Mock()
    monkeypatch.setattr(updater, "run", run)
    if operation == "ls-files":
        monkeypatch.setattr(updater, "prepare", lambda _: tmp_path)
        monkeypatch.setattr(updater, "save", Mock())

    def check_output(args, **options):
        assert args[3] == operation
        raise subprocess.TimeoutExpired(args, 15)

    git = Mock(side_effect=check_output)
    monkeypatch.setattr(subprocess, "check_output", git)
    assert updater.execute() is False
    git.assert_called_once()
    assert git.call_args.kwargs["timeout"] == 15
    assert updater.data["phase"] == "error"
    assert not updater.activated
    assert all(call.args[0][0] != "systemctl" for call in run.call_args_list)
