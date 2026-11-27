from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from app.bot import admin_compact as compact,admin_console as console
from app.bot.admin_catalog import OPERATIONS

def message():return SimpleNamespace(chat=SimpleNamespace(id=42),message_id=10)


@pytest.fixture(autouse=True)
def reset():
    console.drafts.clear();console.buttons.clear()
    yield
    console.drafts.clear();console.buttons.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize('section,key',list(compact.ENTRY.items()))
async def test_sections_open_content_directly(section,key):
    with patch.object(compact,'open_view',AsyncMock()) as view:
        assert await compact.section(console,message(),42,section)
        assert view.call_args.args[-1]==key


@pytest.mark.asyncio
@pytest.mark.parametrize('key',[k for k,v in OPERATIONS.items() if v.section!='nodes'])
async def test_every_operation_has_bounded_non_json_result_and_does_not_leak_unknown_fields(key):
    value={'message':'Готово','secret':'DO_NOT_SHOW','password':'DO_NOT_SHOW',
           'some_nested':{'private_key':'DO_NOT_SHOW'},'token':'DO_NOT_SHOW'}
    if key in {'users','rules','backups','templates','migration_packages','events'}:value=[]
    with patch.object(console,'draw',AsyncMock()) as draw:
        assert await compact.present(console,message(),42,key,value)
        args=draw.call_args.args
        assert len(args[3])<=1700
        assert 'DO_NOT_SHOW' not in args[3]
        assert not any(term in args[3] for term in ('True','False','some_nested','private_key'))
        assert len(args[4])<=14


@pytest.mark.asyncio
async def test_users_have_actions_under_name_without_id_or_permissions_dump():
    users=[{'id':9,'username':'Иван','is_active':True,'can_issue_subscriptions':True}]
    with patch.object(console,'draw',AsyncMock()) as draw:
        await compact.present(console,message(),42,'users',users)
        rows=draw.call_args.args[4]
        assert 'Иван' in rows[0][0][0]
        assert [b[0] for b in rows[1]]==['Права','Пауза']
        assert rows[1][1][2]['values']=={'is_active':False}
        assert rows[1][0][2]['params']=={'user_id':9}


@pytest.mark.asyncio
async def test_protocol_toggle_only_changes_selected_profile_and_keeps_confirmation():
    schema,root=console.api.body_schema('protocols_edit')
    current={'enabled':{'awg':True,'mtproto':True},'ports':{'mtproto':8445}}
    data=compact.bound('Выключить','protocols_edit',values={'enabled':{'awg':False}},view=('protocols',0),go=True)[2]
    with patch.object(console.api,'current_values',AsyncMock(return_value=current)),patch.object(console,'review',AsyncMock()) as review:
        await compact.bound_action(console,message(),42,data)
        draft=review.call_args.args[2]
        assert draft.values=={'enabled':{'awg':False}}
        assert [p for p,s in draft.fields]==[('enabled','awg')]
        assert draft.current=={'enabled':{'awg':True}}


@pytest.mark.asyncio
async def test_update_shortcut_keeps_confirm_flag_and_exact_revision():
    data=compact.bound('Обновить','update_install',values={'commit':'a'*40},view=('updates',0),go=True)[2]
    with patch.object(console,'review',AsyncMock()) as review:
        await compact.bound_action(console,message(),42,data)
        draft=review.call_args.args[2]
        assert ('confirm',) in dict(draft.fields)
        assert draft.values['commit']=='a'*40


@pytest.mark.asyncio
async def test_saving_returns_to_same_view_without_result_card():
    draft=console.Draft('group_delete',params={'group_id':9},return_view=('groups',2))
    with patch.object(console,'draw',AsyncMock()),patch.object(console.api,'call',AsyncMock(return_value={})), \
         patch.object(compact,'open_view',AsyncMock()) as view,patch.object(console,'result_screen',AsyncMock()) as card:
        await console.execute(message(),42,draft)
        assert view.call_args.args[-2:]==('groups',2)
        card.assert_not_awaited()


@pytest.mark.asyncio
async def test_collections_paginate_three_at_a_time_without_losing_items():
    value=[{'id':i,'name':f'Группа {i}','count':0} for i in range(8)]
    with patch.object(console,'draw',AsyncMock()) as draw:
        await compact.present(console,message(),42,'groups',value,1)
        rows=draw.call_args.args[4]
        titles=[r[0][0] for r in rows]
        assert any('Группа 3' in t for t in titles) and any('Группа 5' in t for t in titles)
        assert not any('Группа 6' in t for t in titles)
        assert any(b[0]=='→' for row in rows for b in row)


@pytest.mark.asyncio
async def test_template_opens_editor_directly_by_stable_id():
    current={'templates':[{'id':'one','name':'Первый','period':'month','monthly_traffic_limit':0},
                          {'id':'two','name':'Второй','period':'year','monthly_traffic_limit':0}]}
    with patch.object(console.api,'current_values',AsyncMock(return_value=current)),patch.object(console,'template_item',AsyncMock()) as editor:
        await compact.edit_template(console,message(),42,'two')
        draft,index=editor.call_args.args[-2:]
        assert index==1 and draft.values==current
        assert draft.return_view==('templates',0)


def test_byte_values_and_zero_are_not_confused_with_missing_data():
    assert compact.size(0)=='0 Б'
    assert compact.size(1024**3)=='1.0 ГБ'
    assert compact.size(None)=='—'
