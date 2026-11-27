from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from app.bot import admin_console as console


def message():
    return SimpleNamespace(chat=SimpleNamespace(id=42),message_id=10)


NODES=[{'id':3,'name':'Польша','country_code':'PL','host':'192.0.2.1','is_enabled':True,'status':'healthy','priority':0},
       {'id':5,'name':'NL','country_code':'NL','host':'192.0.2.2','is_enabled':False,'status':'healthy','priority':1}]


async def fake_api(actor,chat,key,params=None,*args):
    if key=='nodes':return NODES
    if key=='route':return {'active_node':{'id':3}}
    if key=='node':return next(n for n in NODES if n['id']==params['node_id'])
    return {}


@pytest.mark.asyncio
async def test_nodes_section_opens_list_directly_with_flags_and_inline_actions():
    with patch.object(console.api,'call',side_effect=fake_api),patch.object(console,'draw',AsyncMock()) as draw:
        await console.section(message(),42,'nodes')
        title,text,rows=draw.call_args.args[2:]
        assert title=='Ноды' and text=='Всего: 2'
        assert '🇵🇱 Польша' in rows[0][0][0]
        assert [b[0] for b in rows[1]]==['● Активна','Приостановить','Удалить']
        assert '🇳🇱 NL' in rows[2][0][0] and rows[3][1][0]=='Включить'
        assert '192.0.2' not in str(rows) and 'healthy' not in str(rows)


@pytest.mark.asyncio
async def test_node_delete_has_one_confirmation_with_node_name():
    with patch.object(console.api,'call',side_effect=fake_api),patch.object(console,'review',AsyncMock()) as review:
        await console.node_action(message(),42,'node_delete',3,None,0)
        draft=review.call_args.args[2]
        assert draft.params=={'node_id':3} and draft.context=='🇵🇱 Польша'
    console.drafts.clear()


@pytest.mark.asyncio
async def test_enabling_paused_node_does_not_add_confirmation():
    with patch.object(console.api,'call',side_effect=fake_api),patch.object(console,'safe_execute',AsyncMock()) as execute,patch.object(console,'review',AsyncMock()) as review:
        await console.node_action(message(),42,'node_toggle',5,True,0)
        execute.assert_awaited_once();review.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_pause_button_is_idempotent():
    with patch.object(console.api,'call',side_effect=fake_api),patch.object(console,'nodes_list',AsyncMock()) as listing,patch.object(console,'safe_execute',AsyncMock()) as execute:
        await console.node_action(message(),42,'node_toggle',5,False,0)
        listing.assert_awaited_once();execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_raw_nodes_result_cannot_show_technical_dump():
    with patch.object(console,'nodes_list',AsyncMock()) as listing:
        await console.result_screen(message(),42,'nodes',NODES)
        listing.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_shortcut_opens_nodes_without_intermediate_menu():
    event=SimpleNamespace(data='adm:nodes',message=message(),from_user=SimpleNamespace(id=42),answer=AsyncMock())
    with patch.object(console.api,'authorize',AsyncMock()),patch.object(console,'nodes_list',AsyncMock()) as listing:
        await console.callback(event)
        listing.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_load_on_shortcut_has_short_retry_screen():
    event=SimpleNamespace(data='adm:nodes',message=message(),from_user=SimpleNamespace(id=42),answer=AsyncMock())
    with patch.object(console.api,'authorize',AsyncMock()),patch.object(console,'nodes_list',AsyncMock(side_effect=RuntimeError('internal-secret'))),patch.object(console,'draw',AsyncMock()) as draw:
        await console.callback(event)
        assert 'internal-secret' not in str(draw.call_args)
        assert draw.call_args.args[4][0][0][0]=='Обновить'
