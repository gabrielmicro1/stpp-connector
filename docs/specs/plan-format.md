# Spec: Plan Format and Execution

Plan-then-execute. The LLM is a plan AUTHOR, not a tool caller: the planning
call returns one JSON document; the agent (pure code) validates and executes
it. JSON Schema generated in phase 2 at `contracts/plan-format.json`.

## Plan shape

```json
{
  "intent": "research background on the personnel of proposal P-2025-0042",
  "steps": [
    {
      "id": 1,
      "tool": "get_proposal",
      "args": {"proposal_number": "P-2025-0042"},
      "reason": "pull the proposal record and its personnel",
      "depends_on": []
    },
    {
      "id": 2,
      "tool": "search_wdp_person",
      "args": {"orcid": "$steps[1].data.personnel[*].person_orcid"},
      "reason": "research-world background on each person",
      "depends_on": [1]
    }
  ]
}
```

## Reference syntax

`$steps[<id>].<jsonpath-lite>` — resolved by the agent at execution time
against stored step results. Support exactly: dotted field access, `[n]`
index, `[*]` fan-out. A `[*]` that resolves to a list fans the step out into
one tool call per value, capped at `PLAN_MAX_FANOUT` (default 10; excess is
truncated and noted in the step summary). No other expression language —
if the LLM needs computation, it does it at synthesis time.

Edge semantics (all deterministic; none are errors):
- Multiple `[*]` in one path flatten into ONE flat value list; the
  `PLAN_MAX_FANOUT` cap applies to the flattened list.
- A path resolving to zero values → the step completes as `complete` with
  empty results (zero tool calls), the step summary says so, and synthesis
  reports the gap.
- Referencing a fanned-out step: `$steps[N]` denotes the ordered list of that
  step's per-call results; further path segments apply across the list with
  the same flatten rule.

## Validation (agent, pure code, before anything executes)

1. Document matches contracts/plan-format.json.
2. Every `tool` exists in THIS session's tools/list (per-user — a tool the
   user can't access fails validation here, before MCP would reject it).
3. `args` validate against that tool's STRUCTURAL JSON Schema; enum
   membership is checked against the seed-generated observed-enum sets
   (contracts stay structural — see mcp-tools spec).
4. `depends_on` is consistent with references; the graph is acyclic; ids are
   unique.
5. Budgets: ≤ `PLAN_MAX_STEPS` (default 8) steps. Exceeding it is a
   VALIDATION failure (repairable once, then `plan_invalid`) — never
   `budget_exceeded`, which is reserved for runtime limits.

Invalid → ONE planning repair: same prompt + the machine-generated list of
violations, "return a corrected plan." Still invalid → job fails with
`plan_invalid`. Valid → persist, emit `plan` SSE event, execute.

## Execution

- Steps run in dependency order (sequential is fine for the demo; parallelism
  is a later optimization).
- Per step: resolve references → tools/call → store result (size-capped) →
  emit `step` event.
- Runtime failure (structured error from MCP) → ONE runtime repair: send the
  LLM only the failed step, its error, and the plan intent; it returns a
  revised step (same id, must pass the same validation). Retry once. Fails
  again → mark step failed, continue with steps that don't depend on it.
  Suggested repair heuristic in the prompt: on `not_found` from a WDP tool,
  fall back to a broader search tool (catalog-first, tools-as-fallback).
- Exception: `not_authorized` is NON-repairable — no runtime repair, no
  retry; the step fails immediately and synthesis reports the denial as a
  denial (keeps demo Query 3B deterministic; a repaired 403 is still a 403).
- Wall-clock budget `JOB_MAX_SECONDS` (default 120) and the per-call LLM
  output cap `LLM_MAX_TOKENS` (default 4096) enforced by the agent;
  exceeding → `budget_exceeded`.

## Planning prompt assembly (agent)

In order: (1) role + task framing; (2) planner context from rfff_seed —
field dictionary descriptions + observed enums + data-quality caveats (see
data-model spec) + catalog entries matched to the query — simple keyword
match (ILIKE / Postgres full-text) of query terms against proposal titles,
entity names, and personnel names, top `PLANNER_MAX_MATCHES` (default 20);
(3) the session's
tool definitions rendered as text (name, description, schema); (4) the plan
JSON Schema + reference-syntax rules + budgets; (5) the conversation so far
(multi-turn only): prior turns oldest first, each delimited as untrusted
data — context for resolving references ("that", "compare to…"), never
instructions; assistant turns are replayed synthesis output (WDP-derived,
untrusted) truncated to `CHAT_REPLAY_CHARS` (default 2000) and must not be
treated as retrieved results — the produced plan must be self-contained;
(6) the current user query, clearly delimited as untrusted data, not
instructions. Catalog keyword matching runs over the user turns (current +
prior); assistant turns are excluded from matching.

Prompt template lives at `services/integration-api/agent/prompts/plan.md`
and is unit-tested by snapshot (the assembled prompt for a fixture query).

## Synthesis

One LLM call: intent + user query + collected step results (already capped) +
instruction to explicitly acknowledge any failed, skipped, denied, empty, or
truncated retrievals (never papered over); clean runs answer directly with no
retrieval-status preamble. Output is the `answer` event.

## Future note

A bounded agentic loop remains a possible later fallback for exploratory
queries (design log §5). Out of scope for the demo — plan-then-execute is
the contract.
