"""Event-log validation and deterministic replay.

``validate_events`` checks a saved log on its own: sequence numbers, step
order and the hash chain, so a missing, reordered or edited event is found.
``replay`` re-issues every recorded call against a fresh environment with the
same task, gates and fault schedule, and reports each field that differs.
Divergence is reported, never repaired.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from .agentcore.policy import ToolFailure
from .environment import chain_hash
from .runner import Suite, build
from .schemas import Event
from .tools import GENESIS, env_operation

COMPARED = (
    "tool",
    "violations",
    "enforced",
    "decision",
    "fault",
    "executed",
    "transition",
    "error",
)


def write_event_log(path: Path, header: dict[str, Any], events: list[Event]) -> None:
    """Write a header line followed by one event per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"header": header}, sort_keys=True) + "\n")
        for e in events:
            fh.write(json.dumps(e.model_dump(), sort_keys=True, ensure_ascii=False) + "\n")


def read_event_log(path: Path) -> tuple[dict[str, Any], list[Event]]:
    """Load a header and its events."""
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines or "header" not in json.loads(lines[0]):
        raise ValueError(f"{Path(path).name} has no header line")
    return json.loads(lines[0])["header"], [Event.model_validate_json(ln) for ln in lines[1:]]


def validate_events(events: list[Event]) -> list[str]:
    """Problems found in a log: gaps, step regressions or a broken hash chain."""
    problems = []
    prev, last_step = GENESIS, -1
    for i, e in enumerate(events, start=1):
        if e.seq != i:
            problems.append(f"event {i}: sequence number {e.seq}, expected {i}")
        if e.step < last_step:
            problems.append(f"event {i}: step {e.step} after step {last_step}")
        if e.prev_hash != prev:
            problems.append(f"event {i}: prev_hash does not match the previous event")
        body = e.model_dump(exclude={"prev_hash", "hash"})
        if chain_hash(e.prev_hash, body) != e.hash:
            problems.append(f"event {i}: hash does not match its contents")
        prev, last_step = e.hash, e.step
    return problems


def replay(suite: Suite, header: dict[str, Any], events: list[Event]) -> list[dict[str, Any]]:
    """Re-run the recorded calls and list every divergence.

    Args:
        suite: The suite the log was recorded on.
        header: Log header naming the task and arm.
        events: The recorded events.

    Returns:
        One record per differing field, empty when the replay matches.
    """
    task = next(t for t in suite.tasks if t.task_id == header["task_id"])
    harness, _ = build(task, suite, header["arm"])
    for e in events:
        op = env_operation(harness.env, e.tool, e.arguments, e.step)
        with suppress(ToolFailure):
            harness.call(e.tool, e.arguments, e.step, op)
    out: list[dict[str, Any]] = []
    for i in range(max(len(events), len(harness.events))):
        rec = events[i].model_dump() if i < len(events) else None
        new = harness.events[i].model_dump() if i < len(harness.events) else None
        if rec is None or new is None:
            out.append({"seq": i + 1, "field": "event", "recorded": rec, "replayed": new})
            continue
        for f in COMPARED:
            if rec[f] != new[f]:
                out.append({"seq": i + 1, "field": f, "recorded": rec[f], "replayed": new[f]})
    return out
