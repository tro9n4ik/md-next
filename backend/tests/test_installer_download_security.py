"""A failed download or altered installer must never execute remote code."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Linux Bash installer check')
@pytest.mark.parametrize('script,function', [('install.sh', 'install_xray'), ('join-node.sh', 'install_xray_node')])
@pytest.mark.parametrize('failure', ['download', 'checksum'])
def test_xray_download_fails_closed(tmp_path, script, function, failure):
    source = (Path(__file__).resolve().parents[2] / 'scripts' / script).read_text(encoding='utf-8')
    declaration = source.split(f'{function}() {{', 1)[1].split('\n}', 1)[0]
    marker = tmp_path / 'executed'
    mock_download = '''
curl() {
    if [ "$FAILURE" = download ]; then return 22; fi
    while [ "$#" -gt 0 ]; do
        if [ "$1" = -o ]; then
            printf 'touch "%s"\n' "$MARKER" > "$2"
            return 0
        fi
        shift
    done
    return 1
}
'''
    command = f'{mock_download}\n{function}() {{{declaration}\n}}\n{function}\n'
    env = {**os.environ, 'MARKER': str(marker), 'FAILURE': failure, 'TMPDIR': str(tmp_path)}
    result = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert not marker.exists()
    assert not list(tmp_path.iterdir()), 'Downloaded temporary files must be removed'
