# Spec: Mock Frontend (demo-only)

Impersonates the STPP side for the demo. Hard rule (CLAUDE.md invariant 11):
it consumes ONLY the public Integration API contract — same endpoints, same
JWT auth, same SSE — so it doubles as executable integration documentation
for the real STPP team. No demo-only backdoors, no extra endpoints added to
the API "for the UI."

## Scope: one page, deliberately dumb

Vite + vanilla JS — no framework (per CLAUDE.md conventions; the earlier
"minimal React" escape hatch is removed), no router, no state library, no
design system. Served by the `frontend` compose service.

Elements:
1. **User picker** — dropdown with the two test users (`analyst-full`,
   `analyst-local`); selecting one sets the JWT used for requests (tokens
   baked in at build/dev time from `scripts/mint_jwt.py` output). This is the
   live per-user-scoping demo control.
2. **Query box + submit** — POSTs to `/v1/query` with the JWT, opens the SSE
   stream (fetch + ReadableStream; EventSource can't set Authorization
   headers). Sends the full conversation as `messages` (the page keeps it
   client-side; the server is stateless). A **transcript** above the box
   shows prior turns; completed answers are appended as assistant turns.
   The conversation resets on: the New-conversation button, switching user
   (cross-user history replay is incoherent), or any non-follow-up preset.
   Layout is chat-shaped: one scroll region holds the whole transcript (no
   per-message scrolling), the composer is pinned at the bottom, and the
   Plan / Answer / raw-event-log panels are collapsible dropdowns inside the
   scroll region (collapsed by default; toggle state persists across runs).
3. **Plan panel** — renders the `plan` event as a checklist: one row per step
   showing tool, human-readable reason, and args summary.
4. **Progress** — `step` events tick the checklist (spinner → check/✗;
   `repairing` shown distinctly — the repair loop is demo material, make it
   visible). Row/truncation counts from the step event's `rows`/`truncated`
   fields shown inline.
5. **Answer panel** — renders the `answer` event text; on `error`, shows
   code + message plainly.
6. **Raw event log** (collapsible) — every SSE event as received; the
   "here's what your backend will consume" view for STPP engineers.

## Behaviors

- Reconnect: if the stream drops, fall back to polling `GET /v1/jobs/{id}`
  every 2s using the stored job_id — demonstrating the recovery path exists.
  If it drops before the first `job` event delivers a job_id, resubmit the
  query instead.
- Three demo-query preset buttons (from specs/demo-script.md) so nobody
  live-types under pressure.
- No client-side interpretation of results beyond rendering; honesty about
  failures comes from synthesis, not UI smoothing. Answers (and assistant
  transcript turns) render as markdown, sanitized before DOM insertion —
  answer text derives from untrusted WDP results.

## Non-goals

Auth flows, styling polish, mobile, multiple concurrent jobs, server-side
history (the conversation lives in the page and is resent each turn).
