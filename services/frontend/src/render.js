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

const GLYPHS = {
  pending: "○",   // ○
  running: "⟳",   // ⟳ (spun by CSS)
  repairing: "⟲", // ⟲
  complete: "✓",  // ✓
  failed: "✗",    // ✗
};

export function resetPanels() {
  el("intent").textContent = "";
  el("plan").replaceChildren();
  el("answer").textContent = "";
  el("log").textContent = "";
  el("seq-gap").style.display = "none";
  clearError();
}

export function setMode(text, cls = "") {
  el("mode").textContent = text;
  el("mode").className = cls;
}

export function renderPlan(plan) {
  el("intent").textContent = plan.intent || "";
  el("plan").replaceChildren();
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
  let li = el("plan").querySelector(`li[data-step-id="${stepId}"]`);
  if (!li) {
    li = document.createElement("li");
    li.dataset.stepId = stepId;
    li.innerHTML =
      '<span class="glyph"></span><span class="tool"></span>' +
      '<span class="state-label"></span><span class="reason"></span>' +
      '<span class="args"></span><span class="meta"></span>' +
      '<div class="summary"></div><div class="step-error"></div>';
    el("plan").appendChild(li);
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
}

// Conversation transcript (multi-turn): prior turns stay on screen; the
// current-run panels below still reset per submit via resetPanels().
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
}

export function clearTranscript() {
  el("transcript").replaceChildren();
}

export function renderAnswer(text) {
  setMarkdown(el("answer"), text);
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
  if (job.answer != null) renderAnswer(job.answer);
  const err = job.error?.error;
  if (err) renderError(err.code, err.message);
}

export function logEvent(name, raw) {
  logLine(`${name.padEnd(7)} ${raw}`);
}

export function logNote(text) {
  logLine(`[client ${new Date().toLocaleTimeString()}] ${text}`);
}

export function logPoll(job) {
  logLine(`[poll ${new Date().toLocaleTimeString()}] ${JSON.stringify(job)}`);
}

export function showSeqGap() {
  el("seq-gap").style.display = "inline";
}

function logLine(line) {
  const pre = el("log");
  pre.textContent += line + "\n";
  pre.scrollTop = pre.scrollHeight;
}
