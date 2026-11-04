"""Ограничение MSS на принадлежащих панели туннелях."""
import shutil
import subprocess
import json
import importlib.util
from pathlib import Path

source = Path(__file__).with_name("network-policy.py")
if not source.is_file():
    source = Path(__file__).parents[1] / "backend/app/services/network_policy.py"
spec = importlib.util.spec_from_file_location("network_policy", source)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
MTU = policy.TUNNEL_MTU
TABLE = "mdnext_mss"
OWNER = "MD-Next managed MSS"


def command(*args, check=True, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=10)


def rules():
    for ingress, egress in (("awg0", "mdawg"), ("mdawg", "awg0")):
        for binary, ceiling in (("iptables", policy.IPV4_MSS), ("ip6tables", policy.IPV6_MSS)):
            yield binary, (
                "FORWARD", "-i", ingress, "-o", egress, "-p", "tcp",
                "--tcp-flags", "SYN,RST", "SYN", "-m", "tcpmss",
                "--mss", f"{ceiling + 1}:65535", "-m", "comment",
                "--comment", "MD-Next fixed MSS", "-j", "TCPMSS", "--set-mss", str(ceiling),
            )


def remove_iptables():
    for binary, rule in rules():
        if shutil.which(binary):
            while command(binary, "-w", "5", "-t", "mangle", "-C", *rule, check=False).returncode == 0:
                command(binary, "-w", "5", "-t", "mangle", "-D", *rule)


def nft_script(exists):
    source = f"delete table inet {TABLE}\n" if exists else ""
    source += f'table inet {TABLE} {{\n comment "{OWNER}";\n chain forward {{\n type filter hook forward priority mangle; policy accept;\n'
    for ingress, egress in (("awg0", "mdawg"), ("mdawg", "awg0")):
        for family, ceiling in (("ipv4", policy.IPV4_MSS), ("ipv6", policy.IPV6_MSS)):
            source += (
                f'iifname "{ingress}" oifname "{egress}" meta nfproto {family} '
                f'tcp flags & (syn | rst) == syn tcp option maxseg size > {ceiling} '
                f'tcp option maxseg size set {ceiling}\n'
            )
    return source + "}\n}\n"


def owned_table():
    result = command("nft", "-j", "list", "table", "inet", TABLE, check=False)
    if result.returncode:
        return False
    tables = [row["table"] for row in json.loads(result.stdout)["nftables"] if "table" in row]
    if len(tables) != 1 or tables[0].get("comment") != OWNER:
        raise RuntimeError("Имя nftables занято чужой таблицей")
    return True


def apply():
    use_nft = bool(shutil.which("nft"))
    exists = owned_table() if use_nft else False
    script = nft_script(exists) if use_nft else None
    if use_nft:
        command("nft", "-c", "-f", "-", input=script)
    # Интерфейсы принадлежат панели; MTU внешней сети не меняется.
    for interface in ("awg0", "mdawg"):
        command("ip", "link", "set", "dev", interface, "mtu", str(MTU))
    if use_nft:
        command("nft", "-f", "-", input=script)
        remove_iptables()
        return
    for binary, rule in rules():
        if command(binary, "-w", "5", "-t", "mangle", "-C", *rule, check=False).returncode:
            command(binary, "-w", "5", "-t", "mangle", "-A", *rule)


def remove():
    if shutil.which("nft") and owned_table():
        command("nft", "delete", "table", "inet", TABLE)
    remove_iptables()
