import os
import time
import asyncio
import base64
import hashlib
import ipaddress
import logging
from typing import Tuple, Dict, Any
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import Setting
from app.models.client import Client, ClientProfile
from app.services.client_limits import access_allowed
from app.services.events import log_event
from app.services.shell import run_cmd

logger = logging.getLogger(__name__)

_awg_ip_lock = asyncio.Lock()
_awg_sync_lock = asyncio.Lock()

class AWGService:
    """
    Сервис для управления конфигурацией AmneziaWG, IP Pool и runtime-синхронизацией
    """

    @staticmethod
    def generate_keypair() -> Tuple[str, str]:
        """
        Генерация пары ключей (Private и Public) для AmneziaWG.
        """
        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key()

        priv_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

        return base64.b64encode(priv_bytes).decode('utf-8'), base64.b64encode(pub_bytes).decode('utf-8')

    @staticmethod
    def hash_private_key(private_key: str) -> str:
        """
        Хэширование приватного ключа SHA-256
        """
        return hashlib.sha256(private_key.encode('utf-8')).hexdigest()

    @staticmethod
    def protocol_parameters(server_private_key: str) -> Dict[str, str]:
        """Общие параметры AWG 3.1 сервера и всех клиентских профилей."""
        if not server_private_key:
            raise ValueError("Не найден закрытый ключ сервера AmneziaWG")
        try:
            private_bytes = base64.b64decode(server_private_key, validate=True)
        except (ValueError, TypeError):
            private_bytes = server_private_key.encode("utf-8")
        protection_key = hashlib.sha256(
            b"MD-Next AmneziaWG 3.1 header protection\0" + private_bytes
        ).digest()
        return {
            "Jc": "8", "Jmin": "50", "Jmax": "1000",
            "S1": "15", "S2": "30", "S3": "45", "S4": "60",
            "H1": "111111", "H2": "222222", "H3": "333333", "H4": "444444",
            "HeaderProtectionKey": base64.b64encode(protection_key).decode("ascii"),
            "ContentPaddingAddition": "0",
            "RandomTrailers": "on", "DisableCookies": "on",
        }

    @classmethod
    async def get_or_create_server_keys(cls, db: AsyncSession) -> Tuple[str, str]:
        """
        Возвращает или автоматически генерирует и сохраняет пару ключей сервера AmneziaWG
        """
        priv_res = await db.execute(select(Setting).where(Setting.key == "awg_server_private_key"))
        pub_res = await db.execute(select(Setting).where(Setting.key == "awg_server_public_key"))

        priv_setting = priv_res.scalar_one_or_none()
        pub_setting = pub_res.scalar_one_or_none()

        if priv_setting and pub_setting and priv_setting.value and pub_setting.value:
            return priv_setting.value, pub_setting.value

        priv, pub = cls.generate_keypair()

        if not priv_setting:
            db.add(Setting(key="awg_server_private_key", value=priv))
        else:
            priv_setting.value = priv

        if not pub_setting:
            db.add(Setting(key="awg_server_public_key", value=pub))
        else:
            pub_setting.value = pub

        await db.flush()
        return priv, pub

    @classmethod
    async def get_server_settings(cls, db: AsyncSession) -> Dict[str, Any]:
        """
        Получает полные настройки сервера AWG (подсеть, порт, endpoint и ключи)
        """
        subnet_res = await db.execute(select(Setting).where(Setting.key == "awg_subnet"))
        subnet_setting = subnet_res.scalar_one_or_none()
        subnet = subnet_setting.value if (subnet_setting and subnet_setting.value) else os.getenv("AWG_SUBNET", "10.8.0.0/24")

        port_res = await db.execute(select(Setting).where(Setting.key == "awg_port"))
        port_setting = port_res.scalar_one_or_none()
        port = int(port_setting.value) if (port_setting and port_setting.value) else int(os.getenv("AWG_PORT", "51820"))

        net = ipaddress.ip_network(subnet)
        server_ip = str(list(net.hosts())[0])

        server_host = os.getenv("SERVER_HOST", "127.0.0.1")

        priv_key, pub_key = await cls.get_or_create_server_keys(db)

        return {
            "subnet": subnet,
            "net": net,
            "server_ip": server_ip,
            "port": port,
            "server_host": server_host,
            "server_endpoint": f"{server_host}:{port}",
            "server_private_key": priv_key,
            "server_public_key": pub_key
        }

    @classmethod
    async def allocate_ip(cls, db: AsyncSession) -> str:
        """
        Выделяет следующий свободный IP-адрес из пула с асинхронной блокировкой от race condition
        """
        async with _awg_ip_lock:
            settings = await cls.get_server_settings(db)
            net = settings["net"]
            server_ip = settings["server_ip"]

            res = await db.execute(select(Client).where(Client.protocol == "awg").where(Client.ip_address.isnot(None)))
            clients = res.scalars().all()

            used_ips = set()
            for c in clients:
                if c.ip_address:
                    ip_str = c.ip_address.split('/')[0]
                    used_ips.add(ip_str)

            used_ips.add(server_ip)

            for host in net.hosts():
                ip_str = str(host)
                if ip_str not in used_ips:
                    return f"{ip_str}/32"

            raise ValueError("В пуле AmneziaWG нет свободных IP-адресов")

    @classmethod
    async def allocate_profile_ip(cls, db: AsyncSession) -> str:
        async with _awg_ip_lock:
            settings = await cls.get_server_settings(db)
            used_res = await db.execute(
                select(ClientProfile.ip_address).where(
                    ClientProfile.kind == "awg", ClientProfile.ip_address.isnot(None)
                )
            )
            used_ips = {value.split("/")[0] for value in used_res.scalars().all() if value}
            used_ips.add(settings["server_ip"])
            for host in settings["net"].hosts():
                if str(host) not in used_ips:
                    return f"{host}/32"
            raise ValueError("В настроенной подсети не осталось свободных адресов AmneziaWG")

    @classmethod
    async def sync_server_config(cls, db: AsyncSession, config_path: str = None) -> Tuple[bool, str]:
        """
        Генерирует и применяет реальный конфигурационный файл awg0.conf и синхронизирует его с интерфейсом awg0
        """
        async with _awg_sync_lock:
            if config_path is None:
                config_path = os.getenv("AWG_CONFIG_PATH", "/etc/amnezia/amneziawg/awg0.conf")

            settings = await cls.get_server_settings(db)
            enabled_setting = await db.get(Setting, "profiles.enabled.awg")
            awg_globally_enabled = enabled_setting is None or enabled_setting.value.lower() in ("true", "1", "yes")

            res = await db.execute(
                select(ClientProfile, Client)
                .join(Client, Client.id == ClientProfile.client_id)
                .where(ClientProfile.kind == "awg")
                .where(ClientProfile.is_enabled == True)
                .where(Client.is_active == True)
                .where(ClientProfile.public_key.isnot(None))
                .where(ClientProfile.ip_address.isnot(None))
            )
            active_clients = res.all() if awg_globally_enabled else []

            protocol = cls.protocol_parameters(settings["server_private_key"])
            lines = [
                "[Interface]",
                f"PrivateKey = {settings['server_private_key']}",
                f"Address = {settings['server_ip']}/24",
                f"ListenPort = {settings['port']}",
                *(f"{key} = {value}" for key, value in protocol.items()),
                ""
            ]

            for profile, c in active_clients:
                if not access_allowed(c):
                    continue
                lines.extend([
                    "[Peer]",
                    f"# Клиент: {c.name} (ID: {c.id})",
                    f"PublicKey = {profile.public_key}",
                    f"AllowedIPs = {profile.ip_address}",
                    ""
                ])

            config_content = "\n".join(lines)

            tmp_path = f"{config_path}.tmp.{time.time_ns()}"
            stripped_path = f"{config_path}.stripped.{time.time_ns()}"
            rollback_path = f"{config_path}.rollback.{time.time_ns()}"
            previous_config = None
            previous_runtime = None
            config_replaced = runtime_touched = False

            def secure_write(path, content):
                # Ключи не должны попадать в файлы с правами из общего umask.
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as target:
                    target.write(content)
                    target.flush()
                    os.fsync(target.fileno())

            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                if os.path.exists(config_path):
                    with open(config_path, "rb") as source:
                        previous_config = source.read()
                interface_code, _, _ = await run_cmd("awg", "show", "awg0", timeout=5)
                if interface_code == 0:
                    snapshot_code, previous_runtime, _ = await run_cmd("awg", "showconf", "awg0", timeout=10)
                    if snapshot_code or not previous_runtime.strip():
                        raise RuntimeError("Не удалось сохранить работающую конфигурацию AmneziaWG")
                secure_write(tmp_path, config_content.encode("utf-8"))
                os.replace(tmp_path, config_path)
                config_replaced = True

                if interface_code != 0:
                    runtime_touched = True
                    up_code, _, _ = await run_cmd("awg-quick", "up", "awg0", timeout=60)
                    if up_code:
                        raise RuntimeError("Не удалось поднять интерфейс AmneziaWG")
                else:
                    strip_code, stripped_config, _ = await run_cmd("awg-quick", "strip", "awg0", timeout=10)
                    if strip_code != 0:
                        raise RuntimeError("Не удалось подготовить конфигурацию AmneziaWG")
                    secure_write(stripped_path, stripped_config.encode("utf-8"))
                    runtime_touched = True
                    sync_code, _, _ = await run_cmd("awg", "syncconf", "awg0", stripped_path, timeout=20)
                    if sync_code != 0:
                        raise RuntimeError("Команда awg syncconf завершилась с ошибкой")
                log_event("info", "awg", "Конфигурация AmneziaWG успешно применена")

                return True, "Конфигурация AWG успешно синхронизирована"

            except (Exception, asyncio.CancelledError) as e:
                rollback_failed = False
                try:
                    if config_replaced:
                        if previous_config is None:
                            os.remove(config_path)
                        else:
                            secure_write(tmp_path, previous_config)
                            os.replace(tmp_path, config_path)
                    # syncconf может применить часть netlink-изменений до ошибки.
                    if runtime_touched and previous_runtime:
                        secure_write(rollback_path, previous_runtime.encode("utf-8"))
                        rollback_code, _, _ = await run_cmd("awg", "syncconf", "awg0", rollback_path, timeout=20)
                        rollback_failed = rollback_code != 0
                except Exception:
                    rollback_failed = True
                reason = "Не удалось применить конфигурацию AmneziaWG"
                if rollback_failed:
                    reason += "; автоматический откат не завершён"
                # stderr системных команд может содержать ключ из ошибочного файла.
                logger.warning(reason)
                log_event("error", "awg", reason)
                if isinstance(e, asyncio.CancelledError):
                    raise
                return False, reason
            finally:
                for path in (tmp_path, stripped_path, rollback_path):
                    if os.path.exists(path):
                        os.remove(path)

    @staticmethod
    def generate_client_conf(
        private_key: str,
        address: str,
        server_public_key: str,
        server_endpoint: str,
        server_private_key: str,
        dns: str = "1.1.1.1, 8.8.8.8"
    ) -> str:
        """
        Генерирует клиентский конфигурационный файл .conf для AmneziaWG
        со случайными динамическими параметрами обфускации (H1..H4, S1..S2, Jc, Jmin, Jmax).
        """
        protocol = AWGService.protocol_parameters(server_private_key)
        protocol_lines = "\n".join(f"{key} = {value}" for key, value in protocol.items())

        conf = f"""[Interface]
PrivateKey = {private_key}
Address = {address}
DNS = {dns}
{protocol_lines}

[Peer]
PublicKey = {server_public_key}
Endpoint = {server_endpoint}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
        return conf
