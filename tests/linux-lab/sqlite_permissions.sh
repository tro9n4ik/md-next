#!/usr/bin/env bash
set -euo pipefail

scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
install -d -o root -g nogroup -m 3770 "$scratch/data"
chgrp nogroup "$scratch"
chmod 0750 "$scratch"
install -o root -g nogroup -m 0660 /dev/null "$scratch/data/md_next.db"
ln -s "$scratch/data/md_next.db" "$scratch/panel.db"

runuser -u nobody -g nogroup -- python3 - "$scratch/panel.db" <<'PY'
from pathlib import Path
import os
import sqlite3
import stat
import sys

os.umask(0o077)
db = Path(sys.argv[1])
with sqlite3.connect(db) as connection:
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO sample VALUES (1)")
    connection.commit()
    for suffix in ("-wal", "-shm"):
        file = Path(str(db.resolve()) + suffix)
        info = file.stat()
        mode = stat.S_IMODE(info.st_mode)
        assert stat.S_ISREG(info.st_mode)
        assert info.st_uid == os.geteuid() and info.st_gid == os.getegid()
        assert mode & 0o600 == 0o600 and mode & ~0o660 == 0
        print(f"SQLite {suffix}: {mode:04o}, владелец и группа проверены")
PY
