# Agent Control Room

Observability and control plane for AI agents. Register agents, execute runs
asynchronously, and inspect every LLM call, tool invocation and data access as a
trace with per-step token counts and cost.

**Phase 4 (this repository state) delivers evaluations and replay:** Ragas,
DeepEval and a deterministic built-in evaluator, plus first-class replay sessions
(live what-if runs and offline replays driven by recorded LLM responses) with a
side-by-side comparison. Phase 3 provides the Next.js dashboard (overview,
filterable runs, live trace waterfall, agent management), and Phase 2 the
backend: agent CRUD, run execution through Celery, a provider-agnostic agent
runner, OpenTelemetry + PostgreSQL trace collection, live streaming, Alembic
migrations, tests, and a Docker Compose stack.

## Architecture

```
    Browser (Next.js dashboard, :3000)
           │  server components fetch via API_BASE_URL
           │  client fetch / EventSource via NEXT_PUBLIC_API_BASE_URL
           ▼
  ┌────────────────┐        ┌──────────────┐
  │  FastAPI API   │ ─────▶ │  PostgreSQL  │ ◀─── traces, runs
  │    /api/v1     │        └──────────────┘
  └───────┬────────┘                ▲
          │ enqueue                 │ span export
  ┌───────▼────────┐        ┌───────┴───────┐
  │     Redis      │        │ TraceCollector│
  │ broker/events  │        └───────▲───────┘
  └───────┬────────┘                │
  ┌───────▼────────┐        ┌───────┴───────┐
  │ Celery worker  │ ─────▶ │ Agent runner  │ ──▶ LLM providers
  │ + beat         │        │ tools + spans │     OpenAI / Anthropic
  └────────────────┘        └───────────────┘     / local
                                   │
                            ┌──────▼────────┐
                            │ OTLP collector│ ──▶ Jaeger
                            └───────────────┘
```

Key modules:

| Path | Responsibility |
| --- | --- |
| `backend/app/models/entities.py` | Authoritative schema: `agents`, `runs`, `traces`, `tool_calls`, `evaluations`, `replay_sessions` |
| `backend/app/api/v1/endpoints/` | Agents, runs, traces, tools, SSE/WebSocket streaming |
| `backend/app/services/agent_runner.py` | LLM/tool loop, deadline/cancellation, per-step accounting |
| `backend/app/services/trace_collector.py` | Span tree, tool calls, PostgreSQL + OTel export |
| `backend/app/services/evaluation/` | Evaluator abstraction: custom heuristics, Ragas, DeepEval |
| `backend/app/services/replay.py` | Replay sessions, execution, comparison, cancellation |
| `backend/app/services/llm/` | OpenAI, Anthropic, local and recorded (offline replay) adapters |
| `backend/app/worker/` | Celery app, tasks (runs, evaluations, replays) and dispatch helpers |
| `backend/app/core/` | Settings, DB, logging, errors, Redis, rate limiting, telemetry |
| `backend/alembic/` | Migrations |
| `frontend/src/app/` | App Router pages: overview, runs, run detail, replays, agents |
| `frontend/src/components/` | Trace waterfall, evaluation/replay panels, tables, forms, badges |
| `frontend/src/lib/` | Server API client, browser client, trace-tree math, formatting |
| `frontend/src/types/` | Types mirrored from the Pydantic schemas |

Execution flow:

1. `POST /api/v1/runs` validates the agent, stores a `pending` run, captures the
   incoming W3C `traceparent`, and enqueues `runs.run_agent`.
2. The worker loads the run, marks it `running`, and calls the LLM in a bounded
   tool-calling loop.
3. Every iteration, LLM call, tool call and data access becomes a `traces` row
   (plus `tool_calls`), with tokens, cost, latency and status.
4. Token/cost totals are committed after each generation step so the dashboard
   updates while the run is live. Events are published to Redis for SSE and
   WebSocket subscribers.
5. Terminal state, totals and latency are written to the `runs` row.

## Quick start (Docker Compose)

