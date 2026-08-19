"""Plan validation: the five checks from docs/specs/plan-format.md, in pure
code, before anything executes. Returns machine-generated violation strings
(ALL of them — the planning-repair prompt gets the complete list); an empty
list means the plan is valid.
"""
from dataclasses import dataclass

import jsonschema

from agent.refs import RefSyntaxError, find_refs, has_star

_REF_PLACEHOLDER = "resolved-at-execution-time"
_ENUM_SOURCE_KEY = "x-enum-source"


@dataclass(frozen=True)
class ToolIndex:
    """The tool definitions visible to THIS session (per-user tools/list)."""

    defs: dict

    @classmethod
    def from_tools(cls, tools: list) -> "ToolIndex":
        return cls(defs={t["name"]: t for t in tools})

    def __contains__(self, name: str) -> bool:
        return name in self.defs

    def input_schema(self, name: str) -> dict:
        return self.defs[name].get("inputSchema", {"type": "object"})


def validate_plan(plan, *, schema, tools, observed_enums, max_steps) -> list:
    """All five checks. If the document fails the structural schema (check
    1) the remaining checks are skipped — they cannot be applied safely to
    an arbitrarily-shaped document."""
    violations = _schema_violations(plan, schema, context="plan")
    if violations:
        return violations

    steps = plan["steps"]

    # Check 5 first-class alongside the others; order of the returned list
    # follows the spec's checklist order.
    for step in steps:
        violations.extend(_tool_violations(step, tools))
    for step in steps:
        violations.extend(_args_violations(step, tools, observed_enums))
    violations.extend(_graph_violations(steps))
    if len(steps) > max_steps:
        violations.append(
            f"plan has {len(steps)} steps, exceeding the PLAN_MAX_STEPS budget of {max_steps}"
        )
    return violations


def validate_step(step, *, schema, tools, observed_enums, valid_dep_ids) -> list:
    """Validate a single (runtime-repaired) step with the same checks. The
    revised step may only depend on / reference steps that already exist
    (valid_dep_ids); keeping the same id is enforced by the caller."""
    step_schema = schema["$defs"]["step"]
    violations = _schema_violations(step, step_schema, context="step")
    if violations:
        return violations
    violations.extend(_tool_violations(step, tools))
    violations.extend(_args_violations(step, tools, observed_enums))
    for dep in step["depends_on"]:
        if dep not in valid_dep_ids:
            violations.append(
                f"step {step['id']}: depends_on references step {dep}, which does not exist"
            )
    try:
        for _, ref in find_refs(step["args"]):
            if ref.step_id not in valid_dep_ids:
                violations.append(
                    f"step {step['id']}: reference {ref.raw!r} targets step "
                    f"{ref.step_id}, which does not exist"
                )
            elif ref.step_id not in step["depends_on"]:
                violations.append(
                    f"step {step['id']}: reference {ref.raw!r} requires "
                    f"{ref.step_id} in depends_on"
                )
    except RefSyntaxError as exc:
        violations.append(f"step {step['id']}: malformed reference: {exc}")
    return violations


# --- check 1 -------------------------------------------------------------------

def _schema_violations(document, schema, *, context: str) -> list:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{context} schema: {error.json_path}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=str)
    ]


# --- check 2 -------------------------------------------------------------------

def _tool_violations(step, tools: ToolIndex) -> list:
    if step["tool"] not in tools:
        return [
            f"step {step['id']}: tool {step['tool']!r} is not available in "
            "this session's tools/list"
        ]
    return []


# --- check 3 -------------------------------------------------------------------

