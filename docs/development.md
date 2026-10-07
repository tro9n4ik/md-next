# Локальная разработка

Нужны Python 3.10–3.13, Node.js 24, npm и Git. Выполняйте команды от обычного пользователя. Полную VPN-инфраструктуру проверяйте на отдельной Linux VM: разработческий сервер не заменяет Xray, AWG, Nginx и systemd.

## Backend

```bash
cd backend
python3 -m venv venv
. venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
mkdir -p .runtime/awg
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export INITIAL_ADMIN_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export XRAY_CONFIG_PATH="$PWD/.runtime/xray.json"
export AWG_CONFIG_PATH="$PWD/.runtime/awg/awg0.conf"
export NGINX_PANEL_CONFIG="$PWD/.runtime/nginx-panel.conf"
export NGINX_STREAM_CONFIG="$PWD/.runtime/nginx-stream.conf"
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Пароль администратора задаётся переменной `INITIAL_ADMIN_PASSWORD`. Сохраните его локально перед запуском; не коммитьте. SQLite-база создаётся в `backend/md_next.db` и игнорируется Git. Переменные задаются окружением процесса; копирование `.env.example` само по себе не загружает их. Для настроек используйте имена из `backend/.env.example`. Диагностика системных служб без установленной инфраструктуры покажет замечания. Не запускайте локальный backend с sudo для устранения этих замечаний.

## Frontend

В отдельном терминале:

```bash
cd frontend
npm ci
npm run dev
```

Откройте адрес, указанный Vite. Запросы `/api` проксируются на локальный backend согласно `vite.config.ts`.

## Проверки

```bash
cd backend
. venv/bin/activate
pytest tests -q
pip-audit -r requirements.txt
cd ../frontend
npm ci
npm run build
npm run lint
npm audit
cd ..
for script in scripts/*.sh; do bash -n "$script" || exit; done
python3 scripts/check-public-files.py
```

Тесты backend используют отдельную временную базу. Установку и удаление запускайте только на машине, которую можно полностью очищать; workflow `Installers` предназначен для проверки чистых VM.
