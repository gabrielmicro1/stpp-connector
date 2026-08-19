# Architecture

## Context

Production topology (from the STPP PSP7 Authorization Boundary Diagram):
STPP end users talk to a React frontend + Node backend inside STPP Platform
One. The STPP Node backend sends user queries over TLS through BCAP and the
Game Warden Managed Gateway to this connector (PSP7, K8s in a Game Warden ATO
boundary). The connector plans and executes retrieval against the War Data
Platform (WDP), a separate authorization boundary, and returns JSON or SSE.
RFFF (the research-security proposal-assessment catalog) remains authoritative
in Platform One Postgres; PSP7 pulls a cold-start seed and polls deltas.

For the demo, the STPP side is impersonated by a mock frontend, and WDP is
impersonated by the fake-wdp service. Both impersonations happen at the
network boundary; nothing inside the connector changes between demo and prod.

## Components

### integration-api (container 1)
- **API layer**: terminates requests, validates the delegated JWT, creates a
  job, returns an SSE stream of plan/progress/result events. Hosts
  `GET /v1/jobs/{job_id}` for recovery. Contract: `contracts/openapi.yaml`.
- **agent/ module (same process, separate module)**: the query agent.
  Assembles planning prompts (RFFF planner context + per-user tool list from
  MCP `tools/list` + plan-format instructions), validates the returned plan
  (pure code, against contracts), executes steps via MCP `tools/call`,
  resolves `$steps[...]` references, runs the one-shot repair loop, emits
  progress events, synthesizes the final answer with one LLM call.
- **Job store access**: writes job/plan/step/result state to the `jobs` db as
  it works. The SSE emitter and `GET /jobs/{id}` both read from it — the
  streaming mechanism is swappable; the state is the truth.
- **LLMClient**: thin wrapper exposing ONE interface over two providers,
  selected by `LLM_PROVIDER`: `bedrock` — the Bedrock Converse API, with
  credentials inherited from the container's IAM role, no keys in env (prod,
  GovCloud) — and `gemini` — a simple API-key HTTP API (local testing /
  demos). Model via `LLM_MODEL`; per-call output cap `LLM_MAX_TOKENS`. The
  agent never imports a provider SDK directly; only the wrapper knows
  provider specifics.

Rationale for the merge: the API↔agent boundary carries no security property,
no ABD box, and no assessor claim. Keeping the agent a clean module preserves
later extraction. The MCP boundary is different — see below.

### mcp-server (container 2 — separate on purpose)
Exposes the tool families (see specs/mcp-tools.md) over MCP/HTTP. Per-request
sessions bound to the user context; `tools/list` filtered per user;
authorization re-checked on every `tools/call`; results size-capped and
sanitized before returning to the agent (WDP results are untrusted input into
an LLM context); every call audited. Contains the only WDP egress path
(WDPClient). In K8s, a NetworkPolicy will restrict WDP egress to this pod —
the demo preserves the same shape so that claim stays demonstrable.

### fake-wdp (container 3 — demo only)
HTTP API fronting the `wdp` database, shaped like our best guess at the WDP
Query Interface (specs/fake-wdp.md). Supports scripted failure modes (403,
delay, missing dataset) so the demo can show scoping and the repair loop.
Deleted at deployment; only WDPClient's base URL/auth change.

### frontend (container 4 — demo only)
Single dumb page impersonating the STPP Node backend via the public API
contract only (specs/fake-frontend.md).

### postgres (container 5)
One instance, three databases:
- `rfff_seed` — normalized RFFF records + field_dictionary + profiling output
  (planner context). Read-heavy; written only by seed script (demo) / sync
  service (prod).
- `jobs` — job/plan/step/result state. Retention note: results contain
  personnel assessment data; keep demo retention short and configurable.
- `wdp` — synthetic research-world data for fake-wdp, keyed on ORCIDs/UEIs
  present in rfff_seed.

### Deferred to prod (not built for demo)
- RFFF Seed/Sync Service (scheduled delta polling of the STPP Node API). The
  demo's `seed_rfff.py` is its first draft: same ingest + validate-and-profile
  stages, minus scheduling and delta detection.
- Keycloak integration: demo mints local JWTs with the same claim shape; only
  the validator's issuer/JWKS config changes later.
- Telemetry forwarding (Game Warden → C5ISR CSSP): demo logs the same
  structured/audit records to stdout.

## Request lifecycle (happy path)

1. Frontend POSTs query + JWT → integration-api. Job row created; SSE stream
   opens; `job_id` is the first event.
2. Agent looks up planner context in `rfff_seed` (dictionary + observed enums
   + relevant catalog entries).
3. Agent opens an MCP session (user context bound), calls `tools/list`.
4. LLMClient planning call → JSON plan. Agent validates against contracts;
   invalid plans get one repair round-trip, then hard-fail.
5. Plan emitted on SSE. Steps execute in dependency order via `tools/call`;
   each completion emits a progress event and persists to `jobs`.
6. Failed step → one runtime repair (failed step + error back to LLM), retry
   once, else fail the step and let synthesis explain.
