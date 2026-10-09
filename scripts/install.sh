#!/bin/bash
# Автоматический скрипт установки панели MD-Next v2.2.0

set -e

export DEBIAN_FRONTEND=noninteractive
LOG_FILE="/tmp/md-next-install.log"
echo "=== MD-Next Installation Log Started: $(date) ===" > "$LOG_FILE"

# Цвета для вывода
GREEN='\031[0;32m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Пожалуйста, запустите скрипт от имени root (sudo)${NC}"
  exit 1
fi

echo "================================================="
echo "              MD-Next Management                 "
echo "================================================="
echo "1) Установить"
echo "2) Обновить"
echo "3) Удалить"
echo ""
ACTION="${1:-}"
if [ -z "$ACTION" ]; then
  read -r -p "Выберите действие [1-3]: " ACTION
fi

case "$ACTION" in
  1) ACTION="install" ;;
  2) ACTION="update" ;;
  3|remove) ACTION="remove" ;;
  *) echo "Неверный выбор."; exit 1 ;;
esac

APP_DIR="/opt/md-next"
EXISTING_INSTALL=false
[ ! -f "$APP_DIR/backend/.env" ] || EXISTING_INSTALL=true
REPOSITORY_URL="https://github.com/tro9n4ik/md-next.git"

update_nginx_routes() {
  local nginx_config="/etc/nginx/sites-available/md-next.conf"
  local env_file="$APP_DIR/backend/.env"
  [ -f "$nginx_config" ] || return 0

  local xhttp_path
  xhttp_path="$(sed -n 's/^XRAY_XHTTP_TLS_PATH=//p' "$env_file" 2>/dev/null | tail -n 1)"
  if [ -z "$xhttp_path" ]; then
    xhttp_path="/$(openssl rand -hex 16)"
    echo "XRAY_XHTTP_TLS_PATH=$xhttp_path" >> "$env_file"
    chmod 600 "$env_file"
  fi

  python3 - "$nginx_config" "$xhttp_path" <<'PY'
import re
import sys

config_path, xhttp_path = sys.argv[1:]
with open(config_path, "r", encoding="utf-8") as source:
    text = source.read()
starts = [match.start() for match in re.finditer(r"(?m)^\s*server\s*\{", text)]
segments = []
for index, start in enumerate(starts):
    end = starts[index + 1] if index + 1 < len(starts) else len(text)
    segments.append((start, end, text[start:end]))

for start, end, segment in reversed(segments):
    if "listen 127.0.0.1:8443" in segment and "location /sub/" not in segment:
        api = re.search(r"(?s)(\n\s*location /api/\s*\{.*?\n\s*\})", segment)
        if api:
            route = """
    location /sub/ {
        proxy_pass http://127.0.0.1:8000/api/v1/sub/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
"""
            segment = segment[:api.end()] + route + segment[api.end():]
    if "listen 127.0.0.1:8080" in segment:
        block = f"""    # MD_NEXT_XHTTP_TLS_BEGIN
    location {xhttp_path} {{
        proxy_pass http://127.0.0.1:8446;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        client_max_body_size 0;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
    # MD_NEXT_XHTTP_TLS_END
"""
        marker = re.compile(r"\s*# MD_NEXT_XHTTP_TLS_BEGIN.*?# MD_NEXT_XHTTP_TLS_END\s*", re.S)
        if marker.search(segment):
            segment = marker.sub("\n" + block, segment)
        else:
            location = re.search(r"(?m)^\s*location /\s*\{", segment)
            if location:
                segment = segment[:location.start()] + block + segment[location.start():]
    text = text[:start] + segment + text[end:]

with open(config_path, "w", encoding="utf-8") as target:
    target.write(text)
PY

  nginx -t
  systemctl reload nginx
}

clone_repo() {
  local target="$1"
  local askpass_script=""
  local askpass_env=()

  # Приватный репозиторий требует авторизации. Токен берётся из переменной
  # окружения и не сохраняется ни в файле, ни в истории команд.
  if [ -n "${MDNEXT_GITHUB_TOKEN:-}" ]; then
    askpass_script="$(mktemp /tmp/md-next-askpass.XXXXXX)"
    chmod 700 "$askpass_script"
    cat > "$askpass_script" <<'EOF'
#!/bin/sh
case "$1" in
  *Username*) echo "x-access-token" ;;
  *) echo "$MDNEXT_GITHUB_TOKEN" ;;
