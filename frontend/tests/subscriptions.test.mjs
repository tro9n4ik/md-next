import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(fs.readFileSync(new URL('../src/utils/subscriptions.ts', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const context = { exports: {}, Date }; vm.runInNewContext(code, context);
const payload = context.exports.subscriptionPayload;
const values = { period: 'keep', date: '', quotaGB: '', cdnQuotaGB: '' };

test('Пустые лимиты означают отсутствие ограничений, keep сохраняет срок', () => {
  const result = payload(values);
  assert.equal(result.monthly_traffic_limit, 0);
  assert.equal(result.cdn_monthly_traffic_limit, 0);
  assert.equal('subscription_period' in result, false);
});
test('Основной и CDN лимиты независимы и переводятся в байты', () => {
  const result = payload({ ...values, quotaGB: '1.5', cdnQuotaGB: '2' });
  assert.equal(result.monthly_traffic_limit, 1.5 * 1024 ** 3);
  assert.equal(result.cdn_monthly_traffic_limit, 2 * 1024 ** 3);
});
test('Некорректные и чрезмерные лимиты отклоняются до запроса', () => {
  for (const key of ['quotaGB', 'cdnQuotaGB']) {
    for (const value of ['-1', '0', 'abc', 'Infinity', '100000000000']) {
      assert.throws(() => payload({ ...values, [key]: value }));
    }
  }
});
test('Прошедшая и неверная дата отклоняются, будущая сохраняется в ISO', () => {
  for (const date of ['', 'invalid', '2000-01-01']) {
    assert.throws(() => payload({ ...values, period: 'custom', date }));
  }
  const date = new Date(Date.now() + 86400000).toISOString();
  assert.equal(payload({ ...values, period: 'custom', date }).expires_at, date);
});
