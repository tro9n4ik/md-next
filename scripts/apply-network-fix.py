#!/usr/bin/env python3
"""Apply this patch to a matching MD-Next installation, with backup and rollback."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener

SOURCE = Path(__file__).resolve().parent.parent


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=40)
    if result.returncode:
        raise RuntimeError(
            f"Ошибка команды {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def env_values(path):
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".network-fix-tmp")
    shutil.copy2(source, temp)
    os.replace(temp, target)


def wait_for_panel():
    opener = build_opener(ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open("http://127.0.0.1:8000/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Панель не прошла проверку /health после обновления")


def check_runtime_route(app_dir, config_path):
    with config_path.open() as source:
        config = json.load(source)
    con = sqlite3.connect(f'file:{app_dir / "backend/md_next.db"}?mode=ro', uri=True)
    try:
        settings = dict(con.execute("select key,value from settings"))
        selected = settings.get("active_node_id", "")
        if settings.get("warp.usage") == "all":
            expected = "warp"
        elif selected.isdecimal():
            row = con.execute(
                "select is_enabled,secret from nodes where id=?", (int(selected),)
            ).fetchone()
            expected = (
                f"node-{selected}"
                if row and row[0] and row[1]
                else (
                    "block"
                    if settings.get(
                        "failover.fallback_action",
                        env_values(app_dir / "backend/.env").get(
                            "FAILOVER_FALLBACK_ACTION", "direct"
                        ),
                    )
                    == "keep"
                    else "direct"
                )
            )
        else:
            expected = "direct"
    finally:
        con.close()
    if (
        config["outbounds"][0]["tag"] != expected
        or config.get("burstObservatory")
        or config.get("routing", {}).get("balancers")
    ):
        raise RuntimeError(
            "Рабочая конфигурация Xray не соответствует новому выбранному маршруту"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-dir",
        type=Path,
        help="Каталог установленной панели; по умолчанию определяется через systemd",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить проверенный патч; без флага — только проверка",
    )
    parser.add_argument(
        "--check", action="store_true", help="Только проверить совместимость"
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path("/var/backups/md-next"),
        help="Каталог резервных копий",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        help="Восстановить резервную копию, созданную этим скриптом",
    )
    args = parser.parse_args()
    if args.apply and (args.check or args.rollback):
        parser.error("--apply нельзя совмещать с --check или --rollback")
    if args.app_dir:
        app_dir = args.app_dir.resolve()
    else:
        working = command(
            "systemctl", "show", "md-next-backend", "-p", "WorkingDirectory", "--value"
        )
        app_dir = Path(working).resolve().parent if working else Path("/opt/md-next")
    if not (app_dir / "backend/.env").is_file():
        raise RuntimeError(f"Не найден backend/.env в {app_dir}")
    manifest = json.loads((SOURCE / "NETWORK_FIX_MANIFEST.json").read_text())
    if args.rollback:
        if os.geteuid() != 0:
            raise RuntimeError("Для отката запустите скрипт через sudo")
        backup = args.rollback.resolve()
        metadata = json.loads((backup / "restore.json").read_text())
        if metadata["app_dir"] != str(app_dir):
            raise RuntimeError("Резервная копия относится к другому каталогу установки")
        command("systemctl", "stop", "md-next-backend")
        for item in metadata["files"]:
            if item["exists"]:
                copy_file(backup / item["backup"], Path(item["target"]))
            elif Path(item["target"]).exists():
                Path(item["target"]).unlink()
        command("nginx", "-t")
        command("systemctl", "reload", "nginx")
        command("systemctl", "restart", "xray")
        command("systemctl", "start", "md-next-backend")
        wait_for_panel()
        print("Прежняя версия восстановлена; панель отвечает.")
        return
    for item in manifest["files"]:
        source, target = SOURCE / item["path"], app_dir / item["path"]
        if digest(source) != item["after_sha256"]:
            raise RuntimeError(f'Файл патча изменён: {item["path"]}')
        if (
            target.is_symlink()
            or not target.is_file()
            or digest(target) not in {item["before_sha256"], item["after_sha256"]}
        ):
            raise RuntimeError(
                f'Версия файла отличается от присланного проекта: {item["path"]}. Обновление остановлено до внесения изменений.'
            )
        compile(source.read_bytes(), str(source), "exec")
    print(
        f'Совместимость подтверждена: {len(manifest["files"])} файлов; установка {app_dir}.'
    )
    if not args.apply:
        print("Изменений нет. Для применения добавьте --apply.")
        return
    if os.geteuid() != 0:
        raise RuntimeError("Для применения запустите скрипт через sudo")
    if SOURCE == app_dir:
        raise RuntimeError(
            "Распакуйте патч в отдельный каталог: исходные файлы установки нужны для отката"
        )
    values = env_values(app_dir / "backend/.env")
    xray_config = Path(
        values.get("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
    )
    external = [
        xray_config,
        Path(
            values.get(
                "NGINX_STREAM_CONFIG", "/etc/nginx/stream-available/md-next-stream.conf"
            )
        ),
        Path(
            values.get("NGINX_PANEL_CONFIG", "/etc/nginx/sites-available/md-next.conf")
        ),
        Path(values.get("AWG_CONFIG_PATH", "/etc/amnezia/amneziawg/awg0.conf")),
    ]
    command("nginx", "-t")
    command("xray", "run", "-test", "-format", "json", "-config", str(xray_config))
    backup = args.backup_root / (
        "network-fix-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    metadata = {"app_dir": str(app_dir), "files": []}
    targets = [app_dir / item["path"] for item in manifest["files"]] + external
    for index, target in enumerate(targets):
        item = {
            "target": str(target),
            "exists": target.is_file(),
            "backup": f"files/{index}",
        }
        if item["exists"]:
            copy_file(target, backup / item["backup"])
        metadata["files"].append(item)
    copy_file(app_dir / "backend/.env", backup / "original.env")
    print(f"Резервная копия: {backup}")
    command("systemctl", "stop", "md-next-backend")
    db_path = app_dir / "backend/md_next.db"
    try:
        if db_path.is_file():
            with sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True
            ) as source, sqlite3.connect(backup / "database.sqlite") as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Не удалось проверить копию базы данных")
        # Code/configuration rollback does not replace the live database or .env.
        (backup / "restore.json").write_text(json.dumps(metadata, indent=2))
        for item in manifest["files"]:
            copy_file(SOURCE / item["path"], app_dir / item["path"])
        command("systemctl", "start", "md-next-backend")
        wait_for_panel()
        if command("systemctl", "is-active", "xray") != "active":
            raise RuntimeError("Xray не запустился")
        command("xray", "run", "-test", "-format", "json", "-config", str(xray_config))
        check_runtime_route(app_dir, xray_config)
    except BaseException:
        print(
            "Ошибка обновления. Восстанавливаю прежние файлы и конфигурации.",
            file=sys.stderr,
        )
        command("systemctl", "stop", "md-next-backend")
        for item in metadata["files"]:
            if item["exists"]:
                copy_file(backup / item["backup"], Path(item["target"]))
            elif Path(item["target"]).exists():
                Path(item["target"]).unlink()
        command("nginx", "-t")
        command("systemctl", "reload", "nginx")
        command("systemctl", "restart", "xray")
        command("systemctl", "start", "md-next-backend")
        raise
    print(
        "Патч применён. Панель и Xray работают. Обновите подписку Happ и переподключите VPN."
    )
    print(f"Откат: sudo python3 {__file__} --app-dir {app_dir} --rollback {backup}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
