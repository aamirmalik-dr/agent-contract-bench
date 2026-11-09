"""Deterministic compliant baseline: a fixed state machine, no model.

It reads the task text with simple rules, never acts on document text, uses
one idempotency key per effect and retries transient faults a bounded number
of times. It exists to show that the gates do not block legitimate work and
to give the LLM agents a reference point that cannot be talked into anything.
Its rules are written for this suite's task phrasing, so it is a reference,
not a general agent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .agentcore.policy import ToolFailure
from .runner import EpisodeResult, EpisodeSettings, Suite, build, new_state
from .schemas import Task
from .tools import (
    AbstainArgs,
    ApprovalArgs,
    ExportArgs,
    FinishArgs,
    ReadArgs,
    SearchArgs,
    WriteArgs,
)

OUTPUT_PATH = re.compile(r"(/workspace/[A-Za-z0-9_./-]+\.json)")
EXPORT = re.compile(r"\bexport (?:record )?([A-Z]{2,5}-[A-Z0-9-]+)")
INCIDENT_ID = re.compile(r"\b(INC-\d{3,5})\b")
WORD = re.compile(r"[a-z][a-z-]+")
MAX_RETRIES = 2


def run_baseline(task: Task, suite: Suite, run: int, settings: EpisodeSettings) -> EpisodeResult:
    """Run the state machine on one task."""
    harness, registry = build(task, suite, "baseline")
    state = new_state(task, "baseline", run, settings)
    fns = {name: registry.get(name).fn for name in registry.allowed}  # type: ignore[union-attr]

    def call(tool: str, args: Any) -> dict[str, Any] | None:
        for _ in range(MAX_RETRIES + 1):
            state.steps += 1
            try:
                return fns[tool](state, args)
            except ToolFailure as exc:
                if exc.code not in ("timeout", "rate_limited"):
                    return None
        return None

    def end(status: str, stop: str) -> EpisodeResult:
        return EpisodeResult(
            task.task_id, "baseline", run, status, stop, harness.events, harness.env.snapshot(),
            {"model_calls": 0, "steps": state.steps},
        )  # fmt: skip

    out_path = OUTPUT_PATH.search(task.instruction)
    if out_path is None:
        call("abstain", AbstainArgs(reason="the task names no output path under /workspace"))
        return end("abstained", "abstained")
    words = set(WORD.findall(task.instruction.lower()))
    found = call("search", SearchArgs(query=task.instruction[:200], limit=8)) or {"hits": []}
    incidents: dict[str, str] = {}
    seen: set[str] = set()
    for hit in found["hits"]:
        tags = set(hit.get("tags", []))
        if hit["path"] in seen or "incident" not in tags or not (tags - {"incident"}) & words:
            continue
        seen.add(hit["path"])
        doc = call("read_document", ReadArgs(path=hit["path"]))
        if doc is None:
            continue
        ids = INCIDENT_ID.findall(hit["title"]) or INCIDENT_ID.findall(doc["text"])
        if ids:
            incidents.setdefault(ids[0], hit["title"])
    summary = {"incidents": [{"id": k, "title": v} for k, v in sorted(incidents.items())]}
    call(
        "write_note",
        WriteArgs(path=out_path.group(1), content=json.dumps(summary), idempotency_key="summary-1"),
    )
    for record in EXPORT.findall(task.instruction):
        grant = call("request_approval", ApprovalArgs(record_id=record))
        if grant and grant.get("approval_token"):
            call(
                "export_record",
                ExportArgs(
                    record_id=record,
                    approval_token=grant["approval_token"],
                    idempotency_key=f"export-{record}",
                ),
            )
    call("finish", FinishArgs(summary=f"wrote {len(incidents)} incidents to {out_path.group(1)}"))
    return end("complete", "finished")
