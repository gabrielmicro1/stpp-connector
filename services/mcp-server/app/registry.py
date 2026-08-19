"""Tool registry over the frozen contracts (contracts/mcp-tools/*.json).

The contract files are the interface truth and are never modified; observed
enum values are appended to DEEP COPIES of per-property description strings
at render time. x-enum-source / x-role annotations and all schema structure
survive untouched — the agent's plan validator reads them straight off what
tools/list serves.
"""
import copy
import json
from pathlib import Path


class ToolRegistry:
    def __init__(self, contracts_dir: Path) -> None:
        paths = sorted((contracts_dir / "mcp-tools").glob("*.json"))
        self._defs = [json.loads(p.read_text()) for p in paths]
        self._by_name = {d["name"]: d for d in self._defs}
        if not self._by_name:
            raise FileNotFoundError(f"no tool contracts under {contracts_dir}/mcp-tools")

    def get(self, name: str) -> dict | None:
        return self._by_name.get(name)

    def render(self, observed: dict[str, set[str]]) -> list[dict]:
        rendered = copy.deepcopy(self._defs)
        for tool in rendered:
            _render_observed(tool.get("inputSchema", {}), observed)
        return rendered

    def visible(self, rendered: list[dict], roles: tuple[str, ...]) -> list[dict]:
        """tools/list filtering: a user only ever sees tools their role may
        call (scoping matrix, mcp-tools spec)."""
        return [t for t in rendered if t.get("x-role") in roles]


def _render_observed(node: object, observed: dict[str, set[str]]) -> None:
    """Walk a schema in place, appending observed values to the description
    of every property annotated x-enum-source: observed_enums."""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            for field_name, prop in properties.items():
                if (
                    isinstance(prop, dict)
                    and prop.get("x-enum-source") == "observed_enums"
                    and field_name in observed
                ):
                    values = " | ".join(f"'{v}'" for v in sorted(observed[field_name]))
                    prop["description"] = (
                        prop.get("description", "").rstrip()
                        + f" Observed values: {values}."
                    ).lstrip()
        for value in node.values():
            _render_observed(value, observed)
    elif isinstance(node, list):
        for item in node:
            _render_observed(item, observed)
