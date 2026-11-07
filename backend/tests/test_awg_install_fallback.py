"""Негативные сценарии ядра и повторной подготовки без запуска apt на хосте."""
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest


def load(name):
    path=Path(__file__).resolve().parents[2]/'scripts'/name
    spec=importlib.util.spec_from_file_location('test_'+name.replace('-','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def runtime_fixture(tmp_path,monkeypatch,*,prior='',interfaces='',failure='amneziawg-dkms'):
    module=load('install-awg-runtime.py');calls=[];attempted=False
    original_path=module.Path
    monkeypatch.setattr(module,'Path',lambda value:tmp_path/'module' if str(value)=='/sys/module/amneziawg' else tmp_path/'version' if str(value)=='/sys/module/amneziawg/version' else original_path(value))
    (tmp_path/'version').write_text('3.1.20260812')
    def output(*args,**kwargs):
        if args[0]=='uname':return '7.0.0-test'
        if args[:3]==('awg','show','interfaces'):return interfaces
        if args[0]=='dpkg-query':
            if args[-1]=='amneziawg-dkms':return 'install ok half-configured' if attempted else prior
            return ''
        raise AssertionError(args)
    def command(*args,**kwargs):
        nonlocal attempted
        calls.append(args)
        if args[0]=='apt-get' and args[-1]=='amneziawg-dkms' and 'install' in args:
            attempted=True
            if failure:raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(module,'output',output);monkeypatch.setattr(module,'command',command)
    return module,calls


def test_failed_new_dkms_is_removed_before_userspace(tmp_path,monkeypatch):
    module,calls=runtime_fixture(tmp_path,monkeypatch)
    assert module.configure()=='userspace'
    remove=next(i for i,c in enumerate(calls) if 'remove' in c)
    build=next(i for i,c in enumerate(calls) if c[0]=='bash')
    assert remove<build
    assert calls[remove][-1]=='amneziawg-dkms'
    assert 'APT::Get::AutomaticRemove=false' in calls[remove]
    assert all('--no-install-recommends' in c for c in calls if c[0]=='apt-get' and 'install' in c)


def test_existing_dkms_is_never_removed_on_build_failure(tmp_path,monkeypatch):
    module,calls=runtime_fixture(tmp_path,monkeypatch,prior='install ok installed')
    with pytest.raises(RuntimeError,match='сохранён'):module.configure()
    assert not any('remove' in c or c[0]=='bash' for c in calls)


def test_active_interface_blocks_replacement(tmp_path,monkeypatch):
    module,calls=runtime_fixture(tmp_path,monkeypatch,interfaces='awg0')
    with pytest.raises(RuntimeError,match='интерфейсы'):module.configure()
    assert not any('remove' in c or c[0]=='bash' for c in calls)


def test_compatible_kernel_does_not_start_userspace(tmp_path,monkeypatch):
    module,calls=runtime_fixture(tmp_path,monkeypatch,failure=None)
    assert module.configure()=='kernel'
    assert not any('remove' in c or c[0]=='bash' for c in calls)


@pytest.mark.parametrize('source,phase', [('foreign','installing'),('a'*64,'reboot_required'),('a'*64,'updating')])
def test_new_build_refuses_foreign_or_unfinished_system_update(tmp_path,monkeypatch,source,phase):
    module=load('migration-target.py');monkeypatch.setattr(module,'STATE',tmp_path);monkeypatch.setattr(module,'ROOT',tmp_path/'app')
    with pytest.raises(RuntimeError) as error:
        module.check_resume({'commit':'b'*40,'source':source,'phase':phase},'c'*40,'a'*64)
    assert error.value.code=='MIG_TARGET_BINDING'


@pytest.mark.parametrize('restored', [False,True])
def test_changed_build_only_accepts_empty_owned_staging(tmp_path,monkeypatch,restored):
    module=load('migration-target.py');monkeypatch.setattr(module,'STATE',tmp_path);monkeypatch.setattr(module,'ROOT',tmp_path/'app')
    backend=tmp_path/'app/backend';backend.mkdir(parents=True);(backend/'.env').write_text('MDNEXT_MIGRATION_STAGING=1\n')
    with sqlite3.connect(backend/'md_next.db') as db:
        db.execute('create table clients(id integer)')
        if restored:db.execute('insert into clients values(1)')
    previous={'commit':'b'*40,'source':'a'*64,'phase':'installing'}
    if restored:
        with pytest.raises(RuntimeError) as error:module.check_resume(previous,'c'*40,'a'*64)
        assert error.value.code=='MIG_TARGET_OCCUPIED'
    else:module.check_resume(previous,'c'*40,'a'*64)


@pytest.mark.parametrize('interfaces', ['', 'awg0'])
def test_package_recovery_requires_empty_staging_and_no_interfaces(tmp_path,monkeypatch,interfaces):
    module=load('migration-target.py');monkeypatch.setattr(module,'STATE',tmp_path);monkeypatch.setattr(module,'ROOT',tmp_path/'app')
    original=module.Path
    monkeypatch.setattr(module,'Path',lambda value:tmp_path/'no-module' if str(value)=='/sys/module/amneziawg' else original(value))
    calls=[]
    monkeypatch.setattr(module.subprocess,'run',lambda args,**kw:SimpleNamespace(returncode=0,stdout='install ok half-configured' if args[0]=='dpkg-query' else interfaces))
    monkeypatch.setattr(module,'run',lambda *a,**kw:calls.append(a))
    monkeypatch.setattr(module,'emit',lambda code:None)
    if interfaces:
        with pytest.raises(RuntimeError) as error:module.recover_failed_awg({'phase':'installing'})
        assert error.value.code=='MIG_TARGET_OCCUPIED' and not calls
    else:
        module.recover_failed_awg({'phase':'installing'})
        assert len(calls)==1
        assert calls[0][0][-2:]==['amneziawg','amneziawg-dkms']


def test_detached_canonical_database_blocks_retry(tmp_path,monkeypatch):
    module=load('migration-target.py');state=tmp_path/'migration';state.mkdir()
    monkeypatch.setattr(module,'STATE',state);monkeypatch.setattr(module,'ROOT',tmp_path/'absent-app')
    data=tmp_path/'data/md_next.db';data.parent.mkdir()
    with sqlite3.connect(data) as db:
        db.execute('create table clients(id integer)');db.execute('insert into clients values(1)')
    with pytest.raises(RuntimeError) as error:
        module.check_resume({'commit':'b'*40,'source':'a'*64,'phase':'installing'},'c'*40,'a'*64)
    assert error.value.code=='MIG_TARGET_OCCUPIED'
