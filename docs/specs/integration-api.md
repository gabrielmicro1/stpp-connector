# Spec: Integration API

The front door. In prod this is what the STPP Node backend calls through the
Game Warden Gateway; in the demo the mock frontend plays that role using the
identical contract.

## Endpoints (source of truth: contracts/openapi.yaml, generated in phase 2)

### POST /v1/query
Body: `{ "query": "<natural language>" }`
Headers: `Authorization: Bearer <JWT>` (delegated user context)
Response: `text/event-stream` (SSE). The response IS the stream — single
request from the client's perspective. (GET with `?q=` may be added for STPP
compatibility later; POST is primary for the demo. If GET is ever added it
MUST include idempotency/dedup on a query-hash window — proxy retries and
refetches must not double-run jobs.)

### GET /v1/jobs/{job_id}
Returns current job state as JSON: status, plan, per-step statuses, result if
complete. Recovery/polling path — same data the SSE emits, read from the job
store. STPP never has to use it; it exists so a dropped stream doesn't orphan
a 60-second query. Unknown or already-swept job_id → 404
`{error:{code:"job_not_found"}}`.

### GET /v1/healthz
Liveness for compose/K8s.

## SSE event contract

Events are `event: <type>` + `data: <json>`; every event carries `job_id` and
a monotonically increasing `seq` so a reconnecting client can detect gaps.

| type | data | when |
|---|---|---|
| `job` | `{job_id, status:"planning"}` | first event, immediately |
| `plan` | the validated plan JSON (see specs/plan-format.md) | after validation |
| `step` | `{step_id, status: running\|complete\|repairing\|failed, summary?, rows?, truncated?}` | per transition; `rows`/`truncated` mirror the MCP result meta |
| `answer` | `{text}` | synthesis complete |
| `error` | `{code, message}` | terminal failure |
| `done` | `{status: complete\|failed}` | always last |
| `ping` | `{}` | every `SSE_PING_SECONDS` (default 15) heartbeat (gateway idle-timeout insurance; keep in demo so the behavior is exercised) |

## Auth (demo)

- JWTs minted by a dev script (`scripts/mint_jwt.py`), HS256 with shared
  secret `JWT_SECRET`, TTL `JWT_TTL_HOURS` (default 720 — the frontend bakes
  tokens in at build time; they must outlive the demo week), claims:
  `sub` (user id), `name`, `component` (e.g. "DARPA"), `roles`
  (e.g. `["rfff_reader"]`, `["rfff_reader","wdp_reader"]`).
- Middleware validates signature + expiry, builds a `UserContext` object, and
  threads it through agent → MCP session. Rejects missing/invalid with 401.
- The `component` claim is carried through all layers and recorded in MCP
  audit records but enforces NOTHING in the demo (cross-component visibility
  is an open STPP question — see architecture Open items).
- Two standing test users (used by frontend picker and demo script):
  - `analyst-full`: rfff_reader + wdp_reader
  - `analyst-local`: rfff_reader only (no WDP tools — scoping demo)
- Prod swap: replace validator config with Keycloak issuer/JWKS; claim shape
  and everything downstream unchanged.

## Job store (database `jobs`)

Tables (final DDL in phase 2 migrations):
- `jobs(job_id uuid pk, user_sub, query, status, created_at, updated_at)`
  status: planning | executing | synthesizing | complete | failed
- `job_plans(job_id fk, plan jsonb, validated_at)`
- `job_steps(job_id fk, step_id, status, tool, args jsonb, result jsonb,
  error text, started_at, finished_at)` — result rows are size-capped before
  storage (same cap the MCP server applies).
- `job_events(job_id fk, seq, type, data jsonb, at)` — the SSE emitter reads
  from here (write-then-emit), which is what makes GET /jobs/{id} and the
  stream consistent.

Retention: `JOBS_RETENTION_HOURS` env var; a startup sweep deletes older jobs.
Default 24 for demo. (Results contain personnel assessment data.)

## Errors

- `plan_invalid` — plan validation failed after the one planning repair.
  Includes exceeding `PLAN_MAX_STEPS`: that is a VALIDATION failure, never a
  budget one.
- `budget_exceeded` — RUNTIME budgets only: wall-clock `JOB_MAX_SECONDS` or
  the per-call LLM output cap `LLM_MAX_TOKENS`.
- `llm_unavailable` — LLM endpoint unreachable or erroring after retry
  (planning, repair, or synthesis call).
- `internal_error` — anything unexpected; message sanitized.
- Step failures are NEVER terminal. Synthesis always runs once execution
  ends — even if every step failed — and states what could and couldn't be
  retrieved; the job then ends `answer` + `done {status:"complete"}`.
  `done {status:"failed"}` accompanies only the `error` codes above.
