"""The planner/executor orchestrator (plan-format spec).

Plan-then-execute: one planning LLM call returns a JSON plan; pure code
validates it (one planning repair), executes it in dependency order over
the MCP session (one runtime repair per failed step; not_authorized is
non-repairable), then one synthesis LLM call produces the answer. Step
failures are never terminal — synthesis always runs; only AgentError
subclasses end the job as failed (the host owns the wall clock and
unexpected exceptions).
"""
import json
import logging
from dataclasses import dataclass, field

from agent.config import AgentConfig
from agent.context import ContextProvider
from agent.errors import AgentError, MCPToolError, MCPTransportError, PlanInvalidError
from agent.interface import EventSink, Outcome
from agent.llm import LLMClient
from agent.mcp import MCPSession, MCPSessionFactory
from agent.prompt import (
    Budgets,
    StepReport,
    assemble_plan_prompt,
    assemble_plan_repair_suffix,
    assemble_step_repair_prompt,
    assemble_synthesis_prompt,
)
from agent.refs import FanPlan, RefFanoutError, StepResult, plan_step_calls
from agent.validator import ToolIndex, validate_plan, validate_step
from shared.types import UserContext

logger = logging.getLogger("agent.runner")

# Defensive per-step cap on what gets persisted via sink.step(result=...);
# MCP envelopes are already row/text-capped server-side, so this only guards
# against pathological payloads. Internal constant, not an env knob.
_RESULT_MAX_BYTES = 100_000

_EMPTY_ENVELOPE = {"data": [], "meta": {"total": 0, "returned": 0, "truncated": False}}


@dataclass
class _StepOutcome:
    report: StepReport
    failed: bool


