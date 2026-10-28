import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture(blocked = false) {
  const handlers = new Map();
  let dirty = false;
  const document = { addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener() {} };
  const window = { ...document, confirm: () => { throw new Error('Системное подтверждение не должно вызываться'); } };
  const confirmation = {};
  const navigation = { state: blocked ? 'blocked' : 'unblocked', proceeded: 0, resetCount: 0, proceed() { this.proceeded++; }, reset() { this.resetCount++; } };
  class Element {
    constructor(section, autosave = false) { this.section = section; this.autosave = autosave; }
    closest(selector) { return selector === '[data-autosave]' ? (this.autosave ? this.section : null) : ['[data-editable], .ui-card', '[data-editable]'].includes(selector) ? this.section : null; }
    matches() { return false; }
  }
  const react = { useEffect: fn => fn(), useRef: value => ({ current: value }), useState: () => [false, value => { dirty = value; }] };
  const source = fs.readFileSync(new URL('../src/components/UnsavedGuard.tsx', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const context = { exports: {}, require: name => name === 'react' ? react : name === 'react/jsx-runtime' ? { jsx: (Component, props) => Component(props) } : name.includes('ConfirmationContext') ? { useConfirm: () => options => { confirmation.options = options; return new Promise(resolve => { confirmation.resolve = resolve; }); } } : { useLocation: () => ({ pathname: '/warp' }), useBlocker: () => navigation }, document, window, Element };
  vm.runInNewContext(output, context);
  context.exports.default();
  return { input: target => handlers.get('change')({ target }), click: target => handlers.get('click')({ target }), start: detail => handlers.get('api:mutation-start')({ detail }), saved: detail => handlers.get('api:mutation-saved')({ detail }), logout: detail => handlers.get('ui:before-logout')({ detail }), dirty: () => dirty, confirmation, navigation, Element };
}

test('Сохранение кнопкой вне карточки сбрасывает только сохранённую секцию', () => {
  const f = fixture(), target = {}, mode = {};
  f.input(new f.Element(target)); f.input(new f.Element(mode));
  f.click(new f.Element(null));
  const detail = { scope: target }; f.start(detail); f.saved(detail);
  assert.equal(f.dirty(), true);
  const other = { scope: mode }; f.start(other); f.saved(other);
  assert.equal(f.dirty(), false);
});
test('Неудачный запрос сохраняет предупреждение; осмотр страницы не создаёт его', () => {
  const f = fixture(); assert.equal(f.dirty(), false);
  const section = {}; f.input(new f.Element(section));
  f.start({ scope: section }); assert.equal(f.dirty(), true);
});
test('Изменение автоматически сохраняемого режима не оставляет предупреждение', () => {
  const f = fixture(); f.input(new f.Element({}, true)); assert.equal(f.dirty(), false);
});

test('Переход ждёт ответа оформленного окна и сохраняет страницу при отмене', async () => {
  const f = fixture(true);
  assert.equal(f.navigation.proceeded, 0);
  assert.equal(f.navigation.resetCount, 0);
  assert.equal(f.confirmation.options.confirmText, 'Уйти со страницы');
  f.confirmation.resolve(false);
  await Promise.resolve();
  assert.equal(f.navigation.proceeded, 0);
  assert.equal(f.navigation.resetCount, 1);
});

test('Подтверждённый переход выполняется один раз', async () => {
  const f = fixture(true);
  f.confirmation.resolve(true);
  await Promise.resolve();
  assert.equal(f.navigation.proceeded, 1);
  assert.equal(f.navigation.resetCount, 0);
});

test('Выход сообщает о черновике синхронно, а решение остаётся за интерфейсом', () => {
  const f = fixture();
  f.input(new f.Element({}));
  const detail = { hasUnsavedChanges: false };
  f.logout(detail);
  assert.equal(detail.hasUnsavedChanges, true);
  assert.equal(f.confirmation.options, undefined);
});
