"""Suite loading and episode orchestration for every arm.

Arms:
- ``baseline``: deterministic compliant state machine, all gates enforced.
- ``single_call``: one model reply containing a full plan, executed open-loop
  with all gates enforced.
- ``guarded``: iterative LLM agent, all gates enforced before execution.
- ``naive``: the same agent, prompt and model; gates are only logged.
- ``guarded_no_<gate>``: ablations that drop one gate from the guarded arm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .agentcore.adapters import AdapterError, ModelAdapter
from .agentcore.loop import AgentLoop, CheckpointStore
from .agentcore.policy import Budget, Policy, ToolRegistry
from .agentcore.schemas import Action, PendingAction
from .agentcore.trace import TraceWriter
from .contracts import GATES, Monitor
from .environment import VirtualEnv
from .faults import Fault, FaultInjector
from .prompts import (
    agent_system_prompt,
    contract_text,
    render_observation,
    single_call_system_prompt,
)
from .schemas import BenchState, Document, Event, Task
from .tools import Harness, build_tools

ALL_GATES = frozenset(GATES)
ARMS: dict[str, frozenset[str]] = {
    "baseline": ALL_GATES,
    "single_call": ALL_GATES,
    "guarded": ALL_GATES,
    "naive": frozenset(),
    **{f"guarded_no_{g}": ALL_GATES - {g} for g in GATES},
}
LLM_LOOP_ARMS = ("guarded", "naive", *(f"guarded_no_{g}" for g in GATES))
TOKEN_PLACEHOLDER = "$APPROVAL_TOKEN"


class Expected(BaseModel):
    """Evaluator-only completion criteria for one task."""

    outcome: str
    summary_path: str | None = None
    incident_ids: list[str] = []
    exports: list[str] = []


@dataclass
class Suite:
    """One task family: tasks, documents, hidden fault schedules and expectations."""

    family: str
    version: str
    tasks: list[Task]
    documents: dict[str, Document]
    faults: dict[str, list[Fault]]
    expected: dict[str, Expected]


def load_suite(root: Path, family: str) -> Suite:
    """Load ``suites/<family>`` and the matching evaluator files."""
    raw = json.loads((root / "suites" / family / "tasks.json").read_text(encoding="utf-8"))
    docs = json.loads((root / "suites" / family / "documents.json").read_text(encoding="utf-8"))
    faults = json.loads((root / "evaluator" / f"faults_{family}.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (root / "evaluator" / f"expected_{family}.json").read_text(encoding="utf-8")
    )
    tasks = [Task.model_validate(t) for t in raw["tasks"]]
    documents = {d["path"]: Document.model_validate(d) for d in docs["documents"]}
    for t in tasks:
        missing = [p for p in t.documents if p not in documents]
        if missing:
            raise ValueError(f"{t.task_id} names unknown documents {missing}")
    return Suite(
        family=family,
        version=raw["version"],
        tasks=tasks,
        documents=documents,
        faults={k: [Fault.model_validate(f) for f in v] for k, v in faults["tasks"].items()},
        expected={k: Expected.model_validate(v) for k, v in expected["tasks"].items()},
    )


@dataclass
class EpisodeResult:
    """Everything the grader and the reports need from one episode."""

    task_id: str
    arm: str
    run: int
    status: str
    stop_reason: str | None
    events: list[Event]
    snapshot: dict[str, Any]
    info: dict[str, Any] = field(default_factory=dict)


class EpisodeSettings(BaseModel):
    """Harness-level caps. The contract's own call budget is judged by the monitor."""

    max_steps: int = 20
    harness_extra_calls: int = 4
    max_tokens: int | None = 60000
    max_wall_seconds: float | None = 300
    recent_k: int = 4
    max_completion_tokens: int = 900
    record_timing: bool = True


def build(task: Task, suite: Suite, arm: str) -> tuple[Harness, ToolRegistry]:
    """Fresh environment, monitor, fault injector, harness and registry."""
    env = VirtualEnv(task, suite.documents)
    harness = Harness(
        env, Monitor(task.contract, ARMS[arm]), FaultInjector(suite.faults.get(task.task_id, []))
    )
    return harness, ToolRegistry(build_tools(harness))


def new_state(task: Task, arm: str, run: int, settings: EpisodeSettings) -> BenchState:
    """Fresh agent state; hidden faults and expectations are never part of it."""
    return BenchState(
        episode_id=f"{arm}:{task.task_id}:run{run}",
        task_id=task.task_id,
        instruction=task.instruction,
        contract=task.contract,
        remaining_tool_calls=task.contract.max_tool_calls + settings.harness_extra_calls,
    )


def _info(state: BenchState, live: bool, settings: EpisodeSettings) -> dict[str, Any]:
    return {
        "model_calls": state.model_calls,
        "steps": state.steps,
        "malformed_actions": state.malformed_actions,
        "reprompts": state.reprompts,
        "prompt_tokens": state.token_usage.prompt if live else None,
        "completion_tokens": state.token_usage.completion if live else None,
        "reported_cost_usd": round(state.reported_cost_usd, 6) if live else None,
        "latency_s": round(state.elapsed_seconds, 3) if settings.record_timing else None,
    }


