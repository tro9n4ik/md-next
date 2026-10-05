import asyncio
import base64
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text, delete, func, cast, Integer, case
from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.operations import NodeSample, TelegramLink
from app.models.client import Client
from app.models.event import Event
from app.models.setting import Setting
from app.services.client_limits import utc, limit_info, refresh_period
from app.services.events import log_event
from app.services.diagnostics import run_diagnostics
from app.services.templates import SubscriptionTemplate, get_templates
from app.services import backups

router = APIRouter(prefix='/api/v1/operations', tags=['Обслуживание'], dependencies=[Depends(get_current_user)])
restore_lock = asyncio.Lock()
previews = {}


@asynccontextmanager
async def pause_background():
    from app.main import app
    from app.services.watchdog import watchdog
    from app.services.traffic_collector import start_traffic_collector
    from app.services.notifications import notification_loop
    running=watchdog.is_running
    tasks=[]
    if running: await watchdog.stop()
    for name,factory in [('traffic_task',start_traffic_collector),('notification_task',notification_loop)]:
        task=getattr(app.state,name,None)
        if task and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            tasks.append((name,factory))
    try: yield
    finally:
        if running: watchdog.start()
        for name,factory in tasks: setattr(app.state,name,asyncio.create_task(factory()))


@router.post('/diagnostics')
async def diagnostics(db=Depends(get_db)):
    return await run_diagnostics(db)


