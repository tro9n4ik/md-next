import json
import os
import time
import shutil
import asyncio
import logging
import urllib.parse
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

_apply_config_lock = asyncio.Lock()

from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.setting import Setting
from app.models.node import Node
from app.services.shell import run_cmd

class XrayService:
    """
    Сервис для управления конфигурацией Xray (VLESS Reality + SOCKS5 + Failover outbound cascades)
    """

    @classmethod
    async def get_active_node(cls, db: AsyncSession) -> Optional[Node]:
        """
        Получает текущую активную ноду из таблицы Setting ('active_node_id') и возвращает объект Node, если она существует и активна.
        """
        setting_res = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
        setting = setting_res.scalar_one_or_none()
        if not setting or not setting.value:
            return None
        try:
            active_node_id = int(setting.value)
            node_res = await db.execute(select(Node).where(Node.id == active_node_id))
            node = node_res.scalar_one_or_none()
            # Keep the configured exit route while it is unhealthy in manual mode;
            # the watchdog or an administrator changes the route explicitly.
            if node and getattr(node, 'is_enabled', True):
                return node
            return None
        except ValueError:
            return None

    @staticmethod
    def generate_config(
        clients: List[Dict[str, Any]],
        server_private_key: str = None,
        active_node: Any = None,
        dest: str = None,
        server_name: str = None,
        profile_options: Dict[str, Any] = None,
    ) -> str:
        """
        Генерирует полный config.json для Xray-core из параметров окружения.
        """
        options = profile_options or {}
        if server_private_key is None:
            server_private_key = options.get("reality_private_key", os.getenv("XRAY_PRIVATE_KEY", ""))
        if dest is None:
            dest = options.get("reality_dest", os.getenv("XRAY_DEST", "127.0.0.1:8080"))
        if server_name is None:
            server_name = options.get("reality_server_name", os.getenv("XRAY_SERVER_NAME", ""))
        reality_short_id = str(options.get("reality_short_id", ""))
        reality_short_ids = [reality_short_id] if reality_short_id else [""]
        warp_usage = options.get("warp_usage", "off")
        outbounds = []
        for node in options.get("nodes", []):
            if active_node and int(node.get("id", -1)) == int(active_node.id):
                continue
            outbounds.append({
                "tag": f"node-{node['id']}", "protocol": node.get("protocol") or "trojan",
                "settings": {"servers": [{"address": node["host"], "port": int(node["port"]), "password": node["secret"]}]},
                "streamSettings": {"network": "grpc", "grpcSettings": {"serviceName": "MD-Next-Node"}},
            })

        if active_node and getattr(active_node, 'is_enabled', True):
            node_secret = getattr(active_node, 'secret', None)
            if node_secret:
                outbounds.append({
                    "tag": f"node-{active_node.id}",
                    "protocol": active_node.protocol or "trojan",
                    "settings": {
                        "servers": [
                            {
                                "address": active_node.host,
                                "port": active_node.port,
                                "password": node_secret
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "grpc",
                        "grpcSettings": {
                            "serviceName": "MD-Next-Node"
                        }
                    }
                })
            else:
                logger.warning(f"Нода {getattr(active_node, 'id', 'unknown')} активна, но секрет ноды отсутствует. Outbound каскада не добавлен.")

        if warp_usage != "off":
            outbounds.append({"tag": "warp", "protocol": "socks", "settings": {
                "servers": [{"address": "127.0.0.1", "port": int(options.get("warp_port", 40000))}]
            }})

        outbounds.append({
            "tag": "direct",
            "protocol": "freedom",
            "settings": {}
        })

        outbounds.append({
            "tag": "block",
            "protocol": "blackhole",
            "settings": {}
        })
        active_tag = f"node-{active_node.id}" if active_node and getattr(active_node, "is_enabled", True) and getattr(active_node, "secret", None) else None
        default_tag = active_tag or ("warp" if warp_usage == "all" else "direct")
        outbounds.sort(key=lambda item: item["tag"] != default_tag)

        config = {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [
                {
                    "port": 8444,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": clients,
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "show": False,
                            "dest": dest,
                            "xver": 1,
                            "serverNames": [server_name],
                            "privateKey": server_private_key,
                            "shortIds": reality_short_ids
                        },
                        "sockopt": {
                            "acceptProxyProtocol": True
                        }
                    }
                },
                {
                    "port": 10808,
                    "listen": "127.0.0.1",
                    "protocol": "socks",
                    "settings": {
                        "auth": "noauth",
                        "udp": True
                    }
                }
            ],
            "outbounds": outbounds
        }
        enabled = options.get("enabled", set())
        if profile_options is not None and "vless_reality_tcp" not in enabled:
            config["inbounds"] = [item for item in config["inbounds"] if item.get("port") != 8444]
        xhttp_reality = options.get("vless_xhttp_reality_clients", [])
        xhttp_tls = options.get("vless_xhttp_tls_clients", [])
        hysteria_clients = options.get("hysteria2_clients", [])
        if "vless_xhttp_reality" in enabled:
            config["inbounds"].append({
                "listen": "0.0.0.0",
                "port": int(options.get("xhttp_reality_port", os.getenv("XRAY_XHTTP_REALITY_PORT", "2053"))),
                "protocol": "vless",
                "settings": {"clients": xhttp_reality, "decryption": "none"},
                "streamSettings": {
                    "network": "xhttp", "security": "reality",
                    "realitySettings": {
                        "show": False, "dest": dest, "serverNames": [server_name],
                        "privateKey": server_private_key, "shortIds": reality_short_ids
                    },
                    "xhttpSettings": {"path": options.get("xhttp_reality_path", "/"), "mode": options.get("xhttp_reality_mode", "auto")}
                }
            })
        if "vless_xhttp_tls" in enabled:
            config["inbounds"].append({
                "listen": "127.0.0.1", "port": 8446, "protocol": "vless",
                "settings": {"clients": xhttp_tls, "decryption": "none"},
                "streamSettings": {
                    "network": "xhttp", "security": "none",
                    "xhttpSettings": {"path": options.get("xhttp_tls_path", "/"), "mode": options.get("xhttp_tls_mode", "auto")}
                }
            })
        if "hysteria2" in enabled:
            config["inbounds"].append({
                "listen": "0.0.0.0", "port": int(options.get("hysteria2_port", 443)),
                "protocol": "hysteria", "settings": {"version": 2, "clients": hysteria_clients},
                "streamSettings": {
                    "network": "hysteria", "security": "tls",
                    "tlsSettings": {
                        "alpn": ["h3"],
                        "certificates": [{"certificateFile": options.get("tls_cert", ""), "keyFile": options.get("tls_key", "")}]
                    },
                    "hysteriaSettings": {"version": 2}
                }
            })
        config["stats"] = {}
        config["api"] = {"tag": "api", "services": ["StatsService"]}
        config["policy"] = {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}}
        config["inbounds"].append({
            "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door",
            "settings": {"address": "127.0.0.1"}, "tag": "api-in"
        })
        config["routing"] = {"domainStrategy": "AsIs", "rules": [{"type": "field", "inboundTag": ["api-in"], "outboundTag": "api"}, *options.get("routing_rules", [])]}
        return json.dumps(config, indent=2)

    @classmethod
    async def apply_config(
        cls,
        clients: List[Dict[str, Any]],
        active_node: Any = None,
        config_path: str = None,
        profile_options: Dict[str, Any] = None,
    ) -> Tuple[bool, str]:
        """
        Атомарное безопасное применение конфигурации Xray c проверкой через xray run -test -format json и откатом при ошибках.
        Защищено asyncio.Lock от гонки процессов/потоков.
        """
        if config_path is None:
            config_path = os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")

        async with _apply_config_lock:
            options = profile_options or {}
            private_key = options.get("reality_private_key", os.getenv("XRAY_PRIVATE_KEY", ""))
            server_name = options.get("reality_server_name", os.getenv("XRAY_SERVER_NAME", ""))
            dest = options.get("reality_dest", os.getenv("XRAY_DEST", "127.0.0.1:8080"))

            if not private_key or not server_name:
                err_msg = "XRAY_PRIVATE_KEY или XRAY_SERVER_NAME не заданы. Конфигурация не применяется."
                logger.error(err_msg)
                return False, err_msg

            config_str = cls.generate_config(
                clients=clients,
                server_private_key=private_key,
                active_node=active_node,
                dest=dest,
                server_name=server_name,
                profile_options=profile_options,
            )

            tmp_path = f"{config_path}.tmp.{time.time_ns()}"
            backup_path = f"{config_path}.bak.{int(time.time())}"

            existed_before = os.path.exists(config_path)

            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(config_str)

                # Проверка конфигурации xray run -test -format json -config <tmp_path>
                test_code, test_stdout, test_stderr = await run_cmd(
                    "xray", "run", "-test", "-format", "json", "-config", tmp_path, timeout=20
                )
                if test_code != 0:
                    err_text = test_stderr or test_stdout
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    logger.error("Проверка конфигурации Xray завершилась с ошибкой: %s", err_text)
                    return False, f"Ошибка синтаксиса конфигурации Xray: {err_text}"

                # Создание бэкапа текущего конфига
                if existed_before:
                    shutil.copy2(config_path, backup_path)

                # Атомарная замена
                os.replace(tmp_path, config_path)

                await run_cmd("systemctl", "restart", "xray", timeout=20)
                active_code, active_out, _ = await run_cmd("systemctl", "is-active", "xray", timeout=5)
                if active_code != 0 or active_out.strip() != "active":
                    logger.error("Xray не запустился. Выполняется откат конфигурации.")
                    if existed_before and os.path.exists(backup_path):
                        os.replace(backup_path, config_path)
                        try:
                            await run_cmd("systemctl", "restart", "xray", timeout=20)
                        except RuntimeError:
                            logger.exception("Не удалось перезапустить Xray после отката")
                    elif os.path.exists(config_path):
                        os.remove(config_path)
                    return False, "Служба Xray не активировалась после перезапуска. Выполнен откат."

                if os.path.exists(backup_path):
                    os.remove(backup_path)

                return True, "Конфигурация Xray успешно применена"

            except Exception as e:
                logger.error(f"Исключение при применении конфигурации Xray: {e}")
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if existed_before and os.path.exists(backup_path):
                    os.replace(backup_path, config_path)
                elif not existed_before and os.path.exists(config_path):
                    os.remove(config_path)
                return False, str(e)

    @staticmethod
    def generate_vless_link(
        uuid: str, host: str, port: int, sni: str, pbk: str, name: str,
        *, fingerprint: str = "chrome", short_id: str = "", flow: str = "xtls-rprx-vision",
    ) -> str:
        """
        Генерирует клиентскую ссылку VLESS Reality с утилитой xtls-rprx-vision и закодированным именем
        vless://<uuid>@<host>:<port>?encryption=none&flow=xtls-rprx-vision&security=reality&sni=<sni>&fp=chrome&pbk=<pbk>&type=tcp#<quote(name)>
        """
        quoted_name = urllib.parse.quote(name, safe="")
        params = {"encryption": "none", "security": "reality", "sni": sni, "fp": fingerprint, "pbk": pbk, "type": "tcp"}
        if short_id:
            params["sid"] = short_id
        if flow:
            params["flow"] = flow
        return f"vless://{uuid}@{host}:{port}?{urllib.parse.urlencode(params)}#{quoted_name}"
