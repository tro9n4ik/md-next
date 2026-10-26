import os
from pathlib import Path
import shutil
import subprocess
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
SHA = 'a' * 40


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Bash bootstrap check')
@pytest.mark.parametrize('revision', [SHA, 'main', 'a' * 39, 'bad;command'])
def test_bootstrap_downloads_pinned_archive_and_passes_revision(tmp_path, revision):
    source = (SCRIPTS / 'bootstrap.sh').read_text()
    body = 'work_dir=$(mktemp -d)' + source.split('work_dir=$(mktemp -d)', 1)[1]
    # Проверяем реальный путь загрузки без root, сетевых запросов и установки ОС.
    body = body.replace('</dev/tty', '</dev/null')
    mocks = '''
curl() {
    case "$*" in
      *api.github.com*) printf '%s' "$TEST_REVISION" ;;
      *) printf '%s\n' "$*" >> "$TEST_LOG" ;;
    esac
}
tar() { mkdir -p "$work_dir/md-next-$TEST_REVISION"; }
bash() { printf 'INSTALL_COMMIT=%s\n' "$MDNEXT_INSTALL_COMMIT" >> "$TEST_LOG"; }
'''
    log = tmp_path / 'calls'
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + mocks + body], env={**os.environ, 'TEST_REVISION': revision, 'TEST_LOG': str(log)}, capture_output=True, text=True, timeout=10)
    if revision == SHA:
        assert result.returncode == 0, result.stderr
        text = log.read_text()
        assert f'/archive/{SHA}.tar.gz' in text
        assert f'INSTALL_COMMIT={SHA}' in text
        assert '/refs/heads/main.tar.gz' not in text
    else:
        assert result.returncode != 0
        assert not log.exists(), 'Invalid revision must not reach archive download/installer'


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Bash installer check')
@pytest.mark.parametrize('revision', [SHA, '', 'main'])
def test_installer_records_readable_exact_metadata_or_removes_stale_marker(tmp_path, revision):
    source = (SCRIPTS / 'install.sh').read_text()
    function = 'record_install_commit() {' + source.split('record_install_commit() {', 1)[1].split('\n}', 1)[0] + '\n}'
    for name in ('DEPLOYED_COMMIT', 'FRONTEND_DEPLOYED_COMMIT'):
        (tmp_path / name).write_text('stale')
    # resolve_install_commit не принимает неполный или произвольный идентификатор.
    resolve = 'resolve_install_commit() {' + source.split('resolve_install_commit() {', 1)[1].split('\n}', 1)[0] + '\n}'
    command = 'set -e\numask 077\n' + resolve + '\n' + function + '\nresolve_install_commit "$APP_DIR"\nrecord_install_commit\n'
    result = subprocess.run(['bash', '-c', command], env={**os.environ, 'APP_DIR': str(tmp_path), 'MDNEXT_INSTALL_COMMIT': revision}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for name in ('DEPLOYED_COMMIT', 'FRONTEND_DEPLOYED_COMMIT'):
        marker = tmp_path / name
        if revision == SHA:
            assert marker.read_text() == SHA + '\n'
            assert marker.stat().st_mode & 0o777 == 0o644
        else:
            assert not marker.exists()
