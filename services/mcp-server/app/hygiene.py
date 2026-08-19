"""Result hygiene (mcp-tools spec): row caps, long-text truncation, and the
honest {data, meta:{total, returned, truncated}} envelope."""
from typing import Any


def truncate_strings(node: Any, max_chars: int) -> tuple[Any, bool]:
    """Recursively truncate strings longer than max_chars. Returns the
    (possibly rebuilt) node and whether anything was truncated."""
    if isinstance(node, str):
        if len(node) > max_chars:
            return node[:max_chars] + "…", True
        return node, False
    if isinstance(node, dict):
        truncated = False
        out = {}
        for key, value in node.items():
            out[key], hit = truncate_strings(value, max_chars)
            truncated = truncated or hit
        return out, truncated
    if isinstance(node, (list, tuple)):
        truncated = False
        out_list = []
        for value in node:
            item, hit = truncate_strings(value, max_chars)
            out_list.append(item)
            truncated = truncated or hit
        return out_list, truncated
    return node, False


def envelope(
    rows: list,
    total: int,
    *,
    max_rows: int,
    max_chars: int,
    extra_meta: dict | None = None,
) -> dict:
    kept = rows[:max_rows]
    kept, text_truncated = truncate_strings(kept, max_chars)
    meta = {
        "total": total,
        "returned": len(kept),
        "truncated": text_truncated or total > len(kept),
    }
    if extra_meta:
        meta.update(extra_meta)
    return {"data": kept, "meta": meta}
