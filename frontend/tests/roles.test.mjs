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
const { NodesManager } = require('../src/components/NodesManager.tsx');
const subadmin = { id: 2, username: 'issuer', role: 'subadmin', view_all_clients: false, can_issue_subscriptions: true, can_delete_subscriptions: false };
const admin = { ...subadmin, id: 1, username: 'admin', role: 'admin' };
function fixture(clientOverrides = {}) {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const client = { id: 123, name: 'Issued client', phone: '', email: '', is_active: true, access_allowed: true,
    expires_at: null, profiles: [], connected_protocols: [], monthly_traffic_used: 0, monthly_traffic_limit: 0,
    cdn_monthly_traffic_used: 0, cdn_monthly_traffic_limit: 0, traffic_total: 0, connection_status: 'offline', can_delete: false, ...clientOverrides };
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
test('Меню субадминистратора скрывает инфраструктуру и настройки', () => {
  const html = render(Sidebar, subadmin);
  assert.match(html, /href="\/clients"/);
  assert.match(html, /href="\/account"/);
  const full = render(Sidebar, admin);
  for (const path of ['settings', 'nodes', 'warp', 'routing', 'events', 'diagnostics']) {
    assert.doesNotMatch(html, new RegExp('href="/' + path));
    assert.match(full, new RegExp('href="/' + path));
  }
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
test('Видимость и права используют тумблеры в создании и редактировании учётки', () => {
  const query = fixture();
  query.setQueryData(['subadmins'], [{ ...subadmin, is_active: true, view_all_clients: true, totp_enabled: false }]);
  const html = render(UsersPage, admin, query);
  assert.doesNotMatch(html, /type="checkbox"/);
  for (const label of ['Видит всех клиентов', 'Создание и выдача подписок', 'Удаление своих подписок', 'Создание и выдача подписок: issuer', 'Удаление своих подписок: issuer']) {
    assert.ok(html.includes(`role="switch" aria-checked="${label.startsWith('Создание') ? 'true' : 'false'}" aria-label="${label}"`));
  }
  assert.match(html, /Все клиенты: issuer/);
  assert.match(html, /aria-checked="true"/);
  assert.match(html, /Сбросить пароль/);
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