7. Synthesis LLM call → final answer event → job complete.

## Trust model (short form)

User queries and WDP results are untrusted. The agent is the component most
exposed to manipulation, so: the agent can only reach WDP through the MCP
server (process + network boundary, not code discipline); the MCP server
enforces per-call regardless of plan validation; audit records are emitted by
the MCP server, not the agent.

## Environment variables (canonical names; invariant 7 — all config is env)

| Var | Service(s) | Demo default | Notes |
|---|---|---|---|
| `RFFF_SEED_DATABASE_URL` | integration-api, mcp-server | compose-set | `rfff_seed` DSN |
| `JOBS_DATABASE_URL` | integration-api | compose-set | `jobs` DSN |
| `WDP_DATABASE_URL` | fake-wdp | compose-set | `wdp` DSN |
| `PORT` | every service | per compose | listen port |
| `LLM_PROVIDER` | integration-api | `gemini` | `bedrock` \| `gemini` |
| `LLM_MODEL` | integration-api | required | provider model name |
| `LLM_API_KEY` | integration-api | required for gemini | unused for bedrock (IAM role) |
| `LLM_BASE_URL` | integration-api | provider default | optional endpoint override |
| `LLM_MAX_TOKENS` | integration-api | 4096 | per-call output cap (runtime budget) |
| `AWS_REGION` | integration-api | — | bedrock only; credentials come from the IAM role, never env |
| `JWT_SECRET` | integration-api, mcp-server, mint_jwt.py | compose-set | HS256 shared secret (demo; Keycloak JWKS in prod) |
| `JWT_TTL_HOURS` | mint_jwt.py | 720 | baked-in frontend tokens must outlive demo week |
| `MCP_SERVER_URL` | integration-api | compose-set | agent → MCP base URL |
| `MCP_MAX_ROWS` | mcp-server | 200 | result row cap |
| `WDP_BASE_URL` | mcp-server | fake-wdp URL | prod: WDP Query Interface |
| `WDP_AUTH_TOKEN` | mcp-server | compose-set | bearer sent to WDP; demo compose sets it equal to `WDP_FAKE_TOKEN` |
| `WDP_FAKE_TOKEN` | fake-wdp | compose-set | token fake-wdp presence-checks |
| `FAKE_WDP_DENY_ORCIDS` | fake-wdp, seed_wdp.py | compose-set | comma list; must exist in rfff_seed WITH WDP records |
| `PLAN_MAX_STEPS` | integration-api | 8 | validation limit (→ `plan_invalid`) |
| `PLAN_MAX_FANOUT` | integration-api | 10 | fan-out cap, applied to the flattened list |
| `JOB_MAX_SECONDS` | integration-api | 120 | runtime budget (→ `budget_exceeded`) |
| `JOBS_RETENTION_HOURS` | integration-api | 24 | startup sweep |
| `SSE_PING_SECONDS` | integration-api | 15 | heartbeat interval |
| `PLANNER_MAX_MATCHES` | integration-api | 20 | catalog entries matched into the planning prompt |

## Open items (prod) — consolidated from the design log

- Gateway/BCAP SSE pass-through: verify with Game Warden before prod. If the
  gateway buffers or times out streams, the fallback contract is `202 +
  job_id` with STPP polling `GET /v1/jobs/{job_id}` — same job store, no new
  boundary crossing.
- WDP Query Interface spec (blocks final WDP tool shapes; fake-wdp's
  endpoints double as the question list).
- Delegated identity claim contract with STPP (demo guesses
  sub/name/component/roles).
- Delta-update semantics of the STPP Node API (changed-since vs full-pull
  diffing) for the prod sync service.
- Job/result retention policy for personnel-assessment data.
- LLM inference placement: the ABD has no box/flow for inference; longest
  approval lead time of any open item — raise early. Working assumption:
  Bedrock Converse in GovCloud via the container IAM role (see LLMClient).
- Cross-component visibility rules: the JWT `component` claim is carried
  through every layer and recorded in audit records but enforces nothing in
  the demo.
- Data-dictionary questions: real `ssa` enum; whether undocumented observed
  enum values are real; the person_overall_assessment formula.

## Build order (one Claude Code session per phase)

| Phase | Deliverable | Verify |
|---|---|---|
| 1 | Scaffold: compose, migrations, Makefile | `make up` starts clean |
| 2 | Contracts: openapi.yaml, mcp-tools/*.json, plan-format.json | schemas validate |
| 3 | Seed pipeline (`seed_rfff.py`) | `make seed` ingests mock CSVs; profile report flags undocumented enums |
| 4 | Integration API + job store + stub agent (canned plan) | `curl` streams SSE |
| 5 | Mock frontend | canned plan streams in a browser |
| 6 | Query agent (real plans; MCP mocked) | validation/resolution tests pass |
| 7 | MCP server + fake-wdp + `seed_wdp.py` | two test users see different tool lists |
| 8 | Integration pass | demo script passes end-to-end twice |

Phase 5 is the first end-to-end demo-able milestone; target mid-week.
