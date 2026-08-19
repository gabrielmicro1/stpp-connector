"""The $steps reference language (plan-format spec).

Exactly three path constructs: dotted field access, [n] index, [*] fan-out.
All resolution edge cases are deterministic and none are errors: missing
fields, out-of-range indexes, and [*] over non-lists resolve to zero values.
Multiple [*] in one path flatten into ONE flat value list; the fan-out cap
applies to the flattened list. Referencing a fanned-out step denotes the
ordered list of its per-call results, and further segments apply across that
list with the same flatten rule.
"""
import re
from dataclasses import dataclass

_REF_PREFIX = "$steps["
_FIELD_RE = re.compile(r"[^.\[\]]+")


class RefSyntaxError(ValueError):
    """A string that starts like a reference but is not well-formed.
    Surfaces as a plan-validation violation, never a runtime error."""


class RefFanoutError(RuntimeError):
    """More than one reference in a step fans out at execution time. The
    validator statically rejects multiple [*]; this covers the dynamic case
    (two refs into fanned-out steps) as a deterministic step failure."""


@dataclass(frozen=True)
class _Field:
    name: str


@dataclass(frozen=True)
class _Index:
    n: int


class _Star:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Star)

    def __hash__(self) -> int:
        return hash(_Star)


@dataclass(frozen=True)
class Ref:
    step_id: int
    segments: tuple
    raw: str


@dataclass(frozen=True)
class StepResult:
    """A completed step's stored result: the {data, meta} envelope, or the
    ordered per-call envelope list when the step fanned out."""

    value: object
    fanned: bool
    failed: bool = False


@dataclass(frozen=True)
class Resolution:
    values: list
    fanned: bool


@dataclass(frozen=True)
class FanPlan:
    calls: list
    truncated_count: int
    empty: bool
    # True when a reference fanned out — even to a single value: a fanned
    # step stores the ordered per-call result list either way, so that
    # later $steps[N] references resolve consistently.
    fanned: bool = False


def parse_ref(s: object) -> Ref | None:
    """Parse a full-string reference. Returns None for anything that does
    not begin with "$steps[" (including refs embedded mid-string — those
    are literals). Raises RefSyntaxError for malformed ref-like strings."""
    if not isinstance(s, str) or not s.startswith(_REF_PREFIX):
        return None
    pos = len(_REF_PREFIX)
    end = s.find("]", pos)
    if end == -1:
        raise RefSyntaxError(f"unclosed step id bracket: {s!r}")
    id_text = s[pos:end]
    if not id_text.isdigit():
        raise RefSyntaxError(f"step id must be a positive integer: {s!r}")
    step_id = int(id_text)
    if step_id < 1:
        raise RefSyntaxError(f"step id must be >= 1: {s!r}")
    segments: list = []
    pos = end + 1
    while pos < len(s):
        ch = s[pos]
        if ch == ".":
            match = _FIELD_RE.match(s, pos + 1)
            if not match:
                raise RefSyntaxError(f"empty field segment at {pos}: {s!r}")
            segments.append(_Field(match.group()))
            pos = match.end()
        elif ch == "[":
            end = s.find("]", pos)
            if end == -1:
                raise RefSyntaxError(f"unclosed bracket at {pos}: {s!r}")
            inner = s[pos + 1 : end]
            if inner == "*":
                segments.append(_Star())
            elif inner.isdigit():
                segments.append(_Index(int(inner)))
            else:
                raise RefSyntaxError(f"index must be [n] or [*]: {s!r}")
            pos = end + 1
        else:
            raise RefSyntaxError(f"expected '.' or '[' at {pos}: {s!r}")
    return Ref(step_id=step_id, segments=tuple(segments), raw=s)


def has_star(ref: Ref) -> bool:
    return any(isinstance(seg, _Star) for seg in ref.segments)


def find_refs(args: object) -> list:
    """Recursively collect (path, Ref) pairs from a step's args. Paths are
    tuples of dict keys / list indexes, used later for substitution."""
    found: list = []

    def walk(node: object, path: tuple) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, path + (i,))
        else:
            ref = parse_ref(node)
            if ref is not None:
                found.append((path, ref))

    walk(args, ())
    return found


def resolve(ref: Ref, results: dict) -> Resolution:
    """Resolve a reference against stored step results. `results` maps
    step_id -> StepResult."""
    target = results.get(ref.step_id)
    if target is None:
        return Resolution(values=[], fanned=False)
    if target.fanned:
        values = list(target.value)
        fanned = True
    else:
        values = [target.value]
        fanned = False
    for seg in ref.segments:
        if isinstance(seg, _Field):
            values = [v[seg.name] for v in values if isinstance(v, dict) and seg.name in v]
        elif isinstance(seg, _Index):
            values = [v[seg.n] for v in values if isinstance(v, list) and seg.n < len(v)]
        else:  # _Star: flatten one level across every list value
            values = [item for v in values if isinstance(v, list) for item in v]
            fanned = True
    return Resolution(values=values, fanned=fanned)


def _substitute(args: object, replacements: dict) -> object:
    if isinstance(args, dict):
        return {k: _substitute(v, replacements) for k, v in args.items()}
    if isinstance(args, list):
        return [_substitute(v, replacements) for v in args]
    if isinstance(args, str) and args in replacements:
        return replacements[args]
    return args


def plan_step_calls(args: dict, results: dict, *, max_fanout: int) -> FanPlan:
    """Turn a step's args into concrete tool-call argument sets.

    Scalar references substitute in place; at most one reference may fan
    out (one call per fanned value, capped at max_fanout with the excess
    counted for the step summary). Any reference resolving to zero values
    empties the whole step: zero tool calls, completed as empty.
    """
    refs = find_refs(args)
    scalar: dict = {}
    fan_raw: str | None = None
    fan_values: list = []
    for _, ref in refs:
        res = resolve(ref, results)
        if res.fanned:
            if fan_raw is not None and ref.raw != fan_raw:
                raise RefFanoutError(
                    f"references {fan_raw!r} and {ref.raw!r} both fan out; "
                    "at most one reference per step may fan out"
                )
            fan_raw = ref.raw
            fan_values = res.values
        else:
            if not res.values:
                return FanPlan(calls=[], truncated_count=0, empty=True)
            scalar[ref.raw] = res.values[0]
    if fan_raw is None:
        return FanPlan(calls=[_substitute(args, scalar)], truncated_count=0, empty=False)
    if not fan_values:
        return FanPlan(calls=[], truncated_count=0, empty=True, fanned=True)
    truncated = max(0, len(fan_values) - max_fanout)
    calls = [
        _substitute(args, {**scalar, fan_raw: value})
        for value in fan_values[:max_fanout]
    ]
    return FanPlan(calls=calls, truncated_count=truncated, empty=False, fanned=True)
