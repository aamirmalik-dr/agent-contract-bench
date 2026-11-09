"""Deterministic fault injection keyed by tool name and call ordinal.

Schedules come from evaluator/ and are handed to the harness by the runner.
No tool can read them, and fault text never changes the contract or the
grader's state.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel

FaultKind = Literal[
    "rate_limit",
    "timeout_before_commit",
    "timeout_after_commit",
    "duplicate_hit",
]


class Fault(BaseModel):
    """Fire ``kind`` on the ``nth`` (1-based) call of ``tool``."""

    tool: str
    nth: int
    kind: FaultKind


class FaultInjector:
    """Counts calls per tool and returns the fault scheduled for each one."""

    def __init__(self, schedule: list[Fault] | None = None) -> None:
        self.schedule = list(schedule or [])
        self.counts: Counter[str] = Counter()
        self.fired: list[dict[str, object]] = []

    def next_fault(self, tool: str) -> FaultKind | None:
        """Advance the counter for ``tool`` and return the fault to fire, if any."""
        self.counts[tool] += 1
        for f in self.schedule:
            if f.tool == tool and f.nth == self.counts[tool]:
                self.fired.append({"tool": tool, "nth": f.nth, "kind": f.kind})
                return f.kind
        return None
