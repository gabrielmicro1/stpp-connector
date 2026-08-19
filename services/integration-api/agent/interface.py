"""The agent-side contract with its host. The agent defines what it needs
(this Protocol); the API layer implements it. Dependency points inward so
the agent stays extractable (CLAUDE.md invariant 3)."""
from typing import Literal, Protocol


class EventSink(Protocol):
    async def status(self, status: str) -> None: ...
    async def plan(self, plan: dict) -> None: ...
    async def step(
        self,
        step_id: int,
        status: str,
        *,
        tool: str | None = None,
        args: dict | None = None,
        result: object = None,
        summary: str | None = None,
        rows: int | None = None,
        truncated: bool | None = None,
        error: str | None = None,
    ) -> None: ...
    async def answer(self, text: str) -> None: ...
    async def error(self, code: str, message: str) -> None: ...


Outcome = Literal["complete", "failed"]
