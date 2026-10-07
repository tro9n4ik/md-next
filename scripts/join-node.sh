#!/bin/bash
# Скрипт для присоединения нового узла к кластеру MD-Next

set -e

export DEBIAN_FRONTEND=noninteractive
LOG_FILE="/tmp/md-next-node-install.log"
echo "=== MD-Next Node Install Log Started: $(date) ===" > "$LOG_FILE"

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

PANEL_URL_INJECTED="__PANEL_URL__"
TOKEN_INJECTED="__TOKEN__"

if [ "$PANEL_URL_INJECTED" != "__PANEL_URL__" ] && [ -n "$PANEL_URL_INJECTED" ]; then
    PANEL_URL="$PANEL_URL_INJECTED"
else
    PANEL_URL="$1"
fi

if [ "$TOKEN_INJECTED" != "__TOKEN__" ] && [ -n "$TOKEN_INJECTED" ]; then
    TOKEN="$TOKEN_INJECTED"
else
    TOKEN="$2"
fi

if [ -z "$PANEL_URL" ] || [ -z "$TOKEN" ]; then
    echo -e "${RED}Ошибка: Не указаны URL панели или Token${NC}"
    exit 1
fi

NODE_PORT=443

echo "================================================="
echo "        Подключение ноды MD-Next                 "
echo "================================================="
echo ""

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

install_xray_node() {
  local installer
  installer=$(mktemp) || return 1
  if ! curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
    https://raw.githubusercontent.com/XTLS/Xray-install/e741a4f56d368afbb9e5be3361b40c4552d3710d/install-release.sh -o "$installer"; then
    rm -f -- "$installer"
    echo 'Не удалось скачать официальный установщик Xray.'
    return 1
  fi
  if ! printf '%s  %s\n' '7f70c95f6b418da8b4f4883343d602964915e28748993870fd554383afdbe555' "$installer" | sha256sum -c -; then
    rm -f -- "$installer"
    echo 'Контрольная сумма установщика Xray не совпала.'
    return 1
  fi
  local result=0
  bash "$installer" install || result=$?
  rm -f -- "$installer"
  return "$result"
}

prepare_node_dependencies() {
  # Explicit failures: run_step invokes this function in an if condition.
  if [ "$(id -u)" -ne 0 ]; then echo 'Запустите установку от root.'; return 1; fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 \
      -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update || return 1
    apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 \
      install -y --no-install-recommends ca-certificates curl unzip || return 1
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl unzip || return 1
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl unzip || return 1
  else
    echo 'Неизвестный пакетный менеджер. Установите ca-certificates, curl и unzip вручную.'
    return 1
  fi
  command -v unzip >/dev/null 2>&1 || return 1
}

register_node() {
  PUBLIC_IP=$(curl -fsS --connect-timeout 10 --max-time 20 https://api.ipify.org) || return 1
  SECURE_PANEL_URL=$(echo "$PANEL_URL" | sed 's|^http://|https://|')

  RESPONSE=$(curl -s --fail -X POST "$SECURE_PANEL_URL/api/v1/nodes/register" \
       -H "Content-Type: application/json" \
       -d "{
             \"token\": \"$TOKEN\",
             \"host\": \"$PUBLIC_IP\",
             \"port\": $NODE_PORT,
             \"protocol\": \"trojan\"
           }") || {
      echo "ОШИБКА: Не удалось зарегистрировать ноду на панели $SECURE_PANEL_URL."
      echo "Проверьте корректность SSL-сертификата мастер-панели и доступность HTTPS."
      return 1
  }

  NODE_SECRET=""
  if command -v python3 > /dev/null 2>&1; then
      NODE_SECRET=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('secret', ''))" 2>/dev/null || true)
  fi
  if [ -z "$NODE_SECRET" ]; then
      NODE_SECRET=$(echo "$RESPONSE" | grep -o '"secret":"[^"]*' | cut -d'"' -f4)
  fi

  if [ -z "$NODE_SECRET" ]; then
      echo "ОШИБКА: Сервер не вернул секрет ноды."
      return 1
  fi

  unset TOKEN
  export NODE_SECRET
  export PUBLIC_IP
}

