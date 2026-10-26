# Database schema

PostgreSQL 16 with pgvector. Migrations live in `backend/migrations/versions` (Alembic).
`alembic upgrade head`, `downgrade base` and `alembic check` (no drift) were verified against the
dev database, and the test suite migrates a fresh schema on every run. LangGraph checkpoint tables
are created by `PostgresSaver.setup()` and are not part of these migrations.

Enum-like columns are `VARCHAR` with a `CHECK` constraint, which is portable and easy to extend.
All timestamps are `timestamptz`.

## Entities

| Table | Purpose | Key constraints / indexes | Delete behavior |
|---|---|---|---|
| `users` | Accounts (bcrypt hash, `token_version`) | unique `email` | Cascades to everything the user owns |
| `user_preferences` | Study hours/days, deadline, locations, arrangements, salary | unique `user_id` | cascade from user |
| `candidate_profiles` | Target role/seniority, confirmed years, education/certs/achievements, `active_resume_id` | unique `user_id`; FK `active_resume_id` → resumes **SET NULL** (added after both tables exist: circular) | cascade from user |
| `resumes` | File metadata, extracted text (PII), analysis JSON, security flags | unique `storage_key`; unique (`profile_id`, `sha256`) dedupe | cascade from profile; file removed by API/retention |
| `extracted_skills` | Canonical skill, source (explicit/inferred/user), evidence level and snippets, confidence | unique (`profile_id`, `skill`) | cascade from profile; `resume_id` SET NULL |
| `work_experiences`, `project_experiences` | Parsed history (user-editable) | index `profile_id` | cascade |
| `job_listings` | Shared, normalized job cache with provenance, quality flags, embedding `vector(256)` | unique (`source`, `external_id`); index `fingerprint`; (`title`, `company`); **HNSW** cosine index on `embedding` | — |
| `job_skill_requirements` | Required/preferred skills with the source sentence | unique (`job_id`, `skill`) | cascade from job |
| `job_matches` | Per-profile score components, explanation JSON, `is_saved` | unique (`profile_id`, `job_id`); (`profile_id`, `score`) | cascade from profile and job |
| `job_applications` | Tracker: status, dates, next step, notes, `status_history` | unique (`profile_id`, `job_id`); (`profile_id`, `status`); `next_step_due` | cascade from profile; `job_id` SET NULL (keeps the record if a listing is purged) |
| `skill_gaps` | One row per skill per **snapshot**; `is_current` marks the latest | (`profile_id`, `is_current`); `snapshot_id` | cascade |
| `learning_resources` | Curated catalog, `verification_status` set only by real HTTP checks, embedding | unique `url`; **HNSW** cosine index | — |
| `learning_roadmaps` | Versioned plan, summary (milestones, evaluation), `revision_log` | (`profile_id`, `status`) | cascade; `workflow_execution_id` SET NULL |
| `roadmap_tasks` | Scheduled tasks with type, skill, hours, status, origin (generated/replanned/remediation) | (`roadmap_id`, `status`); `scheduled_date` | cascade |
| `roadmap_task_resources` | Task ↔ resource association | composite PK | cascade both sides |
| `interview_sessions` | Category, difficulty, status, LangGraph `thread_id`, summary | unique `thread_id`; (`profile_id`, `started_at`) | cascade |
| `interview_questions` | Asked questions (rubric snapshot, hints, code spec), follow-ups via `parent_question_id` | index `session_id` | cascade |
| `interview_responses` | Answer, code, score, per-criterion results, evaluator, sandbox result | unique `question_id` | cascade |
| `assessment_results` | Append-only history: per-skill interview scores and overall readiness | (`profile_id`, `skill`, `created_at`) | cascade; `session_id` SET NULL |
| `workflow_executions` | Async run status, per-node steps, pending question, result, error | (`user_id`, `created_at`); unique `thread_id` | cascade from user; purged by retention |

## Derived values and history

- Match scores and gap priorities are stored with their **components** so explanations are stable
  even if formulas evolve. Dashboard metrics are computed from rows at request time.
- Gap snapshots and readiness records are kept to chart progress over time. Tasks are never
  deleted on replan; they are marked `superseded`.

## pgvector usage

Vectors are used only where semantic retrieval helps:
- **Resource retrieval** (`repositories/career.py::retrieve_resources`): ordered by `<=>` cosine
  distance, then filtered to resources tagged with the skill.
- **Job search relevance** (`GET /api/jobs?q=...&sort=relevance`).

Embeddings are deterministic feature hashing (`services/embeddings.py`, 256 dimensions) so retrieval
works offline and in tests. Swapping in a hosted embedding model only changes `embed()` and
`EMBEDDING_DIM` (plus a migration for the column size).
