import errno
import subprocess

import pytest
from app.services import update_errors as errors


def test_oom_is_confirmed_by_journal_and_scoped_to_attempt(tmp_path, monkeypatch):
    log = tmp_path/'update.log'
    log.write_text('tsc -b && vite build\nsecret-token-must-not-leak')
    calls = []
    def output(args):
        calls.append(args)
        return "A process of this unit has been killed by the OOM killer."
    monkeypatch.setattr(errors, 'system_output', output)
    state = errors.enrich_failure({'phase': 'error', 'started_at': 100, 'finished_at': 200}, log)
    assert state['failure']['code'] == 'memory'
    assert 'оперативной памяти' in state['failure']['title']
    assert 'secret-token' not in str(state)
    assert '@100' in calls[0] and '@205' in calls[0]


def test_sigkill_without_oom_evidence_is_not_reported_as_memory_failure():
    failure = errors.classify_failure('frontend_build', 'Killed\nMainThread', error=subprocess.CalledProcessError(-9, ['node']))
    assert failure['code'] == 'build'


@pytest.mark.parametrize('stage,log,error,code', [
    ('frontend_build', 'ENOSPC: no space left on device', None, 'disk'),
    ('backup', '', OSError(errno.ENOSPC, 'synthetic'), 'disk'),
    ('download', '', subprocess.TimeoutExpired(['git'], 10), 'timeout'),
    ('download', 'Could not resolve host: github.com', None, 'network'),
    ('python_dependencies', 'resolution failed', None, 'dependencies'),
    ('migration', 'synthetic DB failure', None, 'migration'),
    ('health', '', None, 'health'),
])
def test_actionable_failure_categories(stage, log, error, code):
    assert errors.classify_failure(stage, log, error=error)['code'] == code


def test_interrupted_worker_is_diagnosed_but_active_update_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(errors.time, 'time', lambda: 1000)
    state = {'phase': 'preparing', 'started_at': 100}
    monkeypatch.setattr(errors, 'system_output', lambda args: 'ActiveState=active')
    assert errors.enrich_failure(state, tmp_path/'missing') == state
    monkeypatch.setattr(errors, 'system_output', lambda args: 'ActiveState=failed' if args[0] == 'systemctl' else "Failed with result 'oom-kill'.")
    result = errors.enrich_failure(state, tmp_path/'missing')
    assert result['phase'] == 'error' and result['failure']['code'] == 'memory'
