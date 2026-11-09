"""Command line: demo, evaluate, report and replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .agentcore.adapters import FixtureAdapter, LiveModelUnavailable, OpenAICompatibleAdapter
from .evaluation import load_config, run_evaluation
from .grader import grade
from .replay import read_event_log, replay, validate_events, write_event_log
from .report import write_reports
from .runner import EpisodeResult, EpisodeSettings, load_suite, run_agent

ROOT = Path.cwd()
SCRIPTS = Path("tests/fixtures/scripts")
DEMO = (
    ("dev", "dev-04", "guarded", "benign task: summary plus an approved export"),
    ("dev", "dev-02", "guarded", "injected export instruction, gates enforced"),
    ("dev", "dev-02", "naive", "the same replies, gates only logged"),
    ("heldout", "ho-08", "guarded", "export times out after committing; retried with the same key"),
)


def _short(obj: object, n: int = 58) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def events_table(result: EpisodeResult, title: str) -> Table:
    """One row per environment event."""
    t = Table(title=title)
    for col in ("seq", "step", "tool", "arguments", "monitor", "decision", "executed", "error"):
        t.add_column(col, overflow="fold")
    for e in result.events:
        args = {k: v for k, v in e.arguments.items() if k != "content"}
        t.add_row(
            str(e.seq),
            str(e.step),
            e.tool,
            _short(args),
            ", ".join(e.violations) or "ok",
            e.decision,
            "yes" if e.executed else "no",
            (e.error or "") + (f" ({e.fault})" if e.fault else ""),
        )
    return t


def run_demo(mode: str, root: Path, console: Console | None, model: str | None = None) -> None:
    """Run the demo episodes and save one event log for the replay command."""
    live = OpenAICompatibleAdapter.from_env(model=model) if mode == "live" else None
    settings = EpisodeSettings(record_timing=False)
    suites = {f: load_suite(root, f) for f in ("dev", "heldout")}
    for family, task_id, arm, label in DEMO:
        suite = suites[family]
        task = next(t for t in suite.tasks if t.task_id == task_id)
        adapter = live or FixtureAdapter.from_file(root / SCRIPTS / f"{family}_agent.json", task_id)
        result = run_agent(task, suite, adapter, arm, 0, settings)
        g = grade(task, suite.expected[task_id], result)
        if console:
            console.print(f"\n[bold]{task_id} ({arm}): {label}[/bold]\n{task.instruction}")
            console.print(events_table(result, f"{task_id} / {arm}: environment events"))
            console.print(
                f"completed={g['completed']} attempted_violation_calls="
                f"{g['attempted_violation_calls']} blocked={g['blocked_calls']} "
                f"executed_violations={g['executed_violations']} "
                f"{g['executed_violation_kinds'] or ''} duplicate_effects={g['duplicate_effects']}"
            )
        if (task_id, arm) == ("dev-02", "guarded") and mode == "fixture":
            header = {"task_id": task_id, "family": family, "arm": arm, "run": 0,
                      "suite_version": suite.version, "status": result.status}  # fmt: skip
            write_event_log(root / "reports" / "example_events.jsonl", header, result.events)
    if console:
        label = "scripted fixture replies, no model" if live is None else live.name
        console.print(f"\nMode: {mode} ({label}).")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m agent_contract_bench``."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="agent_contract_bench", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("demo", help="benign task, blocked injection, timeout recovery")
    p.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    p.add_argument("--model", help="model id for live mode; overrides AGENT_MODEL_NAME")
    p = sub.add_parser("evaluate", help="run every arm on every task of a config")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--require-live", action="store_true", help="fail unless a live model is set")
    p = sub.add_parser("report", help="regenerate evidence files from saved outputs")
    p.add_argument("--update-readme", action="store_true")
    p = sub.add_parser("replay", help="validate a saved event log and replay it")
    p.add_argument("--trace", type=Path, default=Path("reports/example_events.jsonl"))
    args = parser.parse_args(argv)
    console = Console(width=170)
    try:
        if args.cmd == "demo":
            run_demo(args.mode, ROOT, console, model=args.model)
        elif args.cmd == "evaluate":
            cfg, sha = load_config(args.config)
            if args.require_live and cfg.mode != "live":
                raise LiveModelUnavailable("--require-live needs a config with mode: live")
            out = run_evaluation(cfg, sha, ROOT, require_live=args.require_live)
            console.print(f"wrote {out.relative_to(ROOT).as_posix()}/")
        elif args.cmd == "report":
            ev = write_reports(ROOT, update_readme=args.update_readme)
            modes = ", ".join(
                f"{k}={'pending' if v == 'pending' else 'saved'}" for k, v in ev["modes"].items()
            )
            console.print(f"wrote reports/evidence.json and reports/evidence.md; {modes}")
        elif args.cmd == "replay":
            header, events = read_event_log(args.trace)
            problems = validate_events(events)
            for msg in problems:
                console.print(f"[red]invalid:[/red] {msg}")
            diffs = replay(load_suite(ROOT, header["family"]), header, events)
            for d in diffs:
                console.print(
                    f"[yellow]divergence[/yellow] event {d['seq']} {d['field']}: "
                    f"recorded {_short(d['recorded'])} replayed {_short(d['replayed'])}"
                )
            console.print(
                f"{len(events)} events, {len(problems)} validation problems, "
                f"{len(diffs)} divergences ({header['task_id']}, {header['arm']})"
            )
            return 1 if problems or diffs else 0
    except LiveModelUnavailable as exc:
        console.print(f"[red]live model unavailable:[/red] {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
