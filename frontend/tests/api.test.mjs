import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(status = 200) {
  const events = [], requests = [];
  const storage = new Map([['token', 'test-token']]);
  const scope = {};
  const source = fs.readFileSync(new URL('../src/utils/api.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const context = { exports: {}, Headers, Event, CustomEvent,
    document: { getElementById: () => scope },
    window: { dispatchEvent: event => events.push(event) },
    localStorage: { getItem: key => storage.get(key), removeItem: key => storage.delete(key) },
    fetch: async (...args) => { requests.push(args); return new Response('', { status }); },
  };
  vm.runInNewContext(code, context);
  return { api: context.exports.apiFetch, events, requests, storage, scope };
}

test('Авторизация передаётся без перезаписи явно заданного заголовка', async () => {
  const f = fixture();
  await f.api('/api/v1/clients');
  assert.equal(f.requests[0][1].headers.get('Authorization'), 'Bearer test-token');
  await f.api('/api/v1/clients', { headers: { Authorization: 'Bearer explicit' } });
  assert.equal(f.requests[1][1].headers.get('Authorization'), 'Bearer explicit');
});

test('Сохранение сообщает об успехе только после успешного ответа', async () => {
  for (const status of [200, 422, 500]) {
    const f = fixture(status);
    await f.api('/api/v1/settings', { method: 'PUT' }, 'settings');
    assert.equal(f.events[0].type, 'api:mutation-start');
    assert.equal(f.events[0].detail.scope, f.scope);
    assert.equal(f.events.some(e => e.type === 'api:mutation-saved'), status === 200);
  }
});

test('Проверки и создание копии не сбрасывают несохранённые настройки', async () => {
  for (const action of ['check', 'test', 'diagnostics', 'backups']) {
    const f = fixture();
    await f.api('/api/v1/system/' + action, { method: 'POST' });
    assert.equal(f.events.length, 0);
  }
});

test('Истёкшая авторизация завершает сеанс, неверный пароль при входе не стирает его', async () => {
  const f = fixture(401);
  await f.api('/api/v1/auth/login', { method: 'POST' });
  assert.equal(f.storage.get('token'), 'test-token');
  await f.api('/api/v1/clients');
  assert.equal(f.storage.has('token'), false);
  assert.equal(f.events.at(-1).type, 'auth:unauthorized');
});
