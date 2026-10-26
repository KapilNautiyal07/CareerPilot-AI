"""Asynchronous execution of long-running workflows.

POST /workflows creates a WorkflowExecution row (queued) and returns 202 immediately; a worker
thread runs the graph with a per-execution checkpoint thread. The UI polls the row for status
and per-node steps. Interrupted runs (human input needed) stop in `awaiting_input` and resume
via Command(resume=...). On startup, runs left `queued`/`running` by a crash are resumed from
their last checkpoint, which is why graph nodes are written to be idempotent.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger, log_ctx
from app.db.session import session_scope
from app.models import WorkflowExecution
from app.models.enums import WorkflowKind, WorkflowStatus
from app.services.llm import UsageMeter, current_meter

logger = get_logger(__name__)
TERMINAL = {WorkflowStatus.succeeded, WorkflowStatus.partial, WorkflowStatus.failed}


class WorkflowRunner:
    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = None
        self._graphs: dict[WorkflowKind, Any] = {}
        self._lock = threading.Lock()
        self.futures: dict[str, Future] = {}

    def _graph(self, kind: WorkflowKind):
        with self._lock:
            if kind not in self._graphs:
                if kind == WorkflowKind.career_analysis:
                    from app.graph.career_graph import build_career_graph

                    self._graphs[kind] = build_career_graph()
                else:
                    from app.graph.progress_graph import build_progress_graph

                    self._graphs[kind] = build_progress_graph()
            return self._graphs[kind]

    def reset(self) -> None:
        with self._lock:
            self._graphs.clear()

    def _submit(self, execution_id: str, payload: Any) -> None:
        if get_settings().workflow_inline:
            self._run(execution_id, payload)
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=get_settings().workflow_workers, thread_name_prefix="workflow")
        self.futures[execution_id] = self._pool.submit(self._run, execution_id, payload)

    # ------------------------------------------------------------------ public API
    def start(self, kind: WorkflowKind, user_id: int, profile_id: int, request: dict) -> str:
        with session_scope() as db:
            active = db.scalar(
                select(WorkflowExecution.id).where(
                    WorkflowExecution.user_id == user_id,
                    WorkflowExecution.kind == kind,
                    WorkflowExecution.status.in_([WorkflowStatus.queued, WorkflowStatus.running]),
                )
            )
            if active:
                raise ConflictError("A workflow of this kind is already running", details={"execution_id": active})
            ex = WorkflowExecution(
                user_id=user_id, kind=kind, thread_id=f"{kind.value}-{uuid.uuid4().hex}", input=request, steps=[]
            )
            db.add(ex)
            db.flush()
            ex_id = ex.id
        state = {
            "execution_id": ex_id,
            "user_id": user_id,
            "profile_id": profile_id,
            "errors": [],
            "warnings": [],
            "revision_history": [],
        }
        if kind == WorkflowKind.career_analysis:
            state["request"] = request
        else:
            state["trigger"] = request.get("trigger", "manual")
        self._submit(ex_id, state)
        return ex_id

    def resume(self, execution_id: str, user_id: int, value: dict) -> None:
        with session_scope() as db:
            ex = db.get(WorkflowExecution, execution_id)
            if ex is None or ex.user_id != user_id:
                raise NotFoundError("Workflow execution not found")
            if ex.status != WorkflowStatus.awaiting_input:
                raise ConflictError("This workflow is not waiting for input")
            ex.status = WorkflowStatus.queued
            ex.pending_question = None
        self._submit(execution_id, Command(resume=value))

    def recover(self) -> int:
        """Resume executions interrupted by a process restart (from their last checkpoint)."""
        with session_scope() as db:
            stuck = list(
                db.scalars(
                    select(WorkflowExecution).where(
                        WorkflowExecution.status.in_([WorkflowStatus.queued, WorkflowStatus.running])
                    )
                )
            )
            ids = [(e.id, e.kind, e.thread_id) for e in stuck]
        for ex_id, kind, thread_id in ids:
            snapshot = self._graph(kind).get_state({"configurable": {"thread_id": thread_id}})
            if snapshot.values:
                logger.info("Resuming workflow from checkpoint", extra=log_ctx(execution_id=ex_id))
                self._submit(ex_id, None)
            else:
                self._mark(
                    ex_id,
                    status=WorkflowStatus.failed,
                    error={"code": "interrupted", "message": "Server restarted before the workflow began; retry."},
                )
        return len(ids)

    # ------------------------------------------------------------------ execution
    def _mark(self, execution_id: str, **fields: Any) -> None:
        with session_scope() as db:
            ex = db.get(WorkflowExecution, execution_id)
            for k, v in fields.items():
                setattr(ex, k, v)

    def _run(self, execution_id: str, payload: Any) -> None:
        with session_scope() as db:
            ex = db.get(WorkflowExecution, execution_id)
            kind, thread_id = ex.kind, ex.thread_id
            ex.status = WorkflowStatus.running
            ex.started_at = ex.started_at or datetime.now(UTC)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
        meter = UsageMeter()
        token = current_meter.set(meter)
        graph = self._graph(kind)
        try:
            out = graph.invoke(payload, config)
            snapshot = graph.get_state(config)
            if snapshot.interrupts:
                self._mark(
                    execution_id, status=WorkflowStatus.awaiting_input, pending_question=snapshot.interrupts[0].value
                )
                return
            status = (
                WorkflowStatus(out.get("status", "failed"))
                if out.get("status") in {"succeeded", "partial", "failed"}
                else WorkflowStatus.failed
            )
            result = dict(out.get("result") or {})
            result["llm_usage"] = meter.as_dict()
            error = None
            if status == WorkflowStatus.failed:
                errs = result.get("errors") or [{"message": "Workflow failed"}]
                error = {
                    "code": "workflow_failed",
                    "message": errs[0].get("message", "Workflow failed"),
                    "details": errs,
                }
            self._mark(
                execution_id,
                status=status,
                result=result,
                error=error,
                current_node=None,
                revision_count=out.get("revision", 0),
                finished_at=datetime.now(UTC),
            )
        except Exception as exc:  # recorded on the execution and logged with traceback
            logger.exception("Workflow crashed", extra=log_ctx(execution_id=execution_id))
            self._mark(
                execution_id,
                status=WorkflowStatus.failed,
                finished_at=datetime.now(UTC),
                error={
                    "code": exc.__class__.__name__,
                    "message": "The workflow failed unexpectedly. "
                    "It can be retried; partial results were checkpointed.",
                },
            )
        finally:
            current_meter.reset(token)
            self.futures.pop(execution_id, None)


runner = WorkflowRunner()
