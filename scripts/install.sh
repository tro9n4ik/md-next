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
read -r -p "Выберите действие [1-3]: " ACTION

case "$ACTION" in
  1) ACTION="install" ;;
  2) ACTION="update" ;;
  3) ACTION="remove" ;;
  *) echo "Неверный выбор."; exit 1 ;;
esac

APP_DIR="/opt/md-next"
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

install_awg() {
  if [ ! -r /etc/os-release ]; then
    echo "Не удалось определить дистрибутив Linux для установки AmneziaWG." >&2
    return 1
  fi

  . /etc/os-release
  apt-get update -qq
  apt-get install -y -qq software-properties-common python3-launchpadlib gnupg2 "linux-headers-$(uname -r)"

  case "${ID:-}" in
    ubuntu)
      add-apt-repository -y ppa:amnezia/ppa
      ;;
    debian)
      apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 57290828
      local awg_repo="/etc/apt/sources.list.d/amneziawg.list"
      if [ ! -f "$awg_repo" ] || ! grep -q "ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main" "$awg_repo"; then
        cat > "$awg_repo" <<'EOF'
deb https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main
deb-src https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main
EOF
      fi
      ;;
    *)
      echo "Установка AmneziaWG поддерживается только на Ubuntu и Debian (обнаружен ${ID:-неизвестный дистрибутив})." >&2
      return 1
      ;;
  esac

  apt-get update -qq
  apt-get install -y -qq amneziawg
  if ! command -v awg >/dev/null 2>&1; then
    echo "Пакет AmneziaWG установлен, но команда awg не найдена." >&2
    return 1
  fi

  modprobe amneziawg
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

  echo "Резервная копия настроек и базы: $backup_dir"
  git clone --depth 1 "$REPOSITORY_URL" "$update_dir/repo"
  cp -a "$update_dir/repo/." "$APP_DIR/"
  rm -rf "$update_dir"
  update_nginx_routes
  install_awg

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
  rm -f /etc/systemd/system/md-next-backend.service
  rm -f /etc/nginx/sites-enabled/md-next.conf /etc/nginx/sites-available/md-next.conf
  rm -f /etc/nginx/stream-enabled/md-next-stream.conf /etc/nginx/stream-available/md-next-stream.conf
  systemctl daemon-reload
  systemctl restart nginx 2>/dev/null || true

  read -r -p "Удалить базу данных и файлы приложения из $APP_DIR? [y/N]: " delete_data
  if [[ "$delete_data" =~ ^[Yy]$ ]]; then
    rm -rf "$APP_DIR"
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
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
}

install_xray() {
  bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
}

setup_repo() {
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
  mkdir -p /opt/md-next
  if [ "$SCRIPT_DIR" != "/opt/md-next" ]; then
    cp -r "$SCRIPT_DIR/"* /opt/md-next/ 2>/dev/null || true
  fi
  cd /opt/md-next
}

setup_backend() {
  cd /opt/md-next/backend
  python3 -m venv venv
  source venv/bin/activate
  pip install --quiet -r requirements.txt

  # Идемпотентная генерация пары ключей x25519 Reality через python cryptography
  KEYS=$(python3 -c "
import base64
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

priv = x25519.X25519PrivateKey.generate()
pub = priv.public_key()

priv_b64 = base64.urlsafe_b64encode(priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode().rstrip('=')
pub_b64 = base64.urlsafe_b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip('=')
print(f'{priv_b64}:{pub_b64}')
")
  XRAY_PRIV=$(echo "$KEYS" | cut -d':' -f1)
  XRAY_PUB=$(echo "$KEYS" | cut -d':' -f2)
  XRAY_XHTTP_TLS_PATH="/$(openssl rand -hex 16)"

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
EOF
  chmod 600 /opt/md-next/backend/.env

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
    server_name _;

    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

    ssl_certificate /etc/nginx/ssl_dummy/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl_dummy/privkey.pem;

    root /opt/md-next/backend/app/static/fake/;
    index index.html;

    # MD_NEXT_XHTTP_TLS_BEGIN
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
run_step "Установка Cloudflare WARP" install_warp
run_step "Установка Node.js (v20)" install_node
run_step "Установка Xray-core" install_xray
run_step "Развертывание MD-Next из рабочей директории" setup_repo
run_step "Настройка Backend, миграции БД и конфигурация Xray" setup_backend
run_step "Сборка Frontend (React/Vite)" setup_frontend
run_step "Настройка Nginx, SSL и системных сервисов" setup_services_and_nginx

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
