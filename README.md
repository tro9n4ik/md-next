# MD-Next

MD-Next — веб-панель для управления VPN-сервером и кластером узлов. Backend построен на FastAPI, SQLAlchemy и SQLite; frontend — React, TypeScript и Vite.

## Возможности

- Управление клиентами и профилями VLESS Reality, VLESS XHTTP, Hysteria 2 и AmneziaWG.
- Подписки для совместимых клиентов; профиль DNS для Happ передаётся заголовком `routing`.
- Управление узлами кластера, ручное переключение выхода и автоматический failover/failback.
- Правила маршрутизации Xray, включая direct, block, proxy и WARP.
- Управление WARP, настройками протоколов, DNS, Telegram и безопасностью.
- Журнал событий, статистика трафика, состояние сервера и двухфакторная аутентификация.

## Установка на сервер

Установщик рассчитан на Ubuntu/Debian и требует root-доступа. Перед установкой настройте DNS-записи доменов на адрес сервера. Нужны основной домен и поддомен панели; для выдачи сертификатов они должны быть доступны снаружи.

Скопируйте проект на сервер и запустите скрипт из его каталога:

```bash
git clone https://github.com/tro9n4ik/md-next.git
cd md-next
sudo bash scripts/install.sh
```

При запуске появится меню: установить, обновить или удалить MD-Next. При первой установке скрипт запросит основной домен, домен панели и email администратора. После установки он покажет пароль администратора; сохраните его в надёжном месте.

Скрипт настройки узла `scripts/join-node.sh` запускается на дочернем сервере по одноразовой команде-приглашению из панели. Не публикуйте URL приглашения и его токен.

### Резервное копирование

На сервере `scripts/backup.sh` создаёт архив с базой данных, `.env` и конфигурациями Xray, AmneziaWG и Nginx. Копия базы снимается через SQLite Backup API, поэтому остаётся целостной даже при работающем backend, и проверяется командой `PRAGMA integrity_check`.

```bash
sudo bash scripts/backup.sh          # создать копию
sudo bash scripts/backup.sh --list   # показать существующие копии
```

Копии по умолчанию складываются в `/var/backups/md-next`, хранятся последние 7 штук, архивы имеют права `600`. Пути и глубину ротации можно переопределить: `BACKUP_ROOT=/путь KEEP=14 sudo -E bash scripts/backup.sh`.

## Разработка

Требуются Python 3.11 или новее и Node.js с npm. Команды выполняются из корня проекта.

### Backend

```bash
cd backend
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
export JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export INITIAL_ADMIN_PASSWORD="Задайте-длинный-уникальный-пароль"
alembic upgrade head
uvicorn app.main:app --reload
```

Windows PowerShell после активации окружения:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:JWT_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
$env:INITIAL_ADMIN_PASSWORD = "Задайте-длинный-уникальный-пароль"
alembic upgrade head
uvicorn app.main:app --reload
```

Локальная база SQLite создаётся в каталоге `backend`. Не добавляйте файл базы, `.env`, реальные токены или ключи в Git.

Полный список переменных окружения с описанием — в [`backend/.env.example`](backend/.env.example). Он лежит в Git, а `.env` с реальными секретами — нет.

### Frontend

В отдельном терминале из корня проекта:

```bash
cd frontend
npm ci
npm run dev
```

Для проверки TypeScript и production-сборки:

```bash
npm run build
```

### Проверка перед отправкой изменений

Тесты backend. Переменные окружения для тестов задаются в `conftest.py`, `.env` не требуется:

```bash
cd backend
pytest
```

Отдельный тест или файл:

```bash
pytest tests/test_xray.py
pytest tests/test_xray.py::test_имя_теста
```

Линтер frontend:

```bash
cd frontend
npm run lint
```

## Сервисы и порты

В установленной конфигурации используются FastAPI, Nginx, Xray, а при включении соответствующих функций — AmneziaWG и WARP. Nginx принимает публичный HTTPS-трафик и передаёт протоколы на локальные сервисы. Конкретные адреса и порты могут отличаться в зависимости от настроек панели и конфигурации сервера.

Публичный `/health` предназначен для простой проверки доступности API. Управляющие API требуют авторизации.

## Безопасность

- Используйте уникальные длинные пароли и секрет `JWT_SECRET_KEY`.
- Храните серверный `.env`, базу данных, резервные копии, приватные ключи и токены отдельно от Git.
- Перед публикацией проверяйте изменения и содержимое коммита на секреты и пользовательские данные.
- Не запускайте скрипты установки из непроверенного источника с правами root.

Архивы `scripts/backup.sh` содержат `.env` с секретами и приватные ключи. Храните их вне репозитория и не прикрепляйте к issues и pull request.

## Лицензия

MIT — см. [LICENSE](LICENSE).
