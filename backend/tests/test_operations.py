import base64
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.models.operations import TelegramLink,NodeSample
from app.models.event import Event
from app.services import backups
from app.services.notifications import reminder_candidates
from app.bot import portal,admin_tools
from app.api import operations


@pytest.mark.asyncio
async def test_operations_require_auth():
    async with AsyncClient(app=app,base_url='http://test') as http:
        for path in ('templates','backups','history/1'):
            response=await http.get('/api/v1/operations/'+path)
            assert response.status_code in (401,403)


@pytest.mark.asyncio
async def test_templates_validate_unique_and_limits(auth_headers):
    async with AsyncClient(app=app,base_url='http://test') as http:
        data={'templates':[{'id':'month','name':'Test','period':'month','monthly_traffic_limit':1024}]}
        assert (await http.put('/api/v1/operations/templates',json=data,headers=auth_headers)).status_code==200
        result=(await http.get('/api/v1/operations/templates',headers=auth_headers)).json()
        assert result[0]['monthly_traffic_limit']==1024
        data['templates']*=2
        assert (await http.put('/api/v1/operations/templates',json=data,headers=auth_headers)).status_code==422


@pytest.mark.asyncio
async def test_code_is_one_time_and_actor_scoped(auth_headers):
    async with AsyncSessionLocal() as db:
        db.add_all([Client(id=1,name='One',phone='',email=''),Client(id=2,name='Two',phone='',email='')]);await db.commit()
    async with AsyncClient(app=app,base_url='http://test') as http:
        result=await http.post('/api/v1/operations/clients/1/telegram-code',headers=auth_headers)
        code=result.json()['code']
    portal.attempts.clear()
    message=SimpleNamespace(from_user=SimpleNamespace(id=123),chat=SimpleNamespace(id=123),text='/bind '+code,answer=AsyncMock())
    with patch.object(portal,'show_account',new_callable=AsyncMock):
        await portal.bind(message)
        other=SimpleNamespace(from_user=SimpleNamespace(id=456),chat=SimpleNamespace(id=456),text='/bind '+code,answer=AsyncMock())
        await portal.bind(other)
    async with AsyncSessionLocal() as db:
        assert (await portal.own_client(db,123)).id==1
        assert await portal.own_client(db,456) is None
        row=await db.get(TelegramLink,1)
        assert row.code_hash is None
    assert 'неверен' in other.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_portal_denies_access_when_subscription_disabled():
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='One',phone='',email='',is_active=False));db.add(TelegramLink(client_id=1,telegram_id='123'));await db.commit()
    cb=SimpleNamespace(data='mine:access',from_user=SimpleNamespace(id=123),answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()))
    await portal.mine(cb)
    assert 'недоступна' in cb.message.answer.call_args.args[0]


def test_encrypted_backup_tampering_and_instance(tmp_path,monkeypatch):
    payload={'format':1,'revision':'test','instance':backups.instance_id(),'created_at':'now','tables':{t:[] for t in backups.TABLES}}
    monkeypatch.setattr(backups,'export_data',lambda:payload)
    monkeypatch.setattr(backups,'BACKUP_DIR',tmp_path)
    meta=backups.create_backup('a-long-backup-password')
    blob=(tmp_path/meta['name']).read_bytes()
    assert b'created_at' not in blob and b'instance' not in blob
    with pytest.raises(ValueError,match='Пароль'):
        backups.read_backup(blob,'wrong-but-long-password')
    changed=blob[:-5]+b'ABCDE'
    with pytest.raises(ValueError):backups.read_backup(changed,'a-long-backup-password')
    monkeypatch.setenv('JWT_SECRET_KEY','another-installation')
    with pytest.raises(ValueError,match='другой установки'):
        backups.read_backup(blob,'a-long-backup-password')


def test_reminder_thresholds_and_paused_subscription():
    now=datetime.now(timezone.utc)
    c=Client(name='One',phone='',email='',is_active=True,created_at=now-timedelta(days=1),expires_at=now+timedelta(hours=20),monthly_traffic_limit=100,monthly_traffic_up=85,monthly_traffic_down=0,traffic_period_start=now-timedelta(days=1))
    candidates=reminder_candidates(c,now)
    assert any('1d' in key for key,_ in candidates)
    assert any('quota:' in key and key.endswith(':80') for key,_ in candidates)
    c.is_active=False
    assert reminder_candidates(c,now)==[]


