"""LangGraph checkpointer selection (PostgreSQL in deployment, SQLite/memory for local tests)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_lock = threading.Lock()
_saver: BaseCheckpointSaver | None = None
_pool = None


def _pg_conninfo(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def get_checkpointer() -> BaseCheckpointSaver:
    global _saver, _pool
    with _lock:
        if _saver is not None:
            return _saver
        s = get_settings()
        kind = s.checkpointer
        if kind == "auto":
            kind = "postgres" if s.is_postgres else "sqlite"
        if kind == "postgres":
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(
                _pg_conninfo(s.database_url),
                min_size=1,
                max_size=max(4, s.workflow_workers * 2),
                open=True,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
            saver = PostgresSaver(_pool)
            saver.setup()  # idempotent: creates/migrates checkpoint tables
        elif kind == "sqlite":
            from langgraph.checkpoint.sqlite import SqliteSaver

            Path(s.checkpoint_sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            saver = SqliteSaver(sqlite3.connect(s.checkpoint_sqlite_path, check_same_thread=False))
        else:
            saver = InMemorySaver()
        logger.info(f"LangGraph checkpointer: {kind}")
        _saver = saver
        return saver


def reset_checkpointer(saver: BaseCheckpointSaver | None = None) -> None:
    global _saver, _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
        _saver = saver
