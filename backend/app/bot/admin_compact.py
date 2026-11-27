"""Короткие экраны владельца: только нужные значения и действия на месте."""
from datetime import datetime
from html import escape
import math

from .admin_catalog import OPERATIONS, LABELS, VALUE_LABELS

ENTRY = {'protocols':'protocols','dns':'dns','routing':'rules','warp':'warp_status','cdn':'cdn',
         'users':'users','backups':'backups','system':'system_stats','migration':'migration_jobs','security':'account'}
GROUPS = {
    'clients_extra': ('Клиенты', ('client_group','client_exit','client_routes','profile_regenerate','client_sub','client_delete')),
    'settings': ('Настройки', ('subscription','telegram','placeholder')),
    'system_extra': ('Обслуживание', ('components','events','diagnostics','system_info')),
    'warp_extra': ('WARP', ('warp_ipv4','warp_setup','uplink_status','gemini','gemini_check','gemini_rules')),
    'uplink_extra': ('Канал к нодам', ('uplink','uplink_prepare','uplink_check')),
    'migration_extra': ('Переезд', ('migration_source','migration_fingerprint','migration_start','migration_packages','migration_export')),
    'security_extra': ('Безопасность', ('password','totp_setup','totp_verify','totp_disable')),
    'placeholder_extra': ('Заглушка', ('placeholder_edit','placeholder_generate','placeholder_restore')),
}
COLLECTIONS = {'groups','users','rules','backups','templates','migration_packages','events','components','warp_presets'}


def text(value, limit=110):
    return str(value if value is not None else '—').replace('\n',' ').replace('\r',' ')[:limit]


def size(value):
    if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:return '—'
    for unit in ('Б','КБ','МБ','ГБ','ТБ'):
        if value<1024 or unit=='ТБ':return f'{value:.0f} {unit}' if value>=10 or unit=='Б' else f'{value:.1f} {unit}'
        value/=1024


def date(value):
    try:
        dt=datetime.fromtimestamp(value) if isinstance(value,(int,float)) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt.strftime('%d.%m %H:%M')
    except (ValueError,TypeError,OverflowError,OSError):return '—'


def status(value):
    return {'ok':'🟢 Работает','healthy':'🟢 В сети','running':'🟢 Работает','success':'🟢 Готово',
            'warning':'🟠 Внимание','error':'🔴 Ошибка','failed':'🔴 Ошибка','unhealthy':'🔴 Нет связи',
            'stopped':'⏸ Остановлено','disabled':'Выключено','not_installed':'Не установлен',
            'not_configured':'Не настроено','starting':'Запускается','stopping':'Останавливается',
            'unknown':'Не проверено','idle':'Ожидание','queued':'В очереди','preparing':'Подготовка',
            'installing':'Установка','checking':'Проверка','rolled_back':'Восстановлена прежняя версия',
            'rollback_failed':'Ошибка восстановления','unavailable':'Недоступно'}.get(value,'Не проверено')


def start(key, title=None):return (title or OPERATIONS[key].title,'start',key)
def group(key,title):return (title,'compact_group',key)
def bound(title,key,params=None,values=None,view=None,context='',go=False):
    return (title,'compact_bound',{'key':key,'params':params or {},'values':values or {},'view':view,
                                  'context':context,'go':go})
def refresh(key,page=0):return ('Обновить','compact_page',(key,page))


async def section(C,message,actor,key):
    if key=='clients':
        from .admin_tools import client_list
        await client_list(message,actor)
    elif key=='settings':await menu(C,message,actor,'settings')
    elif key in ENTRY:await open_view(C,message,actor,ENTRY[key])
    else:return False
    return True


async def menu(C,message,actor,key):
    title,keys=GROUPS[key]
    rows=[[start(item)] for item in keys]
    rows.append([('← Разделы','home',None)])
    await C.draw(message,actor,title,'',rows)


async def open_view(C,message,actor,key,page=0):
    if key.startswith('section:'):
        await C.section(message,actor,key.split(':',1)[1]);return
    if key not in OPERATIONS or OPERATIONS[key].method!='GET':raise ValueError('Экран недоступен.')
    value=await C.api.call(actor,message.chat.id,key)
    await present(C,message,actor,key,value,page)