Requirements: Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY (or ANTHROPIC_API_KEY / a local model endpoint)
docker compose up --build
```

Then:

- Dashboard: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health> and `/health/ready`
- Jaeger UI: <http://localhost:16686>

The backend container runs `alembic upgrade head` before starting. Seed demo
agents, runs and traces (no LLM calls, no token spend):

```bash
docker compose exec backend python -m scripts.seed_data
```

The frontend image bakes `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_JAEGER_URL`
(consumed by the browser) at build time, and reads `API_BASE_URL` at runtime for
server components. Defaults already point at `localhost:8000` / `localhost:16686`,
matching the Compose port mappings and the backend CORS default
(`http://localhost:3000`).

Hot reload / bind mounts for local development:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Local development (without Docker)

Python 3.12 is required. PostgreSQL and Redis can still come from Compose:

```bash
docker compose up -d postgres redis
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
# If PyPI is slow or blocked, point pip/uv at a trusted mirror first:
#   set PIP_INDEX_URL=https://your-mirror/simple
#   set UV_INDEX_URL=https://your-mirror/simple
# point DATABASE_URL at localhost instead of the `postgres` Docker hostname
set DATABASE_URL=postgresql+psycopg://acr:acr_password@localhost:5432/acr
set REDIS_URL=redis://localhost:6379/0
alembic upgrade head
uvicorn app.main:app --reload
```

Start a worker and beat in two more shells (same environment):

```bash
celery -A app.worker.celery_app worker --loglevel=info
celery -A app.worker.celery_app beat --loglevel=info
```

Run the test suite (uses in-memory SQLite, telemetry/Redis/rate limiting off):

```bash
cd backend
pytest
```

## Dashboard (Phase 3/4)

Next.js 15 App Router, React 19, TypeScript (strict) and Tailwind CSS. All pages
are server-rendered on demand and fetch through the typed server client; only
mutations and the live run stream run in the browser.

| Route | Contents |
| --- | --- |
| `/` | KPIs (active runs, success rate, sampled spend, average latency), recent runs, cost by model, status mix, agent fleet |
| `/runs` | Prompt/agent/status/date/cost filters, sorting, pagination, and a panel to start a run with per-run execution overrides |
| `/runs/[id]` | Run summary, prompt/output, metadata, agent sidebar, timeline, cancel/export, Jaeger deep link, live trace waterfall, evaluation panel, replay launcher and recent replay sessions |
| `/replays` | Replay sessions with mode (live/offline), status filter, output similarity, latency/cost deltas and pagination |
| `/replays/[id]` | Side-by-side prompts/outputs, evaluation score deltas, span/tool-call stats, overrides, timeline and raw comparison JSON |
| `/agents` | Search/filter, create form with tool picker, per-agent run/token/cost stats, edit/clone/activate/delete actions |
| `/agents/[id]` | Full configuration, tool config JSON, recent runs, and an inline run launcher |

Live updates: the run detail page opens an `EventSource` against
`GET /api/v1/runs/{id}/stream` and handles `run.snapshot`, `run.started`,
`run.progress`, `trace.completed`, `run.completed`, `run.failed`,
`stream.unavailable` and `stream.error`. Completed spans are merged into the
client tree immediately; a debounced full refresh loads span payloads and the
final run totals. The waterfall reconstructs the parent/child tree from the flat
trace list, so a partially loaded live view never loses spans.

Cost views are computed from the latest 200 runs returned by the existing list
endpoint (no new analytics endpoint); the UI labels sampled aggregates.

Local development (the API must be reachable on port 8000):

```bash
cd frontend
npm ci
npm run dev        # http://localhost:3000
npm run typecheck
npm run lint
npm run build
```

| Variable | Scope | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | build-time | API base URL used by the browser for mutations and SSE |
| `NEXT_PUBLIC_JAEGER_URL` | build-time | Jaeger UI base URL for trace deep links |
| `API_BASE_URL` | runtime | API base URL used by server components (Compose: `http://backend:8000`) |
| `FRONTEND_PORT` | Compose | Published dashboard port (default `3000`) |