@dataclass
class Agent:
    llm: LLMClient
    mcp: MCPSessionFactory
    context: ContextProvider
    config: AgentConfig
    _plan_schema: dict | None = field(default=None, repr=False)

    def _schema(self) -> dict:
        if self._plan_schema is None:
            path = self.config.contracts_dir / "plan-format.json"
            self._plan_schema = json.loads(path.read_text())
        return self._plan_schema

    def _budgets(self) -> Budgets:
        return Budgets(
            plan_max_steps=self.config.plan_max_steps,
            plan_max_fanout=self.config.plan_max_fanout,
        )

    async def run_query(self, query: str, user: UserContext, sink: EventSink) -> Outcome:
        try:
            return await self._run(query, user, sink)
        except AgentError as exc:
            await sink.error(exc.code, str(exc))
            return "failed"

    async def _run(self, query: str, user: UserContext, sink: EventSink) -> Outcome:
        inputs = await self.context.load(query)
        async with self.mcp.session(user) as session:
            tools = await session.tools_list()
            tool_index = ToolIndex.from_tools(tools)

            plan = await self._plan_with_repair(query, inputs, tools, tool_index)
            self._current_intent = plan["intent"]
            await sink.plan(plan)
            await sink.status("executing")

            reports = await self._execute(plan, session, tools, tool_index, inputs, sink)

            await sink.status("synthesizing")
            synthesis_prompt = assemble_synthesis_prompt(
                intent=plan["intent"],
                query=query,
                step_reports=reports,
                caveats=inputs.planner_context.get("caveats", []),
                tool_names=[t["name"] for t in tools],
            )
            answer = await self.llm.complete(synthesis_prompt)
            await sink.answer(answer)
            return "complete"

    # --- planning ----------------------------------------------------------

    async def _plan_with_repair(self, query, inputs, tools, tool_index) -> dict:
        prompt = assemble_plan_prompt(
            query=query,
            planner_context=inputs.planner_context,
            catalog_matches=inputs.catalog_matches,
            tools=tools,
            plan_schema=self._schema(),
            budgets=self._budgets(),
        )
        plan, violations = await self._plan_once(prompt, tool_index, inputs)
        if not violations:
            return plan
        logger.info(
            "plan invalid; attempting the one planning repair",
            extra={"ctx": {"violations": violations}},
        )
        repair_prompt = prompt + assemble_plan_repair_suffix(violations)
        plan, violations = await self._plan_once(repair_prompt, tool_index, inputs)
        if violations:
            raise PlanInvalidError(violations)
        return plan

    async def _plan_once(self, prompt, tool_index, inputs):
        raw = await self.llm.complete(prompt, json_mode=True)
        plan, parse_error = _parse_json(raw)
        if parse_error:
            return None, [parse_error]
        violations = validate_plan(
            plan,
            schema=self._schema(),
            tools=tool_index,
            observed_enums=inputs.observed_enums,
            max_steps=self.config.plan_max_steps,
        )
        return plan, violations

    # --- execution ---------------------------------------------------------

    async def _execute(self, plan, session, tools, tool_index, inputs, sink) -> list:
        steps = {step["id"]: step for step in plan["steps"]}
        results: dict = {}
        failed_ids: set = set()
        reports: dict = {}

        for step in _topological_order(plan["steps"]):
            step_id = step["id"]
            failed_deps = [d for d in step["depends_on"] if d in failed_ids]
            if failed_deps:
                error = f"not run: dependency step {failed_deps[0]} failed"
                await sink.step(
                    step_id, "failed", tool=step["tool"], args=step["args"],
                    error=error, summary=error,
                )
                failed_ids.add(step_id)
                reports[step_id] = _failed_report(step, error)
                continue
            outcome = await self._execute_step(
                step, session, tools, tool_index, inputs, results, sink,
                already_completed=set(results),
            )
            reports[step_id] = outcome.report
            if outcome.failed:
                failed_ids.add(step_id)

        return [reports[step["id"]] for step in plan["steps"]]

    async def _execute_step(
        self, step, session, tools, tool_index, inputs, results, sink,
        *, already_completed,
    ) -> _StepOutcome:
        step_id = step["id"]
        await sink.step(step_id, "running", tool=step["tool"], args=step["args"])

        try:
            fan = plan_step_calls(
                step["args"], results, max_fanout=self.config.plan_max_fanout
            )
        except RefFanoutError as exc:
            return await self._fail_step(step, str(exc), sink)

        attempt = await self._attempt_calls(step, fan, session, sink, results)
        if attempt is not None:
            return attempt

        # The attempt failed with a repairable error recorded on the step.
        error = self._last_error
        if error.code == "not_authorized":
            # Non-repairable by design: the denial is the result.
            return await self._fail_step(step, f"not_authorized: {error.message}", sink)

        # tool/args included so the DB sink's partial upsert never proposes a
        # NULL tool (job_steps.tool is NOT NULL; checked before ON CONFLICT).
        await sink.step(step_id, "repairing", tool=step["tool"], args=step["args"])
        repaired = await self._repair_step(step, error, tools, tool_index, inputs,
                                           already_completed)
        if repaired is None:
            return await self._fail_step(
                step, f"{error.code}: {error.message} (repair produced no valid step)",
                sink,
            )
        try:
            fan = plan_step_calls(
                repaired["args"], results, max_fanout=self.config.plan_max_fanout
            )
        except RefFanoutError as exc:
            return await self._fail_step(repaired, str(exc), sink)
        attempt = await self._attempt_calls(repaired, fan, session, sink, results)
        if attempt is not None:
            return attempt
        error = self._last_error
        return await self._fail_step(
            repaired, f"{error.code}: {error.message} (after one repair)", sink
        )

    async def _attempt_calls(self, step, fan: FanPlan, session, sink, results):
        """Run the step's tool calls. Returns a _StepOutcome on completion
        (including fan-out partial failure), or None when the step failed
        with a whole-step error stored in self._last_error."""
        step_id = step["id"]
        tool = step["tool"]

        if fan.empty:
            results[step_id] = StepResult(value=_EMPTY_ENVELOPE, fanned=fan.fanned)
            summary = "reference resolved to no values; 0 tool calls made"
            await sink.step(
                step_id, "complete", tool=tool, args=step["args"],
                result=_EMPTY_ENVELOPE, summary=summary, rows=0, truncated=False,
            )
            return _StepOutcome(
                report=_report(step, "complete", summary, 0, False, _EMPTY_ENVELOPE),
                failed=False,
            )

        if not fan.fanned:
            try:
                envelope = await session.tools_call(tool, fan.calls[0])
            except MCPToolError as exc:
                self._last_error = exc
                return None
            except MCPTransportError as exc:
                self._last_error = MCPToolError("upstream_unavailable", str(exc))
                return None
            stored, summary_notes = _cap_result(envelope)
            results[step_id] = StepResult(value=stored, fanned=False)
            meta = envelope.get("meta", {}) if isinstance(envelope, dict) else {}
            rows = meta.get("returned", 0)
            truncated = bool(meta.get("truncated", False))
            summary = _join_notes(f"{rows} rows", summary_notes)
            await sink.step(
                step_id, "complete", tool=tool, args=fan.calls[0],
                result=stored, summary=summary, rows=rows, truncated=truncated,
            )
            return _StepOutcome(
                report=_report(step, "complete", summary, rows, truncated, stored),
                failed=False,
            )

        # Fan-out: one call per value; per-call errors become slots, and the
        # step completes when at least one call succeeds.
        slots: list = []
        errors: list = []
        rows = 0
        any_truncated = fan.truncated_count > 0
        for call_args in fan.calls:
            try:
                envelope = await session.tools_call(tool, call_args)
            except MCPToolError as exc:
                slots.append({"error": {"code": exc.code, "message": exc.message}})
                errors.append(exc)
                continue
            except MCPTransportError as exc:
                wrapped = MCPToolError("upstream_unavailable", str(exc))
                slots.append({"error": {"code": wrapped.code, "message": wrapped.message}})
                errors.append(wrapped)
                continue
            slots.append(envelope)
            meta = envelope.get("meta", {}) if isinstance(envelope, dict) else {}
            rows += meta.get("returned", 0)
            any_truncated = any_truncated or bool(meta.get("truncated", False))

        if errors and len(errors) == len(fan.calls):
            # Every call failed: treat like a single-step failure. A denial
            # anywhere makes the step non-repairable.
            denial = next((e for e in errors if e.code == "not_authorized"), None)
            self._last_error = denial or errors[0]
            return None

        stored, cap_notes = _cap_result(slots)
        results[step_id] = StepResult(value=stored, fanned=True)
        notes = []
        if fan.truncated_count:
            notes.append(
                f"fan-out truncated to {len(fan.calls)} calls "
                f"({fan.truncated_count} values dropped at PLAN_MAX_FANOUT)"
            )
        if errors:
            codes = ", ".join(sorted({e.code for e in errors}))
            notes.append(f"{len(errors)} of {len(fan.calls)} calls failed ({codes})")
        summary = _join_notes(
            f"{len(fan.calls)} calls, {rows} rows", cap_notes, *notes
        )
        await sink.step(
            step_id, "complete", tool=tool, args=step["args"],
            result=stored, summary=summary, rows=rows, truncated=any_truncated,
        )
        return _StepOutcome(
            report=_report(step, "complete", summary, rows, any_truncated, stored),
            failed=False,
        )

    async def _repair_step(self, step, error: MCPToolError, tools, tool_index,
                           inputs, already_completed):
        """The one runtime repair: LLM sees only the failed step, its error,
        and the plan intent (plus the session tool list so the broader-tool
        fallback heuristic is actionable). Returns a validated revised step
        with the same id, or None."""
        prompt = assemble_step_repair_prompt(
            intent=self._current_intent,
            failed_step=step,
            error_code=error.code,
            error_message=error.message,
            tools=tools,
            budgets=self._budgets(),
        )
        raw = await self.llm.complete(prompt, json_mode=True)
        revised, parse_error = _parse_json(raw)
        if parse_error:
            logger.info("step repair returned unparseable JSON",
                        extra={"ctx": {"step_id": step["id"]}})
            return None
        if not isinstance(revised, dict) or revised.get("id") != step["id"]:
            logger.info("step repair changed or dropped the step id",
                        extra={"ctx": {"step_id": step["id"]}})
            return None
        violations = validate_step(
            revised,
            schema=self._schema(),
            tools=tool_index,
            observed_enums=inputs.observed_enums,
            valid_dep_ids=already_completed,
        )
        if violations:
            logger.info("step repair failed validation",
                        extra={"ctx": {"step_id": step["id"], "violations": violations}})
            return None
        return revised

    async def _fail_step(self, step, error_text: str, sink) -> _StepOutcome:
        await sink.step(
            step["id"], "failed", tool=step["tool"], args=step["args"],
            error=error_text, summary=error_text,
        )
        return _StepOutcome(report=_failed_report(step, error_text), failed=True)

    # Set by _run before execution; used by the repair prompt.
    _current_intent: str = ""
    _last_error: MCPToolError | None = None


