"""Validate the phase-2 contract artifacts.

Usage: python scripts/check_contracts.py  (run from the repo root; `make
check-contracts` runs it in a container with the needed deps).

Checks:
- contracts/plan-format.json and every contracts/mcp-tools/*.json
  inputSchema/outputSchema are valid JSON Schema (draft 2020-12).
- Each tool file is a complete MCP tool definition whose name matches its
  filename.
- The plan-format spec's example plan validates against plan-format.json.
- contracts/openapi.yaml is a valid OpenAPI document.
"""
import json
import pathlib
import sys

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"

# Example from docs/specs/plan-format.md.
EXAMPLE_PLAN = {
    "intent": "research background on the personnel of proposal P-2025-0042",
    "steps": [
        {
            "id": 1,
            "tool": "get_proposal",
            "args": {"proposal_number": "P-2025-0042"},
            "reason": "pull the proposal record and its personnel",
            "depends_on": [],
        },
        {
            "id": 2,
            "tool": "search_wdp_person",
            "args": {"orcid": "$steps[1].data.personnel[*].person_orcid"},
            "reason": "research-world background on each person",
            "depends_on": [1],
        },
    ],
}

TOOL_REQUIRED_KEYS = {"name", "description", "inputSchema", "outputSchema"}

failures: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - report every failure, keep going
        failures.append(f"{label}: {type(exc).__name__}: {exc}")
    else:
        print(f"ok: {label}")


def check_plan_format() -> None:
    schema = json.loads((CONTRACTS / "plan-format.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(EXAMPLE_PLAN)


def check_tool(path: pathlib.Path) -> None:
    tool = json.loads(path.read_text())
    missing = TOOL_REQUIRED_KEYS - tool.keys()
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    if tool["name"] != path.stem:
        raise ValueError(f"name {tool['name']!r} != filename {path.stem!r}")
    Draft202012Validator.check_schema(tool["inputSchema"])
    Draft202012Validator.check_schema(tool["outputSchema"])


def check_openapi() -> None:
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())
    validate_openapi(spec, base_uri=(CONTRACTS / "openapi.yaml").as_uri())


def main() -> int:
    check("plan-format.json (schema + spec example)", check_plan_format)
    tool_files = sorted((CONTRACTS / "mcp-tools").glob("*.json"))
    if not tool_files:
        failures.append("mcp-tools: no tool files found")
    for path in tool_files:
        check(f"mcp-tools/{path.name}", lambda p=path: check_tool(p))
    check("openapi.yaml", check_openapi)
    if failures:
        print(f"\n{len(failures)} contract check(s) FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nall contract checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
