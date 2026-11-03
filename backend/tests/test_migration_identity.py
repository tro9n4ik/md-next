"""Проверки снижения привилегий перед применением VPN-конфигураций."""
import sys
from types import SimpleNamespace

import pytest

from test_migration_bundle import load_script


def test_verification_changes_groups_before_uid(monkeypatch):
    runtime=load_script('migrate-panel.py');calls=[]
    user=SimpleNamespace(pw_name='md-next',pw_uid=123,pw_gid=456,pw_dir='/var/lib/md-next')
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda name:user))
    for operation in ('initgroups','setgid','setuid'):
        monkeypatch.setattr(runtime.os,operation,lambda *args,op=operation:calls.append((op,args)),raising=False)
    monkeypatch.setenv('HOME','/synthetic-root')
    runtime.drop_to_panel_user()
    assert calls==[('initgroups',('md-next',456)),('setgid',(456,)),('setuid',(123,))]
    assert runtime.os.environ['HOME']=='/var/lib/md-next'


def test_root_panel_account_is_rejected(monkeypatch):
    runtime=load_script('migrate-panel.py')
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_uid=0)))
    monkeypatch.setattr(runtime.os,'initgroups',lambda *a:pytest.fail('Нельзя менять группы'),raising=False)
    with pytest.raises(RuntimeError):runtime.drop_to_panel_user()


def test_failed_group_setup_never_changes_uid(monkeypatch):
    runtime=load_script('migrate-panel.py')
    user=SimpleNamespace(pw_name='md-next',pw_uid=123,pw_gid=456,pw_dir='/var/lib/md-next')
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda name:user))
    def denied(*a):raise PermissionError('synthetic denial')
    monkeypatch.setattr(runtime.os,'initgroups',denied,raising=False)
    monkeypatch.setattr(runtime.os,'setuid',lambda *a:pytest.fail('Нельзя менять uid'),raising=False)
    with pytest.raises(PermissionError):runtime.drop_to_panel_user()
