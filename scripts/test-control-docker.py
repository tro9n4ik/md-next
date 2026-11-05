"""Изолированные Docker-проверки идентичности тома и MSS; запуск только с --run."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("План: временный образ из системного Python, два пересоздания тома, три контейнера и две тестовые сети; очистка.")
        return
    if os.geteuid() != 0:
        raise RuntimeError("Требуется root тестового VPS")
    run("docker", "info", "--format", "{{.ServerVersion}}")
    project = Path(__file__).resolve().parents[1]
    prefix = "mdnext-plan-" + uuid.uuid4().hex[:8]
    image = prefix + ":test"
    containers, networks = [], []
    original_forward = {name: run("sysctl", "-n", name).stdout.strip() for name in ("net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding")}
    def net(pid, *command):
        return run("nsenter", "-t", str(pid), "-n", *command)
    try:
        with tempfile.TemporaryDirectory(prefix="mdnext-docker-") as temporary:
            base = Path(temporary)
            fs = base / "rootfs"
            fs.mkdir()
            binaries = set()
            def copy_file(source):
                source = Path(source)
                target = fs / str(source).lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=True)
                if source.suffix == ".so" or ".so." in source.name or source == Path(sys.executable):
                    binaries.add(source)
            copy_file(sys.executable)
            library = Path(os.__file__).parent
            shutil.copytree(library, fs / str(library).lstrip("/"), ignore=shutil.ignore_patterns("__pycache__", "site-packages", "dist-packages"))
            binaries.update(library.rglob("*.so"))
            import cryptography
            crypto_package = Path(cryptography.__file__).parent
            shutil.copytree(crypto_package, fs / str(crypto_package).lstrip("/"), ignore=shutil.ignore_patterns("__pycache__"))
            binaries.update(crypto_package.rglob("*.so"))
            spec = importlib.util.find_spec("_cffi_backend")
            if spec and spec.origin:
                copy_file(spec.origin)
            for source in binaries.copy():
                result = subprocess.run(["ldd", str(source)], capture_output=True, text=True)
                for dependency in re.findall(r"(/[^\s()]+)", result.stdout):
                    if Path(dependency).is_file():
                        copy_file(dependency)
            (fs / "app").mkdir()
            for name in ("crypto.py", "host_identity.py"):
                shutil.copy2(project / "backend/app/services" / name, fs / "app" / name)
            archive = base / "image.tar"
            with tarfile.open(archive, "w") as tar:
                for item in fs.iterdir():
                    tar.add(item, arcname=item.name)
            run("docker", "import", str(archive), image)
            volume = base / "volume"
            volume.mkdir(mode=0o700)
            os.chown(volume, 65534, 65534)
            for generation in (1, 2):
                identity = base / f"machine{generation}"
                identity.write_text(str(generation) * 32)
                identity.chmod(0o644)
                script = "import sqlite3,crypto; d=sqlite3.connect('/var/lib/md-next/test.db'); "
                if generation == 1:
                    script += "d.execute('CREATE TABLE nodes(secret TEXT)');d.execute('INSERT INTO nodes VALUES (?)',(crypto.encrypt_node_secret('synthetic-node'),));d.commit()"
                else:
                    script += "assert crypto.decrypt_node_secret(d.execute('SELECT secret FROM nodes').fetchone()[0])=='synthetic-node';print('CONTAINER_RECREATE_OK')"
                result = run("docker", "run", "--rm", "--network", "none", "--user", "65534:65534",
                    "-e", "PYTHONPATH=/app:/usr/lib/python3/dist-packages", "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "-e", "CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1",
                    "-e", "JWT_SECRET_KEY=synthetic-container-test-key", "-v", f"{volume}:/var/lib/md-next",
                    "-v", f"{identity}:/etc/machine-id:ro", image, sys.executable, "-S", "-c", script)
                if generation == 2:
                    print(result.stdout.strip(), flush=True)
            assert (volume / "host.key").stat().st_mode & 0o777 == 0o600
            for side in (81, 82):
                name = prefix + str(side)
                run("docker", "network", "create", "--ipv6", "--subnet", f"10.231.{side}.0/24", "--subnet", f"fd42:231:{side}::/64", name)
                networks.append(name)
            def container(name, side, host):
                cid = run("docker", "run", "-d", "--name", prefix + name,
                    "--network", networks[side-81], "--ip", f"10.231.{side}.{host}", "--ip6", f"fd42:231:{side}::{host}",
                    image, sys.executable, "-S", "-c", "import time;time.sleep(600)").stdout.strip()
                containers.append(cid)
                return int(run("docker", "inspect", "-f", "{{.State.Pid}}", cid).stdout)
            left, router, right = container("left",81,2),container("router",81,3),container("right",82,2)
            run("docker","network","connect","--ip","10.231.82.3","--ip6","fd42:231:82::3",networks[1],containers[1])
            net(router,"ip","link","set","eth0","name","awg0")
            net(router,"ip","link","set","eth1","name","mdawg")
            net(router,"sysctl","-qw","net.ipv4.ip_forward=1","net.ipv6.conf.all.forwarding=1")
            for pid,local,remote in ((left,81,82),(right,82,81)):
                net(pid,"ip","route","add",f"10.231.{remote}.0/24","via",f"10.231.{local}.3")
                net(pid,"ip","-6","route","add",f"fd42:231:{remote}::/64","via",f"fd42:231:{local}::3")
            net(router,"iptables","-t","mangle","-A","FORWARD","-j","RETURN")
            helper = str(project / "scripts/network-mtu.py")
            load = "import runpy; m=runpy.run_path(__import__('sys').argv[1]); "
            for fallback in (False,True):
                choice="m['shutil'].which=lambda n: None if n=='nft' else '/usr/sbin/'+n; " if fallback else ""
                for _ in range(2):
                    net(router,sys.executable,"-c",load+choice+"m['apply']()",helper)
                net(left,"ping","-c","3","-W","2","-M","do","-s","1352","10.231.82.2")
                for source,target,side in ((left,right,82),(right,left,81)):
                    for family,address,ceiling in ((4,f"10.231.{side}.2",1340),(6,f"fd42:231:{side}::2",1320)):
                        capture=subprocess.Popen(["nsenter","-t",str(target),"-n","tcpdump","-l","-nn","-i","eth0","-c","1","tcp dst port 39876 and "+("ip" if family==4 else "ip6")],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                        try:
                            time.sleep(0.3)
                            net(source,sys.executable,"-c","import socket,sys;s=socket.socket(socket.AF_INET if sys.argv[1]=='4' else socket.AF_INET6);s.settimeout(2);s.connect_ex((sys.argv[2],39876))",str(family),address)
                            output,_=capture.communicate(timeout=5)
                            match=re.search(r"mss (\d+)",output)
                            assert match and int(match[1])==ceiling,output
                        finally:
                            if capture.poll() is None: capture.kill();capture.communicate()
                if fallback:
                    assert any(int(line.split()[0])>0 for line in net(router,"iptables","-t","mangle","-L","FORWARD","-vn").stdout.splitlines() if "TCPMSS" in line)
                net(router,sys.executable,"-c",load+choice+"m['remove']()",helper)
                print(("iptables" if fallback else "nftables")+": DOCKER_MSS_IPV4_IPV6_DF_OK",flush=True)
    finally:
        for cid in reversed(containers): subprocess.run(["docker","rm","-f",cid],capture_output=True,timeout=30)
        for name in reversed(networks): subprocess.run(["docker","network","rm",name],capture_output=True,timeout=30)
        subprocess.run(["docker","image","rm",image],capture_output=True,timeout=30)
        for name,value in original_forward.items(): run("sysctl","-qw",name+"="+value)


if __name__ == "__main__": main()
