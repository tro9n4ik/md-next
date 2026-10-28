import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { MemoryRouter, Routes, Route } = require('react-router-dom');
const { QueryClient, QueryClientProvider } = require('@tanstack/react-query');
for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true, target: ts.ScriptTarget.ES2022,
  } }).outputText, filename);
}
globalThis.localStorage = { getItem: () => null };
const { AuthContext } = require('../src/contexts/AuthContext.ts');
const Sidebar = require('../src/components/Sidebar.tsx').default;
const ClientsTable = require('../src/components/ClientsTable.tsx').default;
const ClientAccessPage = require('../src/pages/ClientAccessPage.tsx').default;
const UsersPage = require('../src/pages/UsersPage.tsx').default;
const { AccountEditor } = require('../src/pages/UsersPage.tsx');
const UpdatePage = require('../src/pages/UpdatePage.tsx').default;
const WarpPage = require('../src/pages/WarpPage.tsx').default;
const SettingsPage = require('../src/pages/SettingsPage.tsx').default;
const AccountPage = require('../src/pages/AccountPage.tsx').default;
const HelpPage = require('../src/pages/HelpPage.tsx').default;
const { wikiArticles, wikiCategories, findWikiArticles } = require('../src/data/wiki.ts');
const { NodesManager } = require('../src/components/NodesManager.tsx');
const subadmin = { id: 2, username: 'issuer', role: 'subadmin', view_all_clients: false, can_issue_subscriptions: true, can_delete_subscriptions: false, can_pause_subscriptions: false };
const admin = { ...subadmin, id: 1, username: 'admin', role: 'admin' };
function fixture(clientOverrides = {}) {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const client = { id: 123, name: 'Issued client', phone: '', email: '', is_active: true, access_allowed: true,
    expires_at: null, profiles: [], connected_protocols: [], monthly_traffic_used: 0, monthly_traffic_limit: 0,
    cdn_monthly_traffic_used: 0, cdn_monthly_traffic_limit: 0, traffic_total: 0, connection_status: 'offline', can_delete: false, can_pause: false, ...clientOverrides };
  query.setQueryData(['clients', '', 'all', 'created', 'desc'], [client]);
  query.setQueryData(['clientProfiles', 123], { client, access: [], adblock_enabled: false,
    profiles: [{ id: 456, kind: 'vless_reality_tcp', label: 'Reality TCP', data: 'vless://test-profile', is_enabled: true, key_available: true }],
    subscription_url: 'https://vpn.example/sub/test-subscription' });
  return query;
}
function render(component, user, query = fixture()) {
  const path = component === ClientAccessPage ? '/clients/123/access' : '/';
  return renderToStaticMarkup(React.createElement(QueryClientProvider, { client: query },
    React.createElement(AuthContext.Provider, { value: user },
      React.createElement(MemoryRouter, { initialEntries: [path] },
        React.createElement(Routes, null, React.createElement(Route, {
          path: component === ClientAccessPage ? '/clients/:id/access' : '*',
          element: React.createElement(component, { onLogout: () => {} }),
        }))))));
}
test('WARP показывает результат Gemini и объясняет границы автоматической смены IP', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, controls_ready: true, node_id: 1, name: 'Первая нода', ipv4_only: true});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Первая нода', result: {
    status: 'region_blocked', message: 'Gemini явно сообщил, что регион выхода не поддерживается.',
    ip: '104.28.1.2', country: 'DE', warp: 'on', checked_at: 10000,
    rotation_note: 'WARP переподключён; Cloudflare сохранил прежний IP.',
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Автопроверка Gemini/);
  assert.match(html, /Проверить Gemini/);
  assert.match(html, /регион выхода не поддерживается/);
  assert.match(html, /Cloudflare сохранил прежний IP/);
  assert.match(html, /Страна IP по Cloudflare: <b>Германия \(DE\)/);
  assert.match(html, /WARP: подтверждён/);
  assert.match(html, /Региональный отказ Gemini/);
  assert.match(html, /border-red-600/);
  assert.match(html, /Ручная проверка показывает результат и не переподключает WARP/);
  assert.match(html, /не более 3 попыток за 6 часов/);
});
test('Открытая страница Gemini не становится зелёным подтверждением доступа', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, controls_ready: true, node_id: 1, name: 'Нода NL'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Нода NL', result: {
    status: 'page_loaded', message: 'Публичная страница открылась.', country: 'DE', warp: 'on', checked_at: 10000,
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Страница Gemini открылась · регион Google не определён/);
  assert.match(html, /Германия \(DE\)/);
  assert.match(html, /border-amber-500/);
  assert.doesNotMatch(html, /border-emerald-600/);
  assert.match(html, /а не регион аккаунта Google/);
});
test('WARP сообщает о приостановке мониторинга при выключенном выходе', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: false, controls_ready: false, node_id: 1, name: 'Нода'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: false, node_id: 1, node_name: 'Нода', result: {}});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Пока WARP выключен, автопроверка приостановлена/);
  assert.match(html, /disabled=""[^>]*>Проверить Gemini/);
});
test('Неизвестный ответ Gemini не подменяет страну выхода названием ноды', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, node_id: 1, name: 'Нода NL'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Нода NL', result: {
    status: 'unknown', message: 'Ответ не получен.',
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Результат проверки не определён/);
  assert.match(html, /Страна IP по Cloudflare: <b>не определена/);
  assert.match(html, /WARP: не проверен/);
  assert.doesNotMatch(html, /Нидерланды/);
});
test('Настройки в меню доступны всем, служебные разделы переехали внутрь', () => {
  const html = render(Sidebar, subadmin);
  assert.match(html, /href="\/clients"/);
  assert.match(html, /href="\/settings\/account"/);
  assert.match(html, /href="\/settings"/);
  const full = render(Sidebar, admin);
  for (const path of ['nodes', 'warp', 'routing', 'wiki']) {
    assert.doesNotMatch(html, new RegExp('href="/' + path));
    assert.match(full, new RegExp('href="/' + path));
  }
  for (const path of ['account', 'events', 'diagnostics', 'history', 'help']) assert.doesNotMatch(full, new RegExp(`href="/${path}"`));
});
test('Субадминистратор видит только личные настройки, администратор — все разделы', () => {
  const limited = render(SettingsPage, subadmin);
  const full = render(SettingsPage, admin);
  assert.match(limited, /href="\/settings\/account"/);
  for (const path of ['users', 'telegram', 'placeholder', 'diagnostics', 'history', 'events', 'backups', 'update']) {
    assert.doesNotMatch(limited, new RegExp(`href="/settings/${path}"`));
    assert.match(full, new RegExp(`href="/settings/${path}"`));
  }
});
test('Пароль и 2FA в личной учётной записи всегда раскрыты', () => {
  const query = fixture(); query.setQueryData(['me'], {id: 1, username: 'admin', totp_enabled: false});
  const html = render(AccountPage, admin, query);
  assert.match(html, /Текущий пароль/);
  assert.match(html, /Двухфакторная аутентификация/);
  assert.doesNotMatch(html, /<details|<summary/);
});
test('Wiki ищет по всем словам и содержимому, учитывает категории и букву ё', () => {
  assert.ok(findWikiArticles('трафика обхода').some(article => article.id === 'limits'));
  assert.ok(findWikiArticles('учетная').some(article => article.id === 'account'));
  assert.deepEqual(findWikiArticles('слова-которого-нет'), []);
  assert.ok(findWikiArticles('Gemini', 'network').length > 0);
  assert.ok(findWikiArticles('Gemini', 'network').every(article => article.category === 'network'));
  assert.equal(new Set(wikiArticles.map(article => article.id)).size, wikiArticles.length);
  assert.ok(wikiArticles.every(article => wikiCategories.some(category => category.id === article.category)));
  const html = render(HelpPage, admin);
  assert.match(html, /Поиск по Wiki/);
  assert.match(html, /В этой статье/);
  assert.match(html, /article=quickstart/);
});
test('Видимость всех клиентов не даёт кнопок удаления или приостановки', () => {
  for (const view_all_clients of [false, true]) {
    const html = render(ClientsTable, { ...subadmin, view_all_clients });
    assert.match(html, /Добавить/);
    assert.match(html, /Выдать подключение/);
    assert.doesNotMatch(html, /aria-label="Удалить клиента/);
    assert.doesNotMatch(html, /aria-label="Приостановить/);
  }
  const full = render(ClientsTable, admin);
  assert.match(full, /aria-label="Удалить клиента/);
  assert.match(full, /aria-label="Приостановить/);
});
test('Выдача содержит QR и ссылку, без редакторов доступа и перевыпуска', () => {
  const html = render(ClientAccessPage, subadmin);
  assert.match(html, /vless:\/\/test-profile/);
  assert.match(html, /https:\/\/vpn.example\/sub\/test-subscription/);
  assert.match(html, /<svg/);
  for (const forbidden of ['Сохранить доступ', 'Перевыпустить', 'Продлить на 30 дней', 'Сбросить месячный расход']) assert.doesNotMatch(html, new RegExp(forbidden));
  const full = render(ClientAccessPage, admin);
  assert.match(full, /Сохранить доступ/);
  assert.match(full, /Перевыпустить/);
});
test('Список учёток не раскрывает формы; редактор сохраняет все права', () => {
  const query = fixture();
  const account = { ...subadmin, is_active: true, view_all_clients: true, totp_enabled: false };
  query.setQueryData(['subadmins'], [account]);
  const list = render(UsersPage, admin, query);
  assert.match(list, /Настроить issuer/);
  assert.match(list, /Добавить субадминистратора/);
  assert.doesNotMatch(list, /role="switch"|type="password"|<h1|<dialog/);
  const html = render(() => React.createElement(AccountEditor, {account, refresh() {}, onClose() {}}), admin, query);
  assert.doesNotMatch(html, /type="checkbox"/);
  for (const [label, checked] of [['Все клиенты', true], ['Создание и выдача подписок', true], ['Приостановка своих подписок', false], ['Удаление своих подписок', false]]) {
    assert.ok(html.includes(`role="switch" aria-checked="${checked}" aria-label="${label}: issuer"`));
  }
  assert.match(html, /Сбросить пароль/);
});
test('Прогресс обновления виден без раскрытия подробностей', () => {
  const query = fixture();
  query.setQueryData(['panel-update'], {installed: {version: '2.4.18', commit: 'd1d369e'}, can_install: true, job: {phase: 'checking', message: 'Проверяю запуск новой панели'}});
  query.setQueryData(['update-components'], {components: []});
  const html = render(UpdatePage, admin, query);
  assert.match(html, /Обновление выполняется/);
  assert.match(html, /role="status"[^>]*>Проверяю запуск новой панели/);
  assert.doesNotMatch(html, /<details[^>]*\bopen/);
  assert.match(html, /Компоненты сервера/);
});

test('Приостановка и возобновление требуют права учётки и владения подпиской', () => {
  for (const allowed of [false, true]) {
    const user = { ...subadmin, can_issue_subscriptions: false, can_pause_subscriptions: allowed };
    for (const own of [false, true]) {
      const html = render(ClientsTable, user, fixture({ can_pause: own }));
      if (allowed && own) assert.match(html, /aria-label="Приостановить"/);
      else assert.doesNotMatch(html, /aria-label="Приостановить"/);
    }
  }
  const paused = render(ClientsTable, { ...subadmin, can_pause_subscriptions: true }, fixture({ can_pause: true, is_active: false }));
  assert.match(paused, /aria-label="Возобновить"/);
});

test('Отзыв выдачи скрывает создание и подключения, включая ранее загруженный кэш', () => {
  const restricted = { ...subadmin, can_issue_subscriptions: false };
  const table = render(ClientsTable, restricted);
  assert.doesNotMatch(table, /Добавить|Выдать подключение/);
  assert.match(table, /Issued client/);
  const page = render(ClientAccessPage, restricted);
  assert.match(page, /Выдача подписок запрещена/);
  assert.doesNotMatch(page, /test-profile|test-subscription|<svg.*viewBox="0 0 37 37"/);
});

test('Удаление требует разрешения учётки и права на конкретного клиента', () => {
  for (const can_delete_subscriptions of [false, true]) {
    for (const can_delete of [false, true]) {
      const html = render(ClientsTable, { ...subadmin, can_issue_subscriptions: false, can_delete_subscriptions, view_all_clients: true }, fixture({ can_delete }));
      assert.equal(html.includes('aria-label="Удалить клиента'), can_delete_subscriptions && can_delete);
      assert.doesNotMatch(html, /aria-label="Приостановить/);
    }
  }
});

test('У истории приглашений есть отдельная кнопка очистки и пояснение о сохранении нод', () => {
  for (const filled of [false, true]) {
    const query = fixture();
    query.setQueryData(['nodeInvites'], filled ? [{ id: 1, name: 'Old invite', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-01-02T00:00:00Z', revoked: true }] : []);
    const html = render(NodesManager, admin, query);
    assert.match(html, /Одноразовые приглашения/);
    assert.doesNotMatch(html, /Активные одноразовые приглашения/);
    assert.match(html, /Действующие приглашения и ноды сохраняются/);
    const button = [...html.matchAll(/<button\b[^]*?<\/button>/g)].map(match => match[0]).find(markup => markup.includes('Очистить историю'));
    assert.ok(button, 'Кнопка очистки отображается');
    assert.equal(/disabled=""/.test(button), !filled);
  }
});

test('Ноды показывают текущий выход и запрещают выбор недоступного узла', () => {
  const query = fixture();
  query.setQueryData(['nodes'], [{id: 1, name: 'Основная', host: '203.0.113.1', port: 443, protocol: 'vless', status: 'healthy', is_enabled: true, priority: 0, ping_ms: 45},
    {id: 2, name: 'Недоступная', host: '203.0.113.2', port: 443, protocol: 'vless', status: 'unhealthy', is_enabled: true, priority: 1, ping_ms: 0}]);
  query.setQueryData(['clusterRoute'], {server: {hostname: 'panel', public_ip: '192.0.2.1'}, vpn: {xray: true, awg: false}, active_node: {id: 1, name: 'Основная', ip: '203.0.113.1', ping_ms: 45, status: 'healthy'}, manual_direct: false, failover_mode: 'auto'});
  query.setQueryData(['nodeInvites'], []);
  const html = render(NodesManager, admin, query);
  assert.match(html, /Текущий выход/);
  assert.match(html, /aria-label="Список нод"/);
  assert.doesNotMatch(html, /<table|<details[^>]*\bopen/);
  const buttons = [...html.matchAll(/<button\b[^]*?<\/button>/g)].map(match => match[0]);
  const unavailable = buttons.find(button => button.includes('Сделать активной: Недоступная'));
  assert.ok(unavailable);
  assert.match(unavailable, /disabled=""/);
  assert.ok(buttons.some(button => button.includes('Проверить внешний IP')));
});


test('Google RU показывается отдельно от страны Cloudflare и отказа Gemini', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, node_id: 1, name: 'Нода NL'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Нода NL', result: {
    status: 'google_region_ru', message: 'Сигнал региона Google.', country: 'NL', google_country: 'RU', warp: 'on', checked_at: 10000,
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Google определяет выход как РФ/);
  assert.match(html, /Регион по странице входа Google: <b>Россия \(RU\)/);
  assert.match(html, /Страна IP по Cloudflare: <b>Нидерланды \(NL\)/);
  assert.doesNotMatch(html, /Региональный отказ Gemini/);
  assert.doesNotMatch(html, /border-emerald-600/);
});

test('Отсутствующий регион Google не подменяется страной Cloudflare', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, node_id: 1, name: 'Нода DE'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Нода DE', result: {
    status: 'page_loaded', message: 'Страница открылась.', country: 'DE', google_country: '', warp: 'on', checked_at: 10000,
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Регион по странице входа Google: <b>не определён/);
  assert.match(html, /Страница Gemini открылась · регион Google не определён/);
  assert.match(html, /Страна IP по Cloudflare: <b>Германия \(DE\)/);
});


test('Определённый регион Google и открытая страница показываются без предупреждения о сбое', () => {
  const query = fixture();
  query.setQueryData(['warp-status'], {enabled: true, node_id: 1, name: 'Нода NL'});
  query.setQueryData(['warp-presets'], {presets: []});
  query.setQueryData(['warp-gemini-monitor'], {enabled: true, node_id: 1, node_name: 'Нода NL', result: {
    status: 'page_loaded', message: 'Доступ к генерации не подтверждён.', country: 'DE', google_country: 'NL', warp: 'on',
  }});
  const html = render(WarpPage, admin, query);
  assert.match(html, /Регион Google: NL · регионального отказа Gemini нет/);
  assert.match(html, /warp-region-checked/);
  assert.doesNotMatch(html, /border-amber-500|border-red-600/);
  assert.match(html, /Доступ к генерации не подтверждён/);
});