@pytest.mark.asyncio
async def test_history_keeps_failure_during_downsample(auth_headers):
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        db.add_all([NodeSample(node_id=1,ts=now-timedelta(seconds=i),healthy=i!=250,ping_ms=10,reason='port' if i==250 else 'ok') for i in range(600)])
        db.add(Event(category='cluster',level='warning',message='Switch',meta={'from_node_id':1,'node_id':2,'reason':'node_unhealthy'}));await db.commit()
    async with AsyncClient(app=app,base_url='http://test') as http:
        data=(await http.get('/api/v1/operations/history/1',headers=auth_headers)).json()
    assert data['sample_count']==600 and len(data['points'])<=480
    assert any(not p['healthy'] for p in data['points'])
    assert data['events'][0]['reason']=='node_unhealthy'


@pytest.mark.asyncio
async def test_event_filters_by_related_node_and_client(auth_headers):
    async with AsyncSessionLocal() as db:
        db.add_all([Event(category='client',level='info',message='One',meta={'client_id':7}),Event(category='cluster',level='warning',message='Two',meta={'from_node_id':3,'node_id':4})]);await db.commit()
    async with AsyncClient(app=app,base_url='http://test') as http:
        a=(await http.get('/api/v1/events?client_id=7',headers=auth_headers)).json()
        b=(await http.get('/api/v1/events?node_id=3',headers=auth_headers)).json()
    assert [e['message'] for e in a]==['One']
    assert [e['message'] for e in b]==['Two']


@pytest.mark.asyncio
async def test_restore_rolls_back_failed_config_and_preserves_admin(auth_headers):
    from app.models.user import User
    from app.models.setting import Setting
    from app.services.client_service import ClientService
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='Original',phone='',email=''))
        db.add(TelegramLink(client_id=1,telegram_id='123'))
        await db.commit()
        admin=(await db.execute(select(User))).scalar_one()
        owner=admin.id
        rows=(await db.execute(select(Client))).scalars().all()
        # SQLite stores ISO timestamps; selected-table insertion intentionally uses SQL bindings.
        client=rows[0]
        copy={col.name:getattr(client,col.name) for col in Client.__table__.columns}
        for key,value in copy.items():
            if isinstance(value,datetime):copy[key]=value.isoformat()
        copy['name']='Restored'
    data={'tables':{'nodes':[],'clients':[copy],'client_profiles':[],'routing_rules':[],'settings':[]}}
    token='restore-test'
    request=operations.RestoreRequest(token=token,password='long-test-password',confirm=True)
    def prepare():operations.previews[token]={'data':data,'owner':owner,'expires':admin_tools.time.monotonic()+300}
    with patch.object(backups,'create_backup',return_value={'name':'rollback.mdbackup'}),patch.object(operations,'get_current_user'):
        prepare()
        with patch('app.api.clients._sync_protocols',new_callable=AsyncMock,side_effect=RuntimeError('broken')),patch.object(ClientService,'restore_committed_configs',new_callable=AsyncMock):
            async with AsyncSessionLocal() as db:
                with pytest.raises(Exception):await operations.restore(request,db,SimpleNamespace(id=owner))
        async with AsyncSessionLocal() as db:
            assert (await db.get(Client,1)).name=='Original'
            assert (await db.get(TelegramLink,1)).telegram_id=='123'
        prepare()
        with patch('app.api.clients._sync_protocols',new_callable=AsyncMock),patch('app.bot.bot.bot_manager.start',new_callable=AsyncMock),patch('app.services.telegram_settings.resolve_telegram_proxy',new_callable=AsyncMock,return_value=''):
            async with AsyncSessionLocal() as db:
                assert (await operations.restore(request,db,SimpleNamespace(id=owner)))['ok']
        async with AsyncSessionLocal() as db:
            assert (await db.get(Client,1)).name=='Restored'
            assert await db.get(TelegramLink,1) is None
            assert (await db.get(User,owner)).username=='admin'
        assert token not in operations.previews


@pytest.mark.asyncio
async def test_admin_confirmation_replay_and_actor_bound():
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='One',phone='',email=''));await db.commit()
    admin_tools.pending.clear()
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),message_id=10,edit_text=AsyncMock(),answer=AsyncMock())
    await admin_tools.prepare(screen,42,1,{'operation':'extend'},'Extend')
    token=next(iter(admin_tools.pending))
    cb=SimpleNamespace(data='ops:confirm:'+token,from_user=SimpleNamespace(id=99),message=screen,answer=AsyncMock())
    await admin_tools.callback(cb)
    assert token in admin_tools.pending
    cb.from_user.id=42
    with patch.object(operations,'extend',new_callable=AsyncMock) as extend,patch.object(admin_tools,'detail',new_callable=AsyncMock):
        await admin_tools.callback(cb)
        await admin_tools.callback(cb)
    assert extend.await_count==1