esac
EOF
    askpass_env=(GIT_ASKPASS="$askpass_script" MDNEXT_GITHUB_TOKEN="$MDNEXT_GITHUB_TOKEN")
  fi

  env "${askpass_env[@]}" GIT_TERMINAL_PROMPT=0 \
    git -c credential.helper= clone --depth 1 "$REPOSITORY_URL" "$target"
  local clone_status=$?

  [ -n "$askpass_script" ] && rm -f "$askpass_script"
  unset MDNEXT_GITHUB_TOKEN

  if [ "$clone_status" -ne 0 ]; then
    echo -e "${RED}Не удалось получить код с GitHub. Для приватного репозитория задайте токен:${NC}"
    echo "  export MDNEXT_GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx"
    echo "  bash install.sh   # затем выберите «Обновить»"
    exit 1
  fi
}

ensure_probe_env() {
  local env_file="$APP_DIR/backend/.env"
  [ -f "$env_file" ] || return 0

  # Значения дописываются построчно, а не перезаписываются: обновление не должно
  # затирать домены, ключи и пароли, которые уже настроены на сервере.
  local probe_line probe_key
  for probe_line in \
    "XRAY_PROBE_URL=${XRAY_PROBE_URL:-https://cp.cloudflare.com/generate_204}" \
    "XRAY_PROBE_INTERVAL=${XRAY_PROBE_INTERVAL:-3m}" \
    "NODE_PROBE_ENABLED=${NODE_PROBE_ENABLED:-true}" \
    "NODE_PROBE_BASE_PORT=${NODE_PROBE_BASE_PORT:-10900}" \
    "NODE_PROBE_TIMEOUT=${NODE_PROBE_TIMEOUT:-8}"; do
    probe_key="${probe_line%%=*}"
    if grep -q "^${probe_key}=" "$env_file"; then
      sed -i "s|^${probe_key}=.*|${probe_line}|" "$env_file"
    else
      printf '%s\n' "$probe_line" >> "$env_file"
    fi
  done
  chmod 600 "$env_file"
}

wait_backend_ready() {
  local attempt
  for attempt in $(seq 1 60); do
    if curl --silent --output /dev/null --max-time 2 http://127.0.0.1:8000/; then
      return 0
    fi
    sleep 1
  done
  journalctl -u md-next-backend.service -n 40 --no-pager >&2
  return 1
}

write_backend_service() {
  cat <<'EOF' > /etc/systemd/system/md-next-backend.service
[Unit]
Description=MD-Next FastAPI Backend
After=network.target

[Service]
User=root
WorkingDirectory=/opt/md-next/backend
Environment="PATH=/opt/md-next/backend/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EnvironmentFile=/opt/md-next/backend/.env
ExecStart=/opt/md-next/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF
}

install_awg_routing() {
  apt-get install -y -qq iproute2 iptables
  mkdir -p /etc/sysctl.d
  printf '%s\n' 'net.ipv4.ip_forward=1' > /etc/sysctl.d/90-md-next-awg.conf
  sysctl -p /etc/sysctl.d/90-md-next-awg.conf
  mkdir -p /etc/systemd/system/xray.service.d
  cat > /etc/systemd/system/xray.service.d/30-md-next-awg-routing.conf <<'EOF'
[Service]
ExecStartPost=-+/usr/bin/python3 /opt/md-next/scripts/awg-routing.py
EOF
  systemctl daemon-reload
}

install_awg() {
  if [ ! -r /etc/os-release ]; then
    echo "Не удалось определить дистрибутив Linux для установки AmneziaWG." >&2
    return 1
  fi

  . /etc/os-release
  apt-get update -qq || return 1
  apt-get install -y -qq software-properties-common python3-launchpadlib gnupg2 git curl || return 1

  case "${ID:-}" in
    ubuntu)
      add-apt-repository -y ppa:amnezia/ppa || return 1
      ;;
    debian)
      local awg_key="/usr/share/keyrings/md-next-amnezia.gpg"
      curl -fsSL 'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x75C9DD72C799870E310542E24166F2C257290828' | gpg --dearmor --yes -o "$awg_key" || return 1
      local awg_repo="/etc/apt/sources.list.d/amneziawg.list"
      if [ ! -f "$awg_repo" ] || ! grep -q "ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main" "$awg_repo"; then
        cat > "$awg_repo" <<'EOF'
