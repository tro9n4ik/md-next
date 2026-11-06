import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('nginx_capacity',Path(__file__).resolve().parents[2]/'scripts/nginx-capacity.py')
capacity=importlib.util.module_from_spec(spec);spec.loader.exec_module(capacity)


def test_default_capacity_and_idempotence():
    source='worker_processes auto;\nevents {\n worker_connections 768;\n}\nhttp {}\n'
    updated,unit=capacity.render(source,524288)
    assert 'worker_connections 8192;' in updated
    assert 'worker_rlimit_nofile 65536;' in updated
    assert unit=='[Service]\nLimitNOFILE=524288\n'
    assert capacity.render(updated,524288)==(updated,unit)


def test_higher_administrator_limits_are_preserved():
    source='worker_rlimit_nofile 262144;\nevents {\nworker_connections 32768;\n}\n'
    updated,unit=capacity.render(source,524288)
    assert updated==source
    assert '524288' in unit


def test_comments_are_not_edited_and_missing_directive_is_added():
    source='# worker_rlimit_nofile 1;\nevents {\n# worker_connections 1;\n}\n'
    updated,_=capacity.render(source)
    assert '# worker_connections 1;' in updated
    assert '# worker_rlimit_nofile 1;' in updated
    assert 'worker_connections 8192;' in updated


def test_failed_validation_restores_original_files(tmp_path,monkeypatch):
    config=tmp_path/'etc/nginx/nginx.conf';config.parent.mkdir(parents=True)
    original='events {\nworker_connections 768;\n}\n';config.write_text(original)
    calls=[]
    def run(*args):
        calls.append(args)
        if args[:2]==('systemctl','show'):return SimpleNamespace(stdout='524288\n')
        if args==('nginx','-t') and calls.count(args)==1:raise RuntimeError('invalid')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(capacity,'run',run)
    with pytest.raises(RuntimeError,match='invalid'):capacity.configure(tmp_path)
    assert config.read_text()==original
    assert not (tmp_path/'etc/systemd/system/nginx.service.d/50-md-next-capacity.conf').exists()
    assert ('systemctl','reload','nginx') in calls


def test_all_update_paths_apply_capacity():
    root=Path(__file__).resolve().parents[2]
    installer=(root/'scripts/install.sh').read_text()
    assert installer.count('python3 "$APP_DIR/scripts/nginx-capacity.py"')==2
    assert "scripts/nginx-capacity.py" in (root/'scripts/update-panel.py').read_text()
