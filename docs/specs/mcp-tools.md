# Spec: MCP Server and Tools

Separate container. The agent is its only client. Sessions are created
per-request with the delegated `UserContext` bound; `tools/list` is filtered
by role; every `tools/call` re-checks authorization and emits an audit record.
Context transport: the agent forwards the ORIGINAL delegated JWT as the
`Authorization` header on the MCP HTTP session; the MCP server validates it
independently (same `JWT_SECRET` in demo; Keycloak JWKS in prod) and binds
the claims itself — identity is never accepted as plain fields relayed by
the agent.
JSON Schemas for each tool are generated in phase 2 under `contracts/mcp-tools/`
and are the interface truth; this doc is the rationale + shape.

## Design rules

- Task-shaped tools, not a 1:1 mirror of backend APIs. Cheap discovery tools +
  explicit expensive retrieval tools.
- Tool descriptions are prompt engineering: each states what it does, when to
  use it, when NOT to, what each parameter means, and enum values (observed
  set — see data-model spec). Write them for a smart, literal reader.
- Contract schemas under `contracts/mcp-tools/` are STRUCTURAL and frozen in
  phase 2: enum-ish filter fields are typed as plain strings there. The
  observed enum sets live in seed-generated artifacts (`observed_enums` /
  `planner_context` in rfff_seed), are loaded at runtime by the MCP server
  and the agent's plan validator, and are rendered into tool descriptions.
  Seed runs never modify `contracts/`.
- Result hygiene: every tool caps rows (`MCP_MAX_ROWS`, default 200) and
  truncates long text fields; responses carry `{data, meta:{total, returned,
  truncated}}` so the agent can report honestly.
- Structured errors the LLM can act on: `{error: {code: not_authorized |
  not_found | invalid_args | upstream_unavailable, message}}` — never raw
  HTTP errors.
- Audit record per call: user sub, component, tool, args, result count/size,
  duration, outcome, timestamp (canonical list; CLAUDE.md matches). Emitted
  by the MCP server (not the agent) as structured JSON.

## Family 1 — local RFFF tools (db `rfff_seed`; role: rfff_reader)

### search_proposals(filters?, keywords?, limit?)
Filters: fiscal_year, reviewing_component, reviewing_subcomponent,
assessment_state, mitigation_status, award_state, award_type, review_type,
factor1..factor4_assessment, person_overall_assessment (match-only, opaque —
see invariant 9). Keywords search title + entity names. Returns proposal rows
(no personnel detail) + meta.

### get_proposal(proposal_number)
Full record: proposal fields + personnel with roles, affiliations, factor
assessments, mitigation fields, file refs.

### search_personnel(name?, orcid?, affiliation?, limit?)
Person rows + the proposals they appear on (number, title, role, overall
assessment). People recur across ~10 proposals each in the mock data —
person-centric queries are first-class.

### aggregate_assessments(group_by, filters?)
group_by ⊆ {fiscal_year, reviewing_component, reviewing_subcomponent,
assessment_state, mitigation_status, award_state, factor1..4_assessment,
person_overall_assessment}; same filters as search_proposals. Returns counts —
rollups without hauling rows into the LLM context. Buckets reflect observed
values (e.g. "Complete" and "Implemented" appear as separate buckets; never
guess-merge).

Counting semantics: counts DISTINCT proposals. Person-level filters
(factor1..4_assessment, person_overall_assessment) mean "the proposal has at
least one person matching". Grouping by a person-level field counts a
proposal once per distinct value present among its personnel, so buckets can
overlap; `meta` flags this and the tool description states it.

## Family 2 — WDP tools (via WDPClient → fake-wdp/WDP; role: wdp_reader)

Keyed on the bridge identifiers RFFF provides: person_orcid and UEIs.
Shapes are our best guess pending the real WDP Query Interface spec; keep
WDPClient thin so only it changes when the spec lands.

### search_wdp_person(orcid?, name?, limit?)
Cheap discovery: person SUMMARIES — identity, affiliations known to WDP,
publication/funding counts, and a `ref_id`. Actual publications and funding
records come from retrieve_wdp_documents(ref_id); plans that want detail
chain the two (see demo Query 2). Mirrors fake-wdp `/v1/persons`.

### search_wdp_entity(uei?, name?, limit?)
Same for institutions/entities: summaries + record_count + `ref_id`.

### retrieve_wdp_documents(ref_id, limit?)
Pulls document/record detail for a reference returned by a search tool. The
expensive, deliberate call — never the first call in a plan.

## Scoping matrix (demo)

| tool | rfff_reader | wdp_reader |
|---|---|---|
| Family 1 (all) | listed + callable | — |
| Family 2 (all) | — | listed + callable |

`analyst-local` (rfff_reader only) never sees Family 2 in tools/list, so the
LLM cannot plan with them — and if a forged plan tries anyway, tools/call
returns `not_authorized`. Both behaviors are demo material.

## WDPClient

Thin class inside mcp-server: base URL, auth header, one method per endpoint,
timeouts + `upstream_unavailable` mapping. Env: `WDP_BASE_URL`,
`WDP_AUTH_TOKEN` (bearer; demo compose sets it equal to fake-wdp's
`WDP_FAKE_TOKEN`). Demo points at fake-wdp; prod points at the WDP Query
Interface. Nothing above
it changes.
