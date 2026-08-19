# PSP7 RFFF Retrieval Connector

Natural-language query connector for the STPP RFFF Retrieval Platform. Receives
research-security queries from STPP, plans tool-call steps with an LLM, executes
them against a local RFFF store and the War Data Platform (WDP) via an MCP
server, and streams plan + progress + results back over SSE.

## TIER 1 — ARCHITECTURAL INVARIANTS (never violate; ask before deviating)

1. The query agent NEVER talks to WDP or wdp-postgres directly. All WDP access
   goes through the MCP server over HTTP. No exceptions, including tests.
2. The MCP server is a SEPARATE container from the integration API. Do not
   merge it "for simplicity." This boundary is a security control and an ABD
   commitment.
3. The integration API and query agent run in the SAME container/process, but
   the agent lives in its own module (`agent/`) with no imports from the API
   layer except shared types. It must be extractable to its own service later.
4. Mock at the network boundary, never the data layer: the demo replaces the
   real WDP Query Interface with the fake-wdp HTTP service. The MCP server's
   WDPClient speaks HTTP in both demo and prod.
5. Delegated user context (JWT claims) is threaded through every layer:
   API → agent → MCP session → WDP calls. Never a shared/system credential on
   the WDP path. MCP sessions are created per-request with the user context
   bound to them.
6. The MCP server enforces authorization on EVERY tools/call, even though the
   agent validates plans first. It filters tools/list per user. It never trusts
   the plan.
7. All configuration via environment variables (12-factor). No hardcoded URLs,
   ports, credentials, or model names in code.
8. The LLM is accessed only through the `LLMClient` wrapper (env-configured
   endpoint + model). No SDK calls elsewhere.
9. `person_overall_assessment` is an opaque stored value. Never compute,
   derive, or explain it. (Upstream formula unknown; see data-model spec.)
10. Planner context uses OBSERVED enum values from data profiling, merged with
    dictionary descriptions — never the dictionary enums alone.
11. The mock frontend uses only the public Integration API contract (same
    endpoints, same JWT auth, same SSE) — no demo-only backdoors.

## Specs are the source of truth

- `docs/specs/` defines what to build. Read the relevant spec before each phase.
- `docs/architecture.md` defines components, boundaries, and build order.
- `docs/design-notes/` is historical background ONLY. If it conflicts with
  `docs/specs/`, the specs win. Do not read design-notes unless asked.

## Repo layout

```
services/
  integration-api/     # FastAPI app: API layer + agent/ module + job store access
  mcp-server/          # MCP server: tools, scoping, WDPClient, audit
  fake-wdp/            # Demo-only WDP stand-in (HTTP API over wdp db)
  frontend/            # Mock STPP frontend (Vite, single page)
db/
  migrations/          # SQL migrations for seed, jobs, wdp databases
scripts/
  seed_rfff.py         # CSV ingest + validate-and-profile → seed db + planner context
  seed_wdp.py          # Synthetic WDP data keyed on ORCIDs/UEIs from seed db
  mint_jwt.py          # Dev JWT minting (HS256, JWT_TTL_HOURS) for demo users
data/mock/             # proposal-assessment-schema.csv, proposal-assessment-mock.csv
docs/                  # architecture, specs, design-notes
```

## Conventions

- Python 3.12, FastAPI, pydantic v2, asyncpg/SQLAlchemy core, pytest.
- Frontend: Vite + vanilla JS (no framework unless already present).
- One Postgres instance, three databases: `rfff_seed`, `jobs`, `wdp`.
- JSON Schema files under `contracts/` are generated once (phase 2) and are
  the interface truth: `contracts/openapi.yaml`, `contracts/mcp-tools/*.json`,
  `contracts/plan-format.json`. Implement against them; change them only with
  explicit approval. Contracts are STRUCTURAL: enum-ish filter fields are
  typed as plain strings there. Observed enum sets live in seed-generated
  artifacts (`observed_enums`/`planner_context` in rfff_seed), loaded at
  runtime by the MCP server and the plan validator — seed runs never touch
  `contracts/`.
- Structured JSON logging everywhere; every MCP tools/call emits an audit
  record (user sub, component, tool, args, result count/size, duration,
  outcome, timestamp — canonical list; mcp-tools spec matches).
- LLMClient supports two providers behind one interface, selected by
  `LLM_PROVIDER`: `bedrock` (Bedrock Converse API; credentials inherited from
  the container's IAM role; prod only) and `gemini` (simple API-key HTTP API;
  local testing / demos). Model via `LLM_MODEL`. See the env table in
  docs/architecture.md.
- Commit style: `phase-N: <what>`. Commit only after phase verification passes.

## Commands

```
make up        # docker compose up --build
make seed      # run seed_rfff.py then seed_wdp.py
make test      # pytest across services
make demo      # prints the three demo queries (anchors substituted) + test JWTs
```

## Verification habit

End every phase by running `make up` and exercising the phase's verify step
(see docs/architecture.md build order). Green unit tests are necessary, not
sufficient.
