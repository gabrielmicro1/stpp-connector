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

The page is themed as the STPP "Fundamental Research Risk Reviews" (FRRR)
home screen: a navy banner with a generic roundel (deliberately NOT a DoD
or agency seal) and three tabs — LEADERSHIP, ANALYSIS, AI. Leadership and
Analysis are static decorative placeholders (fake data, zero JS, outside
the API contract); the AI tab hosts the entire live client described
below. Tab switching is plain show/hide (`src/tabs.js`); the default tab
is Leadership, so the demo's first beat is clicking AI.

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
   The conversation resets on: the New-conversation button or switching
   user (cross-user history replay is incoherent).
   Layout is chat-shaped: one scroll region holds the whole transcript (no
   per-message scrolling) and the composer is pinned at the bottom. Each
   submitted query renders an in-chat agent-activity block — plan intent +
   the live step checklist ticking as `step` events arrive — between the
   user turn and the assistant answer; blocks from earlier turns stay in
   the transcript.
3. **Plan panel** — renders the `plan` event as a checklist: one row per step
   showing tool, human-readable reason, and args summary.
4. **Progress** — `step` events tick the checklist (spinner → check/✗;
   `repairing` shown distinctly — the repair loop is demo material, make it
   visible). Row/truncation counts from the step event's `rows`/`truncated`
   fields shown inline.
5. **Answer panel** — renders the `answer` event text; on `error`, shows
   code + message plainly.
(There is no in-page raw event log: SSE frames land in the browser
console via `console.debug`, and the terminal narrator
`scripts/demo_tap.py` is the "here's what your backend will consume" view
for STPP engineers.)

## Behaviors

- Reconnect: if the stream drops, fall back to polling `GET /v1/jobs/{id}`
  every 2s using the stored job_id — demonstrating the recovery path exists.
  If it drops before the first `job` event delivers a job_id, resubmit the
  query instead.
- No preset query buttons: demo queries are pasted from the `make demo`
  cheat sheet (specs/demo-script.md remains the source of the queries).
- No client-side interpretation of results beyond rendering; honesty about
  failures comes from synthesis, not UI smoothing. Answers (and assistant
  transcript turns) render as markdown, sanitized before DOM insertion —
  answer text derives from untrusted WDP results.

## Non-goals

Auth flows, mobile, multiple concurrent jobs, server-side history (the
conversation lives in the page and is resent each turn), charting
libraries or real data on the decorative dashboard tabs.
