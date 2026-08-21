// Mock STPP frontend (invariant 11): consumes ONLY the public Integration API
// contract — POST /v1/query (SSE response) and GET /v1/jobs/{id} for the
// polling recovery path. Tokens come from make-generated public/tokens.json.
import { parseSSEStream } from "./sse.js";
import { submitQuery, getJob } from "./api.js";
import * as ui from "./render.js";
import { initTabs } from "./tabs.js";
import "./styles.css";

const USERS = {
  "analyst-full": "Avery Fullaccess (rfff_reader + wdp_reader)",
  "analyst-local": "Logan Localonly (rfff_reader)",
};


const POLL_INTERVAL_MS = 2000;
const MAX_RESUBMITS = 3;

let tokens = null;
let run = null; // current run context; replaced wholesale on each submit
// The conversation (stateless server: we resend it in full each turn).
// Survives across runs; cleared by New conversation / user switch.
let messages = [];

function newConversation() {
  messages = [];
  ui.clearTranscript();
}

function newRun(msgs, token) {
  return {
    messages: msgs,
    token,
    jobId: null,
    lastSeq: null,
    sawDone: false,
    done: false,
    resubmitsLeft: MAX_RESUBMITS,
    abort: new AbortController(),
    pollTimer: null,
    answerText: null,
  };
}

function handleEvent({ event, data, raw }) {
  console.debug("sse", event, raw);
  if (!data) return; // parse failure already visible in the console
  const ctx = run;
  if (ctx) {
    if (typeof data.seq === "number") {
      if (ctx.lastSeq !== null && data.seq > ctx.lastSeq + 1) {
        console.warn(`SSE seq gap: ${ctx.lastSeq} -> ${data.seq}`);
      }
      ctx.lastSeq = data.seq;
    }
    if (data.job_id && !ctx.jobId) ctx.jobId = data.job_id;
  }
  switch (event) {
    case "job":
      ui.setMode("streaming — planning");
      break;
    case "plan":
      ui.renderPlan(data.plan);
      ui.setMode("streaming — executing");
      break;
    case "step":
      ui.upsertStepRow(data.step_id, {
        status: data.status,
        summary: data.summary,
        rows: data.rows,
        truncated: data.truncated,
      });
      break;
    case "answer":
      if (ctx) ctx.answerText = data.text; // appended as the assistant turn on done
      break;
    case "error":
      ui.renderError(data.code, data.message);
      break;
    case "done":
      if (ctx) {
        ctx.sawDone = true;
        finalize(ctx, data.status);
      }
      break;
    case "ping":
      break; // heartbeat; nothing to render
  }
}

function finalize(ctx, status) {
  ctx.done = true;
  ui.setMode(status, status === "complete" ? "complete" : "failed");
  ui.endRun(status);
  // A completed turn joins the conversation; a failed one leaves the user
  // turn unanswered in history (valid — the contract only requires the
  // final message to be role user).
  if (status === "complete" && ctx.answerText != null) {
    messages.push({ role: "assistant", content: ctx.answerText });
    ui.appendTurn("assistant", ctx.answerText);
  }
}

async function startStream(ctx) {
  ui.setMode("streaming");
  let res;
  try {
    res = await submitQuery(ctx.token, ctx.messages, ctx.abort.signal);
  } catch (err) {
    if (ctx !== run) return;
    console.debug(`POST /v1/query failed: ${err.message}`);
    return onStreamEnd(ctx);
  }
  if (ctx !== run) return;
  const contentType = res.headers.get("content-type") || "";
  if (!res.ok || !contentType.includes("text/event-stream")) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      const err = body.error || body;
      ui.renderError(err.code ?? "request_failed", err.message ?? JSON.stringify(err));
      detail += ` ${JSON.stringify(body)}`;
    } catch {
      ui.renderError("request_failed", detail);
    }
    console.debug(`submit rejected: ${detail}`);
    finalize(ctx, "failed");
    return;
  }
  try {
    await parseSSEStream(res.body, (frame) => {
      if (ctx === run) handleEvent(frame);
    });
  } catch (err) {
    console.debug(`stream error: ${err.message}`);
  }
  onStreamEnd(ctx);
}