The image uses Next's `standalone` output, runs as a non-root user, and exposes
`GET /api/health` for container health checks. `CORS_ORIGINS` on the backend must
include the dashboard origin; the default already includes
`http://localhost:3000`.

## Evaluations and replay (Phase 4)

**Evaluators.** `GET /api/v1/evaluations/metrics` returns the catalog. Every
deployment has the deterministic `custom` evaluator; `ragas` and `deepeval` are
optional and report `available: false` with a reason until their packages are
installed (`make backend-eval` / `pip install -r backend/requirements-eval.txt`).
Evaluation runs synchronously per run, or in the background for batches, and
upserts one row per `(run_id, evaluator_type, metric_name)`; `replace` controls
overwriting. Metrics whose prerequisites are missing (contexts or a reference
answer) are returned as `skipped`, never as failures.

| Custom metric | Needs | Measures |
| --- | --- | --- |
| `answer_relevancy` | — | lexical overlap between prompt and answer |
| `faithfulness` | contexts | share of answer terms supported by the contexts |
| `answer_correctness` | reference | agreement with a reference answer |
| `context_precision` | contexts | share of retrieved contexts that are on-topic |
| `context_recall` | contexts + reference | reference coverage by the contexts |

Contexts come from the request when supplied; otherwise the engine extracts them
from successful `data_access` spans and tool outputs, capped by
`EVALUATION_MAX_CONTEXTS`. Automatic evaluation on run completion is opt-in via
`EVALUATION_AUTO_ON_COMPLETE`.

**Replay.** A replay is a first-class run (`replay_of_run_id`) plus a
`replay_sessions` row, so it has normal traces, costs and evaluations. The
original run is never mutated. Two modes:

- **live** — calls the provider again with optional overrides (prompt, model,
  system prompt, tools, temperature, iteration/token/timeout limits). Billed.
- **offline** — reuses the recorded `llm_call` spans through a `RecordedProvider`,
  so no provider request is made and nothing is billed; the cost delta is
  labelled hypothetical. It fails loudly if the recording runs out mid-loop.

The comparison JSON records statuses, `prompt_changed`, output similarity
(character-ratio on normalised text), latency/token/cost deltas, span and
tool-call statistics, and per-metric evaluation deltas.

## API overview

