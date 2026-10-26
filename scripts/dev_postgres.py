"""Local PostgreSQL + pgvector for development machines without Docker.

Uses the PostgreSQL build bundled in the `pgserver` wheel (Python 3.12 venv in
.devtools/pgvenv). Docker Compose remains the primary way to run Postgres; this
script exists so migrations and pgvector queries can be exercised locally.

Usage (from repo root):
    .devtools/pgvenv/Scripts/python scripts/dev_postgres.py start|stop|status
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pgserver  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".devtools" / "pgdata"
LOG_FILE = ROOT / ".devtools" / "postgres.log"
PORT = 54329
DATABASES = ("careerpilot", "careerpilot_test")
BIN = Path(pgserver.__file__).parent / "pginstall" / "bin"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(BIN / args[0]), *args[1:]], capture_output=True, text=True, check=check)


def _psql(sql: str, db: str = "postgres") -> str:
    return _run("psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", db, "-tAc", sql).stdout.strip()


def status() -> bool:
    return _run("pg_isready", "-h", "127.0.0.1", "-p", str(PORT), check=False).returncode == 0


def start() -> None:
    if not (DATA_DIR / "PG_VERSION").exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _run("initdb", "-D", str(DATA_DIR), "-U", "postgres", "--auth=trust", "-E", "UTF8")
    if not status():
        # A server started by pgserver itself may still be running on another port.
        _run("pg_ctl", "-D", str(DATA_DIR), "stop", "-m", "fast", check=False)
        # Do not capture output: on Windows the postmaster inherits the pipe handles
        # and the call would block until the server exits.
        subprocess.run(
            [str(BIN / "pg_ctl"), "-D", str(DATA_DIR), "-l", str(LOG_FILE), "-o", f"-p {PORT} -h 127.0.0.1", "start"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        for _ in range(30):
            if status():
                break
            time.sleep(0.5)
    for db in DATABASES:
        if _psql(f"select 1 from pg_database where datname='{db}'") != "1":
            _psql(f"create database {db}")
        _psql("create extension if not exists vector", db=db)
    print(f"postgresql://postgres@127.0.0.1:{PORT}/careerpilot  (pgvector {_psql('select extversion from pg_extension where extname=$$vector$$', 'careerpilot')})")


def stop() -> None:
    _run("pg_ctl", "-D", str(DATA_DIR), "stop", "-m", "fast", check=False)
    print("stopped")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": start, "stop": stop, "status": lambda: print("running" if status() else "stopped")}[cmd]()
