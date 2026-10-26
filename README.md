# CareerPilot AI

A multi-agent career intelligence platform for job switching. It:
- analyzes your resume with evidence,
- researches real job listings from public company job boards,
- computes explainable skill gaps,
- builds a weekly roadmap that fits your study time,
- runs mock interviews whose results feed back into the plan.

- **Backend:** FastAPI · SQLAlchemy 2 · Alembic · LangGraph 1.2 (PostgreSQL checkpointing) · Pydantic 2 · PostgreSQL 16 + pgvector
- **Frontend:** Next.js 16 · React 19 · TypeScript · Tailwind CSS 4 · shadcn-style components · TanStack Query · React Hook Form + Zod · Recharts

Docs: [architecture](docs/architecture.md) · [agents & workflows](docs/agent-workflows.md) ·
[database schema](docs/database-schema.md) · [testing & evaluation](docs/evaluation.md) ·
API reference at `http://localhost:8000/api/docs` (OpenAPI).

## What is real and what is mocked

| Capability | Default (no configuration) | Real integration |
|---|---|---|
| Resume analysis, gap analysis, roadmap, evaluation, interview grading | Deterministic engines (fully functional) | Grounded LLM enrichment with either `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` (default `gemini-flash-latest`) or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (`claude-opus-5-5`). See [Using an LLM](#using-an-llm) |
| Job listings | **Demo listings** (fictional companies, labeled "DEMO" everywhere) — see [Real job postings](#real-job-postings) | `JOB_PROVIDERS=greenhouse,lever` with `GREENHOUSE_BOARDS` / `LEVER_COMPANIES` (public, unauthenticated board APIs) |
| Code answers in interviews | Static checks only; correctness is excluded from the score and this is stated | `CODE_SANDBOX=docker` (network-less, resource-limited container) |
| Learning-resource links | Curated catalog, shown as "not yet checked" | `python -m app.cli verify-resources` records real HTTP checks |

The UI shows a banner whenever any of these run in demo mode. CareerPilot never submits
applications or contacts employers.

## Deploying

Step-by-step guide for **Vercel (frontend) + Render (API) + Neon (Postgres)**:
[docs/deployment.md](docs/deployment.md). `render.yaml` in the repo root configures the API.

## Quick start with Docker

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and JWT_SECRET (32+ chars)
docker compose -f infrastructure/docker-compose.yml --env-file .env up --build
# open http://localhost:3000  (API docs: http://localhost:8000/api/docs)
docker compose -f infrastructure/docker-compose.yml exec backend python -m app.cli seed-demo   # optional demo account
```

The backend container applies migrations on start. Uploaded files live in the `uploads` volume.

> The Compose files were written for this project but could not be executed in the development
> environment (Docker was not installed there). The local setup below is the verified path.

## Local development (verified on Windows, Python 3.13, Node 24)

**1. Database.** Use any PostgreSQL 16 with the `vector` extension. Without Docker, the repo ships a
helper that runs a bundled PostgreSQL + pgvector from the `pgserver` wheel:

```bash
py -3.12 -m venv .devtools/pgvenv && .devtools/pgvenv/Scripts/pip install pgserver
.devtools/pgvenv/Scripts/python scripts/dev_postgres.py start   # 127.0.0.1:54329, creates careerpilot + careerpilot_test
```

**2. Backend**

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # macOS/Linux: .venv/bin/...
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m app.cli seed-demo                         # optional: demo@example.com / demo-password-123
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Settings are read from environment variables or `.env`; see [.env.example](.env.example) for every
option. The default `DATABASE_URL` points at the local dev server above.

**3. Frontend**

```bash
cd frontend
npm ci
npm run dev                      # http://localhost:3000, proxies /api to BACKEND_URL (default http://127.0.0.1:8000)
```

Production build: `npm run build`, then
`cp -r .next/static .next/standalone/.next/ && node .next/standalone/server.js`.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Cannot reach the CareerPilot API" (or a 500 on sign-up/login) | The backend isn't running. Start uvicorn on port 8000 (step 2). The frontend only proxies `/api` to it. |
| `/api/health` fails or the backend can't connect to the database | Start PostgreSQL: `.devtools/pgvenv/Scripts/python scripts/dev_postgres.py start` |

## Using an LLM

The app works fully without an LLM. With one, resume extraction and interview grading get richer,
and every LLM claim must still quote your resume or answer verbatim to be accepted.

**Google Gemini**
1. Create a key at https://aistudio.google.com/apikey.
2. Copy `.env.example` to `.env` in the repository root (if you haven't) and set:
   ```
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=<your key>
   GEMINI_MODEL=gemini-flash-latest     # alias for the current stable Flash; any model your key can access works
   GEMINI_FALLBACK_MODELS=gemini-3.5-flash,gemini-flash-lite-latest   # tried on quota (429) / overload (503, 504)
   GEMINI_THINKING_LEVEL=low            # lower = faster and cheaper; fine for extraction and grading
   ```
3. Check it: `cd backend && .venv/Scripts/python -m app.cli llm-check` prints `OK: gemini / ...`.
4. Restart the backend (settings are read at startup). **Settings → Integrations** in the UI then
   shows the active model, and the "deterministic mode" notice disappears from the demo banner.

**Anthropic:** the same steps with `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

Keep keys only in `.env`. It is git-ignored and never sent to the browser.

If a call fails (quota, timeout, safety block, invalid output), that step falls back to the
deterministic result and the reason is recorded. Workflow results include `llm_usage`
(calls, failures, tokens, latency).

## Real job postings

Real listings come from the official public job-board APIs that companies use to publish openings:
[Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html) and
[Lever Postings API](https://github.com/lever/postings-api). No scraping and no login are involved;
every listing links to the company's own application page. In `.env`:

```
JOB_PROVIDERS=greenhouse,lever          # add ",mock" to also show the fictional demo listings
GREENHOUSE_BOARDS=coinbase,okta,databricks,gitlab,twilio,mongodb,stripe,druva,rubrik,inmobi,razorpaysoftwareprivatelimited,groww
LEVER_COMPANIES=paytm,meesho,zeta,cred
```

- **Finding a company's board name:** open its careers page. A link like
  `boards.greenhouse.io/<name>` or `jobs.lever.co/<name>` gives the name to add. A board that
  doesn't exist or fails is skipped and logged; it doesn't stop the others.
- **How listings are chosen:** up to 25 matching titles per company, preferring your preferred
  locations. Remote roles count as a fit only if they're unrestricted or limited to your region
  ("Remote - India" fits Bengaluru; "Remote - USA" doesn't).
- **Freshness:** a listing present in today's feed is current. If it isn't seen again for
  `JOB_STALE_AFTER_DAYS`, it's marked "Possibly closed". Roles open for over two months are labeled
  "Open 2+ months".
- **Switching from demo data:** run `python -m app.cli remove-demo-jobs` once, restart the backend,
  then click **Refresh listings** on the Jobs page or re-run the analysis.

## Using the app

1. Sign up, then complete onboarding: target role, preferences, weekly study hours, deadline.
2. Upload a PDF/DOCX resume, or click **Use demo resume**.
3. **Run career analysis.** The dashboard shows each agent step live. If no target role is set, the
   workflow pauses and asks you.
4. Review **Resume** (evidence per skill; correct anything), **Jobs** (ranked with match
   explanations), **Skill gaps** (priority components) and **Roadmap** (weekly checklist).
5. Practice in **Interviews**. When a session ends, your gaps and roadmap are re-evaluated
   automatically, adding remediation for weak areas and dropping mastered ones.
6. Track applications and next steps in **Applications**.

## Tests and checks

```bash
cd backend
.venv/Scripts/python -m pytest                       # 110 tests; uses careerpilot_test on the dev Postgres (TEST_DATABASE_URL to override)
.venv/Scripts/ruff check app tests migrations && .venv/Scripts/ruff format --check app tests migrations
.venv/Scripts/python -m app.evaluation.run_eval      # agent evaluation report (see docs/evaluation.md)

cd ../frontend
npm run lint && npm run typecheck && npm test         # 14 unit/component tests
npm run build
```

### Running end-to-end tests

Start the backend (`:8000`) and a frontend server (`:3000`) as above, then:

```bash
cd frontend
npx playwright install chromium   # first time only
npm run test:e2e                  # 3 browser tests: full journey, auth guard, mobile tracker
```

Results from the latest run are recorded in [docs/evaluation.md](docs/evaluation.md).

## Operations

| Command (in `backend/`) | Purpose |
|---|---|
| `python -m app.cli seed-resources` | Load or refresh the learning-resource catalog |
| `python -m app.cli verify-resources` | HTTP-check resource links (reachable / unreachable / unverified) |
| `python -m app.cli purge` | Apply retention policies now (also runs at startup) |
| `python -m app.cli seed-demo` | Create the demo account with a fictional resume |
| `python -m app.cli llm-check` | Send one small request to verify the configured LLM key and model |
| `python -m app.cli remove-demo-jobs` | Delete the fictional demo listings after switching to real job feeds |
| `python -m app.cli graph career` | Print a workflow graph as Mermaid |

Production checklist:
- Set `ENVIRONMENT=production`. The app refuses to start with the dev JWT secret.
- Use a strong `JWT_SECRET`, set `COOKIE_SECURE=true` behind HTTPS, and restrict `CORS_ORIGINS`.
- Back up the database, which includes LangGraph checkpoints.
- For more than one API replica, move the in-process workflow runner and rate limiter to a shared
  queue/Redis (see [architecture](docs/architecture.md#long-running-work)).

## Dependency versions

Backend (pinned ranges in `backend/pyproject.toml`; versions installed and tested): fastapi 0.159.1,
uvicorn 0.54.0, pydantic 2.14.1, SQLAlchemy 2.1.6, alembic 1.20.1, psycopg 3.3.6, pgvector 0.5.1,
langgraph 1.2.16, langgraph-checkpoint-postgres 3.2.0, langchain-core 1.6.13, anthropic 2.1.0, google-genai 2.32.0,
pypdf 6.21.2, python-docx 1.2.0, bcrypt 5.0.0, PyJWT 2.15.1, pytest 9.1.1, ruff 0.17.1.
Database: PostgreSQL 16.2 with pgvector 0.6.2 (local), `pgvector/pgvector:pg16` (Docker).

Frontend (exact versions in `frontend/package.json`): next 16.4.2, react 19.3.1, typescript 5.9.3
(TypeScript 7 was not used because the Next.js toolchain targets 5.x), tailwindcss 4.3.3,
@tanstack/react-query 5.104.5, react-hook-form 7.90.0, zod 4.6.7, @hookform/resolvers 5.9.2
(6.0.0 has an unresolvable optional-peer conflict), recharts 3.10.1, vitest 5.0.5,
@playwright/test 1.64.1, eslint 10.13.0.

## Known limitations

- Job research covers Greenhouse and Lever public boards only. Other sources would need a new
  `JobProvider`. LinkedIn/Indeed are deliberately not scraped.
- Requirement and skill extraction rely on a curated taxonomy (~80 skills). Unknown skills are not
  matched until added to `services/skills.py`.
- The keyword-based interview rubric rewards covering concepts, not reasoning quality. Enable an
  LLM for richer grading, which still requires verbatim quotes.
- Workflow execution and rate limiting are in-process (single API instance).
- The evaluation dataset is small; see the caveats in [docs/evaluation.md](docs/evaluation.md).
