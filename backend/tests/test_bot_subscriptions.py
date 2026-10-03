from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from aiogram.types import Message, Chat, User

from app.bot import subscriptions as sub
from app.db.database import AsyncSessionLocal
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.client_limits import utc


def callback(draft, command, *, actor=42, message_id=None):
    return SimpleNamespace(data=f"sub:w:{draft.token}:{draft.revision}:{command}", from_user=SimpleNamespace(id=actor),
        message=SimpleNamespace(chat=SimpleNamespace(id=42), message_id=message_id or draft.message,
                                edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock())


@pytest.fixture(autouse=True)
def clear_drafts():
    sub.drafts.clear()
    yield
    sub.drafts.clear()


@pytest.mark.parametrize('step,value', [('name',' '),('name','x'*65),('phone','not a number'),('email','a@b'),('email','@a.b'),('date','01.01.2000'),('quota_custom','nan'),('quota_custom','Infinity'),('quota_custom','-1'),('quota_custom','0'),('quota_custom','8388608')])
def test_invalid_fields(step,value):
    with pytest.raises(ValueError):sub.validate_text(step,value)


def test_fields_and_html_escape():
    assert sub.validate_text('quota_custom','25,5') == int(25.5*1024**3)
    draft=sub.Draft(42,42,1,step='review',values={'name':'<Админ>','phone':'+7 999','email':'a@b.c','subscription_period':'month','monthly_traffic_limit':0})
    assert '&lt;Админ&gt;' in sub.review_text(draft)
    assert 'Без ограничений' in sub.review_text(draft)


@pytest.mark.asyncio
async def test_stale_skip_does_not_skip_next_field():
    draft=sub.Draft(42,42,1,step='phone',values={'name':'Иван'})
    sub.drafts[(42,42)]=draft
    cb=callback(draft,'skip')
    await sub.callback_input(cb)
    assert draft.step=='email' and draft.values['phone']==''
    await sub.callback_input(cb)
    assert draft.step=='email' and 'email' not in draft.values
    assert cb.answer.call_args.kwargs['show_alert']


def test_ticket_checks_actor_message_and_expiration(monkeypatch):
    draft=sub.Draft(42,42,1)
    sub.drafts[(42,42)]=draft
    assert sub.matching_draft(callback(draft,'cancel',actor=99))[0] is None
    assert sub.matching_draft(callback(draft,'cancel',message_id=2))[0] is None
    monkeypatch.setattr(sub.time,'monotonic',lambda:draft.expires+1)
    assert sub.matching_draft(callback(draft,'cancel'))[0] is None


@pytest.mark.asyncio
async def test_confirmation_one_use_and_contacts_and_limits_committed_together():
    async with AsyncSessionLocal() as db:
        db.add(Setting(key='profiles.enabled',value='["vless_reality_tcp","awg"]'))
        await db.commit()
    expiry=datetime.now(timezone.utc)+timedelta(days=30)
    draft=sub.Draft(42,42,1,step='review',values={'name':'Иван','phone':'+79991234567','email':'ivan@example.com',
        'subscription_period':'custom','expires_at':expiry,'monthly_traffic_limit':100*1024**3})
    sub.drafts[(42,42)]=draft
    cb=callback(draft,'confirm')
    from app.api import clients
    seen=[]
    async def check_before_sync(db):
        client=(await db.execute(select(Client).where(Client.name=='Иван'))).scalar_one()
        seen.append((client.phone,client.email,utc(client.expires_at),client.monthly_traffic_limit))
        assert client.traffic_period_start==client.created_at
    with patch.object(clients,'_sync_protocols',side_effect=check_before_sync), patch.object(sub,'issue_access',new_callable=AsyncMock), patch('app.services.awg.AWGService.generate_keypair',return_value=('private','public')):
        await sub.callback_input(cb)
        await sub.callback_input(cb)
    assert seen==[('+79991234567','ivan@example.com',expiry,100*1024**3)]
    async with AsyncSessionLocal() as db:
        assert len((await db.execute(select(Client).where(Client.name=='Иван'))).scalars().all())==1
        assert {p.kind for p in (await db.execute(select(ClientProfile))).scalars()} == {'vless_reality_tcp','awg'}
    assert cb.answer.call_args.kwargs['show_alert']


@pytest.mark.asyncio
async def test_failed_apply_does_not_leave_subscription():
    draft=sub.Draft(42,42,1,step='review',values={'name':'Ошибка','subscription_period':'month','monthly_traffic_limit':0})
    message=SimpleNamespace(answer=AsyncMock(),edit_text=AsyncMock())
    from app.api import clients
    with patch.object(clients,'_sync_protocols',side_effect=RuntimeError('apply failed')), patch.object(clients.ClientService,'restore_committed_configs',new_callable=AsyncMock) as restore:
        await sub.create_subscription(message,draft)
        restore.assert_awaited_once()
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(Client).where(Client.name=='Ошибка'))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_registered_text_filter_accepts_only_active_draft():
    from app.bot.handlers import router
    handler=next(h for h in router.message.handlers if h.callback is sub.text_input)
    message=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=42,type='private'),from_user=User(id=42,is_bot=False,first_name='Админ'),text='Иван')
    ok,_=await handler.check(message)
    assert not ok
    sub.drafts[(42,42)]=sub.Draft(42,42,1)
    ok,_=await handler.check(message)
    assert ok


@pytest.mark.asyncio
async def test_complete_wizard_and_edit_back_to_review():
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),message_id=1,edit_text=AsyncMock(),answer=AsyncMock())
    await sub.start(screen,42,edit=True)
    draft=sub.drafts[(42,42)]
    async def text(value):
        draft.message+=1
        next_screen=SimpleNamespace(chat=screen.chat,message_id=draft.message,edit_text=AsyncMock())
        message=SimpleNamespace(text=value,from_user=SimpleNamespace(id=42),chat=screen.chat,answer=AsyncMock(return_value=next_screen))
        await sub.text_input(message)
    await text('Иван')
    assert draft.step=='phone'
    await sub.callback_input(callback(draft,'skip'))
    await text('ivan@example.com')
    await sub.callback_input(callback(draft,'period_month'))
    await sub.callback_input(callback(draft,'quota_0'))
    assert draft.step=='review' and draft.values['monthly_traffic_limit']==0
    await sub.callback_input(callback(draft,'edit_quota'))
    await sub.callback_input(callback(draft,'quota_custom'))
    await text('25,5')
    assert draft.step=='review' and draft.values['monthly_traffic_limit']==int(25.5*1024**3)
    assert draft.values['name']=='Иван' and draft.values['phone']==''
    assert len(callback(draft,'confirm').data.encode())<64