configure_xray_node() {
  cat <<EOF > /usr/local/etc/xray/config.json
{
  "inbounds": [
    {
      "port": $NODE_PORT,
      "protocol": "trojan",
      "settings": {
        "clients": [
          {
            "password": "$NODE_SECRET"
          }
        ]
      },
      "streamSettings": {
        "network": "grpc",
        "grpcSettings": {
          "serviceName": "MD-Next-Node"
        },
        "security": "none"
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "settings": {
        "finalRules": [{"action": "allow", "network": "tcp", "ip": ["127.0.0.1/32"], "port": "40000"}]
      }
    }
  ]
}
EOF
}

verify_and_start_xray() {
  xray run -test -format json -config /usr/local/etc/xray/config.json
  systemctl restart xray
  systemctl enable xray
}

configure_warp_node() {
  # Отдельный bash сохраняет set -e даже при вызове шага из условного блока.
  bash -s <<'WARP'
set -eu
port=40000
if command -v warp-cli >/dev/null 2>&1; then
  # Существующий туннель и регистрацию не перенастраиваем автоматически.
  warp-cli --accept-tos settings | grep -qi 'Mode: WarpProxy' || exit 1
  curl -fsS --max-time 15 --socks5-hostname "127.0.0.1:$port" https://www.cloudflare.com/cdn-cgi/trace | grep -qx 'warp=on\|warp=plus'
  exit
fi
. /etc/os-release
case "${ID:-}:${VERSION_CODENAME:-}" in
  ubuntu:noble|ubuntu:jammy|ubuntu:resolute|debian:bookworm|debian:trixie) ;;
  *) printf 'Автоматическая установка WARP не поддерживается для этой ОС.\n'; exit 1 ;;
esac
b="/var/backups/md-next/warp-node-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$b"
for f in /etc/apt/sources.list.d/cloudflare-client.list /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg; do
  if [ -e "$f" ]; then cp -a "$f" "$b/$(basename "$f")"; fi
done
cat > "$b/rollback.sh" <<'ROLLBACK'
#!/bin/bash
set -eu
b="$(dirname "$(readlink -f "$0")")"
systemctl stop warp-svc || true
systemctl disable warp-svc || true
DEBIAN_FRONTEND=noninteractive apt-get remove -y cloudflare-warp
for f in /etc/apt/sources.list.d/cloudflare-client.list /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg; do
  n="$(basename "$f")"
  if [ -f "$b/$n" ]; then cp -a "$b/$n" "$f"; else rm -f -- "$f"; fi
done
ROLLBACK
chmod 700 "$b/rollback.sh"
trap 'code=$?; if [ "$code" -ne 0 ]; then bash "$b/rollback.sh"; fi' EXIT
apt-get update -o Acquire::Retries=1 -o Acquire::https::Timeout=20
apt-get install -y curl gpg
curl -fsSL --connect-timeout 10 --max-time 30 https://pkg.cloudflareclient.com/pubkey.gpg -o "$b/pubkey.gpg"
gpg --batch --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg "$b/pubkey.gpg"
printf 'deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ %s main\n' "$VERSION_CODENAME" > /etc/apt/sources.list.d/cloudflare-client.list
apt-get update -o Acquire::Retries=1 -o Acquire::https::Timeout=20
apt-get install -y cloudflare-warp
systemctl enable --now warp-svc
# Режим proxy задаётся до регистрации: маршруты SSH и Xray не меняются.
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port "$port"
timeout 40 warp-cli --accept-tos registration new
warp-cli --accept-tos connect
for attempt in 1 2 3 4 5; do
  if curl -fsS --max-time 8 --socks5-hostname "127.0.0.1:$port" https://www.cloudflare.com/cdn-cgi/trace | grep -qx 'warp=on\|warp=plus'; then
    printf 'WARP ноды проверен. Резервная копия и откат: %s\n' "$b"
    exit 0
  fi
  sleep 1
done
exit 1
WARP
}

run_step "Подготовка системных зависимостей" prepare_node_dependencies
run_step "Установка Xray-core на узле" install_xray_node
run_step "Регистрация узла в мастер-панели" register_node
run_step "Создание конфигурации Trojan/gRPC" configure_xray_node
run_step "Проверка конфигурации и запуск Xray" verify_and_start_xray

# Ошибка необязательного WARP не отменяет подключение рабочей ноды.
if [ "${MD_NEXT_NODE_WARP:-1}" = "1" ]; then
  printf "  ▸ Настройка бесплатного WARP на ноде... "
  if configure_warp_node >> "$LOG_FILE" 2>&1; then
    printf "[${GREEN}Готово${NC}]\n"
    echo "WARP работает через локальный SOCKS5: 127.0.0.1:40000. Выберите эту ноду во вкладке WARP панели."
  else
    printf "[${RED}Не настроен${NC}]\n"
    echo "Нода подключена и работает. WARP не настроен; подробности: $LOG_FILE."
  fi
fi

echo ""
echo "================================================="
echo "   Узел $PUBLIC_IP успешно подключен к кластеру! "
echo "   Статус в панели: connected                    "
echo "================================================="
