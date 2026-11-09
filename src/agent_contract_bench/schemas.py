"""Typed records for tasks, contracts, documents and the event log."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .agentcore.schemas import BaseAgentState

Scenario = Literal[
    "benign",
    "injected_export",
    "injected_read",
    "path_traversal",
    "alias_escape",
    "forged_approval",
    "timeout_after_commit",
    "timeout_before_commit",
    "duplicate_search",
    "rate_limit",
    "ambiguous",
]


class Contract(BaseModel):
    """Capabilities granted to the agent for one task.

    Attributes:
        read_prefixes: Paths the agent may read, after canonicalisation.
        write_prefixes: Paths the agent may write, after canonicalisation.
        max_tool_calls: Calls the contract allows; later calls are violations.
        export_requires_approval: Exports need a token bound to the export.
        approval_ttl_steps: Steps after issue during which a token is valid.
    """

    model_config = ConfigDict(extra="forbid")

    read_prefixes: list[str] = Field(default_factory=lambda: ["/docs", "/workspace"])
    write_prefixes: list[str] = Field(default_factory=lambda: ["/workspace"])
    max_tool_calls: int = 12
    export_requires_approval: bool = True
    approval_ttl_steps: int = 6


class Document(BaseModel):
    """A synthetic file in the virtual workspace."""

    path: str
    title: str
    text: str
    tags: list[str] = Field(default_factory=list)


class Task(BaseModel):
    """One benchmark task. Everything here may be shown to the agent except
    ``approver``, ``aliases`` and ``exportable``, which configure the simulated
    environment. Fault schedules and expected outcomes live in evaluator/.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    family: Literal["dev", "heldout"]
    scenario: Scenario
    adversarial: bool
    twin_of: str | None = None
    instruction: str
    contract: Contract = Field(default_factory=Contract)
    documents: list[str] = Field(description="paths of suite documents present in this task")
    exportable: dict[str, str] = Field(
        default_factory=dict, description="record id -> record text in the export store"
    )
    approver: dict[str, Literal["approve", "deny"]] = Field(
        default_factory=dict, description="simulated approver decision per record id"
    )
    aliases: dict[str, str] = Field(
        default_factory=dict, description="path aliases the virtual filesystem resolves"
    )


class Event(BaseModel):
    """One attempted tool call as seen by the environment.

    ``violations`` is the monitor's judgement of the attempt. ``executed`` says
    whether the environment performed the operation (it is false for blocked
    calls, injected failures before commit and unknown paths); ``transition``
    records what the operation did.
    """

    seq: int
    step: int
    tool: str
    arguments: dict[str, Any]
    calls_used: int
    violations: list[str]
    enforced: list[str]
    decision: Literal["allowed", "blocked", "logged_only"]
    fault: str | None = None
    executed: bool
    transition: dict[str, Any] | None = None
    error: str | None = None
    idempotency_key: str | None = None
    prev_hash: str
    hash: str


class BenchState(BaseAgentState):
    """Agent-visible episode state. Faults and grading data are never here."""

    task_id: str
    instruction: str
    contract: Contract
    finish_summary: str | None = None

    def observable(self) -> dict[str, Any]:
        """Compact summary shown to the model each step."""
        return {
            "steps_used": self.steps,
            "harness_tool_calls_left": self.remaining_tool_calls,
        }
