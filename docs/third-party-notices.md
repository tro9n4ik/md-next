# Сторонние компоненты

MIT-лицензия MD-Next распространяется на код этого проекта. Сторонние компоненты сохраняют собственные лицензии. Репозиторий не содержит готовые бинарники Xray или AmneziaWG: установщик получает их из upstream либо собирает отдельно.

| Компонент | Лицензия upstream | Исходники и текст лицензии |
| --- | --- | --- |
| Xray-core | Mozilla Public License 2.0 | [XTLS/Xray-core](https://github.com/XTLS/Xray-core), [LICENSE](https://github.com/XTLS/Xray-core/blob/main/LICENSE) |
| AmneziaWG Tools | GNU GPL, версия 2 | [amnezia-vpn/amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools), [COPYING](https://github.com/amnezia-vpn/amneziawg-tools/blob/master/COPYING) |
| AmneziaWG Go | MIT | [amnezia-vpn/amneziawg-go](https://github.com/amnezia-vpn/amneziawg-go), [LICENSE](https://github.com/amnezia-vpn/amneziawg-go/blob/master/LICENSE) |
| AmneziaWG Linux kernel module | GNU GPL, версия 2 | [Исходники](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module), [COPYING](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module/blob/master/COPYING) |

Установка и взаимодействие с отдельными процессами не меняют их лицензии. При распространении собственных образов, архивов с бинарниками или модифицированных сборок отдельно проверяйте условия соответствующих лицензий, сохраняйте уведомления и обеспечивайте доступ к соответствующим исходникам там, где это требуется. Эта таблица не разрешает перелицензировать сторонние компоненты под MIT.

Python-зависимости перечислены в `backend/requirements.txt`, JavaScript-зависимости и их версии — в `frontend/package-lock.json`. Они устанавливаются из PyPI/npm; тексты лицензий поставляются с соответствующими пакетами. Перед распространением собранного образа составляйте перечень также транзитивных зависимостей и включайте их уведомления. Системные пакеты Nginx, WireGuard/AWG, Go, Node.js и необязательный Krawl нужно учитывать отдельно в зависимости от состава распространяемой сборки.

[Перечень лицензий прямых Python-зависимостей и всех пакетов frontend из lock-файла](dependency-licenses.md) содержит версии и лицензионные выражения. Среди зависимостей сборки есть Lightning CSS (MPL-2.0) и данные caniuse-lite (CC-BY-4.0): нельзя считать весь состав сборки лицензированным исключительно под MIT.

Иконки интерфейса: [Lucide](https://lucide.dev), авторы Lucide Icons and Contributors; часть иконок происходит из Feather, автор Cole Bemis. Условия ISC и MIT приведены в [upstream LICENSE](https://github.com/lucide-icons/lucide/blob/main/LICENSE). Сохраняйте эти уведомления в распространяемой сборке.
