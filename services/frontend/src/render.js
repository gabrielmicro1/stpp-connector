// All DOM writes live here. Plan rows are upserted by step id so the SSE
// stream and the polling fallback share one rendering path.
import DOMPurify from "dompurify";
import { marked } from "marked";

const el = (id) => document.getElementById(id);

// Answers are LLM output synthesized from WDP results — untrusted input per
// the trust model — so markdown is rendered then sanitized before it touches
// the DOM. Never innerHTML answer text directly.
function setMarkdown(node, text) {
  node.innerHTML = DOMPurify.sanitize(marked.parse(text ?? "", { async: false }));
}

// Keep the single chat scroll pinned to the newest content — but only when
// the user is already near the bottom, so reading back isn't fought.
function scrollChat(force = false) {
  const c = el("chat");
  const nearBottom = c.scrollHeight - c.scrollTop - c.clientHeight < 160;
  if (force || nearBottom) c.scrollTop = c.scrollHeight;
}

const GLYPHS = {
  pending: "○",   // ○
  running: "⟳",   // ⟳ (spun by CSS)
  repairing: "⟲", // ⟲
  complete: "✓",  // ✓
  failed: "✗",    // ✗
};

// The current run's agent-activity block in the transcript (Claude-Code
// style: plan + live step ticks render inline in the chat, before the
// assistant answer). One block per submitted query; old blocks stay.
let runBlock = null;

export function beginRun() {
  const li = document.createElement("li");
  li.className = "agent";
  // Open while the run streams; endRun() collapses it once the answer
  // lands (native <details>, so it stays click-expandable afterwards).
  li.innerHTML =
    '<details class="agent-details" open>' +
    '<summary><span class="role">agent</span>' +
    '<span class="agent-summary">planning…</span></summary>' +
    '<p class="intent"></p><ul class="plan"></ul>' +
    "</details>";
  el("transcript").appendChild(li);
  runBlock = li;
  scrollChat(true);
}

function ensureRunBlock() {
  if (!runBlock || !runBlock.isConnected) beginRun();
  return runBlock;
}

// Clear the "planning…" placeholder once the run ends (a job that dies
// before its plan event would otherwise say planning… forever).
export function endRun(status) {
  if (!runBlock) return;
  const s = runBlock.querySelector(".agent-summary");
  const steps = [...runBlock.querySelectorAll(".plan > li")];
  const failed = steps.filter((li) => li.classList.contains("failed")).length;
  let label = s.textContent === "planning…"
    ? (status === "complete" ? "" : "planning failed")
    : s.textContent;
  if (steps.length) {
    const n = `${steps.length} step${steps.length === 1 ? "" : "s"}`;
    const tally = failed ? `${n} · ${failed} failed` : `${n} ✓`;
    label = label ? `${label} — ${tally}` : tally;
  }
  s.textContent = label;
  runBlock.querySelector(".agent-details").open = false;
  scrollChat();
}

export function resetPanels() {
  clearError();
}

export function setMode(text, cls = "") {
  el("mode").textContent = text;
  el("mode").className = cls;
}

export function renderPlan(plan) {
  const block = ensureRunBlock();
  const intent = plan.intent || "";
  block.querySelector(".agent-summary").textContent =
    intent.length > 90 ? intent.slice(0, 90) + "…" : intent;
  block.querySelector(".intent").textContent = intent;
  block.querySelector(".plan").replaceChildren();
  for (const step of plan.steps || []) {
    upsertStepRow(step.id, {
      status: "pending",
      tool: step.tool,
      reason: step.reason,
      args: step.args,
    });
  }
}

export function upsertStepRow(stepId, fields) {
  const planEl = ensureRunBlock().querySelector(".plan");
  let li = planEl.querySelector(`li[data-step-id="${stepId}"]`);
  if (!li) {
    li = document.createElement("li");
    li.dataset.stepId = stepId;
    li.innerHTML =
      '<span class="glyph"></span><span class="tool"></span>' +
      '<span class="state-label"></span><span class="reason"></span>' +
      '<span class="args"></span><span class="meta"></span>' +
      '<div class="summary"></div><div class="step-error"></div>';
    planEl.appendChild(li);
  }
  const set = (sel, text) => { li.querySelector(sel).textContent = text; };
  if (fields.tool !== undefined) set(".tool", fields.tool);
  if (fields.reason !== undefined) set(".reason", fields.reason ? ` — ${fields.reason} ` : "");
  if (fields.args !== undefined) {
    const full = JSON.stringify(fields.args ?? {});
    set(".args", full.length > 80 ? full.slice(0, 80) + "…" : full);
    li.querySelector(".args").title = full;
  }
  if (fields.status !== undefined) {
    li.className = fields.status;
    set(".glyph", GLYPHS[fields.status] ?? "?");
    set(".state-label", fields.status === "repairing" ? "repairing…" : "");
  }
  if (fields.rows !== undefined || fields.truncated !== undefined) {
    const rows = fields.rows !== undefined ? `${fields.rows} rows` : "";
    set(".meta", `— ${rows}${fields.truncated ? " (truncated)" : ""}`);
  }
  if (fields.summary) set(".summary", fields.summary);
  if (fields.error) set(".step-error", fields.error);
  scrollChat();
}

// Conversation transcript (multi-turn): prior turns stay on screen; the
// error panel still resets per submit via resetPanels().
export function appendTurn(role, text) {
  const li = document.createElement("li");
  li.className = role;
  const roleSpan = document.createElement("span");
  roleSpan.className = "role";
  roleSpan.textContent = role;
  const content = document.createElement("span");
  content.className = role === "assistant" ? "content md" : "content";
  if (role === "assistant") {
    setMarkdown(content, text);
  } else {
    content.textContent = text; // user turns stay literal
  }
  li.append(roleSpan, content);
  el("transcript").appendChild(li);
  scrollChat(true); // a new turn always comes into view
}

export function clearTranscript() {
  el("transcript").replaceChildren();
}

export function renderError(code, message) {
  el("error-code").textContent = code;
  el("error-message").textContent = message;
  el("error-panel").style.display = "block";
}

export function clearError() {
  el("error-panel").style.display = "none";
}

// Poll-mode full re-render from a GET /v1/jobs/{id} snapshot.
export function renderJobSnapshot(job) {
  if (job.plan) renderPlan(job.plan);
  const reasons = new Map((job.plan?.steps || []).map((s) => [s.id, s.reason]));
  for (const step of job.steps || []) {
    upsertStepRow(step.step_id, {
      status: step.status,
      tool: step.tool,
      reason: reasons.get(step.step_id),
      args: step.args,
      error: step.error ?? undefined,
    });
  }
  // job.answer reaches the transcript via finalize() (assistant turn).
  const err = job.error?.error;
  if (err) renderError(err.code, err.message);
}
