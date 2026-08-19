"""Agent error taxonomy.

AgentError subclasses are TERMINAL job failures: the runner maps them to
sink.error(code, ...) and outcome "failed" (integration-api spec error
codes). MCP tool errors are NEVER terminal — step failures always flow into
synthesis (spec: step failures are never terminal).
"""


class AgentError(Exception):
    """Terminal job failure; `code` is the SSE error code."""

    code = "internal_error"


class PlanInvalidError(AgentError):
    """Plan still invalid after the one planning repair."""

    code = "plan_invalid"

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


class LLMUnavailableError(AgentError):
    """LLM endpoint unreachable or erroring after one retry."""

    code = "llm_unavailable"


class BudgetExceededError(AgentError):
    """A runtime budget was exceeded (the per-call LLM_MAX_TOKENS output
    cap; the JOB_MAX_SECONDS wall clock is enforced by the host)."""

    code = "budget_exceeded"


class LLMConfigError(Exception):
    """LLM misconfiguration; propagates to the host -> internal_error."""


class MCPToolError(Exception):
    """Structured error from a tools/call: not_authorized | not_found |
    invalid_args | upstream_unavailable. A step-level failure, never
    terminal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MCPTransportError(Exception):
    """The MCP session itself failed (network, protocol). Treated as a
    step-level failure like upstream_unavailable."""
