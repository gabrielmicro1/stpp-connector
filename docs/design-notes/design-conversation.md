# Design Conversation — Decision Log

Distilled record of the design conversation that produced the specs in
`docs/specs/`. Historical background only — if anything here conflicts with
the specs, **the specs win** (see ../README.md and CLAUDE.md).

Format: each entry gives the question, the decision, the rationale, and any
superseded thinking (marked ⛔ so it isn't resurrected).

---

## 1. Streaming mechanism

**Decision:** SSE, not WebSockets.
**Rationale:** Progress flow is one-directional (server → viewer); WebSockets
add bidirectional handshake complexity for a capability never used. SSE is
plain HTTP with auto-reconnect. The ABD independently pre-approves SSE on
flow 4.
**Open risk:** BCAP / Game Warden Gateway may buffer responses or enforce
idle/total timeouts. Mitigations: 15s heartbeat event; fallback contract is
202 + job_id with STPP polling GET /jobs/{id}. Verify pass-through behavior
with Game Warden before prod.

## 2. External contract shape

**Decision:** Single request → SSE stream externally; durable job state
internally ("hybrid"). Optional GET /jobs/{id} recovery endpoint on the same
API (no new boundary crossing).
**Rationale:** STPP integration stays one request; internal job store makes
the streaming mechanism swappable and dropped streams recoverable.
⛔ Superseded: the initial design required STPP to implement the full async
job pattern (202 + separate events + result endpoints). Rejected as
unnecessary burden once the ABD showed the only client is STPP's Node
backend and SSE was already blessed.

## 3. Inbound transport

**Note:** GET with query param is workable (ABD says GET or POST) but has
URL-length, log-exposure, and retry/dedup caveats. Demo standardizes on POST.
If STPP requires GET, add idempotency/dedup on a query hash window.

## 4. What the connector queries

⛔ Superseded: the very first design assumed the connector plans and executes
**SQL directly against Postgres**, with SQL-specific safety rails (read-only
role, statement_timeout, SQL parsing/validation).
**Current:** the ABD reframed retrieval as **MCP tool calls against WDP** (a
separate authorization boundary), with the Platform One Postgres serving as
the RFFF seed *source*, not the query target. The SQL-era safety principles
carried over transformed: read-only role → per-user MCP tool scoping; SQL
validation → plan/args validation against tool schemas; schema-in-prompt →
RFFF catalog + tool descriptions in prompt.

## 5. Planning style

**Decision:** Plan-then-execute with two bounded repairs (one planning
repair on validation failure, one runtime repair per failed step), not an
agentic loop.
**Rationale:** Simpler, cheaper, faster; whole plan is inspectable/validatable
before any execution touches a network; makes the plan-streaming UX trivial.
Agentic loop noted as a possible later fallback for exploratory queries.
**Corollary:** the LLM is a plan *author*, not a tool *caller*. The agent is
the only MCP client; the LLM only ever sees tool definitions as text.
Later-step references to earlier results use `$steps[...]` symbolic refs
resolved by the agent.

## 6. Why a local RFFF seed store exists at all

**Question raised:** couldn't the agent discover everything through MCP tools?
**Decision:** Catalog-first, tools-as-fallback.
**Rationale:** Tools are verbs; the catalog is nouns. Tool schemas say how to
ask, not what exists or what vocabulary maps to it. Without the catalog the
agent burns multiple blind cross-boundary discovery calls per query
(latency, audit noise, wrong-keyword risk). With it, planning grounds on
real dataset/record identifiers locally, sub-millisecond, and survives STPP
outages. Freshness risk is handled by delta polling (flow 1) plus the
runtime repair falling back to live search on not_found.

## 7. Seed store's role upgraded by the mock data

⛔ Superseded: seed store as pure *menu* (catalog pointing at data living
elsewhere).
**Current:** the RFFF data dictionary revealed actual queryable
proposal-assessment records. Many queries answer entirely locally; WDP is
for what RFFF doesn't contain. Bridge keys: person_orcid and UEIs — the join
handles from RFFF records into WDP's research-world data. Hence two tool
families (local RFFF + WDP).

## 8. Data-quality findings drove pipeline design

Mock-data validation showed the dictionary is aspirational and the data is
empirical: undocumented enum values (Consultant, Canceled, Complete, Pending,
Declined), secretly multi-valued mitigation_strategy_proposal, unreliable PI
coverage (184/336 proposals without a PI), opaque person_overall_assessment
(≠ worst-of-factors, formula unknown), and untrustworthy dates.
**Decisions:** seed pipeline gains a validate-and-profile stage; planner
context = dictionary descriptions merged with *observed* enums; the overall
assessment is never computed or explained; date consistency is never a
reasoning basis; aggregate buckets reflect observed values without
guess-merging. Open questions for STPP: real ssa enum, whether the extra
enum values are real, the overall-assessment formula, cross-component
visibility rules.

## 9. Demo mocking strategy

**Decision:** Mock at the network boundary, never the data layer. fake-wdp is
an HTTP service over a local db, shaped like the guessed WDP Query Interface;
the MCP server's thin WDPClient speaks HTTP in demo and prod alike.
**Rationale:** If the demo MCP server spoke SQL to a local "WDP" database,
deployment would require rewriting its entire data-access layer under
pressure. With the boundary mock, deployment is a config change plus edits
confined to WDPClient once the real WDP spec lands. fake-wdp also scripts
failure modes (403s, delays, gaps) so scoping and repair are demonstrable.
Same principle applied to auth (local JWTs, same claim shape as future
Keycloak) and the LLM (env-configured client wrapper).

## 10. Container topology

**Decision:** Integration API + query agent in ONE container (agent as a
cleanly separated module); MCP server SEPARATE; fake-wdp and frontend as
additional demo-only services; one Postgres with three databases.
**Rationale:** The API↔agent boundary carries no security property, ABD box,
or assessor claim — merging is free simplicity. The agent↔MCP boundary IS a
security control: the agent processes untrusted input (user queries, WDP
results) and is the component most exposed to manipulation; a process +
network boundary means enforcement holds even if the agent misbehaves
(in prod, a K8s NetworkPolicy makes MCP the only pod with WDP egress —
a demonstrable claim, unlike in-process "the code checks permissions").
The MCP server also owns audit emission (records the agent can't tamper
with) and is drawn as a distinct ABD box (a compliance promise).
⛔ Rejected: all-in-one container. Fallback under time pressure is cutting
demo scope, not merging the MCP boundary.

## 11. Defense-in-depth pairing

The agent validates plans fully before execution, AND the MCP server
re-enforces authorization on every tools/call. Both are intentional: from
the MCP server's perspective the agent is a client whose requests were
shaped by untrusted input. Neither layer substitutes for the other.

## 12. LLM inference placement (unresolved — prod blocker, not demo blocker)

The ABD has no box or flow for model inference, but user queries and
WDP-derived data flow through the model, making it a data flow assessors
will ask about. Options weighed: in-cluster self-hosted (no new boundary,
but GPU + ops burden), Bedrock in GovCloud (pragmatic, but new external
connector → ABD update → ATO paperwork), commercial API outside GovCloud
(near-certain non-starter). **Action:** raise early; longest approval lead
time of any open item. Demo sidesteps via the env-configured LLMClient.

## 13. Other open items for the STPP/prod side

- WDP Query Interface spec (blocks final tool shapes; fake-wdp endpoints
  double as the question list).
- Gateway streaming behavior (see §1 risk).
- Delegated identity claim contract with the STPP team (demo guesses:
  sub/name/component/roles).
- Delta-update semantics of the STPP Node API for the prod sync service
  (changed-since support vs. full-pull diffing).
- Job/result retention policy for personnel-assessment data.

## 14. Development approach

Spec-driven with Claude Code: specs committed before code; contracts
(openapi.yaml, tool schemas, plan schema) generated in their own phase and
treated as interface truth across sessions; one session per build phase with
plan mode and per-phase verification; demo script = acceptance criteria;
this design-notes directory firewalled as background-only.
