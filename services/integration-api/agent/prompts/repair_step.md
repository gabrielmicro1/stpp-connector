# Task

One step of a validated retrieval plan failed at execution time. Return a
corrected version of JUST that step as a single JSON object with the keys
id, tool, args, reason, depends_on — no prose, no code fences. Keep the
same step id. You may change tool and args; depends_on may only reference
steps that already exist in the plan.

Plan intent: {{intent}}

Failed step:

{{failed_step}}

Error: {{error_code}}: {{error_message}}

Guidance:
- If a WDP tool returned not_found for an exact identifier, fall back to the
  broader search tool (for example search_wdp_person by name rather than
  retrieve_wdp_documents with a stale ref_id).
- Reference syntax: "$steps[<id>].<path>" with dotted access, [n], and [*]
  only; at most one [*] reference; fan-out capped at {{plan_max_fanout}}.

# Available tools (this session)

{{tools_block}}
