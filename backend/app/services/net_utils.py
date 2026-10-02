import os
import psutil

EXCLUDED_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "tun", "wg", "awg")

def get_primary_interface() -> str:
    env_iface = os.getenv("NET_INTERFACE")
    if env_iface:
        return env_iface

    try:
        if os.path.exists("/proc/net/route"):
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[1:]:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "00000000":
                        iface = parts[0]
                        if not iface.startswith(EXCLUDED_PREFIXES):
                            return iface
    except Exception:
        pass

    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for iface in addrs:
            if iface.startswith(EXCLUDED_PREFIXES):
                continue
            # Не выбираем выключенные интерфейсы. Если статистика недоступна,
            # оставляем интерфейс допустимым: некоторые системы не возвращают
            # данные для всех виртуальных/нестандартных интерфейсов.
            stat = stats.get(iface)
            if stat is not None and not stat.isup:
                continue
            return iface
    except Exception:
        pass

    return "eth0"