@router.get('/history/{node_id}')
async def history(node_id:int, hours:int=Query(24,ge=1,le=720), db=Depends(get_db)):
    cutoff=datetime.now(timezone.utc)-timedelta(hours=hours)
    conditions=(NodeSample.node_id==node_id,NodeSample.ts>=cutoff)
    stats=(await db.execute(select(func.count(NodeSample.id),func.sum(cast(NodeSample.healthy,Integer)),
        func.max(case((NodeSample.healthy.is_(False),NodeSample.ts),else_=None))).where(*conditions))).one()
    # Aggregate in SQLite to bound memory independently of the sampling interval.
    bucket=cast((cast(func.strftime('%s',NodeSample.ts),Integer)-int(cutoff.timestamp()))/max(1,hours*3600//480),Integer)
    rows=(await db.execute(select(func.min(NodeSample.ts),func.min(cast(NodeSample.healthy,Integer)),func.max(NodeSample.ping_ms),
        func.max(case((NodeSample.healthy.is_(False),NodeSample.reason),else_=''))).where(*conditions).group_by(bucket).order_by(bucket))).all()
    points=[{'ts':ts,'healthy':bool(healthy),'ping_ms':ping,'reason':reason or 'ok'} for ts,healthy,ping,reason in rows]
    events=(await db.execute(select(Event).where(Event.category.in_(['node','cluster']),Event.ts>=cutoff).order_by(Event.ts.desc()))).scalars().all()
    relevant=[e for e in events if (e.meta or {}).get('node_id')==node_id or (e.meta or {}).get('from_node_id')==node_id]
    return {'points':points,'sample_count':stats[0],'availability':round(100*(stats[1] or 0)/stats[0],2) if stats[0] else None,
        'last_failure':stats[2],'events':[{'ts':e.ts,'message':e.message,'reason':(e.meta or {}).get('reason')} for e in relevant[:50]]}


@router.get('/templates')
async def templates(db=Depends(get_db)):
    return await get_templates(db)


class TemplateRequest(BaseModel):
    templates:list[SubscriptionTemplate]=Field(max_length=20)


@router.put('/templates')
async def save_templates(request:TemplateRequest,db=Depends(get_db)):
    if len({t.id for t in request.templates})!=len(request.templates):
        raise HTTPException(422,'Идентификаторы шаблонов должны быть уникальными')
    value=json.dumps([t.model_dump() for t in request.templates],ensure_ascii=False)
    row=await db.get(Setting,'subscription.templates')
    if row: row.value=value
    else: db.add(Setting(key='subscription.templates',value=value))
    await db.commit()
    log_event('info','settings','Шаблоны подписок изменены')
    return request.templates


@router.post('/clients/{client_id}/extend')
async def extend(client_id:int,db=Depends(get_db)):
    client=await db.get(Client,client_id)
    if not client: raise HTTPException(404,'Клиент не найден')
    from app.api.clients import update_client,ClientUpdate
    previous=utc(client.expires_at) if client.expires_at else datetime.now(timezone.utc)
    expires=max(previous,datetime.now(timezone.utc))+timedelta(days=30)
    return await update_client(client_id,ClientUpdate(expires_at=expires),db)


@router.post('/clients/{client_id}/reset')
async def reset(client_id:int,db=Depends(get_db)):
    from app.api.clients import _sync_protocols
    from app.services.client_service import ClientService
    client=await db.get(Client,client_id)
    if not client: raise HTTPException(404,'Клиент не найден')
    refresh_period(client)
    client.monthly_traffic_up=client.monthly_traffic_down=0
    try:
        await _sync_protocols(db); await db.commit()
    except Exception:
        await db.rollback();await ClientService.restore_committed_configs(db)
        raise HTTPException(502,'Не удалось применить сброс трафика') from None
    log_event('info','client','Сброшен месячный расход трафика',{'client_id':client_id})
    return limit_info(client)


@router.post('/clients/{client_id}/telegram-code')
async def telegram_code(client_id:int,db=Depends(get_db)):
    if not await db.get(Client,client_id): raise HTTPException(404,'Клиент не найден')
    row=await db.get(TelegramLink,client_id)
    if not row: row=TelegramLink(client_id=client_id);db.add(row)
    code=secrets.token_urlsafe(18)
    row.code_hash=hashlib.sha256(code.encode()).hexdigest()
    row.expires_at=datetime.now(timezone.utc)+timedelta(minutes=15)
    await db.commit()
    log_event('info','client','Создан одноразовый код привязки Telegram',{'client_id':client_id})
    return {'code':code,'expires_at':row.expires_at,'instruction':'Отправьте боту /bind '+code}


@router.delete('/clients/{client_id}/telegram-link')
async def unlink(client_id:int,db=Depends(get_db)):
    await db.execute(delete(TelegramLink).where(TelegramLink.client_id==client_id));await db.commit()
    log_event('info','client','Привязка Telegram отозвана',{'client_id':client_id})
    return {'ok':True}


class PasswordRequest(BaseModel):
    password:str=Field(min_length=12,max_length=256)


@router.get('/backups')
async def backup_list():
    return await asyncio.to_thread(backups.list_backups)


@router.post('/backups')
async def backup_create(request:PasswordRequest):
    async with restore_lock:
        result=await asyncio.to_thread(backups.create_backup,request.password)
    log_event('info','backup','Создана зашифрованная резервная копия')
    return result


@router.get('/backups/{name}')
async def backup_download(name:str):
    if not any(b['name']==name for b in backups.list_backups()): raise HTTPException(404,'Копия не найдена')
    return FileResponse(backups.BACKUP_DIR/name,filename=name,media_type='application/octet-stream',headers={'Cache-Control':'no-store'})


class PreviewRequest(PasswordRequest):
    data:str=Field(max_length=28*1024*1024)


@router.post('/restore/preview')
async def restore_preview(request:PreviewRequest, db=Depends(get_db),user=Depends(get_current_user)):
    try:
        blob=base64.b64decode(request.data,validate=True)
        data=await asyncio.to_thread(backups.read_backup,blob,request.password)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    # Only retain a bounded number of short-lived previews, owned by the authenticated administrator.
    for key,v in list(previews.items()):
        if v['expires']<time.monotonic(): previews.pop(key,None)
    if len(previews)>=5: raise HTTPException(429,'Слишком много подготовленных восстановлений')
    counts=[]
    current_data=await asyncio.to_thread(backups.export_data)
    for table,rows in data['tables'].items():
        current=(await db.execute(text(f'SELECT COUNT(*) FROM {table}'))).scalar()
        key='key' if table=='settings' else 'id'
        before={str(r[key]):r for r in current_data['tables'][table]}
        after={str(r[key]):r for r in rows}
        counts.append({'table':table,'current':current,'backup':len(rows),
            'added':len(after.keys()-before.keys()),'removed':len(before.keys()-after.keys()),
            'changed':sum(before[k]!=after[k] for k in before.keys()&after.keys())})
    token=secrets.token_urlsafe(24)
    previews[token]={'data':data,'owner':user.id,'expires':time.monotonic()+300}
    return {'token':token,'created_at':data['created_at'],'tables':counts,
        'notice':'Будут заменены клиенты, профили, ноды, правила и настройки. Учётная запись администратора и история событий сохранятся. Копия применима только к этой установке.'}


class RestoreRequest(PasswordRequest):
    token:str=Field(max_length=128)
    confirm:bool


@router.post('/restore')
async def restore(request:RestoreRequest,db=Depends(get_db),user=Depends(get_current_user)):
    preview=previews.get(request.token)
    if not request.confirm or not preview or preview['expires']<time.monotonic() or preview['owner']!=user.id:
        raise HTTPException(409,'Подтверждение устарело. Загрузите копию снова')
    from app.api.clients import _sync_protocols
    from app.services.client_service import ClientService
    async with restore_lock, pause_background():
        preview=previews.pop(request.token,None)
        if not preview: raise HTTPException(409,'Копия уже применена')
        rollback=await asyncio.to_thread(backups.create_backup,request.password)
        from app.services.profiles import get_profile_settings
        from app.services.nginx import apply_xhttp_tls_path
        old_settings=await get_profile_settings(db)
        nginx_applied=False
        try:
            # Clear bindings rather than reviving stale one-time codes from a backup.
            await db.execute(text('DELETE FROM telegram_links'))
            for table in reversed(backups.TABLES): await db.execute(text(f'DELETE FROM {table}'))
            for table in backups.TABLES:
                for row in preview['data']['tables'][table]:
                    columns=list(row)
                    stmt=text(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(':'+k for k in columns)})")
                    await db.execute(stmt,row)
            await db.flush()
            new_settings=await get_profile_settings(db)
            if new_settings['profiles.path.vless_xhttp_tls']!=old_settings['profiles.path.vless_xhttp_tls']:
                await apply_xhttp_tls_path(new_settings['profiles.path.vless_xhttp_tls']);nginx_applied=True
            await _sync_protocols(db);await db.commit()
        except Exception:
            await db.rollback()
            if nginx_applied: await apply_xhttp_tls_path(old_settings['profiles.path.vless_xhttp_tls'])
            await ClientService.restore_committed_configs(db)
            raise HTTPException(502,'Восстановление не применено; текущие данные сохранены') from None
    # Reload Telegram transport after settings restoration.
    from app.services.telegram_settings import get_telegram_settings_from_db,resolve_telegram_proxy
    from app.bot.bot import bot_manager
    settings=await get_telegram_settings_from_db()
    notice=''
    try: await bot_manager.start(settings['token'],await resolve_telegram_proxy(db,settings))
    except Exception:
        notice='Данные восстановлены; проверьте настройки подключения Telegram.'
        log_event('warning','telegram','После восстановления требуется проверить подключение Telegram')
    log_event('warning','backup','Восстановлена резервная копия',{'rollback_copy':rollback['name']})
    return {'ok':True,'rollback_copy':rollback['name'],'notice':notice}
