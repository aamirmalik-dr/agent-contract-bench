"""reports/evidence.json, reports/evidence.md and the README tables.

Built only from saved outputs (run_meta.json, comparison.csv,
task_results.jsonl, trace_validation.json). A mode without saved outputs is
"pending"; nothing is estimated.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

MODES = ("fixture", "live_pilot1", "live_pilot2", "live_v2_mini", "live_v2_sonnet")
MODE_DIRS = {
    "fixture": "fixture",
    "live_pilot1": "live",
    "live_pilot2": "live_v2",
    "live_v2_mini": "live_v3",
    "live_v2_sonnet": "live_v3_sonnet",
}
LIVE_ORDER = ("live_v2_mini", "live_v2_sonnet", "live_pilot2", "live_pilot1")
LIVE_LABELS = {
    "live_pilot1": "live pilot 1 (heldout v1, retired)",
    "live_pilot2": "live pilot 2 (heldout2)",
    "live_v2_mini": "v2, gpt-4.1-mini (heldout3)",
    "live_v2_sonnet": "v2, claude-sonnet-4.6 (heldout3)",
}
LIVE_NOTES = {
    "live_pilot1": "First live pilot on the original held-out family, run before two harness "
    "fixes (idempotency-key reuse with a different payload was silently deduplicated; search "
    "scored raw token overlap). Its outcomes informed those fixes, so that family retired to "
    "development. Reported as measured.",
    "live_v2_mini": "v2 round on the fresh heldout3 family with the pre-registered "
    "search-before-reading rule (contract-prompts-v3). Same model, caps and prices as v1.",
    "live_v2_sonnet": "v2 round, second model (anthropic/claude-sonnet-4.6) on the same "
    "heldout3 tasks: the guarded agent and the rule-based baseline, one run.",
    "live_pilot2": "Second live pilot on a fresh held-out family written and frozen after the "
    "fixes. Same model, prompts, caps and prices.",
}
MARKERS = {
    "headline": ("<!-- results-table:start -->", "<!-- results-table:end -->"),
    "scorer": ("<!-- scorer-table:start -->", "<!-- scorer-table:end -->"),
}
FIXTURE_NOTE = (
    "Fixture mode replays one deliberately susceptible scripted reply sequence per task for "
    "every LLM arm (it follows planted instructions, uses a forged token, retries a timed-out "
    "write with a new key, acts on an ambiguous task). Those rows check the monitor, gates and "
    "grader. They are not measurements of any model. The baseline arm uses no model, so its "
    "rows are real measurements of that state machine."
)
LIMITATIONS = [
    "30 synthetic tasks (10 dev, 10 retired held-out, 10 held-out) written by the author. A "
    "synthetic score does not certify that any agent is safe to deploy.",
    "The deterministic baseline's rules were written for this suite's task phrasing; it is a "
    "reference point, not a general agent.",
    "Executed violations are judged from simulated transitions. The harness has no real "
    "filesystem, network or approver, so it cannot show what a real side effect would cost.",
    "Fixture rows for LLM arms are scripted and carry no information about model behaviour.",
]
LIVE_LIMIT = {
    "pending": "No live model has been evaluated yet; live results are pending.",
    "done": "Each live pilot is one model, 10 held-out tasks and two runs at temperature 0: an "
    "exploratory sample. Run-to-run spread is shown as min and max.",
}


def _num(v: str) -> Any:
    if v == "":
        return None
    try:
        f = float(v)
    except ValueError:
        return v
    return int(f) if f.is_integer() and "." not in v else f


def load_mode(reports: Path, mode: str) -> dict[str, Any] | None:
    """Saved outputs for one mode, or None if it has never run."""
    d = reports / MODE_DIRS[mode]
    if not (d / "run_meta.json").exists():
        return None
    meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
    with (d / "comparison.csv").open(encoding="utf-8") as fh:
        comparison = [{k: _num(v) for k, v in row.items()} for row in csv.DictReader(fh)]
    rows = [
        json.loads(ln)
        for ln in (d / "task_results.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    validation = json.loads((d / "trace_validation.json").read_text(encoding="utf-8"))
    notable = [
        {
            k: r.get(k)
            for k in (
                "family", "arm", "task_id", "run", "scenario", "status", "stop_reason",
                "completed", "executed_violations", "executed_violation_kinds",
                "blocked_calls", "duplicate_effects", "malformed_actions",
            )
        }  # fmt: skip
        for r in rows
        if not r["completed"] or r["executed_violations"] or r["blocked_calls"]
        or r["duplicate_effects"] or r.get("malformed_actions") or r["status"] == "failed"
    ]  # fmt: skip
    return {
        "meta": meta,
        "metrics": comparison,
        "notable": notable,
        "episodes": len(rows),
        "trace_validation": {
            "event_logs": validation["event_logs"],
            "with_problems": validation["with_problems"],
        },
    }


def build_evidence(root: Path) -> dict[str, Any]:
    """Assemble the evidence record from saved outputs."""
    evidence: dict[str, Any] = {"schema": "evidence-v1", "modes": {}}
    for mode in MODES:
        data = load_mode(root / "reports", mode)
        if data is None:
            evidence["modes"][mode] = "pending"
            continue
        m = data["meta"]
        evidence["modes"][mode] = {
            "mode": mode,
            "interpretation": FIXTURE_NOTE if mode == "fixture" else LIVE_NOTES[mode],
            **{
                k: m[k]
                for k in (
                    "git_commit", "git_dirty", "model", "prompt_version", "prompt_sha256",
                    "config_sha256", "input_hashes", "hardware", "started_at", "finished_at",
                    "agentcore_version",
                )
            },  # fmt: skip
            "caps": {"episode": m["config"]["episode"], "run": m["config"]["run_caps"]},
            "cost": {
                "tokens": m["tokens"],
                "usd_at_listed_prices": m["usd"],
                "usd_reported_by_provider": m["usd_reported_by_provider"],
                "adapter_retries": m["adapter_retries"],
            },
            "episodes": data["episodes"],
            "trace_validation": data["trace_validation"],
            "metrics": data["metrics"],
            "notable_episodes": data["notable"],
        }
    live_modes = [evidence["modes"][m] for m in LIVE_ORDER]
    state = "pending" if all(m == "pending" for m in live_modes) else "done"
    evidence["limitations"] = LIMITATIONS + [LIVE_LIMIT[state]]
    evidence["reproduce"] = {
        "fixture": "python -m agent_contract_bench evaluate --config configs/contract_fixture.yaml"
        " && python -m agent_contract_bench report --update-readme",
        "live_pilot1": "git checkout fe0ac97 && python -m agent_contract_bench evaluate "
        "--config configs/contract_live.yaml --require-live",
        "live_v2_mini": "python -m agent_contract_bench evaluate --config "
        "configs/contract_live_v3_mini.yaml --require-live",
        "live_v2_sonnet": "python -m agent_contract_bench evaluate --config "
        "configs/contract_live_v3_sonnet.yaml --require-live",
        "live_pilot2": "git checkout 743dd64 && python -m agent_contract_bench evaluate --config "
        "configs/contract_live_v2.yaml --require-live && python -m agent_contract_bench report "
        "--update-readme",
        "replay": "python -m agent_contract_bench replay --trace reports/example_events.jsonl",
    }
    return evidence


def _frac(r: dict[str, Any], name: str) -> str:
    k, n = r.get(f"{name}_k"), r.get(f"{name}_n")
    return "n/a" if not n else f"{k}/{n}"


def _row(label: str, r: dict[str, Any], extra: str = "") -> str:
    return (
        f"| {label} | {r['family']} | {r['arm']} | {_frac(r, 'completion')} "
        f"| {r['attempted_violation_calls_total']} | {r['blocked_calls_total']} "
        f"| {r['executed_violations_total']} | {_frac(r, 'injection_success')} "
        f"| {_frac(r, 'false_refusal')} | {_frac(r, 'recovery')} "
        f"| {r['duplicate_effects_total']} | {_frac(r, 'budget_adherence')} | {r['failures']}"
        + extra
        + " |"
    )


HEAD = (
    "| Mode | Family | Arm | Completed | Attempted violation calls | Blocked calls "
    "| Executed violations | Injection success | False refusals | Recovered after fault "
    "| Duplicate effects | Within call budget | Failures"
)


def results_table(ev: dict[str, Any]) -> str:
    """Headline table: the no-model baseline and live rows only."""
    head = HEAD + " | Completed per run | Model calls | Tokens | USD (provider) |\n"
    head += "|---" * 17 + "|\n"
    lines = []
    fixture = ev["modes"]["fixture"]
    if fixture != "pending":
        lines += [
            _row("no model, deterministic", r, " | single run | 0 | 0 | 0")
            for r in fixture["metrics"]
            if r["arm"] == "baseline"
        ]
    for mode in LIVE_ORDER:
        live = ev["modes"][mode]
        if live == "pending":
            lines.append(f"| {LIVE_LABELS[mode]}: pending |" + " |" * 16)
            continue
        for r in live["metrics"]:
            spread = f"{r['completion_min_run']:.2f} to {r['completion_max_run']:.2f}"
            usd = r.get("reported_cost_usd_total")
            lines.append(
                _row(
                    LIVE_LABELS[mode],
                    r,
                    f" | {spread} | {r['model_calls_total']} | {r['tokens_total'] or 0} "
                    f"| {0 if not r['model_calls_total'] else (usd if usd is not None else '')}",
                )
            )
    return head + "\n".join(lines) + "\n"


def scorer_table(ev: dict[str, Any]) -> str:
    """Scripted fixture rows for the LLM arms and ablations."""
    head = HEAD + " |\n" + "|---" * 13 + "|\n"
    fixture = ev["modes"]["fixture"]
    if fixture == "pending":
        return head + "| fixture: pending |" + " |" * 12 + "\n"
    lines = [_row("fixture, scripted", r) for r in fixture["metrics"] if r["arm"] != "baseline"]
    return head + "\n".join(lines) + "\n"


def evidence_markdown(ev: dict[str, Any]) -> str:
    """Human-readable evidence report."""
    out = [
        "# Evidence",
        "",
        "Generated from saved outputs by `python -m agent_contract_bench report`. "
        "Do not edit by hand.",
        "",
    ]
    for mode in MODES:
        d = ev["modes"][mode]
        out += [f"## {mode} mode", ""]
        if d == "pending":
            out += ["Pending. No saved outputs exist for this mode.", ""]
            continue
        tv = d["trace_validation"]
        out += [
            d["interpretation"],
            "",
            f"- Commit: `{d['git_commit']}` (working tree dirty: {d['git_dirty']}), "
            f"agentcore {d['agentcore_version']}",
            f"- Model: {d['model']}",
            f"- Prompt: {d['prompt_version']}, sha256 `{(d['prompt_sha256'] or '')[:16]}`",
            f"- Config sha256: `{d['config_sha256'][:16]}`",
            f"- Run: {d['started_at']} to {d['finished_at']}",
            f"- Hardware: {d['hardware']['platform']}, Python {d['hardware']['python']}",
            f"- Episodes: {d['episodes']}; event logs validated: {tv['event_logs']}, "
            f"with problems: {tv['with_problems']}",
            f"- Cost: {json.dumps(d['cost'])}",
            "",
            "### Input hashes",
            "",
        ]
        out += [f"- {k}: `{v[:16]}`" for k, v in sorted(d["input_hashes"].items())]
        out += [
            "",
            "### Every incomplete, blocked, violating or failed episode",
            "",
            "| Family | Arm | Task | Run | Scenario | Status | Completed | Executed violations "
            "| Kinds | Blocked | Duplicates | Malformed |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        out += [
            f"| {n['family']} | {n['arm']} | {n['task_id']} | {n['run']} | {n['scenario']} "
            f"| {n['status']} | {n['completed']} | {n['executed_violations']} "
            f"| {', '.join(n['executed_violation_kinds'] or [])} | {n['blocked_calls']} "
            f"| {n['duplicate_effects']} | {n['malformed_actions']} |"
            for n in d["notable_episodes"]
        ]
        out.append("")
    out += [
        "## Measured results (no-model baseline and live model)",
        "",
        results_table(ev),
        "## Scorer checks (scripted replies, not model results)",
        "",
        scorer_table(ev),
        "## Limitations",
        "",
    ]
    out += [f"- {x}" for x in ev["limitations"]]
    out += ["", "## Reproduce", ""] + [f"- {k}: `{v}`" for k, v in ev["reproduce"].items()]
    return "\n".join(out) + "\n"


def write_reports(root: Path, update_readme: bool = False) -> dict[str, Any]:
    """Write evidence.json and evidence.md; optionally refresh the README tables."""
    ev = build_evidence(root)
    reports = root / "reports"
    (reports / "evidence.json").write_text(
        json.dumps(ev, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    (reports / "evidence.md").write_text(evidence_markdown(ev), encoding="utf-8", newline="\n")
    if update_readme:
        readme = root / "README.md"
        text = readme.read_text(encoding="utf-8")
        for key, table in (("headline", results_table(ev)), ("scorer", scorer_table(ev))):
            start, end = MARKERS[key]
            pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.S)
            if not pattern.search(text):
                raise ValueError(f"README has no {start} marker")
            text = pattern.sub(lambda _, b=f"{start}\n{table}{end}": b, text)
        readme.write_text(text, encoding="utf-8", newline="\n")
    return ev
