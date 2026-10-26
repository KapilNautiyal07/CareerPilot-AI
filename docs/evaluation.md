# Testing and agent evaluation

## Automated tests

| Suite | Command | Scope | Result (2026-10-22) |
|---|---|---|---|
| Backend unit + integration + API end-to-end | `cd backend && .venv/Scripts/python -m pytest` | 110 tests on PostgreSQL 16 + pgvector with real Alembic migrations (falls back to SQLite if Postgres is unreachable), incl. the Gemini provider (stubbed SDK), regressions found on real job feeds and retrieval/plan-quality regressions (`test_rag_and_plan_quality.py`) | **110 passed** (2026-10-26) |
| Frontend unit/component | `cd frontend && npm test` | API client (CSRF, error envelope, unreachable backend), utilities, TaskChecklist, WorkflowPanel (interrupt form), domain components | **14 passed** |
| Browser end-to-end | `cd frontend && npm run test:e2e` (live stack running) | Sign-up → onboarding → demo resume → orchestrated analysis → dashboard → resume review → jobs → roadmap task → mock interview → automatic replan; auth redirect and error; mobile tracker without horizontal scroll | **3 passed** |
| Lint / types | `ruff check`, `ruff format --check`, `npm run lint`, `npm run typecheck`, `npm run build` | — | all clean |

All suites use deterministic mock providers: no paid API calls and no network access, except the
optional link checker.

Notable integration tests:
- `test_clarification_interrupt_and_resume`: LangGraph `interrupt()`, then resume via the API; nodes before the interrupt are not re-run.
- `test_checkpoint_resume_after_node_failure`: a simulated crash in `skill_gap`, then `invoke(None)` resumes from the checkpoint; resume analysis ran once.
- `test_bounded_revision_loop`: the evaluator rejects a plan, `revise` forces the uncovered gaps and the second plan validates; an always-invalid plan stops at `MAX_REVISION_LOOPS` with status `partial`.
- `test_full_journey_assessments_influence_recommendations`: a weak Python interview moves Python from *matched* to *partial*, adds remediation tasks, bumps the roadmap version and lowers readiness.
- `test_users_cannot_access_each_others_data`: every user-owned resource returns 404 to another user.

## Agent evaluation

```
cd backend
.venv/Scripts/python -m app.evaluation.run_eval            # isolated SQLite DB, mock providers
LLM_PROVIDER=gemini GEMINI_API_KEY=... .venv/Scripts/python -m app.evaluation.run_eval   # or anthropic
```

Dataset: `backend/app/evaluation/data/eval_set.json`.
- 4 fictional candidates (mid-level backend, data engineer, frontend, new graduate) with
  human-labeled expected skills and graded job relevance (0/1/2) over the bundled mock jobs.
- 3 interview questions, each with good, medium and poor answers.

The runner executes the real agents **and** the full orchestrated workflow for each candidate. It
writes `backend/var/eval_report.json`.

### Latest results (deterministic mode, measured 2026-10-26; same values on SQLite and PostgreSQL)

| Metric | Value | Meaning |
|---|---|---|
| Skill extraction precision / recall / F1 | 1.00 / 1.00 / 1.00 | Explicit skills vs. labels |
| Unsupported-claim rate | 0.00 | Explicit skills whose evidence is not in the resume |
| Hallucination survival rate | 0.00 | Share of 4 invented items (fake skills, fake achievement) from a scripted adversarial LLM that survived grounding |
| Job ranking NDCG@5 / P@3 | 0.947 / 0.917 | Against graded relevance labels (0.867 before role fit was scored) |
| Roadmap top-gap coverage | 1.00 | Top-5 required gaps covered by the plan |
| Roadmap tasks traceable to gaps | 1.00 | No task without a gap or interview track |
| Resource coverage of the skill taxonomy | 1.00 | 78/78 skills have at least one catalog resource (was 59/78) |
| Study tasks with a resource | 1.00 | Learn/practice tasks, incl. DSA-maintenance and STAR prep, with an attached resource |
| Resource level fit | 1.00 | Learn tasks: no advanced-only material for missing skills, no beginner-only for partial ones |
| Soft-skill "project" tasks | 0 | Soft skills get practice and interview-story tasks, never "build with Communication" |
| Interview score spread across repeated runs | 0.0 | Same answer graded 3× |
| Interview ordering accuracy | 1.00 | good > medium > poor, 9 pairs |
| Resource URL well-formed rate | 1.00 | 90 catalog links |
| Resource verification | 90 unverified | The eval DB is fresh; the 28 links added on 2026-10-26 were HTTP-checked (all 2xx/3xx) before being added |
| Workflow completion rate | 1.00 | 4/4 succeeded, 0 revisions needed |
| Node latency p50 | 15–240 ms per node | Mock providers; LLM latency/cost is recorded per run in `result.llm_usage` |

