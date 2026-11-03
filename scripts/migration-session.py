"""Секреты мастера доступны только root; в API передаётся лишь признак сессии."""
import json
import os
from pathlib import Path
import secrets
import stat


def read(path):
    if path.is_symlink():raise ValueError('Ссылка в хранилище сессии')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:
            raise ValueError('Небезопасное хранилище сессии')
        return json.load(stream)


def hydrate(request,state,write):
    request=dict(request)
    if request.get('password'):return request
    path=state/'managed-session.json'
    saved=read(path) if path.exists() else None
    if request['action']=='prepare':
        if saved and saved.get('host')==request.get('host') and saved.get('port')==request.get('port') and saved.get('fingerprint')!=request.get('fingerprint'):
            raise ValueError('SSH-ключ изменился. Нужна независимая проверка сервера')
        if saved and any(saved.get(k)!=request.get(k) for k in ('host','port','fingerprint')):
            if (state/'target.json').exists():raise ValueError('Сначала отмените предыдущую попытку')
            saved=None
        if not saved:
            if not request.get('ssh_password'):raise ValueError('Введите SSH-пароль')
            saved={k:request[k] for k in ('host','port','fingerprint','ssh_password')}
            saved['password']=secrets.token_urlsafe(32)
            saved['id']=secrets.token_hex(16)
        elif request.get('ssh_password'):saved['ssh_password']=request['ssh_password']
        write(path,saved)
    elif not saved:
        raise ValueError('Старая попытка требует прежнего пароля комплекта или ручного восстановления')
    for key in ('host','port','fingerprint','password','ssh_password'):
        if request.get(key) and request[key]!=saved[key] and key!='ssh_password':raise ValueError('Привязка сессии не совпадает')
        request[key]=saved[key]
    request['managed']=True
    return request


def finish(state,write):
    path=state/'managed-session.json'
    if path.exists():
        saved=read(path)
        identifier=saved.get('id') or secrets.token_hex(16)
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Повреждён идентификатор сессии')
        write(state/('recovery-key-'+identifier+'.json'),{'password':saved['password']})
        write(state/'recovery-key.json',{'password':saved['password']})
        path.unlink()
