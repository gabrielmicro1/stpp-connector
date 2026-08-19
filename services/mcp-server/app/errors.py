"""Structured tool errors — the only error vocabulary tools may speak
(mcp-tools spec): never raw HTTP errors."""

TOOL_ERROR_CODES = frozenset(
    {"not_authorized", "not_found", "invalid_args", "upstream_unavailable"}
)


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        if code not in TOOL_ERROR_CODES:
            raise ValueError(f"unknown tool error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