deb [signed-by=/usr/share/keyrings/md-next-amnezia.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu jammy main
EOF
      fi
      ;;
    *)
      echo "Установка AmneziaWG поддерживается только на Ubuntu и Debian (обнаружен ${ID:-неизвестный дистрибутив})." >&2
      return 1
      ;;
  esac

  apt-get update -qq || return 1
  apt-get install -y -qq amneziawg-tools || return 1
  if apt-get install -y -qq "linux-headers-$(uname -r)"; then
    apt-get install -y -qq amneziawg-dkms || return 1
    dkms autoinstall -k "$(uname -r)" || return 1
  fi
  if ! command -v awg >/dev/null 2>&1; then
    echo "Пакет AmneziaWG установлен, но команда awg не найдена." >&2
    return 1
  fi

  if ! modprobe amneziawg; then
    bash "$(dirname "${BASH_SOURCE[0]}")/install-awg-userspace.sh" || return 1
    return 0
  fi
  local awg_module_version=""
  if [ -r /sys/module/amneziawg/version ]; then
    awg_module_version="$(cat /sys/module/amneziawg/version)"
  fi
  case "$awg_module_version" in
    3.1.*) ;;
    *)
      echo "Нужен AmneziaWG 3.1, установленный модуль сообщает версию '${awg_module_version:-неизвестно}'. Обновите ядро/пакеты и повторите установку." >&2
      return 1
      ;;
  esac
}

update_installation() {
  if [ ! -d "$APP_DIR/backend" ] || [ ! -f /etc/systemd/system/md-next-backend.service ]; then
    echo "Установка MD-Next не найдена в $APP_DIR."
    exit 1
  fi

  local backup_dir="/root/md-next-backup-$(date +%Y%m%d-%H%M%S)"
  local update_dir
  update_dir="$(mktemp -d /tmp/md-next-update.XXXXXX)"
  mkdir -p "$backup_dir"
  [ ! -f "$APP_DIR/backend/.env" ] || cp -a "$APP_DIR/backend/.env" "$backup_dir/"
  [ ! -f "$APP_DIR/backend/md_next.db" ] || cp -a "$APP_DIR/backend/md_next.db" "$backup_dir/"
  [ ! -d "$APP_DIR/backend/app/static/fake" ] || cp -a "$APP_DIR/backend/app/static/fake" "$backup_dir/fake"

  echo "Резервная копия настроек и базы: $backup_dir"
  clone_repo "$update_dir/repo"
  cp -a "$update_dir/repo/." "$APP_DIR/"
  [ ! -d "$backup_dir/fake" ] || cp -a "$backup_dir/fake/." "$APP_DIR/backend/app/static/fake/"
  rm -rf "$update_dir"
  ensure_probe_env
  update_nginx_routes
  install_awg
  install_awg_routing

  cd "$APP_DIR/backend"
  python3 -m venv venv
  venv/bin/pip install --quiet -r requirements.txt
  venv/bin/alembic upgrade head

  cd "$APP_DIR/frontend"
  npm install --silent --no-audit --no-fund
  npm run build --silent

  write_backend_service
  systemctl daemon-reload
  systemctl restart md-next-backend
  wait_backend_ready
  systemctl reload nginx
  echo "MD-Next успешно обновлён. Резервная копия: $backup_dir"
}

remove_installation() {
  if [ ! -d "$APP_DIR" ] && [ ! -f /etc/systemd/system/md-next-backend.service ]; then
    echo "Установка MD-Next не найдена."
    exit 1
  fi

  echo "Будут остановлены и удалены MD-Next и его конфигурация Nginx."
  echo "Системные пакеты и сертификаты Let's Encrypt останутся на сервере."
  read -r -p "Для подтверждения введите УДАЛИТЬ: " confirmation
  if [ "$confirmation" != "УДАЛИТЬ" ]; then
    echo "Удаление отменено."
    exit 0
  fi

  systemctl disable --now md-next-backend 2>/dev/null || true
  local owns_xray=0
  local owns_awg=0
  if [ -f /var/lib/md-next/awg-config-owned ] || [ -f /var/lib/md-next/awg-routing-owned ]; then
    owns_awg=1
  fi
  if [ -f /var/lib/md-next/xray-owned ] || [ -f /var/lib/md-next/awg-routing-owned ]; then
    owns_xray=1
    systemctl disable --now xray 2>/dev/null || true
  fi
  # Remove owned AWG policy before deleting its helper or state marker.
  local routing_helper
  routing_helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/awg-routing.py"
  if [ -f "$routing_helper" ]; then
    /usr/bin/python3 "$routing_helper" --remove
  elif [ -f /var/lib/md-next/awg-routing-owned ]; then
    echo "Не найден помощник AWG. Восстановите scripts/awg-routing.py перед удалением."
    exit 1
  fi
  systemctl disable --now md-next-awg awg-quick@awg0 2>/dev/null || true
  if [ "$owns_awg" -eq 1 ] && ip link show awg0 >/dev/null 2>&1; then
    awg-quick down awg0 || ip link delete awg0
  fi
  rm -f /etc/sysctl.d/90-md-next-awg.conf
  rm -f /etc/systemd/system/md-next-awg.service /etc/systemd/system/xray.service.d/md-next-awg.conf /etc/systemd/system/xray.service.d/30-md-next-awg-routing.conf
  rm -f /etc/systemd/system/md-next-backend.service
  rm -f /etc/letsencrypt/renewal-hooks/deploy/md-next-xray-certificate.sh
  rm -f /etc/nginx/sites-enabled/md-next.conf /etc/nginx/sites-available/md-next.conf
  rm -f /etc/nginx/stream-enabled/md-next-stream.conf /etc/nginx/stream-available/md-next-stream.conf
  systemctl daemon-reload
  systemctl restart nginx 2>/dev/null || true

  read -r -p "Удалить базу данных и файлы приложения из $APP_DIR? [y/N]: " delete_data
  if [[ "$delete_data" =~ ^[Yy]$ ]]; then
    if [ "$owns_awg" -eq 1 ]; then
      rm -f /etc/amnezia/amneziawg/awg0.conf
    fi
    if [ "$owns_xray" -eq 1 ]; then
      rm -f /usr/local/etc/xray/config.json /usr/local/etc/xray/tls/fullchain.pem /usr/local/etc/xray/tls/privkey.pem
    fi
    rm -rf "$APP_DIR"
    rm -rf /var/lib/md-next
    echo "Приложение и его данные удалены."
  else
    echo "Конфигурация удалена; данные приложения сохранены в $APP_DIR."
  fi
}