### Live checks against real services (2026-10-22)

- **Real job feeds** (12 Greenhouse + 4 Lever companies): 87 unique postings in about 12 s, with
  requirements extracted from all of them. Running on real data surfaced four defects that the
  demo data couldn't, all fixed and covered by `tests/unit/test_real_feed_regressions.py`:
  - substring title matching ("api" matched "Capital Markets");
  - 70/91 live listings wrongly flagged stale because of their original post date;
  - 9 false "suspicious content" flags ("act as a technical leader");
  - a shared fetch limit that starved later boards.
  Country-restricted remote roles ("Remote - USA") now rank below local ones.
- **Gemini** (`gemini-flash-latest` with fallbacks): resume extraction `llm_grounded` in 8 s, and
  interview grading in 3 s agreed with the rubric (100/100) on a strong answer. Free-tier quota
  (429) and overload (503/504) errors did occur. Automatic model fallback now handles them, and
  `GEMINI_THINKING_LEVEL=low` cut output tokens about 5× per call.

### Self-test probes (2026-10-26)

The eval set had saturated (perfect scores, labeled alongside the code), so extra probes were run
against the eval candidates' persisted results on PostgreSQL. They found defects the metrics above
did not measure; each is now a metric or a test:

| Probe finding | Fix |
|---|---|
| Ranking ignored the target role: a Data Engineer saw "Backend Engineer" at #3; "Python Developer" was not top-3 for a Python Developer | `role_score` in job matching (same role template 100, adjacent roles partial, different 15; weight 0.15) |
| Retrieval took the 40 nearest resources, *then* filtered by skill tag, so a skill could get nothing once the catalog grows | Skill tag filter runs in SQL (JSONB containment) before the pgvector ordering |
| Resources ignored level and task type (partial SQL/Django/Kubernetes gaps got beginner-only links) | `pick_resources`: verified first, practice resources for practice tasks, level by gap status |
| DSA-maintenance and STAR-prep tasks never had resources (up to 14 tasks per plan) | Fixed tasks pick from the retrieved DSA / behavioral resources; replans retrieve them too |
| Soft skills ranked like tech skills (Communication #3 for a backend engineer) and got "Build with Communication" projects | Soft-skill importance x0.5; story/practice task shape; milestones no longer claim a shipped project for a week that only starts one |
| 19 taxonomy skills (GraphQL, Azure, MySQL, Vue.js, Angular, ...) had no resources | 28 official / well-known resources added after an HTTP check |

### How to read these numbers

- The dataset is **small and was labeled alongside the extractor's development**, so perfect
  extraction scores are optimistic. They are a regression guard, not evidence of general accuracy.
  Add candidates written by someone else before relying on the numbers.
- Building the evaluation surfaced two real defects, which were fixed:
  - Sparse 3-requirement listings outranked detailed ones. Fixed with skill-score shrinkage; NDCG@5 went from 0.746 to 0.867.
  - Concurrent step updates were lost on SQLite. Fixed with a lock (Postgres uses `SELECT … FOR UPDATE`).
- **Link check (2026-10-22):** the first run of `verify-resources` found one dead catalog link
  (scikit-learn tutorial, HTTP 404), which was replaced. After the fix: 59 reachable, 3 unverified
  (HTTP 403 bot protection or timeouts are treated as inconclusive, not dead).
- Source validity is measured, not assumed. Run `python -m app.cli verify-resources` to perform HTTP
  checks; only then do links show as verified in the UI.
- With a real LLM, also compare `extraction_method`, the count of discarded items in
  `analysis.warnings`, and `llm_usage` (tokens, latency, failures) across runs.

`tests/unit/test_eval_dataset.py` enforces minimum thresholds (P/R ≥ 0.9, NDCG@5 ≥ 0.7) on every
test run.
