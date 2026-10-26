"""Per-node progress tracking for workflow executions (what the UI polls)."""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from langgraph.errors import GraphBubbleUp

from app.core.logging import get_logger, log_ctx
from app.db.session import session_scope
from app.models import WorkflowExecution

logger = get_logger(__name__)
# SQLite has no SELECT ... FOR UPDATE; parallel branches would otherwise overwrite each other's steps.
_local_lock = threading.Lock()


def _update_step(
    execution_id: str | None, node: str, status: str, detail: str | None = None, duration_ms: int | None = None
) -> None:
    if not execution_id:
        return
    with _local_lock, session_scope() as db:
        ex = db.get(WorkflowExecution, execution_id, with_for_update=db.get_bind().dialect.name == "postgresql")
        if ex is None:
            return
        steps = [dict(s) for s in (ex.steps or [])]
        now = datetime.now(UTC).isoformat()
        existing = next(
            (s for s in reversed(steps) if s["node"] == node and s["status"] in ("running", "awaiting_input")), None
        )
        if status == "running" and existing is None:
            steps.append({"node": node, "status": "running", "started_at": now})
        elif existing is not None:
            existing.update({"status": status, "finished_at": now})
            if detail:
                existing["detail"] = detail[:300]
            if duration_ms is not None:
                existing["duration_ms"] = duration_ms
        ex.steps = steps
        if status == "running":
            ex.current_node = node


def tracked(node: str) -> Callable:
    """Record start/finish/failure of a graph node on its WorkflowExecution row."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            ex_id = state.get("execution_id")
            started = time.monotonic()
            _update_step(ex_id, node, "running")
            try:
                out = fn(state)
            except GraphBubbleUp:
                # interrupt(): the node is waiting for human input, not failing.
                _update_step(ex_id, node, "awaiting_input")
                raise
            except Exception as exc:
                logger.exception("Workflow node failed", extra=log_ctx(node=node, execution_id=ex_id))
                _update_step(
                    ex_id, node, "failed", f"{exc.__class__.__name__}: {exc}", int((time.monotonic() - started) * 1000)
                )
                raise
            detail = None
            if isinstance(out, dict) and out.get("errors"):
                detail = "; ".join(e.get("message", "") for e in out["errors"])
            _update_step(
                ex_id, node, "partial" if detail else "succeeded", detail, int((time.monotonic() - started) * 1000)
            )
            return out

        return wrapper

    return deco