if [ "$ACTION" = "update" ]; then
  update_installation
  exit 0
elif [ "$ACTION" = "remove" ]; then
  remove_installation
  exit 0
fi

echo "================================================="
echo "          md-next v2.2.0 Installation            "
echo "================================================="
echo ""

read -p "Введите основной домен для сайта-заглушки и VLESS (например, example.com): " MAIN_DOMAIN
read -p "Введите поддомен для панели управления (например, panel.example.com): " PANEL_DOMAIN
read -p "Введите Email администратора (для Let's Encrypt): " ADMIN_EMAIL
label='[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
domain_pattern="^($label[.])+$label$"
if [ "${#MAIN_DOMAIN}" -gt 253 ] || [ "${#PANEL_DOMAIN}" -gt 253 ] || \
   ! [[ "$MAIN_DOMAIN" =~ $domain_pattern && "$PANEL_DOMAIN" =~ $domain_pattern ]]; then
  echo 'Укажите домены в ASCII/punycode без протокола, порта, пути и специальных символов.' >&2
  exit 1
fi
if ! [[ "$ADMIN_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$ ]]; then
  echo 'Укажите корректный email администратора.' >&2
  exit 1
fi
echo ""

# Генерация случайных ключей и паролей
# Exactly 16 hexadecimal characters avoid shell/terminal-sensitive password characters.
ADMIN_PASSWORD=$(openssl rand -hex 8)
JWT_SECRET_KEY=$(openssl rand -hex 32)

run_step() {
  local step_name="$1"
  shift
  printf "  ▸ %-52s " "$step_name..."
  if "$@" >> "$LOG_FILE" 2>&1; then
    printf "[${GREEN}OK${NC}]\n"
  else
    printf "[${RED}FAIL${NC}]\n"
    echo ""
    echo -e "${RED}Ошибка при выполнении шага: $step_name${NC}"
    echo "Подробности см. в лог-файле: $LOG_FILE"
    echo "-------------------------------------------------"
    echo "Последние 25 строк лога:"
    tail -n 25 "$LOG_FILE"
    echo "-------------------------------------------------"
    exit 1
  fi
}

install_deps() {
  apt-get update -qq
  apt-get install -y -qq python3 python3-pip python3-venv git curl build-essential nginx certbot python3-certbot-nginx lsb-release gnupg libnginx-mod-stream
  normalize_domains
}

normalize_domains() {
  local original_main="$MAIN_DOMAIN"
  local original_panel="$PANEL_DOMAIN"
  local normalized_main normalized_panel

  normalized_main="$(python3 - "$MAIN_DOMAIN" <<'PY'
import re
import sys

value = sys.argv[1].strip().rstrip(".")
try:
    domain = value.encode("idna").decode("ascii").lower()
except UnicodeError as exc:
    raise SystemExit(f"Некорректное доменное имя: {exc}")
if len(domain) > 253 or not re.fullmatch(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
    domain,
):
    raise SystemExit("Укажите корректное доменное имя, например panel.example.com")
print(domain)
PY
  )" || return 1

  normalized_panel="$(python3 - "$PANEL_DOMAIN" <<'PY'
import re
import sys

value = sys.argv[1].strip().rstrip(".")
try:
    domain = value.encode("idna").decode("ascii").lower()
except UnicodeError as exc:
    raise SystemExit(f"Некорректное доменное имя: {exc}")
if len(domain) > 253 or not re.fullmatch(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
    domain,
):
    raise SystemExit("Укажите корректное доменное имя, например panel.example.com")
print(domain)
PY
  )" || return 1

  if [ "$normalized_main" = "$normalized_panel" ]; then
    echo "Основной домен и домен панели должны отличаться." >&2
    return 1
  fi

  MAIN_DOMAIN="$normalized_main"
  PANEL_DOMAIN="$normalized_panel"
  if [ "$MAIN_DOMAIN" != "$original_main" ] || [ "$PANEL_DOMAIN" != "$original_panel" ]; then
    echo "Домены преобразованы в Punycode для Nginx и Certbot: $MAIN_DOMAIN, $PANEL_DOMAIN"
  fi
}

