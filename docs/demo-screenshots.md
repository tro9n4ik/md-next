# Демонстрационные скриншоты 2.5.1

Снял интерфейс с искусственными данными, без подключения к рабочей панели. Адреса и контакты в примерах не относятся к реальным пользователям. Версию, маршруты и контрольные суммы изображений записал в `assets/screenshots.json`.

Размер каждого изображения — строго **1920×1080**: снимается видимая область окна, без увеличения высоты до всей страницы.

## Повторная съёмка

Установите зависимости frontend и Playwright с Chromium, затем выполните из корня проекта:

```sh
npm --prefix frontend run build
node scripts/demo/capture-screenshots.cjs
python scripts/check-release-consistency.py
```

Если Playwright или Chromium установлены отдельно, задайте `PLAYWRIGHT_MODULE` и `PLAYWRIGHT_CHROMIUM_EXECUTABLE`. Для отдельного комплекта библиотек Chromium предусмотрена переменная `CHROMIUM_RUNTIME_DIR`. Для эмодзи нужен соответствующий шрифт.

Сценарий перехватывает API, блокирует внешние запросы и проверяет ошибки страницы и горизонтальное переполнение. Время внутри демонстрации фиксировано.

## Загрузка на 4PDA

Прикрепите изображения из `docs/assets`: `dashboard-demo.jpg`, `dashboard-dark-demo.jpg`, `clients-demo.jpg`, `nodes-demo.jpg`, `warp-demo.jpg`, `protocols-demo.jpg`, `subadmins-demo.jpg`, `telegram-demo.jpg`, `client-create-demo.jpg`, `settings-demo.jpg`, `wiki-demo.jpg`.

Для публикации используйте описание выпуска `release-2.5.1.md` и коды вложений, которые выдаст форум. Проверьте предпросмотр перед отправкой. Комплект показывает демонстрационные данные и не подтверждает обновление рабочего сервера.
