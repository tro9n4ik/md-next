# Лицензии зависимостей приложения

Снимок 7 октября 2026 года. Backend: metadata установленных закреплённых пакетов; frontend: `package-lock.json`. Таблицы помогают проверить состав сборки и не заменяют тексты лицензий. Системные утилиты описаны в [уведомлениях](third-party-notices.md).

## Прямые зависимости backend

| Пакет | Версия | Лицензия по metadata |
| --- | --- | --- |
| [fastapi](https://pypi.org/project/fastapi/0.142.2/) | 0.142.2 | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | BSD-3-Clause |
| [SQLAlchemy](https://pypi.org/project/SQLAlchemy/2.0.54/) | 2.0.54 | MIT |
| [greenlet](https://pypi.org/project/greenlet/3.5.6/) | 3.5.6 | MIT AND PSF-2.0 |
| [aiogram](https://pypi.org/project/aiogram/3.31.0/) | 3.31.0 | MIT |
| [aiohttp](https://pypi.org/project/aiohttp/3.14.4/) | 3.14.4 | Apache-2.0 AND MIT |
| [pydantic](https://pypi.org/project/pydantic/2.13.5/) | 2.13.5 | MIT |
| [pydantic-settings](https://pypi.org/project/pydantic-settings/2.15.0/) | 2.15.0 | MIT |
| [Jinja2](https://pypi.org/project/Jinja2/3.1.6/) | 3.1.6 | BSD License |
| [aiosqlite](https://pypi.org/project/aiosqlite/0.20.0/) | 0.20.0 | MIT License |
| [cryptography](https://pypi.org/project/cryptography/50.0.2/) | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [httpx](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | BSD-3-Clause |
| [aiohttp-socks](https://pypi.org/project/aiohttp-socks/0.12.0/) | 0.12.0 | Apache-2.0 |
| [qrcode](https://pypi.org/project/qrcode/8.2/) | 8.2 | BSD |
| [Pillow](https://pypi.org/project/Pillow/12.3.0/) | 12.3.0 | MIT-CMU |
| [PyJWT](https://pypi.org/project/PyJWT/2.15.1/) | 2.15.1 | MIT |
| [bcrypt](https://pypi.org/project/bcrypt/4.3.0/) | 4.3.0 | Apache-2.0 |
| [pyotp](https://pypi.org/project/pyotp/2.9.0/) | 2.9.0 | MIT License |
| [alembic](https://pypi.org/project/alembic/1.20.0/) | 1.20.0 | MIT |
| [psutil](https://pypi.org/project/psutil/7.2.2/) | 7.2.2 | BSD-3-Clause |

## Пакеты frontend из lock-файла

Включены также зависимости сборки и транзитивные пакеты. `npm ci` выбирает платформенные бинарники для текущей ОС.

| Пакет | Версия | Лицензия по lock-файлу |
| --- | --- | --- |
| @jridgewell/gen-mapping | 0.3.13 | MIT |
| @jridgewell/remapping | 2.3.5 | MIT |
| @jridgewell/resolve-uri | 3.1.2 | MIT |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| @jridgewell/trace-mapping | 0.3.31 | MIT |
| @oxc-project/types | 0.151.0 | MIT |
| @oxlint/binding-android-arm-eabi | 1.86.0 | MIT |
| @oxlint/binding-android-arm64 | 1.86.0 | MIT |
| @oxlint/binding-darwin-arm64 | 1.86.0 | MIT |
| @oxlint/binding-darwin-x64 | 1.86.0 | MIT |
| @oxlint/binding-freebsd-x64 | 1.86.0 | MIT |
| @oxlint/binding-linux-arm-gnueabihf | 1.86.0 | MIT |
| @oxlint/binding-linux-arm-musleabihf | 1.86.0 | MIT |
| @oxlint/binding-linux-arm64-gnu | 1.86.0 | MIT |
| @oxlint/binding-linux-arm64-musl | 1.86.0 | MIT |
| @oxlint/binding-linux-ppc64-gnu | 1.86.0 | MIT |
| @oxlint/binding-linux-riscv64-gnu | 1.86.0 | MIT |
| @oxlint/binding-linux-riscv64-musl | 1.86.0 | MIT |
| @oxlint/binding-linux-s390x-gnu | 1.86.0 | MIT |
| @oxlint/binding-linux-x64-gnu | 1.86.0 | MIT |
| @oxlint/binding-linux-x64-musl | 1.86.0 | MIT |
| @oxlint/binding-openharmony-arm64 | 1.86.0 | MIT |
| @oxlint/binding-win32-arm64-msvc | 1.86.0 | MIT |
| @oxlint/binding-win32-ia32-msvc | 1.86.0 | MIT |
| @oxlint/binding-win32-x64-msvc | 1.86.0 | MIT |
| @rolldown/binding-android-arm-eabi | 1.2.11 | MIT |
| @rolldown/binding-android-arm64 | 1.2.11 | MIT |
| @rolldown/binding-darwin-arm64 | 1.2.11 | MIT |
| @rolldown/binding-darwin-x64 | 1.2.11 | MIT |
| @rolldown/binding-freebsd-x64 | 1.2.11 | MIT |
| @rolldown/binding-linux-arm-gnueabihf | 1.2.11 | MIT |
| @rolldown/binding-linux-arm64-gnu | 1.2.11 | MIT |
| @rolldown/binding-linux-arm64-musl | 1.2.11 | MIT |
| @rolldown/binding-linux-ppc64-gnu | 1.2.11 | MIT |
| @rolldown/binding-linux-s390x-gnu | 1.2.11 | MIT |
| @rolldown/binding-linux-x64-gnu | 1.2.11 | MIT |
| @rolldown/binding-linux-x64-musl | 1.2.11 | MIT |
| @rolldown/binding-openharmony-arm64 | 1.2.11 | MIT |
| @rolldown/binding-win32-arm64-msvc | 1.2.11 | MIT |
| @rolldown/binding-win32-x64-msvc | 1.2.11 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @tailwindcss/node | 4.3.3 | MIT |
| @tailwindcss/node/node_modules/lightningcss | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-android-arm64 | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-darwin-arm64 | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-darwin-x64 | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-freebsd-x64 | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-linux-arm-gnueabihf | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-linux-arm64-gnu | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-linux-arm64-musl | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-linux-x64-gnu | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-linux-x64-musl | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-win32-arm64-msvc | 1.32.0 | MPL-2.0 |
| @tailwindcss/node/node_modules/lightningcss-win32-x64-msvc | 1.32.0 | MPL-2.0 |
| @tailwindcss/oxide | 4.3.3 | MIT |
| @tailwindcss/oxide-android-arm64 | 4.3.3 | MIT |
| @tailwindcss/oxide-darwin-arm64 | 4.3.3 | MIT |
| @tailwindcss/oxide-darwin-x64 | 4.3.3 | MIT |
| @tailwindcss/oxide-freebsd-x64 | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm-gnueabihf | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm64-gnu | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-arm64-musl | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-x64-gnu | 4.3.3 | MIT |
| @tailwindcss/oxide-linux-x64-musl | 4.3.3 | MIT |
| @tailwindcss/oxide-wasm32-wasi | 4.3.3 | MIT |
| @tailwindcss/oxide-win32-arm64-msvc | 4.3.3 | MIT |
| @tailwindcss/oxide-win32-x64-msvc | 4.3.3 | MIT |
| @tailwindcss/vite | 4.3.3 | MIT |
| @tanstack/query-core | 5.104.0 | MIT |
| @tanstack/react-query | 5.104.0 | MIT |
| @types/node | 24.19.0 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| @vitejs/plugin-react | 6.1.1 | MIT |
| autoprefixer | 10.6.1 | MIT |
| baseline-browser-mapping | 2.11.26 | Apache-2.0 |
| browserslist | 4.29.2 | MIT |
| caniuse-lite | 1.0.30001813 | CC-BY-4.0 |
| cookie | 1.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| electron-to-chromium | 1.5.440 | ISC |
| enhanced-resolve | 5.25.1 | MIT |
| escalade | 3.2.0 | MIT |
| fdir | 6.5.0 | MIT |
| fraction.js | 5.3.4 | MIT |
| fsevents | 2.3.3 | MIT |
| graceful-fs | 4.2.11 | ISC |
| jiti | 2.7.0 | MIT |
| lightningcss | 1.33.0 | MPL-2.0 |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 |
| lucide-react | 1.48.0 | ISC |
| magic-string | 0.30.21 | MIT |
| nanoid | 3.3.19 | MIT |
| node-releases | 2.0.57 | MIT |
| oxlint | 1.86.0 | MIT |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| postcss | 8.5.28 | MIT |
| postcss-value-parser | 4.2.0 | MIT |
| qrcode.react | 4.2.0 | ISC |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| react-router | 7.18.4 | MIT |
| react-router-dom | 7.18.4 | MIT |
| rolldown | 1.2.11 | MIT |
| scheduler | 0.28.0 | MIT |
| set-cookie-parser | 2.7.2 | MIT |
| source-map-js | 1.2.2 | BSD-3-Clause |
| tailwindcss | 4.3.3 | MIT |
| tapable | 2.3.3 | MIT |
| tinyglobby | 0.2.17 | MIT |
| typescript | 6.0.3 | Apache-2.0 |
| undici-types | 7.24.6 | MIT |
| update-browserslist-db | 1.3.3 | MIT |
| vite | 8.3.1 | MIT |

Для собственной сборки или контейнера сохраните LICENSE/NOTICE распространяемых пакетов. MIT-лицензия панели не отменяет этих требований. Отдельно устанавливаемые GPL-компоненты и MPL-код Xray остаются под своими лицензиями; перед включением их исходников или бинарников в единый распространяемый продукт нужна отдельная проверка его состава.
