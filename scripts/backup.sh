#!/bin/bash
# Резервное копирование MD-Next: база данных, .env и конфигурации сервисов.
#
# Использование:
#   bash scripts/backup.sh            # создать резервную копию
#   bash scripts/backup.sh --list     # показать существующие копии
#   bash scripts/backup.sh --help     # справка
#
# Переменные окружения (необязательно):
#   APP_DIR      каталог установки      (по умолчанию /opt/md-next)
#   BACKUP_ROOT  каталог для копий      (по умолчанию /var/backups/md-next)
#   KEEP         сколько копий хранить  (по умолчанию 7)

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/md-next}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/md-next}"
KEEP="${KEEP:-7}"
PREFIX="md-next-backup-"

usage() {
  # Печатаем шапку-комментарий до первой пустой строки, снимая ведущий "# ".
  # Используем awk, а не sed: в sed проверка /^$/ срабатывала бы на уже
  # очищенной строке и обрывала вывод после первой же строки.
  awk 'NR==1 { next } /^[[:space:]]*$/ { exit } { sub(/^# ?/, ""); print }' "$0"
}

list_backups() {
  if [ ! -d "$BACKUP_ROOT" ]; then
    echo "Копий пока нет ($BACKUP_ROOT не существует)."
    return 0
  fi
  local found=0
  local file
  for file in "$BACKUP_ROOT/${PREFIX}"*.tar.gz; do
    [ -e "$file" ] || continue
    found=1
    printf '%s  %s\n' "$(date -r "$file" '+%Y-%m-%d %H:%M:%S')" "$file"
  done
  if [ "$found" -eq 0 ]; then
    echo "Копий пока нет в $BACKUP_ROOT."
  fi
}

case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  --list|-l)  list_backups; exit 0 ;;
  "")         ;;
  *)          echo "Неизвестный аргумент: $1" >&2; usage >&2; exit 2 ;;
esac

case "$KEEP" in
  ''|*[!0-9]*|0) echo "KEEP должен быть положительным числом." >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root: sudo bash scripts/backup.sh" >&2
  exit 1
fi

if [ ! -d "$APP_DIR" ]; then
  echo "Каталог установки не найден: $APP_DIR" >&2
  echo "Задайте путь через APP_DIR=/path/to/md-next" >&2
  exit 1
fi

STAMP="$(date '+%Y%m%d-%H%M%S')"
WORK_DIR="$(mktemp -d /tmp/${PREFIX}XXXXXX)"
ARCHIVE="$BACKUP_ROOT/${PREFIX}${STAMP}.tar.gz"
COPIED=0

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

# Копирование файла в рабочий каталог с сохранением структуры.
# Пустые аргументы игнорируются, отсутствующие файлы не считаются ошибкой.
add_file() {
  local src="$1"
  [ -n "$src" ] || return 0
  [ -f "$src" ] || return 0
  local rel="${src#/}"
  local dest="$WORK_DIR/files/$rel"
  mkdir -p "$(dirname "$dest")"
  cp -a "$src" "$dest"
  COPIED=$((COPIED + 1))
  printf '  %s\n' "$src"
}

# Поиск рабочего Python с модулем sqlite3.
# Проверяем не наличие команды, а реальную работоспособность: в некоторых
# системах есть python3-«заглушка», которая ничего не выполняет.
find_python() {
  local candidate
  for candidate in \
    "${PYTHON_BIN:-}" \
    "$APP_DIR/backend/venv/bin/python" \
    python3 \
    python; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sqlite3' >/dev/null 2>&1 || continue
    printf '%s\n' "$candidate"
    return 0
  done
  return 1
}

# Копирование базы SQLite через Backup API: результат консистентен,
# даже если backend продолжает писать в базу. Обычное копирование файла
# в такой момент может дать повреждённую базу.
sqlite_backup() {
  local src="$1"
  local dest="$2"
  local py
  py="$(find_python)" || return 1
  "$py" - "$src" "$dest" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
# Открываем обычным подключением, а не в режиме mode=ro: если база работает
# в режиме WAL, SQLite нужно иметь возможность обработать файлы -wal/-shm,
# иначе открытие строго на чтение завершается ошибкой.
with sqlite3.connect(source) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check;").fetchone()
if not result or result[0] != "ok":
    sys.exit(f"Ошибка проверки целостности: {result}")
PY
}

backup_sqlite() {
  local src="$1"
  [ -f "$src" ] || return 0

  local rel="${src#/}"
  local dest="$WORK_DIR/files/$rel"
  mkdir -p "$(dirname "$dest")"

  if ! sqlite_backup "$src" "$dest"; then
    echo "  ! не удалось сделать резервную копию базы: $src" >&2
    echo "    требуется sqlite3 либо рабочий python3 с модулем sqlite3" >&2
    return 1
  fi

  if [ ! -s "$dest" ]; then
    echo "  ! копия базы пустая: $dest" >&2
    return 1
  fi

  printf '  %s (API резервного копирования, целостность подтверждена)\n' "$src"
  COPIED=$((COPIED + 1))
}

echo "Резервное копирование MD-Next"
echo "  Источник: $APP_DIR"
echo "  Назначение: $BACKUP_ROOT"

if [ ! -d "$APP_DIR/backend" ]; then
  echo "  ! каталог $APP_DIR/backend не найден" >&2
  exit 1
fi

echo "Копирование базы данных:"
backup_sqlite "$APP_DIR/backend/md_next.db" || {
  echo "Не удалось сделать резервную копию базы данных." >&2
  exit 1
}

echo "Копирование настроек и конфигураций:"
add_file "$APP_DIR/backend/.env"
add_file "/usr/local/etc/xray/config.json"
add_file "/etc/amnezia/amneziawg/awg0.conf"
add_file "/etc/nginx/sites-available/md-next.conf"

if [ "$COPIED" -eq 0 ]; then
  echo "Нечего копировать: не найдено ни базы, ни конфигураций." >&2
  rm -rf "$WORK_DIR"
  trap - EXIT
  exit 1
fi

# Манифест: что вошло в копию и когда она сделана.
cat > "$WORK_DIR/manifest.txt" <<EOF
Резервная копия MD-Next
Дата: $(date '+%Y-%m-%d %H:%M:%S %Z')
Источник: $APP_DIR
Версия приложения: $(cat "$APP_DIR/backend/VERSION" 2>/dev/null || echo 'неизвестно')
Объектов: $COPIED
EOF

mkdir -p "$BACKUP_ROOT"
tar -czf "$ARCHIVE" -C "$WORK_DIR" .
chmod 600 "$ARCHIVE"

if [ ! -s "$ARCHIVE" ]; then
  echo "Архив не создан или пуст: $ARCHIVE" >&2
  exit 1
fi

SIZE="$(du -h "$ARCHIVE" | cut -f1)"

# Ротация: удаляем старые копии, оставляя последние KEEP.
OLD_REMOVED=0
while IFS= read -r old; do
  [ -n "$old" ] || continue
  rm -f "$old"
  OLD_REMOVED=$((OLD_REMOVED + 1))
done < <(ls -1t "$BACKUP_ROOT/${PREFIX}"*.tar.gz 2>/dev/null | tail -n "+$((KEEP + 1))")

echo
echo "Резервная копия создана: $ARCHIVE ($SIZE)"
echo "Объектов в копии: $COPIED"
if [ "$OLD_REMOVED" -gt 0 ]; then
  echo "Удалено старых копий: $OLD_REMOVED (хранится последних $KEEP)"
fi