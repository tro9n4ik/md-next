#!/usr/bin/env python3
"""Render a private, isolated dd trial from the panel's MTProto staging JSON."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

runtime_path = Path(__file__).resolve().parents[2] / 'scripts/mtproto-runtime.py'
spec = importlib.util.spec_from_file_location('mtproto_trial_runtime', runtime_path)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def render(payload, profile, host, port=8448, stats_port=10911):
    runtime.validate(payload)
    if not re.fullmatch(r'p[1-9][0-9]{0,18}', profile) or profile not in payload['users']:
        raise ValueError('Профиль отсутствует в активном наборе')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host):
        raise ValueError('Укажите публичный IPv4 или домен')
    if type(stats_port) is not int or not 1024 <= stats_port <= 65535 or stats_port == port:
        raise ValueError('Неверный локальный порт статистики')
    if port == payload['port']:
        raise ValueError('Тест должен использовать отдельный публичный порт')
    selected = {**payload, 'port': port, 'users': {profile: payload['users'][profile]}}
    text = runtime.render(selected)
    text = text.replace('api-bind-to = "127.0.0.1:10910"', f'api-bind-to = "127.0.0.1:{stats_port}"')
    text = text.replace('[network]', '[secured]\nenabled = true\n[network]', 1)
    # Use the raw 16-byte key from staging; never slice the domain from a URL.
    secret = 'dd' + selected['users'][profile]
    assert len(bytes.fromhex(secret)) == 17
    return text, 'tg://proxy?' + urlencode({'server': host, 'port': port, 'secret': secret})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8448)
    parser.add_argument('--stats-port', type=int, default=10911)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--show-link', action='store_true', help='Вывести приватную ссылку; не использовать в общих журналах')
    args = parser.parse_args()
    text, link = render(json.loads(args.input.read_text()), args.profile, args.host, args.port, args.stats_port)
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit('Выходной файл уже существует; основной конфиг не перезаписывается')
    # O_EXCL prevents replacement of an existing service configuration.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    print('Создан отдельный конфиг режима dd с правами 0600')
    if args.show_link:
        print(link)


if __name__ == '__main__':
    main()