install_warp() {
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list
  apt-get update -qq
  apt-get install -y -qq cloudflare-warp
}

install_node() {
  local installer result=0
  installer=$(mktemp) || return 1
  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 https://deb.nodesource.com/setup_24.x -o "$installer"; then
    rm -f -- "$installer"
    return 1
  fi
  bash "$installer" || result=$?
  rm -f -- "$installer"
  [ "$result" -eq 0 ] || return "$result"
  apt-get install -y -qq nodejs || return 1
}

install_xray() {
  local installer result=0
  installer=$(mktemp) || return 1
  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
    https://raw.githubusercontent.com/XTLS/Xray-install/e741a4f56d368afbb9e5be3361b40c4552d3710d/install-release.sh -o "$installer"; then
    rm -f -- "$installer"
    return 1
  fi
  if ! printf '%s  %s\n' '7f70c95f6b418da8b4f4883343d602964915e28748993870fd554383afdbe555' "$installer" | sha256sum -c -; then
    rm -f -- "$installer"
    return 1
  fi
  bash "$installer" install || result=$?
  if [ "$result" -eq 0 ] && [ ! -s /usr/local/share/xray/geosite.dat ]; then
    bash "$installer" install-geodata || result=$?
  fi
  # Xray ищет geodata рядом с бинарником, если путь не задан в окружении.
  if [ "$result" -eq 0 ] && [ ! -e /usr/local/bin/geosite.dat ]; then
    ln -sfn /usr/local/share/xray/geosite.dat /usr/local/bin/geosite.dat || result=$?
  fi
  rm -f -- "$installer"
  return "$result"
}

setup_repo() {
  local placeholder_backup=""
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
  mkdir -p /opt/md-next
  if [ "$SCRIPT_DIR" != "/opt/md-next" ]; then
    if [ -f /opt/md-next/backend/app/static/fake/index.html ]; then
      placeholder_backup=$(mktemp -d)
      cp -a /opt/md-next/backend/app/static/fake/. "$placeholder_backup/"
    fi
    cp -r "$SCRIPT_DIR/"* /opt/md-next/ 2>/dev/null || true
    if [ -n "$placeholder_backup" ]; then
      cp -a "$placeholder_backup/." /opt/md-next/backend/app/static/fake/
      rm -rf -- "$placeholder_backup"
    fi
  fi
  cd /opt/md-next
}

setup_placeholder() {
  # Существующая установка сохраняет заглушку, даже если метаданных ещё нет.
  if [ "$EXISTING_INSTALL" = "true" ]; then
    return 0
  fi
  (cd /opt/md-next/backend && venv/bin/python -m app.services.placeholder --initialize)
}

