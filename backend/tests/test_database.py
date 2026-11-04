import asyncio
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from sqlalchemy import text


def test_sqlite_connection_settings_and_concurrent_writes(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    pythonpath = os.pathsep.join(filter(None, (str(backend), os.environ.get("PYTHONPATH"))))
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": pythonpath},
        check=True,
        timeout=30,
    )


async def check_sqlite_connection_settings_and_concurrent_writes():
    from app.db.database import engine

    async def write():
        async with engine.begin() as connection:
            for pragma, expected in (
                ("journal_mode", "wal"),
                ("busy_timeout", 5000),
                ("synchronous", 1),
                ("foreign_keys", 1),
            ):
                assert await connection.scalar(text(f"PRAGMA {pragma}")) == expected
            await connection.execute(text("INSERT INTO samples DEFAULT VALUES"))
            await asyncio.sleep(0.01)

    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY)"))
        results = await asyncio.gather(*(write() for _ in range(8)), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM samples")) == 8
            assert Path("md_next.db-wal").is_file()
            with sqlite3.connect("md_next.db") as source, sqlite3.connect(":memory:") as backup:
                source.backup(backup)
                assert backup.execute("SELECT count(*) FROM samples").fetchone() == (8,)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check_sqlite_connection_settings_and_concurrent_writes())