@pytest.mark.asyncio
async def test_persistent_reminders_do_not_repeat():
    from app.services import notifications
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        db.add(Client(name='One',phone='',email='',is_active=True,created_at=now,expires_at=now+timedelta(hours=1)));await db.commit()
    bot=SimpleNamespace(send_message=AsyncMock())
    with patch('app.bot.bot.get_bot',return_value=bot),patch.object(notifications,'get_telegram_settings_from_db',new_callable=AsyncMock,return_value={'admin_id':42,'notify_quota':True}):
        await notifications.send_reminders();await notifications.send_reminders()
    assert bot.send_message.await_count==1


@pytest.mark.asyncio
async def test_bot_profiles_and_subscription_toggle_change_raw_access():
    from app.models.client import ClientProfile
    from app.models.setting import Setting
    from app.services.profiles import PROFILE_KINDS
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='One',phone='',email='',sub_token='bot-access'))
        db.add(ClientProfile(client_id=1,kind='vless_xhttp_tls',uuid='stable-profile'))
        db.add_all([Setting(key='cdn.enabled',value='true'),Setting(key='cdn.domain',value='cdn.example.com')])
        db.add_all([Setting(key='profiles.enabled.'+kind,value='true' if kind=='vless_xhttp_tls' else 'false') for kind in PROFILE_KINDS])
        await db.commit()
    admin_tools.pending.clear()
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),message_id=10,edit_text=AsyncMock(),answer=AsyncMock())
    cb=SimpleNamespace(data='',from_user=SimpleNamespace(id=42),message=screen,answer=AsyncMock())
    async def perform(action):
        cb.data=action;await admin_tools.callback(cb)
        assert admin_tools.pending
        cb.data='ops:confirm:'+next(iter(admin_tools.pending));await admin_tools.callback(cb)
        assert not admin_tools.pending
    async def raw():
        import base64
        async with AsyncClient(app=app,base_url='http://test') as http:
            response=await http.get('/api/v1/sub/bot-access')
        assert response.status_code==200
        return base64.b64decode(response.text).decode().splitlines()
    with patch('app.api.clients._sync_protocols',new_callable=AsyncMock),patch.object(admin_tools,'profile_menu',new_callable=AsyncMock),patch.object(admin_tools,'detail',new_callable=AsyncMock):
        assert len(await raw())==2
        await perform('ops:profile:1:cdn:0')
        links=await raw();assert len(links)==1 and 'cdn.example.com' not in links[0]
        await perform('ops:toggle:1');assert await raw()==[]
        await perform('ops:toggle:1');assert len(await raw())==1
        await perform('ops:profile:1:cdn:1');assert len(await raw())==2
    async with AsyncSessionLocal() as db:
        assert (await db.get(Client,1)).sub_token=='bot-access'
        assert (await db.get(Client,1)).is_active
        assert (await db.execute(select(ClientProfile).where(ClientProfile.kind=='vless_xhttp_tls'))).scalar_one().uuid=='stable-profile'


@pytest.mark.asyncio
async def test_bot_rejects_confirmation_after_profile_changed_in_panel():
    from app.models.client import ClientProfile
    from app.models.setting import Setting
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='One',phone='',email=''))
        db.add(ClientProfile(client_id=1,kind='vless_xhttp_tls',uuid='same'))
        db.add_all([Setting(key='cdn.enabled',value='true'),Setting(key='cdn.domain',value='cdn.example.com'),Setting(key='client.cdn.1',value='true')]);await db.commit()
    admin_tools.pending.clear()
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),message_id=10,edit_text=AsyncMock(),answer=AsyncMock())
    await admin_tools.prepare(screen,42,1,{'profiles':{'cdn':False}},'Disable CDN')
    token=next(iter(admin_tools.pending))
    async with AsyncSessionLocal() as db:
        (await db.get(Setting,'client.cdn.1')).value='false';await db.commit()
    cb=SimpleNamespace(data='ops:confirm:'+token,from_user=SimpleNamespace(id=42),message=screen,answer=AsyncMock())
    with patch('app.api.clients.update_client_access',new_callable=AsyncMock) as apply:
        await admin_tools.callback(cb)
    apply.assert_not_awaited()
    assert 'уже изменился' in screen.answer.call_args.args[0]
    assert token not in admin_tools.pending


@pytest.mark.asyncio
async def test_subscription_card_exposes_profile_and_pause_controls():
    from app.bot import subscriptions
    async with AsyncSessionLocal() as db:
        db.add(Client(id=1,name='One',phone='',email=''));await db.commit()
    screen=SimpleNamespace(edit_text=AsyncMock())
    await subscriptions.detail(screen,1)
    buttons=[b for row in screen.edit_text.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert any(b.callback_data=='ops:profiles:1' for b in buttons)
    assert any(b.callback_data=='ops:toggle:1' and 'Приостановить' in b.text for b in buttons)