def _args_violations(step, tools: ToolIndex, observed_enums) -> list:
    if step["tool"] not in tools:
        return []  # already reported by check 2; nothing to validate against
    violations: list = []
    input_schema = tools.input_schema(step["tool"])

    try:
        refs = find_refs(step["args"])
    except RefSyntaxError as exc:
        return [f"step {step['id']}: malformed reference: {exc}"]

    # Structural validation with refs masked by a plain-string placeholder:
    # references are only supported in string-typed argument slots, so a ref
    # in (say) an integer slot fails here, repairably.
    masked = _mask_refs(step["args"], {ref.raw for _, ref in refs})
    validator = jsonschema.Draft202012Validator(input_schema)
    violations.extend(
        f"step {step['id']}: args{_json_path_suffix(error)}: {error.message}"
        for error in sorted(validator.iter_errors(masked), key=str)
    )

    # Observed-enum membership for literal values only (contracts stay
    # structural; the observed sets are seed-generated — invariant 10).
    ref_raws = {ref.raw for _, ref in refs}
    violations.extend(
        _enum_violations(step, input_schema, input_schema, step["args"], ref_raws, observed_enums)
    )
    return violations


def _json_path_suffix(error) -> str:
    path = error.json_path
    return path[1:] if path.startswith("$") else path


def _mask_refs(node, ref_raws: set):
    if isinstance(node, dict):
        return {k: _mask_refs(v, ref_raws) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_refs(v, ref_raws) for v in node]
    if isinstance(node, str) and node in ref_raws:
        return _REF_PLACEHOLDER
    return node


def _deref(schema_node, root):
    while isinstance(schema_node, dict) and "$ref" in schema_node:
        target = root
        for part in schema_node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        schema_node = target
    return schema_node


def _enum_violations(step, schema_node, root, value, ref_raws, observed_enums) -> list:
    schema_node = _deref(schema_node, root)
    if not isinstance(schema_node, dict):
        return []
    violations: list = []
    if isinstance(value, dict):
        properties = schema_node.get("properties", {})
        for key, sub_value in value.items():
            sub_schema = _deref(properties.get(key, {}), root)
            if (
                isinstance(sub_schema, dict)
                and sub_schema.get(_ENUM_SOURCE_KEY) == "observed_enums"
                and isinstance(sub_value, str)
                and sub_value not in ref_raws
            ):
                observed = observed_enums.get(key)
                if observed is not None and sub_value not in observed:
                    violations.append(
                        f"step {step['id']}: args: {key} value {sub_value!r} is not "
                        f"among the observed values"
                    )
            else:
                violations.extend(
                    _enum_violations(step, sub_schema, root, sub_value, ref_raws, observed_enums)
                )
    elif isinstance(value, list):
        items_schema = schema_node.get("items", {})
        for item in value:
            violations.extend(
                _enum_violations(step, items_schema, root, item, ref_raws, observed_enums)
            )
    return violations


# --- check 4 -------------------------------------------------------------------

def _graph_violations(steps) -> list:
    violations: list = []
    ids = [step["id"] for step in steps]
    id_set = set(ids)
    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        violations.append(f"step ids must be unique; duplicated: {dupes}")

    for step in steps:
        for dep in step["depends_on"]:
            if dep not in id_set:
                violations.append(
                    f"step {step['id']}: depends_on references step {dep}, "
                    "which does not exist"
                )
        try:
            refs = find_refs(step["args"])
        except RefSyntaxError as exc:
            violations.append(f"step {step['id']}: malformed reference: {exc}")
            continue
        star_raws = {ref.raw for _, ref in refs if has_star(ref)}
        if len(star_raws) > 1:
            violations.append(
                f"step {step['id']}: at most one [*] fan-out reference is "
                f"allowed per step; found {sorted(star_raws)}"
            )
        for _, ref in refs:
            if ref.step_id not in id_set:
                violations.append(
                    f"step {step['id']}: reference {ref.raw!r} targets step "
                    f"{ref.step_id}, which does not exist"
                )
            elif ref.step_id not in step["depends_on"]:
                violations.append(
                    f"step {step['id']}: reference {ref.raw!r} requires "
                    f"{ref.step_id} in depends_on"
                )

    # Acyclicity (Kahn) over declared depends_on among existing ids.
    remaining = {step["id"]: {d for d in step["depends_on"] if d in id_set} for step in steps}
    while True:
        ready = [i for i, deps in remaining.items() if not deps]
        if not ready:
            break
        for i in ready:
            del remaining[i]
        for deps in remaining.values():
            deps.difference_update(ready)
    if remaining:
        violations.append(
            f"dependency graph has a cycle involving steps {sorted(remaining)}"
        )
    return violations
