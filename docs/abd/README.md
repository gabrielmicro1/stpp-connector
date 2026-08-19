# Authorization Boundary Diagrams (ABD)

Versioned exports of the STPP PSP7 Authorization Boundary Diagram — the
compliance artifact that `docs/architecture.md` derives its topology and
boundary claims from (MCP server as a distinct box, SSE on flow 4, WDP as a
separate authorization boundary, etc.).

## Conventions

- One file per version, never overwritten:
  `STPP-PSP7-ABD-<Status>-<YYYY-MM-DD>.drawio.pdf`
  (Status: `Draft`, `Review`, `Approved`, ...). If the source `.drawio` file
  is available, commit it alongside the PDF with the same basename.
- The newest date is the current version. When a new version lands, note in
  the table below what changed — especially anything that affects an
  architectural invariant in CLAUDE.md or a spec in `docs/specs/`.

## Versions

| File | Date | Status | Notes |
|---|---|---|---|
| `STPP-PSP7-ABD-Draft-2026-08-19.drawio.pdf` | 2026-08-19 | Draft | Initial draft this repo's specs were written against |