// Stream ended (cleanly or not) without a `done` event => it dropped.
function onStreamEnd(ctx) {
  if (ctx !== run || ctx.done) return;
  if (ctx.jobId) {
    startPolling(ctx.jobId);
  } else if (ctx.resubmitsLeft > 0) {
    ctx.resubmitsLeft -= 1;
    console.debug(
      `stream dropped before job_id arrived; resubmitting query (${ctx.resubmitsLeft} retries left)`,
    );
    ctx.abort = new AbortController();
    startStream(ctx);
  } else {
    ui.renderError(
      "stream_failed",
      `stream dropped before a job_id arrived; gave up after ${MAX_RESUBMITS} resubmits`,
    );
    finalize(ctx, "failed");
  }
}

function startPolling(jobId) {
  const ctx = run;
  if (!ctx || ctx.done) return;
  ui.setMode("reconnecting (polling every 2s)", "warn");
  const schedule = () => {
    ctx.pollTimer = setTimeout(tick, POLL_INTERVAL_MS);
  };
  const tick = async () => {
    if (ctx !== run || ctx.done) return;
    let snap;
    try {
      snap = await getJob(ctx.token, jobId);
    } catch (err) {
      console.debug(`poll failed: ${err.message}; retrying`);
      return schedule();
    }
    if (ctx !== run || ctx.done) return;
    if (snap.status === 404) {
      const err = snap.body.error || {};
      ui.renderError(err.code ?? "job_not_found", err.message ?? "job not found");
      return finalize(ctx, "failed");
    }
    if (snap.status !== 200) {
      console.debug(`poll HTTP ${snap.status}: ${JSON.stringify(snap.body)}; retrying`);
      return schedule();
    }
    console.debug("poll", snap.body);
    ui.renderJobSnapshot(snap.body);
    if (snap.body.status === "complete" || snap.body.status === "failed") {
      if (snap.body.answer != null) ctx.answerText = snap.body.answer;
      return finalize(ctx, snap.body.status);
    }
    ui.setMode(`reconnecting (polling every 2s) — ${snap.body.status}`, "warn");
    schedule();
  };
  tick();
}

function submit() {
  const query = document.getElementById("query").value.trim();
  if (!query || !tokens) return;
  const user = document.getElementById("user").value;
  const token = tokens[user];
  if (!token) {
    ui.renderError("no_token", `no token for user ${user}; run make tokens`);
    return;
  }
  if (run) {
    run.done = true; // stops any poll loop from continuing
    run.abort.abort();
    clearTimeout(run.pollTimer);
  }
  messages.push({ role: "user", content: query });
  ui.appendTurn("user", query);
  run = newRun([...messages], token);
  ui.resetPanels();
  ui.beginRun();
  document.getElementById("query").value = "";
  console.debug(`submitting as ${user} (turn ${messages.length}): ${query}`);
  startStream(run);
}

async function boot() {
  initTabs("leadership");
  const userSel = document.getElementById("user");
  for (const [id, label] of Object.entries(USERS)) {
    userSel.add(new Option(`${id} — ${label}`, id));
  }
  // Cross-user history replay is incoherent (analyst-local must not carry
  // analyst-full's WDP answers), so switching user starts a fresh
  // conversation.
  userSel.addEventListener("change", newConversation);
  document.getElementById("submit").addEventListener("click", submit);
  document.getElementById("new-convo").addEventListener("click", newConversation);

  try {
    const res = await fetch("/tokens.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    tokens = await res.json();
  } catch {
    document.getElementById("token-banner").style.display = "block";
    document.getElementById("submit").disabled = true;
    return;
  }
}

boot();

// Frontend-only debug/verification handle (not an API backdoor): lets the
// phase verify step inject synthetic events and force the polling fallback.
window.__stpp = {
  handleEvent,
  startPolling,
  abortStream: () => run?.abort.abort(),
  get state() {
    return run;
  },
  get messages() {
    return messages;
  },
};
