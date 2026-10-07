"""Публичная заглушка; история замен хранится вне каталога nginx."""
import argparse
import html
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MAX_BYTES = 1024 * 1024
_lock = threading.RLock()


def paths():
    root = Path(__file__).resolve().parents[3]
    return (Path(os.environ.get('MD_PLACEHOLDER_SITE_DIR', root / 'backend/app/static/fake')),
            Path(os.environ.get('MD_PLACEHOLDER_STATE_DIR', root / 'data/placeholder')))


def validate(data: bytes, filename: str):
    if len(filename) > 255 or any(ord(char) < 32 for char in filename) or '/' in filename or '\\' in filename or not filename.lower().endswith(('.html', '.htm')):
        raise ValueError('Выберите файл с расширением .html или .htm')
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Размер HTML-файла должен быть от 1 байта до 1 МБ')
    try:
        content = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Сохраните HTML-файл в кодировке UTF-8') from None
    if '\x00' in content or '<html' not in content.lower() or '</html>' not in content.lower():
        raise ValueError('Нужен полный HTML-документ с тегами <html> и </html>')
    return content.encode('utf-8')


def _atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(6))
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state():
    _, directory = paths()
    file = directory / 'state.json'
    return json.loads(file.read_text('utf-8')) if file.exists() else {}


def status():
    with _lock:
        site, directory = paths()
        active = site / 'index.html'
        state = _state()
        return {**state, 'mode': state.get('mode', 'existing'),
                'filename': state.get('filename', 'index.html'),
                'size': active.stat().st_size if active.exists() else 0,
                'updated_at': state.get('updated_at'),
                'can_restore': bool(list(directory.glob('revision-*.json')))}


def content():
    with _lock:
        return (paths()[0] / 'index.html').read_text('utf-8')


def replace(data: bytes, filename: str, mode='custom'):
    data = validate(data, filename)
    with _lock:
        site, directory = paths()
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        active = site / 'index.html'
        old = active.read_bytes() if active.exists() else None
        previous = _state()
        stamp = datetime.now(timezone.utc).isoformat()
        revision = directory / ('revision-' + stamp.replace(':', '-') + '-' + secrets.token_hex(4) + '.json')
        if old is not None:
            _atomic(revision, json.dumps({'content': old.decode('utf-8'), 'state': previous}, ensure_ascii=False).encode(), 0o600)
        state = {'mode': mode, 'filename': filename, 'updated_at': stamp}
        try:
            _atomic(active, data, 0o644)
            _atomic(directory / 'state.json', json.dumps(state, ensure_ascii=False).encode(), 0o600)
        except OSError:
            if old is not None:
                _atomic(active, old, 0o644)
            else:
                active.unlink(missing_ok=True)
            raise
        for obsolete in sorted(directory.glob('revision-*.json'), reverse=True)[5:]:
            obsolete.unlink()
        return status()


def restore():
    with _lock:
        revisions = sorted(paths()[1].glob('revision-*.json'), reverse=True)
        if not revisions:
            raise ValueError('Нет предыдущей страницы для восстановления')
        saved = json.loads(revisions[0].read_text('utf-8'))
        state = saved['state']
        return replace(saved['content'].encode(), state.get('filename', 'index.html'), state.get('mode', 'existing'))


def generate():
    choose = secrets.choice
    title = choose(['Лист', 'Север', 'Полка', 'Фолио', 'Контур', 'Архив']) + ' · ' + choose(['материалы', 'библиотека', 'документы'])
    description = choose(['Небольшая библиотека полезных материалов для ежедневной работы.',
                          'Документы, памятки и шаблоны в одном спокойном пространстве.',
                          'Подборка открытых материалов для планирования и новых идей.',
                          'Простое место для заметок, инструкций и рабочих списков.'])
    accent, background = choose([('#23745c', '#eef5ef'), ('#3d62a4', '#eff3fa'), ('#8056a0', '#f4eff8'), ('#ad6739', '#fbf3eb')])
    layout = choose(['grid-template-columns:repeat(auto-fit,minmax(240px,1fr))', 'grid-template-columns:1fr'])
    radius = choose([12, 20, 28])
    labels = ['План на неделю', 'Список задач', 'Памятка по документам', 'Идеи для проекта', 'Список полезных привычек', 'Шаблон заметки']
    cards = []
    for label in secrets.SystemRandom().sample(labels, 4):
        payload = quote(label + '\n\nДата: __________\nЗаметки: __________\n', safe='')
        cards.append(f'<article><span class="tag">TXT · открытый материал</span><h2>{html.escape(label)}</h2><p>Текстовый шаблон, который можно сохранить и дополнить.</p><a download="material.txt" href="data:text/plain;charset=utf-8,{payload}">Скачать файл ↓</a></article>')
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:{background};color:#233042;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:50px 24px}}header{{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-bottom:1px solid #0001;padding-bottom:20px}}.brand{{font-weight:700;color:{accent}}}.tag,footer{{font-size:13px;color:#667085}}h1{{font-size:clamp(32px,6vw,58px);line-height:1.15;margin:55px 0 20px}}.description{{max-width:620px;color:#596579}}.files{{display:grid;{layout};gap:18px;margin:36px 0}}article{{padding:28px;border-radius:{radius}px;background:white;border:1px solid #0001}}h2{{font-size:21px}}a{{color:{accent};font-weight:600;text-decoration:none}}a:hover{{text-decoration:underline}}footer{{border-top:1px solid #0001;padding-top:20px}}</style></head><body><main><header><span class="brand">{html.escape(title)}</span><span class="tag">Демонстрационная библиотека</span></header><h1>Материалы всегда<br>под рукой</h1><p class="description">{description}</p><section class="files">{''.join(cards)}</section><footer>Открытые текстовые шаблоны · Без регистрации</footer></main></body></html>'''.encode('utf-8')


def initialize():
    with _lock:
        # Повторный запуск и обновление сохраняют выбранную пользователем страницу.
        if not (paths()[1] / 'state.json').exists():
            return replace(generate(), 'index.html', 'generated')
        return status()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Первичная генерация сайта-заглушки')
    parser.add_argument('--initialize', action='store_true', required=True)
    parser.parse_args()
    initialize()
