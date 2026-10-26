# Architecture

CareerPilot AI is a monorepo with two services and one database.

```
browser ──► Next.js (frontend/, :3000) ──/api/* rewrite──► FastAPI (backend/, :8000) ──► PostgreSQL 16 + pgvector
                                                              │
                                                              ├─ LangGraph graphs (career, progress, interview)
                                                              │    └─ PostgresSaver checkpoints (same database)
                                                              ├─ Background workflow runner (thread pool)
                                                              ├─ Job providers: mock │ Greenhouse │ Lever (public APIs)
                                                              ├─ LLM provider: mock │ Gemini │ Anthropic (optional)
                                                              └─ Local file storage for resumes (pluggable)
```

## Design principles

1. **Deterministic core, optional LLM.** Every agent produces a correct, validated result without an
   LLM: taxonomy-based skill extraction, rule-based requirement parsing, formula-based scoring,
   capacity-bounded scheduling and rubric grading. When `LLM_PROVIDER=gemini` or `anthropic` is set, the LLM
   *enriches* resume extraction and interview grading. Anything the LLM claims must quote the
   source verbatim, otherwise it is discarded (see "Grounding" below). Tests and evaluation run on
   the deterministic path, so they cost nothing and are reproducible.
2. **Typed contracts between agents.** Agents exchange the Pydantic models in
   `backend/app/schemas/agents.py` (`ResumeAnalysis`, `SkillGapReport`, `RoadmapPlan`,
   `EvaluationReport`, ...). Graph state stores them as JSON so checkpoints stay portable.
3. **Explainable numbers.** Match scores, gap priorities and readiness are computed by documented
   formulas. Their components are persisted and shown in the UI.
4. **Honesty constraints are enforced in code, not just prompts.** Demo data is flagged, unverified
   links are labeled, "listed" skills never count as proficiency, readiness carries a disclaimer,
   and no endpoint can submit applications or contact employers.

## Backend layout (`backend/app`)

| Package | Responsibility |
|---|---|
| `api/` | FastAPI routers, auth/CSRF/rate-limit dependencies. Thin: validation, ownership, orchestration calls. |
| `agents/` | The six specialist agents: resume, job research, skill gap, roadmap, interview coach, career evaluation. The orchestrator is the LangGraph graph. |
| `graph/` | LangGraph state, graphs, checkpointer selection, per-node step tracking, background runner. |
| `services/` | Pure domain logic: skill taxonomy, matching, job parsing, document text extraction, embeddings, LLM providers, sandbox, storage, untrusted-content handling, retention. |
| `tools/` | Job data providers and bundled data (mock jobs, resource catalog, question bank). |
| `repositories/` | Data access with ownership scoping, upserts, pgvector retrieval, snapshots. |
| `models/`, `schemas/` | SQLAlchemy models and Pydantic API/agent schemas. |
| `evaluation/` | Reproducible agent evaluation dataset and runner. |
| `core/` | Settings, error envelope, JSON logging with redaction, security helpers, rate limiter. |

## Long-running work

`POST /api/workflows/career-analysis` inserts a `workflow_executions` row and returns **202** with an
`execution_id`. A worker thread runs the graph, with the LangGraph thread id stored on the row. Each
node is wrapped by `graph/tracking.py`, which records start, finish, duration and failure in
`steps`. The UI polls `GET /api/workflows/{id}` every 1.5 s and stops at a terminal state.

- **Human-in-the-loop:** `determine_target` calls `interrupt()` when no target role is known. The
  execution becomes `awaiting_input` with a `pending_question`. `POST /api/workflows/{id}/input`
  resumes it with `Command(resume=...)`. Completed nodes are not re-run.
- **Crash recovery:** on startup, `runner.recover()` resumes executions left `queued`/`running` from
  their last checkpoint. Nodes are idempotent, so re-running an interrupted node is safe. This is
  covered by `test_checkpoint_resume_after_node_failure`.
- **Scaling note:** the runner is in-process. For several API replicas, move execution to a queue
  worker (e.g. arq/Celery) that calls the same `runner._run`. The graph, checkpointer and status
  model stay unchanged.

## Security model

| Concern | Implementation |
|---|---|
| Passwords | bcrypt (cost 12), 10–72 chars with letters and digits, constant-time handling of unknown emails |
| Sessions | HS256 JWT in an httpOnly, SameSite=Lax cookie (Bearer also accepted for API clients). `token_version` invalidates all sessions on "log out everywhere" or account deletion |
| CSRF | Double-submit: the readable `cp_csrf` cookie must be echoed in `X-CSRF-Token` on cookie-authenticated unsafe requests |
| Authorization | Every user resource is resolved through the caller's profile id. Cross-user access returns 404 (tested in `test_users_cannot_access_each_others_data`) |
| Uploads | Size limit, magic-byte type sniffing that must match the extension, page limit, zip-bomb guard, random storage keys, path-traversal guard |
| Untrusted text | Resumes, job descriptions and answers are normalized (invisible characters stripped), scanned for injection patterns, and wrapped in random-fence `<untrusted_*>` blocks. Flagged lines are excluded from extraction, and suspicious job listings never influence skill demand |
| Grounding | LLM-extracted skills and achievements must quote the resume verbatim. LLM rubric credit above the deterministic score requires a verbatim quote from the answer |
| Code execution | Never on the host. `CODE_SANDBOX=docker` uses a throwaway container: no network, read-only FS, dropped capabilities, uid 65534, 128 MB, 0.5 CPU, 64 PIDs, timeout. Static AST checks block imports such as `os`/`subprocess`, dunder access and `eval`/`exec` |
| Rate limits | Sliding window on login/register (per IP and per email) and on expensive endpoints (per user) |
| Logging | JSON logs. Keys like `password`, `token` and `resume_text` are redacted and email addresses masked |
| Headers | `X-Frame-Options: DENY`, `nosniff`, strict referrer, `Cache-Control: no-store` on API responses |
| Data lifecycle | `DELETE /api/auth/me` cascades all rows and deletes files. Retention purge (`RESUME_RETENTION_DAYS`, `WORKFLOW_RETENTION_DAYS`) runs at startup and via `python -m app.cli purge` |
| Secrets | Only in environment variables. The browser never sees API keys or the backend URL |

## Frontend

Next.js 16 (App Router) with React 19, Tailwind CSS 4 and shadcn-style components
(`components/ui`). Data fetching uses TanStack Query (`lib/queries.ts`), forms use react-hook-form
with zod, and charts use Recharts. `/api/*` is rewritten to the backend, so auth cookies are
first-party. `AppShell` guards authenticated pages: no session redirects to `/login`, and an
unfinished profile redirects to `/onboarding`. A banner always states which integrations are mocked.
