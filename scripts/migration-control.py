"""Finite privileged job launcher; commands and filesystem paths are fixed."""
try:
    import fcntl
except ImportError:
    fcntl = None
import importlib.util
import json
from pathlib import Path
import subprocess
import time
import uuid

STATE=Path('/var/lib/md-next/migration')
LIB=Path('/usr/local/lib/md-next')


def worker():
    spec=importlib.util.spec_from_file_location('migration_ssh',LIB/'migration-ssh.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def read_status():
    try:
        data=json.loads((STATE/'job.json').read_text())
        data={k:data[k] for k in ('phase','message','host','action','actor_id','updated_at') if k in data}
        active=subprocess.run(['systemctl','is-active','md-next-migration.service'],capture_output=True,text=True,timeout=5)
        if active.returncode != 0:
            for path in STATE.glob('request-*.json'):
                if not path.is_symlink() and time.time()-path.stat().st_mtime>90:path.unlink()
        if data.get('phase') in {'connecting','preparing','exporting','restoring','verifying'} and active.returncode!=0 and time.time()-data.get('updated_at',0)>60:
            data['phase']='error';data['message']='Процесс переезда прерван. Проверьте оба сервера перед продолжением.'
        data['source_fenced']=(STATE/'source-fenced.json').exists()
        data['activation_started']=(STATE/'source-activated.json').exists()
        if (STATE/'target.json').is_file():
            binding=json.loads((STATE/'target.json').read_text())
            data['target']={key:binding[key] for key in ('host','port','fingerprint')}
        return data
    except (FileNotFoundError,ValueError):return {'phase':'idle','message':'Переезд ещё не запускался.','source_fenced':(STATE/'source-fenced.json').exists(),'activation_started':(STATE/'source-activated.json').exists()}


def start(request):
    module=worker();module.validate(request)
    env=Path('/opt/md-next/backend/.env').read_text()
    if 'MDNEXT_MIGRATION_STAGING=1' in env and not (STATE/'source-fenced.json').exists():
        raise RuntimeError('Операции переезда нельзя запускать с изолированного целевого сервера')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE/'control.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        current=subprocess.run(['systemctl','is-active','md-next-migration.service'],capture_output=True,text=True,timeout=5)
        if current.returncode==0 or current.stdout.strip()=='activating':raise RuntimeError('Переезд уже выполняется')
        if (STATE/'source-activated.json').exists():raise RuntimeError('После активации исходная панель заблокирована для новых операций')
        if request['action']=='prepare' and (STATE/'source-fenced.json').exists():raise RuntimeError('Исходная панель уже изолирована')
        if request['action']=='prepare' and (STATE/'target.json').exists():raise RuntimeError('Сначала отмените предыдущую подготовку')
        path=STATE/('request-'+uuid.uuid4().hex+'.json')
        module.write_json(path,request)
        module.status('connecting','Запускаем проверку SSH.',request)
        try:
            result=subprocess.run(['systemd-run','--quiet','--collect','--unit=md-next-migration',
                '--property=RuntimeMaxSec=3600','--property=KillMode=control-group',
                '/opt/md-next/backend/venv/bin/python','-I',str(LIB/'migration-ssh.py'),str(path)],
                capture_output=True,text=True,timeout=15)
            if result.returncode:raise RuntimeError('Не удалось запустить процесс переезда')
        except Exception:
            path.unlink(missing_ok=True);module.status('error','Не удалось запустить процесс переезда.',request);raise
        return read_status()
