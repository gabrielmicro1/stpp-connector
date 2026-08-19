"""Prompt assembly: pure functions over data already loaded elsewhere, so
every prompt is snapshot-testable. Templates live in agent/prompts/*.md
with {{name}} slots (plain string replacement — no format()/Template, so
JSON braces and $steps references never need escaping).

Assembly order for the planning prompt follows the plan-format spec: (1)
role and task framing, (2) planner context, (3) catalog matches, (4) the
session's tools rendered as text, (5) plan schema + reference rules +
budgets, then the user query delimited as untrusted data.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Budgets:
    plan_max_steps: int
    plan_max_fanout: int


@dataclass(frozen=True)
class StepReport:
    """What synthesis gets to see about one executed step."""

    step_id: int
    tool: str
    status: str
    summary: str | None
    rows: int | None
    truncated: bool | None
    result_json: str | None
    error: str | None


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text()


def _fill(template: str, slots: dict) -> str:
    for key, value in slots.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def render_planner_context(planner_context: dict) -> str:
    lines: list = []
    fields = planner_context.get("fields", {})
    lines.append("Field dictionary (with values OBSERVED in the current data):")
    for name in sorted(fields):
        field = fields[name]
        meta_bits = ", ".join(
            bit for bit in (field.get("data_type"), field.get("requirement")) if bit
        )
        suffix = f" ({meta_bits})" if meta_bits else ""
        lines.append(f"- {name}{suffix}: {field.get('description') or '(no description)'}")
        observed = field.get("observed_values")
        if observed:
            rendered = ", ".join(
                f'"{v["value"]}" ({v["row_count"]} rows'
                + ("" if v.get("in_dictionary") else ", undocumented")
                + ")"
                for v in observed
            )
            lines.append(f"  observed values: {rendered}")
    caveats = planner_context.get("caveats", [])
    if caveats:
        lines.append("")
        lines.append("Data-quality caveats:")
        lines.extend(f"- {c}" for c in caveats)
    return "\n".join(lines)


def render_catalog(matches: list) -> str:
    if not matches:
        return "(no catalog entries matched this query)"
    lines: list = []
    for m in matches:
        if m.get("type") == "proposal":
            lines.append(
                f"- proposal {m['proposal_number']}: {m.get('proposal_title')!r}, "
                f"submitted by {m.get('submitting_entity_name')}, "
                f"fiscal year {m.get('fiscal_year')}"
            )
        elif m.get("type") == "person":
            proposals = ", ".join(m.get("proposal_numbers", []))
            lines.append(
                f"- person {m.get('name')} (ORCID {m['person_orcid']}), "
                f"on proposals: {proposals}"
            )
        else:
            lines.append(f"- {json.dumps(m, sort_keys=True)}")
    return "\n".join(lines)


def render_tools(tools: list) -> str:
    blocks: list = []
    for tool in tools:
        blocks.append(
            f"## {tool['name']}\n\n"
            f"{tool.get('description', '').strip()}\n\n"
            "Input schema:\n"
            f"{json.dumps(tool.get('inputSchema', {}), indent=2, sort_keys=True)}\n\n"
            "Result schema (what $steps references resolve against):\n"
            f"{json.dumps(tool.get('outputSchema', {}), indent=2, sort_keys=True)}"
        )
    return "\n\n".join(blocks)


def render_conversation_block(history: list, replay_chars: int = 2000) -> str:
    """Prior turns, oldest first, each delimited as untrusted data. Assistant
    turns are replayed synthesis output (WDP-derived, untrusted twice over)
    and are truncated to replay_chars — the analyst-visible summary is what
    matters for reference resolution, not full result tables."""
    if not history:
        return "(none — first turn)"
    blocks: list = []
    for turn in history:
        content = turn["content"]
        if turn["role"] == "assistant" and len(content) > replay_chars:
            content = content[:replay_chars] + "\n… [truncated]"
        blocks.append(f"<<<TURN role={turn['role']}\n{content}\nTURN>>>")
    return "\n\n".join(blocks)


def assemble_plan_prompt(
    *,
    query: str,
    planner_context: dict,
    catalog_matches: list,
    tools: list,
    plan_schema: dict,
    budgets: Budgets,
    history: list = (),
    replay_chars: int = 2000,
) -> str:
    return _fill(
        _template("plan.md"),
        {
            "conversation_block": render_conversation_block(history, replay_chars),
            "planner_context_block": render_planner_context(planner_context),
            "catalog_block": render_catalog(catalog_matches),
            "tools_block": render_tools(tools),
            "plan_schema": json.dumps(plan_schema, indent=2, sort_keys=True),
            "plan_max_steps": budgets.plan_max_steps,
            "plan_max_fanout": budgets.plan_max_fanout,
            "query": query,
        },
    )


def assemble_plan_repair_suffix(violations: list) -> str:
    lines = "\n".join(f"- {v}" for v in violations)
    return (
        "\n\n# Validation violations\n\n"
        "Your previous plan failed validation with these violations:\n"
        f"{lines}\n\n"
        "Return a corrected plan as a JSON document only."
    )


def assemble_step_repair_prompt(
    *,
    intent: str,
    failed_step: dict,
    error_code: str,
    error_message: str,
    tools: list,
    budgets: Budgets,
) -> str:
    return _fill(
        _template("repair_step.md"),
        {
            "intent": intent,
            "failed_step": json.dumps(failed_step, indent=2, sort_keys=True),
            "error_code": error_code,
            "error_message": error_message,
            "tools_block": render_tools(tools),
            "plan_max_fanout": budgets.plan_max_fanout,
        },
    )


def render_step_reports(step_reports: list) -> str:
    blocks: list = []
    for r in step_reports:
        lines = [f"## Step {r.step_id} — {r.tool} [{r.status}]"]
        if r.summary:
            lines.append(f"summary: {r.summary}")
        if r.rows is not None:
            lines.append(f"rows: {r.rows} (truncated: {str(bool(r.truncated)).lower()})")
        if r.error:
            lines.append(f"error: {r.error}")
        lines.append(f"result: {r.result_json if r.result_json else '(none)'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def assemble_synthesis_prompt(
    *,
    intent: str,
    query: str,
    step_reports: list,
    caveats: list,
    tool_names: list,
    history: list = (),
    replay_chars: int = 2000,
) -> str:
    caveats_block = "\n".join(f"- {c}" for c in caveats) if caveats else "- (none)"
    tools_block = "\n".join(f"- {n}" for n in tool_names) if tool_names else "- (none)"
    return _fill(
        _template("synthesize.md"),
        {
            "conversation_block": render_conversation_block(history, replay_chars),
            "intent": intent,
            "query": query,
            "step_reports_block": render_step_reports(step_reports),
            "caveats_block": caveats_block,
            "tools_block": tools_block,
        },
    )