setup_backend() {
  cd /opt/md-next/backend
  python3 -m venv venv
  source venv/bin/activate
  pip install --quiet -r requirements.txt

  # Ключи Reality переиспользуются из существующей базы. Переустановка панели не должна
  # ломать ссылки всех клиентов: новый ключ означает, что прошлые ссылки перестанут работать.
  KEYS=$(MD_NEXT_DB="$APP_DIR/backend/md_next.db" python3 -c "
import base64, os, sqlite3, sys
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

path = os.environ.get('MD_NEXT_DB', '')
existing = None
if path and os.path.isfile(path):
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            rows = dict(con.execute(
                \"select key, value from settings where key in ('protocol.reality.private_key','protocol.reality.public_key')\"
            ).fetchall())
        finally:
            con.close()
        priv, pub = rows.get('protocol.reality.private_key', ''), rows.get('protocol.reality.public_key', '')
        if priv:
            # Публичный ключ обязан быть выведен из приватного. Сохранённую пару
            # сверяем: иначе рассинхронизация из базы переживает переустановку,
            # и все Reality-ссылки не проходят handshake без единой ошибки в панели.
            derived = base64.urlsafe_b64encode(
                x25519.X25519PrivateKey.from_private_bytes(
                    base64.urlsafe_b64decode(priv + '=' * (-len(priv) % 4))
                ).public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            ).decode().rstrip('=')
            existing = f'{priv}:{derived}'
            if pub and pub != derived:
                print(f'ИСПРАВЛЕНО: публичный ключ Reality не соответствовал приватному ({pub[:12]} -> {derived[:12]})', file=sys.stderr)
    except Exception:
        existing = None

if existing:
    print(existing)
else:
    priv = x25519.X25519PrivateKey.generate()
    pub = priv.public_key()
    priv_b64 = base64.urlsafe_b64encode(priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode().rstrip('=')
    pub_b64 = base64.urlsafe_b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip('=')
    print(f'{priv_b64}:{pub_b64}')
")
  XRAY_PRIV=$(echo "$KEYS" | cut -d':' -f1)
  XRAY_PUB=$(echo "$KEYS" | cut -d':' -f2)
  if [ -f "$APP_DIR/backend/md_next.db" ] && [ -n "$XRAY_PRIV" ]; then
    echo -e "${GREEN}Ключи Reality сохранены из существующей базы: ссылки клиентов не изменятся${NC}"
  fi

  # Секретный путь XHTTP TLS тоже должен пережить переустановку.
  XHTTP_TLS_PATH=""
  if [ -f "$APP_DIR/backend/md_next.db" ]; then
    XHTTP_TLS_PATH=$(MD_NEXT_DB="$APP_DIR/backend/md_next.db" python3 -c "
import os, sqlite3
path = os.environ.get('MD_NEXT_DB', '')
try:
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        row = con.execute(\"select value from settings where key = 'profiles.path.vless_xhttp_tls'\").fetchone()
    finally:
        con.close()
    print(row[0] if row and row[0] else '')
except Exception:
    print('')
")
  fi
  [ -n "$XHTTP_TLS_PATH" ] || XHTTP_TLS_PATH="/$(openssl rand -hex 16)"
  XRAY_XHTTP_TLS_PATH="$XHTTP_TLS_PATH"

  cat <<EOF > /opt/md-next/backend/.env
PANEL_PUBLIC_URL=https://$PANEL_DOMAIN
INITIAL_ADMIN_PASSWORD=$ADMIN_PASSWORD
JWT_SECRET_KEY=$JWT_SECRET_KEY
ALLOWED_ORIGINS=https://$PANEL_DOMAIN
XRAY_PRIVATE_KEY=$XRAY_PRIV
XRAY_PUBLIC_KEY=$XRAY_PUB
XRAY_DEST=127.0.0.1:8080
XRAY_SERVER_NAME=$MAIN_DOMAIN
SERVER_HOST=$MAIN_DOMAIN
XRAY_XHTTP_TLS_PATH=$XRAY_XHTTP_TLS_PATH
TLS_CERT_PATH=/usr/local/etc/xray/tls/fullchain.pem
TLS_KEY_PATH=/usr/local/etc/xray/tls/privkey.pem
XRAY_PROBE_URL=${XRAY_PROBE_URL:-https://cp.cloudflare.com/generate_204}
XRAY_PROBE_INTERVAL=${XRAY_PROBE_INTERVAL:-3m}
EOF
  chmod 600 /opt/md-next/backend/.env
  ensure_probe_env

  # Применение миграций базы данных Alembic
  venv/bin/alembic upgrade head

  # Keep the displayed installer password synchronized with the admin record,
  # including when a previous installation left the database behind.
  INITIAL_ADMIN_PASSWORD="$ADMIN_PASSWORD" venv/bin/python - <<'PY'
import asyncio
import os

from sqlalchemy import select
from app.api.auth import get_password_hash
from app.db.database import AsyncSessionLocal
from app.models.user import User

async def main():
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        password_hash = get_password_hash(os.environ["INITIAL_ADMIN_PASSWORD"])
        if user is None:
            db.add(User(username="admin", hashed_password=password_hash, totp_enabled=False, token_version=1))
        else:
            user.hashed_password = password_hash
            user.token_version = (user.token_version or 1) + 1
        await db.commit()

asyncio.run(main())
PY

  # Генерация начального конфигурационного файла Xray
  python3 -c "
import os
from app.services.xray import XrayService
os.environ['XRAY_PRIVATE_KEY'] = '$XRAY_PRIV'
os.environ['XRAY_SERVER_NAME'] = '$MAIN_DOMAIN'
os.environ['XRAY_DEST'] = '127.0.0.1:8080'

config_json = XrayService.generate_config(clients=[], server_private_key='$XRAY_PRIV', dest='127.0.0.1:8080', server_name='$MAIN_DOMAIN')
os.makedirs('/usr/local/etc/xray', exist_ok=True)
with open('/usr/local/etc/xray/config.json', 'w') as f:
    f.write(config_json)
"
  mkdir -p /var/lib/md-next
  printf '%s\n' 'MD-Next Xray configuration v1' > /var/lib/md-next/xray-owned
  chmod 600 /var/lib/md-next/xray-owned
  printf '%s\n' 'MD-Next AWG configuration v1' > /var/lib/md-next/awg-config-owned
  chmod 600 /var/lib/md-next/awg-config-owned

  if xray run -test -format json -config /usr/local/etc/xray/config.json; then
    systemctl enable xray || true
    systemctl restart xray || true
  else
    echo "ПРЕДУПРЕЖДЕНИЕ: Валидация начального конфига Xray не прошла."
  fi
}

setup_frontend() {
  cd /opt/md-next/frontend
  npm install --silent --no-audit --no-fund
  npm run build --silent
}

install_xray_tls_files() {
  local certificate_directory="$1"
  local xray_user xray_group

  xray_user="$(systemctl show -p User --value xray)"
  [ -n "$xray_user" ] || xray_user="root"
  xray_group="$(id -gn "$xray_user")"

  install -d -o "$xray_user" -g "$xray_group" -m 0750 /usr/local/etc/xray/tls
  install -o "$xray_user" -g "$xray_group" -m 0644 "$certificate_directory/fullchain.pem" /usr/local/etc/xray/tls/fullchain.pem
  install -o "$xray_user" -g "$xray_group" -m 0640 "$certificate_directory/privkey.pem" /usr/local/etc/xray/tls/privkey.pem

  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat <<'HOOK' > /etc/letsencrypt/renewal-hooks/deploy/md-next-xray-certificate.sh
#!/bin/bash
set -e

XRAY_USER="$(systemctl show -p User --value xray)"
[ -n "$XRAY_USER" ] || XRAY_USER="root"
XRAY_GROUP="$(id -gn "$XRAY_USER")"

install -d -o "$XRAY_USER" -g "$XRAY_GROUP" -m 0750 /usr/local/etc/xray/tls
install -o "$XRAY_USER" -g "$XRAY_GROUP" -m 0644 "$RENEWED_LINEAGE/fullchain.pem" /usr/local/etc/xray/tls/fullchain.pem
install -o "$XRAY_USER" -g "$XRAY_GROUP" -m 0640 "$RENEWED_LINEAGE/privkey.pem" /usr/local/etc/xray/tls/privkey.pem
systemctl restart md-next-backend.service
HOOK
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/md-next-xray-certificate.sh
}

setup_services_and_nginx() {
  write_backend_service

  systemctl daemon-reload
  systemctl enable md-next-backend
  systemctl restart md-next-backend

  mkdir -p /etc/nginx/stream-available /etc/nginx/stream-enabled
  cat <<EOF > /etc/nginx/stream-available/md-next-stream.conf
map \$ssl_preread_server_name \$backend_name {
    $MAIN_DOMAIN xray_backend;
    $PANEL_DOMAIN panel_backend;
    default fake_backend;
}

upstream xray_backend {
    server 127.0.0.1:8444;
}

upstream panel_backend {
    server 127.0.0.1:8443;
}

upstream fake_backend {
    server 127.0.0.1:8080;
}

server {
    listen 443;
    ssl_preread on;
    proxy_protocol on;
    proxy_pass \$backend_name;
}
EOF

  if ! grep -q "include /etc/nginx/stream-enabled/\*;" /etc/nginx/nginx.conf; then
      echo "stream {
          include /etc/nginx/stream-enabled/*;
      }" >> /etc/nginx/nginx.conf
  fi
  ln -sf /etc/nginx/stream-available/md-next-stream.conf /etc/nginx/stream-enabled/

  mkdir -p /etc/nginx/ssl_dummy
  openssl req -x509 -nodes -days 1 -newkey rsa:2048 -keyout /etc/nginx/ssl_dummy/privkey.pem -out /etc/nginx/ssl_dummy/fullchain.pem -subj "/CN=$MAIN_DOMAIN"

  cat <<EOF > /etc/nginx/sites-available/md-next.conf
server {
    listen 80;
    server_name $MAIN_DOMAIN $PANEL_DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 127.0.0.1:8080 ssl http2 proxy_protocol;
    listen 127.0.0.1:8443 ssl http2 proxy_protocol;
    server_name $MAIN_DOMAIN _;

    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

    ssl_certificate /etc/nginx/ssl_dummy/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl_dummy/privkey.pem;

    root /opt/md-next/backend/app/static/fake/;
    index index.html;

    # MD_NEXT_XHTTP_TLS_BEGIN
    location ${XRAY_XHTTP_TLS_PATH%/}/cdn-get {
        proxy_pass http://127.0.0.1:8447;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location $XRAY_XHTTP_TLS_PATH {
        proxy_pass http://127.0.0.1:8446;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        client_max_body_size 0;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    # MD_NEXT_XHTTP_TLS_END

    location / {
        try_files \$uri \$uri/ =404;
    }
}

server {
    listen 127.0.0.1:8443 ssl http2 proxy_protocol;
    # HTTP/2 может повторно использовать TLS-соединение основного домена для панели.
    # После завершения TLS выбираем панель по Host/:authority также на входе заглушки.
    listen 127.0.0.1:8080 ssl http2 proxy_protocol;
    server_name $PANEL_DOMAIN;

    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

    ssl_certificate /etc/nginx/ssl_dummy/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl_dummy/privkey.pem;

    root /opt/md-next/frontend/dist/;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location /sub/ {
        proxy_pass http://127.0.0.1:8000/api/v1/sub/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

  ln -sf /etc/nginx/sites-available/md-next.conf /etc/nginx/sites-enabled/
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl restart nginx

  mkdir -p /var/www/html

  # Explicit test mode for an isolated VPS without public DNS.
  if [ "${MDNEXT_TEST_SELF_SIGNED:-0}" = "1" ]; then
    install_xray_tls_files /etc/nginx/ssl_dummy
    systemctl restart md-next-backend.service
    echo "Тестовый режим: самоподписанный сертификат; выпуск Let's Encrypt пропущен."
    return 0
  fi

  # Certbot в режиме standalone должен занять 80-й порт. Останавливаем nginx
  # перед запуском и гарантированно возвращаем его после завершения certbot,
  # включая сценарий с ошибкой или прерыванием установки.
  certbot_nginx_was_active=0
  if systemctl is-active --quiet nginx; then
    certbot_nginx_was_active=1
  fi

  if [ "$certbot_nginx_was_active" -eq 1 ]; then
    systemctl stop nginx
  fi

  certbot_rc=0
  certbot_cleanup() {
    if [ "$certbot_nginx_was_active" -eq 1 ]; then
      systemctl start nginx || true
    fi
  }
  trap 'certbot_cleanup' EXIT INT TERM

  certbot certonly --standalone \
    --non-interactive \
    --keep-until-expiring \
    --agree-tos \
    --no-eff-email \
    --email "$ADMIN_EMAIL" \
    -d "$MAIN_DOMAIN" -d "$PANEL_DOMAIN" || certbot_rc=$?

  certbot_cleanup
  trap - EXIT INT TERM

  if [ "$certbot_rc" -ne 0 ]; then
    echo "Ошибка Certbot: код завершения $certbot_rc" >&2
    return "$certbot_rc"
  fi

  install_xray_tls_files "/etc/letsencrypt/live/$MAIN_DOMAIN" || return 1

  if [ -f "/etc/letsencrypt/live/$MAIN_DOMAIN/fullchain.pem" ]; then
    sed -i "s|/etc/nginx/ssl_dummy/fullchain.pem|/etc/letsencrypt/live/$MAIN_DOMAIN/fullchain.pem|g" /etc/nginx/sites-available/md-next.conf
    sed -i "s|/etc/nginx/ssl_dummy/privkey.pem|/etc/letsencrypt/live/$MAIN_DOMAIN/privkey.pem|g" /etc/nginx/sites-available/md-next.conf
    systemctl reload nginx
  fi
  systemctl restart md-next-backend.service
}

run_step "Установка системных зависимостей, Nginx и Certbot" install_deps
run_step "Установка AmneziaWG" install_awg
run_step "Установка Node.js (v24)" install_node
run_step "Установка Xray-core" install_xray
run_step "Развертывание MD-Next из рабочей директории" setup_repo
run_step "Настройка выхода AmneziaWG через ноды" install_awg_routing
run_step "Настройка Backend, миграции БД и конфигурация Xray" setup_backend
run_step "Создание сайта-заглушки" setup_placeholder
run_step "Сборка Frontend (React/Vite)" setup_frontend
run_step "Настройка Nginx, SSL и системных сервисов" setup_services_and_nginx
run_step "Ожидание готовности API" wait_backend_ready

echo ""
echo "================================================="
echo "         УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!            "
echo "================================================="
echo " Адрес панели:  https://$PANEL_DOMAIN"
echo " Логин:         admin"
echo " Пароль:        $ADMIN_PASSWORD"
echo "================================================="
echo " ВНИМАНИЕ: Данный пароль выводится РОВНО ОДИН РАЗ!"
echo " Пожалуйста, смените начальный пароль после первого входа!"
echo "================================================="
