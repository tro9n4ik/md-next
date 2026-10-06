# Проверки перед выпуском

## Чистое окружение

Поддерживаемые версии Python для проверок: 3.10–3.13. Ubuntu 22.04 использует Python 3.10, Ubuntu 24.04 — 3.12, Debian 12 — 3.11.

```bash
cd backend
python3 -m venv venv
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
venv/bin/python -m pip_audit -r requirements.txt
cd ../frontend
npm ci
npm run lint
npm run build
npm audit
cd ..
for script in scripts/*.sh; do bash -n "$script"; done
```

GitHub Actions выполняет эти проверки при каждом push и pull request. Локальные тесты на Windows пропускают проверки, требующие Linux; окончательный результат смотрите в Linux CI.

## Проверка установки и удаления

Для каждой ОС нужна отдельная чистая виртуальная машина или переустановка тестового VPS. Проверки нельзя выполнять на работающем сервере с клиентами.

1. Убедитесь, что DNS тестовых доменов указывает на VPS; для выпуска сертификата нужен доступ к порту 80. Для изолированного VPS без доменов запустите `MDNEXT_TEST_SELF_SIGNED=1 bash scripts/install.sh` и используйте имена `md-next.test` и `panel.md-next.test`. Добавьте эти имена в локальный hosts для доступа. Этот режим использует временный самоподписанный сертификат; выпуск и доверие к сертификату Let's Encrypt остаются непроверенными.
2. Выполните `bash scripts/install.sh`, выберите установку. Проверьте `systemctl is-active md-next-backend xray`, `nginx -t`, вход в панель и подключение тестового клиента.
3. Включите AWG, проверьте подключение и выход через ноду. Проверьте `ip rule show` и `iptables -S MDNEXT_AWG_OUT`.
4. Выполните `bash scripts/uninstall.sh`. Проверьте отказ от подтверждения, затем удаление с сохранением данных, повторную установку и удаление с удалением данных.
5. После полного удаления не должны оставаться правила панели в таблице 10086, цепочка `MDNEXT_AWG_OUT`, файл `/etc/sysctl.d/90-md-next-awg.conf`, каталог `/var/lib/md-next` и резервные копии в `/opt/md-next/backend/backups`. Чужие маршруты, пакеты ОС и сертификаты не удаляются.

Перед удалением скачайте нужные копии на другое устройство и сохраните исходный `.env`: зашифрованные копии привязаны к `JWT_SECRET_KEY`.
