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
echo "        md-next Node Join v2.2.0                 "
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
  bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
}

register_node() {
  PUBLIC_IP=$(curl -s ifconfig.me || curl -s api.ipify.org)
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
      "settings": {}
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

run_step "Установка Xray-core на узле" install_xray_node
run_step "Регистрация узла в мастер-панели" register_node
run_step "Создание конфигурации Trojan/gRPC" configure_xray_node
run_step "Проверка конфигурации и запуск Xray" verify_and_start_xray

echo ""
echo "================================================="
echo "   Узел $PUBLIC_IP успешно подключен к кластеру! "
echo "   Статус в панели: connected                    "
echo "================================================="
