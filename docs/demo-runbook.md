# Demo recording runbook — split-screen (chat UI + backend view)

Layout: browser with the chat UI on the LEFT half of the screen; a terminal
on the RIGHT half, split into two stacked panes running the backend narrator
(`scripts/demo_tap.py`). Everything the narrator shows is real: docker logs,
the MCP `tools/list` method, and the public `GET /v1/jobs/{id}` endpoint.

## One-time setup

1. Docker Desktop running; repo cloned; `.env` present with a valid
   `LLM_API_KEY` / `LLM_MODEL` (copy `.env.example` and fill in).
2. Bring everything up and bake the demo artifacts:

       make up
       make seed
       make tokens
       make anchors

3. Print the cheat sheet (queries with anchors substituted + JWTs):

       make demo

   Re-run `make anchors && make demo` after any reseed — the picked
   proposal/person can change.

## Screen setup (before recording)

1. Browser (left half): http://localhost:5173 — normal window, ~50% width.
   The page opens on the Leadership tab (the FRRR establishing shot —
   one line of framing, then click the **AI** tab). Click
   "New conversation" so the transcript is empty.
2. Terminal (right half): dark theme, font 16–18pt, two stacked panes
   (iTerm2: Cmd-Shift-D to split horizontally; or `tmux` with
   `tmux new \; split-window -v`). Run from the repo root:

   - TOP pane — agent/job view (request → tools/list → plan → steps → answer):

         python3 scripts/demo_tap.py agent

   - BOTTOM pane — MCP audit stream (every tools/call, separate container):

         python3 scripts/demo_tap.py audit

   The top pane opens by printing each user's server-filtered tool list —
   leave that visible; it is the role-based-access establishing shot.
3. Recorder: QuickTime (File → New Screen Recording, full screen) or OBS.

## The recorded arc (queries from `make demo`, in order)

For each beat: set the User dropdown first (switching user clears the
conversation by design — never touch it before Q1b), then paste the query
from the `make demo` cheat sheet and Submit.

Between beats, pause ~2s after the answer lands so the collapse animation
and the DONE line both read on video.

1. **Q1 — FY2025 prohibited factors** (analyst-full)
   Left: agent block ticks, direct answer with per-component counts.
   Right-top: REQUEST → 7 tools → 1-step plan → DONE.
   Talk track: "single request in, stream out; the plan is validated before
   anything executes."
2. **Q1b — follow-up: compare FY2024** (same conversation!)
   Do NOT click New conversation. Left: conversational comparison answer.
   Right-top: turn 3 request; plan resolves "that" from history.
   Talk track: "stateless multi-turn — the client resends the conversation."
3. **Q2 — proposal personnel background** (analyst-full)
   Right-bottom: get_proposal → two search_wdp_person calls (one returns
   0 rows — the honest gap) → retrieve_wdp_documents (14 rows).
   Talk track: "cheap discovery, expensive retrieval; absence reported
   honestly."
4. **Q3A — same query, local-only user** (switch User to analyst-local)
   Right-top: "4 tools — WDP tools withheld"; plan contains only local
   tools. Left: scoped answer. Talk track: "the WDP tools are never even
   listed for this user — filtering is server-side in the MCP container."
5. **Q3B — denied person, full user** (switch User back to analyst-full)
   Right-bottom: search_wdp_person → not_authorized in red, no repair.
   Left: denial reported in the answer. Talk track: "that denial came from
   WDP's side, not ours — and both are audited."
6. **Optional failure drill** — paste Q2 again, Submit, then immediately
   in a third terminal:

       docker compose stop fake-wdp

   Right side shows upstream_unavailable + a repair attempt; the answer
   still arrives and reports the gap; `done` lands. Afterwards:

       docker compose start fake-wdp

## Between takes

- Click "New conversation" in the UI.
- The tap scripts are stateless — leave them running (Ctrl-C to stop).
- If the top pane says `tokens.json not found`, run `make tokens`.
- If nothing appears on a request, confirm the taps were started from the
  repo root and `docker compose ps` shows all services healthy.
