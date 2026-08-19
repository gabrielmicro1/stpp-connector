# Spec: fake-wdp (demo-only WDP stand-in)

Purpose: let the MCP server speak HTTP to "WDP" exactly as it will in prod,
so deployment is a WDPClient config/edit change, never an MCP rewrite. The
endpoint shapes below are our best guess at the WDP Query Interface and
double as the question list to verify when the real spec arrives.

## Service

Small FastAPI app over db `wdp`. Auth: single static bearer token
(`WDP_FAKE_TOKEN` env; demo compose sets the MCP server's `WDP_AUTH_TOKEN`
to the same value) — presence-checked only; the real per-user enforcement
being demoed lives in the MCP server, while fake-wdp simulates WDP's own
denials via the failure hooks below.

## Endpoints

```
GET /v1/persons?orcid=&name=&limit=
    → {results: [{ref_id, orcid, name, affiliations[], publication_count,
                  funding_count}], total}
GET /v1/entities?uei=&name=&limit=
    → {results: [{ref_id, uei, name, country, record_count}], total}
GET /v1/documents/{ref_id}?limit=
    → {results: [{doc_id, type: publication|funding_record|entity_record,
                  title, year, source, detail}], total}
GET /v1/healthz
```

`/v1/persons` and `/v1/entities` return summaries and COUNTS only; document
detail is behind `/v1/documents/{ref_id}`. The asymmetry is deliberate — it
mirrors the cheap-discovery / expensive-retrieval tool split (see mcp-tools
spec).

404 with `{error:{code:"not_found"}}` for unknown ref_ids/identifiers, which
the MCP server passes through as its structured `not_found` (drives the
agent's repair path).

## Scripted failure modes (demo material — make them togglable, not random)

- `?_delay=<seconds>` on any endpoint: sleep before responding (shows SSE
  progress genuinely streaming during a slow step).
- `FAKE_WDP_DENY_ORCIDS` env (comma list): those persons return 403
  `{error:{code:"not_authorized"}}` — simulates WDP-side denial, distinct
  from MCP-side scoping; synthesis should report it as "not authorized" not
  as "no data". Values must be ORCIDs present in rfff_seed that DO have WDP
  records (denial must be distinguishable from absence); `seed_wdp.py`
  verifies this and records the anchor (see data-model spec).
- Deterministic gaps: seed script leaves some ORCIDs without WDP records
  (see data-model spec) → natural empty results / not_found.

## Non-goals

No pagination beyond limit, no auth realism, no write endpoints, no attempt
to guess WDP's actual query language. Anything fancier belongs in the real
WDPClient integration after the WDP spec lands.
