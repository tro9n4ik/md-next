import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  const handlers = new Map();
  let dirty = false;
  const document = { addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener() {} };
  const window = { ...document, confirm: () => true };
  class Element {
    constructor(section, autosave = false) { this.section = section; this.autosave = autosave; }
    closest(selector) { return selector === '[data-autosave]' ? (this.autosave ? this.section : null) : selector === '[role=switch]' ? null : this.section; }
    matches() { return false; }
  }
  const react = { useEffect: fn => fn(), useRef: value => ({ current: value }), useState: () => [false, value => { dirty = value; }] };
  const source = fs.readFileSync(new URL('../src/components/UnsavedGuard.tsx', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const context = { exports: {}, require: name => name === 'react' ? react : { useLocation: () => ({ pathname: '/warp' }), useBlocker: () => ({ state: 'unblocked' }) }, document, window, Element };
  vm.runInNewContext(output, context);
  context.exports.default();
  return { input: target => handlers.get('change')({ target }), click: target => handlers.get('click')({ target }), start: detail => handlers.get('api:mutation-start')({ detail }), saved: detail => handlers.get('api:mutation-saved')({ detail }), dirty: () => dirty, Element };
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
