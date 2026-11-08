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
    active=subprocess.run(['systemctl','is-active','md-next-migration.service'],capture_output=True,text=True,timeout=5)
    running=active.returncode==0 or active.stdout.strip() in {'activating','reloading','deactivating'}
    if not running:
        # Cleanup does not depend on a readable job.json (launcher may have
        # crashed between saving credentials and publishing the initial state).
        for path in STATE.glob('request-*.json'):
            if not path.is_symlink() and time.time()-path.stat().st_mtime>90:path.unlink()
    try:
        data=json.loads((STATE/'job.json').read_text())
        if not isinstance(data,dict):raise ValueError('Invalid state')
        data={k:data[k] for k in ('phase','stage','message','host','port','action','actor_id','updated_at','error_code','exit_code','history') if k in data}
    except FileNotFoundError:
        data={'phase':'idle','message':'Переезд ещё не запускался.'}
    except (ValueError,TypeError):
        data={'phase':'error','error_code':'MIG_STATE','message':'Не удалось прочитать сохранённое состояние. Проверьте оба сервера.'}
    if data.get('phase') in {'connecting','preparing','rebooting','exporting','restoring','verifying','activating','acknowledging','reconciling','cancelling'} and not running and time.time()-data.get('updated_at',0)>60:
        data.update(phase='error',error_code='MIG_INTERRUPTED',message='Процесс переезда прерван. Проверьте оба сервера перед продолжением.')
    data['source_fenced']=(STATE/'source-fenced.json').exists()
    data['activation_started']=(STATE/'source-activated.json').exists()
    data['managed']=(STATE/'managed-session.json').is_file()
    data['target_ready']=False
    data['can_reconcile']=False
    if (STATE/'target.json').is_file():
        try:
            binding=json.loads((STATE/'target.json').read_text())
            data['target']={key:binding[key] for key in ('host','port','fingerprint')}
            if data['activation_started']:
                activated=json.loads((STATE/'source-activated.json').read_text())
                fields=('session_id','installation','target_commit','target_machine','final_snapshot')
                matched=all(binding.get(key) and activated.get(key)==binding[key] for key in fields)
                data['can_reconcile']=bool(matched and data['managed'] and activated.get('state')!='completed')
                if matched and activated.get('state')=='completed':
                    data.update(phase='activated',stage='completed',message='Новый VPS активирован. Замените IP в DNS-записях ваших доменов и проверьте подключения.')
                    data.pop('error_code',None);data.pop('exit_code',None)
            if (STATE/'target-ready.json').is_file():
                ready=json.loads((STATE/'target-ready.json').read_text())
                data['target_ready']=all(ready.get(key)==binding[key] for key in ('host','port','fingerprint'))
        except (ValueError,KeyError,TypeError,AttributeError):
            data.update(phase='error',error_code='MIG_STATE',message='Повреждена привязка целевого VPS. Проверьте серверы вручную.')
    return data


def start(request):
    module=worker()
    env=Path('/opt/md-next/backend/.env').read_text()
    if 'MDNEXT_MIGRATION_STAGING=1' in env.splitlines() and not (STATE/'source-fenced.json').exists():
        raise RuntimeError('Операции переезда нельзя запускать с изолированного целевого сервера')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE/'control.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        current=subprocess.run(['systemctl','is-active','md-next-migration.service'],capture_output=True,text=True,timeout=5)
        if current.returncode==0 or current.stdout.strip() in {'activating','reloading','deactivating'}:raise module.events.MigrationError('MIG_BUSY')
        if (STATE/'source-activated.json').exists() and request['action']!='reconcile':raise module.events.MigrationError('MIG_STATE')
        if request['action']=='reconcile' and not read_status().get('can_reconcile'):raise module.events.MigrationError('MIG_STATE')
        if request['action']=='prepare' and ((STATE/'source-fenced.json').exists() or (STATE/'target.json').exists()):raise module.events.MigrationError('MIG_STATE')
        if request['action']=='finalize' and not read_status().get('target_ready'):raise module.events.MigrationError('MIG_STATE')
        if request['action']=='commit' and (read_status().get('phase') not in {'prepared','awaiting_activation'} or not read_status().get('target_ready')):raise module.events.MigrationError('MIG_STATE')
        if request['action']=='activate' and read_status().get('phase')!='awaiting_activation':raise module.events.MigrationError('MIG_STATE')
        spec=importlib.util.spec_from_file_location('migration_session',LIB/'migration-session.py')
        session=importlib.util.module_from_spec(spec);spec.loader.exec_module(session)
        request=session.hydrate(request,STATE,module.write_json)
        module.validate(request)
        request['operation_id']=uuid.uuid4().hex
        path=STATE/('request-'+uuid.uuid4().hex+'.json')
        module.write_json(path,request)
        module.write_json(STATE/'job.json',{})
        module.status('connecting','Запускаем проверку SSH.',request)
        try:
            result=subprocess.run(['systemd-run','--quiet','--collect','--unit=md-next-migration',
                '--property=RuntimeMaxSec=10800','--property=KillMode=control-group',
                '/opt/md-next/backend/venv/bin/python','-I',str(LIB/'migration-ssh.py'),str(path)],
                capture_output=True,text=True,timeout=15)
            if result.returncode:raise RuntimeError('Не удалось запустить процесс переезда')
        except Exception:
            path.unlink(missing_ok=True);module.status('error','Не удалось запустить процесс переезда.',request,error_code='MIG_INTERNAL');raise
        return read_status()
