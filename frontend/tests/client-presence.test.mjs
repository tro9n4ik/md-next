import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/utils/clientPresence.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { lastOnlineLabel } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('Последняя активность показывает точную локальную дату и время', () => {
  const value = '2026-10-28T07:35:00+00:00';
  const date = new Date(value);
  const expected = `Был онлайн ${date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })} в ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
  assert.equal(lastOnlineLabel(value), expected);
  assert.match(lastOnlineLabel(value), /2026/);
});

test('Отсутствующая или неверная активность не подменяется текущим временем', () => {
  assert.equal(lastOnlineLabel(null), 'Активность ещё не зафиксирована');
  assert.equal(lastOnlineLabel(undefined), 'Активность ещё не зафиксирована');
  assert.equal(lastOnlineLabel('invalid'), 'Время активности недоступно');
});
