# Spec: Demo Script (= acceptance criteria)

The MVP is done when this five-minute arc runs end-to-end twice in a row via
`make up && make seed` and the mock frontend. Each query escalates the story.

## Query 1 — local-only aggregate (fast win; the "it understands RFFF" beat)

User: `analyst-full`
Query: "How many proposals had Prohibited Factors on factor 4 in fiscal year
2025? Break it down by reviewing component."

Expected: plan of one `aggregate_assessments` step (group_by
reviewing_component, filters fiscal_year=2025, factor4=Prohibited Factors);
completes in seconds; answer gives counts per component. Demonstrates: SSE
plan + progress, observed-enum grounding, no WDP round trip needed.

## Query 1b — follow-up in the same conversation (the multi-turn beat)

User: `analyst-full`, same conversation as Query 1.
Query: "How does that compare to fiscal year 2024?"

Expected: the plan resolves "that" from the conversation history (aggregate
on factor4 Prohibited Factors by reviewing component, now for FY2024 —
grouping/filtering by fiscal year as the LLM sees fit); the answer compares
the two years. Demonstrates: stateless multi-turn — the frontend resent the
whole conversation as `messages`; the connector kept no state between
requests.

## Query 2 — cross-boundary join (the architecture beat)

User: `analyst-full`
Query: "Give me research background on the personnel of proposal
<PROPOSAL_NUMBER>." (Picked by `seed_wdp.py`: a proposal whose personnel
include at least one ORCID with WDP records AND one without; recorded in the
`demo_anchors` table; `make demo` prints the fully substituted query.)
Current seed picks proposal **12443080** ("Assimilated tangible parallelism
Project", Wayne Enterprises): David Anderson has WDP records, Peter Jones
has none.

Expected: three-step plan — get_proposal → search_wdp_person fan-out
(`$steps[1].data.personnel[*].person_orcid`) → retrieve_wdp_documents
fan-out over the ref_ids found; progress ticks per person; the no-records
person surfaces as an honest gap (possibly after a visible repair attempt on
the search step); answer merges RFFF roles/assessments with WDP
publication/funding detail.
Demonstrates: catalog-first planning, reference fan-out, the cheap-discovery
→ expensive-retrieval tool split, MCP → fake-wdp path, honest synthesis.

## Query 3 — scoping + failure (the security beat)

Part A — user: `analyst-local` (no wdp_reader), same query as Query 2.
Expected: plan contains ONLY local tools (WDP tools never in tools/list);
answer covers RFFF data and states that external research background is
outside this user's access — not an error, a scoped answer.

Part B — user: `analyst-full`, query a person whose ORCID is in
`FAKE_WDP_DENY_ORCIDS` (name printed by `make demo` from `demo_anchors`):
"What research background do we have on <NAME>?"
Expected: WDP step fails `not_authorized` immediately — non-repairable by
design, so no repair attempt appears — visible in the checklist; answer
reports the denial explicitly. Distinguishes MCP-side scoping (A) from
WDP-side denial (B).

## Talk track anchors (one line each)

- Single request in, stream out — STPP's integration is one POST.
- The plan you're watching is validated before anything executes.
- The agent physically cannot reach WDP except through the MCP server.
- Same containers deploy to Game Warden; only fake-wdp and the frontend are
  demo scaffolding, and only env vars change.

## Failure drill (rehearse once before demo day)

Kill fake-wdp mid-Query-2: step fails after retry, remaining local steps
complete, synthesis reports the gap, `done` still arrives. The demo survives
its own outage.
