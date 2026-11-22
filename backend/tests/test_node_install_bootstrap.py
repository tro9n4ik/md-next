"""Полная загрузка до запуска и автономное выполнение без SSH-родителя."""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / 'scripts/node-install-bootstrap.sh'


def render(payload):
    return TEMPLATE.read_text(encoding='utf-8').replace('__INSTALLER_BASE64__', base64.b64encode(payload).decode()).replace('__INSTALLER_SHA256__', hashlib.sha256(payload).hexdigest())


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Требуется Linux/Bash')
@pytest.mark.parametrize('failure', [None, 'truncated', 'corrupt', 'busy', 'launch'])
def test_detached_job_and_rejected_incomplete_payload(tmp_path, failure):
    marker = tmp_path / 'finished'
    runtime = tmp_path / 'runtime'
    commands = tmp_path / 'bin'
    commands.mkdir()
    def command(name, body):
        path = commands / name
        path.write_text('#!/bin/bash\n' + body)
        path.chmod(0o700)
    command('id', 'echo 0\n')
    command('stat', 'echo 0\n')
    command('systemctl', 'if [ "$1" = show ]; then echo "${FAKE_STATE:-dead}"; fi\n')
    command('systemd-run', '''if [ "$FAIL_LAUNCH" = 1 ]; then exit 1; fi
while [ "$1" != /bin/bash ]; do shift; done
nohup "$@" </dev/null >/dev/null 2>&1 &
''')
    payload = f'#!/bin/bash\nsleep .3\nprintf done > "{marker}"\n'.encode()
    text = render(payload).replace('/run/systemd/system', str(tmp_path)).replace('/run/md-next-node-installer', str(runtime))
    if failure == 'truncated':
        text = text.split('\nMDNEXT_PAYLOAD', 1)[0]
    if failure == 'corrupt':
        text = text.replace(hashlib.sha256(payload).hexdigest(), '0' * 64)
    result = subprocess.run(['bash'], input=text, text=True, capture_output=True, timeout=10,
        env={**os.environ, 'PATH':str(commands) + os.pathsep + os.environ['PATH'],
             'FAKE_STATE':'running' if failure == 'busy' else 'dead', 'FAIL_LAUNCH':'1' if failure == 'launch' else '0'})
    if failure:
        if failure != 'truncated':
            assert result.returncode != 0
        assert not marker.exists()
        assert not list(runtime.glob('job.*/install.sh'))
    else:
        assert result.returncode == 0, result.stderr
        # Родительский Bash уже завершился; дочернее задание завершает работу.
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.read_text() == 'done'
        deadline = time.monotonic() + 5
        while list(runtime.glob('job.*/install.sh')) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not list(runtime.glob('job.*/install.sh'))


def test_real_systemd_survives_launcher_exit(tmp_path):
    if os.environ.get('MDNEXT_RUN_SYSTEMD_INSTALL_TEST') != '1':
        pytest.skip('Только одноразовая GitHub-hosted VM с systemd')
    assert os.environ.get('GITHUB_ACTIONS') == 'true'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert os.geteuid() == 0 and Path('/proc/1/comm').read_text().strip() == 'systemd'
    root = Path('/run/md-next-node-installer')
    assert not root.exists() and not Path('/opt/md-next/backend/.env').exists()
    marker = tmp_path / 'finished'
    try:
        result = subprocess.run(['bash'], input=render(f'sleep 2\nprintf done > "{marker}"\n'.encode()),
                                text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = subprocess.run(['systemctl','show','md-next-node-install','-p','SubState','--value'], capture_output=True, text=True)
            if state.stdout.strip() == 'exited':
                break
            time.sleep(.1)
        assert marker.read_text() == 'done'
        assert state.stdout.strip() == 'exited'
        assert not list(root.glob('job.*/install.sh')), 'Токен должен удаляться после завершения'
    finally:
        subprocess.run(['systemctl','stop','md-next-node-install'], capture_output=True, timeout=15)
        subprocess.run(['systemctl','reset-failed','md-next-node-install'], capture_output=True, timeout=15)
        if root.exists():
            shutil.rmtree(root)
