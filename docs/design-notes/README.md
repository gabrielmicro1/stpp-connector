# Design Notes — historical background only

Place the exported design conversation here as `design-conversation.md`
(export it from the Claude UI).

Rule (also in CLAUDE.md): this directory records how decisions were reached,
including superseded thinking — early drafts assumed direct SQL against
Postgres before the ABD reframed retrieval as MCP tools against WDP, and the
external job-pattern contract evolved into single-request + SSE with internal
job state. If anything here conflicts with `docs/specs/`, the specs win.

Claude Code sessions should not read this directory by default; consult it
only when asked for rationale behind a spec decision.