async def client_list(C,message,actor,clients,page=0,query=''):
    page=max(0,min(page,max(0,(len(clients)-1)//3)))
    rows=[]
    from app.services.client_limits import limit_info
    for client in clients[page*3:(page+1)*3]:
        enabled=limit_info(client)['access_allowed']
        rows.append([(('🟢 ' if enabled else '⏸ ')+text(client.name,34),'direct',f'ops:detail:{client.id}')])
        rows.append([('Подключить','direct',f'sub:link:{client.id}'),('Срок','direct',f'ops:conditions:{client.id}'),
                     ('Профили','direct',f'ops:profiles:{client.id}')])
    nav=[]
    if page:nav.append(('←','direct',f'ops:list:{page-1}'))
    if (page+1)*3<len(clients):nav.append(('→','direct',f'ops:list:{page+1}'))
    if nav:rows.append(nav)
    rows += [[('＋ Создать','direct','sub:new'),('Поиск','direct','ops:search'),('Фильтр','direct','ops:filters')],
             [start('groups','Группы'),start('templates','Шаблоны'),group('clients_extra','Ещё')]]
    await C.draw(message,actor,'Подписки',f'Найдено: {len(clients)}'+(' · '+escape(text(query,25)) if query else ''),rows)


async def collection(C,message,actor,key,value,page):
    field={'groups':'groups','components':'components','warp_presets':'presets'}.get(key)
    items=value.get(field,[]) if field and isinstance(value,dict) else value
    if not isinstance(items,list):items=[]
    page=max(0,min(page,max(0,(len(items)-1)//3)))
    rows=[]
    for item in items[page*3:(page+1)*3]:
        if not isinstance(item,dict):continue
        name=text(item.get('name') or item.get('username') or item.get('title') or item.get('domain_or_ip') or 'Запись',38)
        actions=[]
        if key=='groups':
            name+=f" · {item.get('count',0)}"
            actions=[bound('Изменить','group_edit',{'group_id':item['id']},view=(key,page)),
                     bound('Удалить','group_delete',{'group_id':item['id']},view=(key,page),context=name,go=True)]
        elif key=='users':
            name=('🟢 ' if item.get('is_active') else '⏸ ')+name
            actions=[bound('Права','user_edit',{'user_id':item['id']},view=(key,page)),
                     bound('Пауза' if item.get('is_active') else 'Включить','user_edit',{'user_id':item['id']},
                           {'is_active':not item.get('is_active')},(key,page),name,True)]
        elif key=='rules':
            name+=' · '+VALUE_LABELS.get(item.get('action'),text(item.get('action'),15))
            actions=[bound('Удалить','rule_delete',{'rule_id':item['id']},view=(key,page),context=name,go=True)]
        elif key in {'backups','migration_packages'}:
            name=date(item.get('created_at'))+' · '+size(item.get('size'))
            download='backup_download' if key=='backups' else 'migration_download'
            actions=[bound('Скачать',download,{'name':item['name']},view=(key,page),go=True)]
            if key=='migration_packages':actions.append(bound('Удалить','migration_delete',{'name':item['name']},view=(key,page),context=name,go=True))
        elif key=='components':name=text(item.get('name'),32)+' · '+text(item.get('version') or '—',18)+' · '+status(item.get('status'))
        elif key=='events':name=date(item.get('ts') or item.get('timestamp'))+' · '+text(item.get('message'),85)
        elif key=='templates':
            name+=' · '+VALUE_LABELS.get(item.get('period'),'')
            actions=[('Изменить','compact_template',item['id'])]
        elif key=='warp_presets':
            preset_state=item.get('state') or {}
            active=isinstance(preset_state,dict) and preset_state.get('total',0)>0 and preset_state.get('missing')==0
            name=('✅ ' if active else '⬜ ')+name
            actions=[bound('Применить','warp_preset_add',values={'key':item['key']},view=(key,page),go=True),
                     bound('Убрать','warp_preset_remove',values={'key':item['key']},view=(key,page),go=True)]
        rows.append([(name,'compact_page',(key,page))])
        if actions:rows.append(actions)
    nav=[]
    if page:nav.append(('←','compact_page',(key,page-1)))
    if (page+1)*3<len(items):nav.append(('→','compact_page',(key,page+1)))
    if nav:rows.append(nav)
    extras={'groups':[start('group_new','＋ Группа')], 'users':[start('user_new','＋ Создать')],
            'rules':[start('rule_new','＋ Правило'),bound('Применить','rules_apply',view=(key,page),go=True)],
            'backups':[start('backup_create','＋ Копия'),start('restore_preview','Восстановить')],
            'templates':[('＋ Шаблон','compact_template',None)],
            'migration_packages':[start('migration_export','＋ Комплект')],
            'warp_presets':[refresh(key,page)]}
    if key in extras:rows.append(extras[key])
    rows.append([('← Разделы','home',None)])
    await C.draw(message,actor,OPERATIONS[key].title,f'Всего: {len(items)}' if items else 'Пока пусто.',rows)


async def present(C,message,actor,key,value,page=0):
    if key in COLLECTIONS:
        await collection(C,message,actor,key,value,page);return True
    if isinstance(value,bytes):return False
    d=value if isinstance(value,dict) else {}
    rows=[];lines=[]
    if key=='protocols':
        for profile in d.get('profiles',[]):
            kind=profile['kind'];enabled=profile['enabled'];name=LABELS.get(kind,kind)
            rows.append([bound(('✅ ' if enabled else '⬜ ')+name,'protocols_edit',
                               values={'enabled':{kind:not enabled}},view=(key,0),context=('Отключить ' if enabled else 'Включить ')+name,go=True)])
        rows.append([start('protocols_edit','Настройки'),start('sni','Проверка домена')])
    elif key=='dns':
        lines=[f"Внешний: {text(d.get('remote_ip'))} · {text(d.get('remote_type'))}",
               f"Локальный: {text(d.get('domestic_ip'))} · {text(d.get('domestic_type'))}"]
        rows=[[start('dns_edit','Изменить')]]
    elif key=='cdn':
        lines=[('🟢 Включён' if d.get('enabled') else 'Выключен')+' · '+text(d.get('domain') or 'домен не задан')]
        rows=[[start('cdn_edit','Настроить'),bound('Проверить','cdn_check',values={'domain':d.get('domain',''),'enabled':False},view=(key,0),go=True)]]
    elif key=='warp_status':
        lines=[('На паузе' if d.get('paused') else '🟢 Включён' if d.get('enabled') else 'Выключен')+' · '+text(d.get('name'))]
        rows=[[start('warp_usage','Использование'),start('warp_test','Проверить')],
              [start('warp_rotate','Сменить IP'),start('warp_presets','Правила')],[group('warp_extra','Настройки')]]
    elif key=='uplink_status':
        lines=['🟢 Включён' if d.get('enabled') else 'Выключен']
        rows=[[group('uplink_extra','Настроить'),start('uplink_check','Проверить')]]
    elif key=='system_stats':
        mem=d.get('memory') or {};disk=d.get('disk') or {}
        cpu=d.get('cpu_percent');cpu=f'{cpu:g}%' if isinstance(cpu,(int,float)) else '—'
        lines=[f'CPU {cpu}',f"RAM {size(mem.get('used'))} / {size(mem.get('total'))}",
               f"Диск: свободно {size(disk.get('free'))}"]
        rows=[[refresh(key),start('system_health','Сервисы')],[start('updates','Обновление'),group('system_extra','Ещё')]]
    elif key in {'system_health','diagnostics'}:
        for item in d.get('checks',[]):
            state=status(item.get('status')) if 'status' in item else '🟢' if item.get('ok') is True else '🔴' if item.get('ok') is False else '—'
            lines.append(text(item.get('name'),30)+' · '+state)
        rows=[[start(key,'Проверить снова')]]
    elif key=='system_info':lines=[text(d.get('hostname')), 'Версия '+text(d.get('version'))]
    elif key=='updates':
        installed=d.get('installed') or {};job=d.get('job') or {}
        lines=['Версия '+text(installed.get('version'))+' · '+text(installed.get('commit',''),7),status(job.get('phase','idle'))]
        if job.get('failure'):lines.append(text(job['failure'].get('title')))
        rows=[[start('update_check','Проверить'),refresh(key)]]
    elif key=='update_check':
        lines=['Доступно обновление' if d.get('available') else 'Установлена актуальная версия']
        if d.get('available'):lines.append(text(d.get('summary'),100))
        if d.get('available') and d.get('ready'):
            rows=[[bound('Обновить','update_install',values={'commit':d['commit']},view=('updates',0),context='Панель и бот перезапустятся.',go=True)]]
        elif d.get('available'):lines.append('Проверки ещё не завершены.')
    elif key=='account':
        lines=[text(d.get('username'))+' · '+('2FA включена' if d.get('totp_enabled') else '2FA выключена')]
        rows=[[group('security_extra','Пароль и 2FA')]]
    elif key=='placeholder':
        lines=['Заглушка настроена' if d.get('size',0)>0 else 'Заглушка не установлена']
        rows=[[group('placeholder_extra','Изменить')]]
    elif key in {'migration_source','migration_jobs'}:
        fallback=('Успешных проверок: '+str(sum(c.get('status')=='ok' for c in d.get('checks',[])))) if key=='migration_source' else status(d.get('phase','idle'))
        lines=[text(d.get('message') or fallback)]
        rows=[[group('migration_extra','Действия'),refresh(key)]]
    elif key=='migration_fingerprint':lines=[text(d.get('host')),text(d.get('fingerprint'),100),'Сверьте с консолью хостера.']
    elif key=='totp_setup':
        lines=['Добавьте ключ в приложение-аутентификатор.']
        if d.get('secret'):rows=[[('Ключ 2FA','totp_key',d['secret'])],[start('totp_verify','Подтвердить код')]]
    elif key=='client_sub':lines=[text(d.get('subscription_url'),500)]
    elif key in {'warp_test','warp_rotate','gemini_check','uplink_check','cdn_check','sni'}:
        lines=[text(d.get('message') or d.get('ip') or ('Проверка пройдена' if d.get('ok') else 'Проверка завершена'))]
    elif key in {'backup_create','migration_export'}:
        lines=['Копия создана' if key=='backup_create' else 'Комплект подготовлен']
        if d.get('name'):
            rows=[[bound('Скачать','backup_download' if key=='backup_create' else 'migration_download',{'name':d['name']},go=True)]]
    else:
        # Любая новая операция получает короткий ответ вместо раскрытия JSON.
        lines=[text(d.get('notice') or d.get('message') or 'Готово.')]
    rows.append([('← Разделы','home',None)])
    await C.draw(message,actor,OPERATIONS[key].title,C.escaped_text('\n'.join(lines),1700),rows)
    return True


def return_view(key):
    if key.startswith('group_'):return ('groups',0)
    if key.startswith('user_'):return ('users',0)
    if key in {'rule_new','rule_delete','rules_apply'}:return ('rules',0)
    if key=='templates_edit':return ('templates',0)
    if key=='update_install':return ('updates',0)
    if key in {'warp_preset_add','warp_preset_remove'}:return ('warp_presets',0)
    if key=='migration_delete':return ('migration_packages',0)
    if key in {'password','totp_verify','totp_disable'}:return ('account',0)
    if key=='warp_setup':return ('warp_status',0)
    if key=='uplink_prepare':return ('uplink_status',0)
    if key in {'placeholder_generate','placeholder_restore'}:return ('placeholder',0)
    if key=='telegram_test':return ('section:settings',0)
    operation=OPERATIONS[key]
    if operation.method in {'PUT','PATCH'} and key not in {'active_node','node_toggle','node_hosting','node_order','client_access'}:
        entry=ENTRY.get(operation.section)
        return (entry,0) if entry else ('section:'+operation.section,0)
    return None


async def bound_action(C,message,actor,data):
    key=data['key'];operation=OPERATIONS[key]
    draft=C.Draft(key,params=data.get('params',{}),context=data.get('context',''),return_view=data.get('view') or return_view(key))
    C.drafts[(actor,message.chat.id)]=draft
    if operation.method=='GET':
        C.cancel(actor,message.chat.id)
        await C.safe_execute(message,actor,draft)
        return
    schema,root=C.api.body_schema(key)
    current=await C.api.current_values(actor,message.chat.id,key,draft.params)
    draft.fields=C.api.fields(schema,root,current)
    if data.get('go'):
        missing=object();values=data.get('values',{})
        draft.fields=[(path,spec) for path,spec in draft.fields if path==('confirm',) or C.api.get_value(values,path,missing) is not missing]
        draft.values=values
        draft.current=C.api.initial_values(draft.fields,current)
        # API остаётся окончательным валидатором, без обхода его зависимостей.
        await C.review(message,actor,draft)
    else:
        draft.current=C.api.initial_values(draft.fields,current)
        draft.values={**draft.current,**data.get('values',{})}
        await C.form(message,actor,draft)


async def edit_template(C,message,actor,identifier):
    key='templates_edit'
    current=await C.api.current_values(actor,message.chat.id,key,{})
    schema,root=C.api.body_schema(key)
    fields=C.api.fields(schema,root,current)
    baseline=C.api.initial_values(fields,current)
    import copy,secrets
    draft=C.Draft(key,fields=fields,current=baseline,values=copy.deepcopy(baseline),return_view=('templates',0))
    values=draft.values.setdefault('templates',[])
    if identifier is None:
        if len(values)>=20:raise ValueError('Не больше 20 шаблонов.')
        values.append({'id':secrets.token_hex(6),'name':'Новый шаблон','period':'month','monthly_traffic_limit':0})
        index=len(values)-1
    else:
        index=next((i for i,v in enumerate(values) if v['id']==identifier),None)
        if index is None:raise ValueError('Шаблон удалён. Обновите список.')
    C.drafts[(actor,message.chat.id)]=draft
    await C.template_item(message,actor,draft,index)
