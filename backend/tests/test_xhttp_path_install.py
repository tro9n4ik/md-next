"""Устаревший env не должен менять действующий путь профилей при обновлении."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest


spec=importlib.util.spec_from_file_location('install_xhttp_path',Path(__file__).resolve().parents[2]/'scripts/xhttp-path.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_database_wins_over_stale_environment_and_is_not_changed(tmp_path):
    path=tmp_path/'panel.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE settings(key TEXT,value TEXT)')
        db.execute('INSERT INTO settings VALUES(?,?)',('profiles.path.vless_xhttp_tls','/current'))
    before=path.read_bytes()
    assert module.resolve(path,'/old')=='/current'
    assert path.read_bytes()==before


def test_missing_database_uses_existing_environment(tmp_path):
    path=tmp_path/'absent.db'
    assert module.resolve(path,'"/existing"')=='/existing'
    assert not path.exists()


@pytest.mark.parametrize('value',['/','//bad','/bad;include','/bad\nlocation /'])
def test_invalid_path_does_not_reach_nginx(tmp_path,value):
    with pytest.raises(ValueError):module.resolve(tmp_path/'absent.db',value)


def test_unreadable_database_does_not_silently_reset_path(tmp_path):
    path=tmp_path/'broken.db';path.write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):module.resolve(path,'/fallback')