def _topological_order(steps: list) -> list:
    """Dependency order, stable by id among ready steps. The validator
    guarantees acyclicity; ids are unique."""
    by_id = {step["id"]: step for step in steps}
    done: set = set()
    ordered: list = []
    while len(ordered) < len(steps):
        ready = sorted(
            sid for sid, step in by_id.items()
            if sid not in done and all(d in done or d not in by_id
                                       for d in step["depends_on"])
        )
        for sid in ready:
            ordered.append(by_id[sid])
            done.add(sid)
    return ordered


def _parse_json(raw: str):
    """Parse the LLM's JSON output, tolerating markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"response was not valid JSON: {exc}"


def _cap_result(value):
    """Cap what gets persisted for a step result."""
    encoded = json.dumps(value, default=str)
    if len(encoded) <= _RESULT_MAX_BYTES:
        return value, None
    capped = {
        "data": None,
        "meta": {"stored_truncated": True, "original_bytes": len(encoded)},
    }
    return capped, f"stored result truncated ({len(encoded)} bytes > cap)"


def _join_notes(*notes) -> str:
    return "; ".join(n for n in notes if n)


def _report(step, status, summary, rows, truncated, stored) -> StepReport:
    result_json = json.dumps(stored, default=str)
    if len(result_json) > _RESULT_MAX_BYTES:
        result_json = result_json[:_RESULT_MAX_BYTES]
    return StepReport(
        step_id=step["id"], tool=step["tool"], status=status, summary=summary,
        rows=rows, truncated=truncated, result_json=result_json, error=None,
    )


def _failed_report(step, error_text: str) -> StepReport:
    return StepReport(
        step_id=step["id"], tool=step["tool"], status="failed", summary=None,
        rows=None, truncated=None, result_json=None, error=error_text,
    )
