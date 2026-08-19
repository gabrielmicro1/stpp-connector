# MCP tool contracts

One file per tool; each is a full MCP tool definition:
`{name, description, inputSchema, outputSchema}` plus `x-` annotations.
These files are the interface truth (frozen in phase 2; change only with
explicit approval). Rationale and semantics: `docs/specs/mcp-tools.md`.

## Conventions shared by every tool

- **Structural contracts.** Enum-ish filter fields are typed as plain
  `string` and annotated `"x-enum-source": "observed_enums"`. The allowed
  values are the OBSERVED sets profiled from `rfff_seed`, regenerated on
  every seed run; the MCP server and the agent's plan validator load them at
  runtime, and the MCP server renders them into the tool descriptions it
  serves. Seed runs never modify these files.
- **Result envelope.** Every success response is
  `{data, meta: {total, returned, truncated}}`. Rows are capped at
  `MCP_MAX_ROWS` and long text fields are truncated; `meta` reports honestly.
- **Errors** are structured, protocol-level (not in each `outputSchema`):
  `{error: {code: not_authorized | not_found | invalid_args |
  upstream_unavailable, message}}` — never raw HTTP errors. `not_authorized`
  is non-repairable by design (see plan-format spec).
- **Scoping.** `x-role` names the role required to list and call the tool
  (`rfff_reader` for `x-family: rfff-local`, `wdp_reader` for
  `x-family: wdp`). `tools/list` is filtered per user; every `tools/call`
  re-checks authorization and emits an audit record.