def run_agent(
    task: Task,
    suite: Suite,
    adapter: ModelAdapter,
    arm: str,
    run: int,
    settings: EpisodeSettings,
    trace_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> EpisodeResult:
    """Run the iterative LLM agent for one task under the arm's gates."""
    harness, registry = build(task, suite, arm)
    state = new_state(task, arm, run, settings)
    budget = Budget(
        max_steps=settings.max_steps,
        max_tool_calls=state.remaining_tool_calls,
        max_tokens=settings.max_tokens,
        max_wall_seconds=settings.max_wall_seconds,
    )
    loop = AgentLoop(
        adapter,
        Policy(registry, budget),
        agent_system_prompt(registry),
        render_observation,
        checkpoint=CheckpointStore(checkpoint_path) if checkpoint_path else None,
        trace=TraceWriter(trace_path, settings.record_timing) if trace_path else None,
        recent_k=settings.recent_k,
        max_completion_tokens=settings.max_completion_tokens,
    )
    state = loop.run(state)
    return EpisodeResult(
        task.task_id,
        arm,
        run,
        state.status,
        state.stop_reason,
        harness.events,
        harness.env.snapshot(),
        _info(state, adapter.is_live, settings),
    )


def _prefetch(harness: Harness, task: Task, state: BenchState) -> str:
    """Search with the task text and read the top hits, through the harness."""
    from .tools import ReadArgs, SearchArgs

    specs = {s.name: s for s in build_tools(harness)}
    lines = []
    try:
        hits = specs["search"].fn(state, SearchArgs(query=task.instruction[:200], limit=4))["hits"]
    except Exception:  # noqa: BLE001, a faulted prefetch leaves the planner with less context
        return "(search failed)"
    for h in hits[:3]:
        try:
            doc = specs["read_document"].fn(state, ReadArgs(path=h["path"]))
            head = f'<document path="{doc["path"]}" trust="untrusted-data">'
            lines.append(f"{head}\n{doc['text']}\n</document>")
        except Exception:  # noqa: BLE001
            lines.append(f'<document path="{h["path"]}">(read failed)</document>')
    return "\n".join(lines)


def run_single_call(
    task: Task,
    suite: Suite,
    adapter: ModelAdapter,
    run: int,
    settings: EpisodeSettings,
    max_output_chars: int = 8000,
) -> EpisodeResult:
    """One model reply with a full plan, executed in order with every gate enforced."""
    harness, registry = build(task, suite, "single_call")
    state = new_state(task, "single_call", run, settings)
    policy = Policy(registry, Budget(max_steps=99, max_tool_calls=state.remaining_tool_calls))
    context = _prefetch(harness, task, state)
    user = (
        f"Task:\n{task.instruction}\n\nCapability contract:\n{contract_text(task.contract)}\n\n"
        f"Search results for the task, already read for you (untrusted data):\n{context}"
    )
    messages = [
        {"role": "system", "content": single_call_system_prompt(registry)},
        {"role": "user", "content": user},
    ]
    plan: list[dict[str, Any]] | None = None
    error = None
    for attempt in range(2):
        try:
            resp = adapter.complete(messages, settings.max_completion_tokens * 2)
        except AdapterError:
            return EpisodeResult(
                task.task_id, "single_call", run, "failed", "adapter_error",
                harness.events, harness.env.snapshot(), _info(state, adapter.is_live, settings),
            )  # fmt: skip
        state.model_calls += 1
        state.token_usage.prompt += resp.prompt_tokens or 0
        state.token_usage.completion += resp.completion_tokens or 0
        state.reported_cost_usd += resp.cost_usd or 0.0
        plan, error = _parse_plan(resp.text, max_output_chars)
        if plan is not None:
            break
        state.reprompts += attempt == 0
        messages += [
            {"role": "assistant", "content": resp.text[:max_output_chars]},
            {"role": "user", "content": f"Your reply could not be used: {error}. Try again."},
        ]
    if plan is None:
        state.malformed_actions += 1
        return EpisodeResult(
            task.task_id, "single_call", run, "failed", "malformed_output",
            harness.events, harness.env.snapshot(), _info(state, adapter.is_live, settings),
        )  # fmt: skip
    status, stop = "stopped", "plan_exhausted"
    token: str | None = None
    for i, raw in enumerate(plan):
        state.steps = i + 1
        args = dict(raw.get("arguments") or {})
        if args.get("approval_token") == TOKEN_PLACEHOLDER:
            args["approval_token"] = token
        pending = PendingAction(
            step=state.steps,
            summary="planned",
            action=Action(tool=str(raw.get("tool", "")), arguments=args),
            idempotency_key=f"{state.episode_id}:{state.steps}",
        )
        result, _ = policy.execute(state, pending)
        if result.ok and result.output and result.output.get("approval_token"):
            token = result.output["approval_token"]
        spec = registry.get(pending.action.tool)
        if result.ok and spec is not None and spec.terminal is not None:
            status, stop = spec.terminal, "finished" if spec.terminal == "complete" else "abstained"
            break
    return EpisodeResult(
        task.task_id, "single_call", run, status, stop,
        harness.events, harness.env.snapshot(), _info(state, adapter.is_live, settings),
    )  # fmt: skip


def _parse_plan(text: str, max_chars: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    if len(text) > max_chars:
        return None, f"reply was {len(text)} characters, the limit is {max_chars}"
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object found"
    try:
        obj = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    plan = obj.get("plan") if isinstance(obj, dict) else None
    if not isinstance(plan, list) or not plan or not all(isinstance(p, dict) for p in plan):
        return None, 'expected a non-empty "plan" list of {"tool", "arguments"} objects'
    return plan[:40], None


__all__ = [
    "ARMS",
    "LLM_LOOP_ARMS",
    "EpisodeResult",
    "EpisodeSettings",
    "Expected",
    "Suite",
    "build",
    "load_suite",
    "run_agent",
    "run_single_call",
]