All resource routes are versioned under `/api/v1`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET/POST` | `/agents` | List / create agents |
| `GET/PATCH/DELETE` | `/agents/{id}` | Read / update / deactivate (or hard delete) |
| `POST` | `/agents/{id}/clone` | Clone a configuration |
| `GET` | `/agents/{id}/runs` | Runs for one agent |
| `POST` | `/runs` | Create and enqueue a run (`202`) |
| `GET` | `/runs` | Filter by agent/status/date/cost, search, sort, paginate |
| `GET` | `/runs/{id}` | Run with its spans |
| `GET` | `/runs/{id}/export` | Download a JSON bundle (run, agent, traces, evaluations) |
| `POST` | `/runs/{id}/cancel` | Cooperative cancellation |
| `GET` | `/runs/{id}/stream` | Server-Sent Events |
| `WS` | `/runs/{id}/ws` | WebSocket event stream |
| `GET` | `/traces` | Span search and filtering |
| `GET` | `/traces/tree?run_id=…` | Nested waterfall tree |
| `GET` | `/traces/by-trace-id/{trace_id}` | All spans for one OTel trace |
| `GET` | `/traces/{span_pk}` | One span with tool calls |
| `GET` | `/tools` | Tools an agent can be configured with |
| `GET` | `/evaluations/metrics` | Evaluator catalog and metric definitions |
| `POST` | `/runs/{id}/evaluations` | Evaluate one run synchronously |
| `GET/DELETE` | `/runs/{id}/evaluations` | Read the score summary / delete scores |
| `POST` | `/evaluations/batch` | Enqueue batch evaluation (`202`) |
| `GET` | `/evaluations/summary` | Aggregate scores by agent/metric/date |
| `POST` | `/runs/{id}/replay` | Start a live or offline replay (`202`) |
| `GET` | `/replay-sessions` | List sessions (filter by run/status) |
| `GET` | `/replay-sessions/{id}` | Session with comparison result |
| `POST` | `/replay-sessions/{id}/cancel` | Cancel a pending/running replay |
| `GET` | `/health`, `/health/ready` | Liveness / readiness |

Errors always use one envelope:

```json
{"error": {"code": "not_found", "message": "...", "details": null, "request_id": "..."}}
```

List endpoints return `{items, total, limit, offset}` with `limit` 1–200.

## Configuration

Every setting is an environment variable documented in `.env.example`. The most
important groups:

- **Database:** `DATABASE_URL` (psycopg 3), pool sizing, echo.
- **Redis:** `REDIS_URL`, `EVENTS_ENABLED`.
- **Providers:** `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`,
  `LOCAL_LLM_BASE_URL`, `LOCAL_MODEL_PREFIXES`, `DEFAULT_MODEL`, retry/timeouts.
- **Execution:** iteration/token/timeout limits, tool timeout, HTTP SSRF policy.
- **Evaluation/replay:** `EVALUATION_AUTO_ON_COMPLETE`,
  `EVALUATION_AUTO_EVALUATOR`, `EVALUATION_AUTO_METRICS`, context/reference caps,
  `EVALUATION_JUDGE_MODEL`, `REPLAY_SIMILARITY_MAX_CHARS`.
- **Observability:** `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, sampling.
- **Dashboard:** `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_JAEGER_URL` (browser,
  build-time), `API_BASE_URL` (server runtime), `FRONTEND_PORT`.
- **Operations:** rate limiting, CORS, trace retention, Celery limits, logging.

Model names may carry a provider prefix (`openai/`, `anthropic/`, `local/`,
`ollama/`); prefixes are stripped before the request and before pricing lookup.
Pricing is USD per million tokens, stored as `NUMERIC(10,6)`, and can be extended
with `CUSTOM_PRICING_JSON`.

## Database migrations

```bash
cd backend
alembic upgrade head          # apply
alembic revision --autogenerate -m "change"
alembic downgrade -1
```

The initial migration creates all six tables, enum types, indexes, check
constraints and cascades. Schema additions made for cost attribution and replay
linkage (`runs.created_at`, `runs.replay_of_run_id`, `traces.tokens_input`,
`traces.tokens_output`, `traces.model_name`, `replay_sessions.replay_run_id`,
`replay_sessions.overrides`) are included.

## Reliability and security notes

- **Retries:** provider calls retry transient failures with exponential backoff
  and jitter; deterministic failures are not retried. Celery retries are disabled
  for runs because the runner records terminal failures on the run row.
- **Idempotency:** a redelivered Celery task skips runs that are already running
  (and fails runs that exceeded the task time limit while "running").
- **Cancellation:** an operator flag in Redis plus a database fallback; both are
  checked between steps.
- **SSRF:** `http_get` only allows http(s), blocks credentials, private/loopback/
  link-local addresses and redirects, and caps response size.
- **Rate limiting:** fixed-window Redis limiter that fails open if Redis is down.
- **Telemetry safety:** trace persistence and event publishing never break run
  execution; failures are logged and swallowed.
- **Secrets:** `.env` is git-ignored. `.env.example` only contains placeholders.
  If a real key was ever placed in `.env`, rotate it at the provider.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Project scaffold, schema design | Done (schema superseded in Phase 2) |
| 2 | Agent CRUD, run execution, Celery, trace collection, tests | **Delivered** |
| 3 | Next.js dashboard (runs, traces, cost views, agent management) | **Delivered** |
| 4 | Ragas/DeepEval evaluations, replay sessions, offline replay | **Delivered** |

Ragas and DeepEval are optional: the core image stays lean and the deterministic
`custom` evaluator always works. Install the judge-based families with
`backend/requirements-eval.txt` when you want them.
