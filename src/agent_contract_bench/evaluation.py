"""Evaluation runner: every arm on every task, graded, aggregated and saved."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from . import __version__
from .agentcore import AGENTCORE_VERSION
from .agentcore.adapters import (
    FixtureAdapter,
    LiveModelUnavailable,
    ModelAdapter,
    OpenAICompatibleAdapter,
)
from .baselines import run_baseline
from .grader import grade
from .prompts import PROMPT_VERSION, prompt_hash
from .replay import validate_events, write_event_log
from .runner import (
    ARMS,
    LLM_LOOP_ARMS,
    EpisodeResult,
    EpisodeSettings,
    build,
    load_suite,
    run_agent,
    run_single_call,
)


class RunCaps(BaseModel):
    max_total_tokens: int | None = None
    max_usd: float | None = None
    usd_per_mtok_input: float | None = None
    usd_per_mtok_output: float | None = None


class EvalConfig(BaseModel):
    """Frozen evaluation settings, loaded from configs/*.yaml."""

    mode: Literal["fixture", "live"]
    model: str | None = None
    families: list[str]
    arms: list[str]
    runs: int = 1
    fixture_scripts: str = "tests/fixtures/scripts"
    episode: EpisodeSettings = Field(default_factory=EpisodeSettings)
    run_caps: RunCaps = Field(default_factory=RunCaps)
    output_dir: str = "reports/fixture"
    checkpoint_dir: str = "runs"


def load_config(path: Path) -> tuple[EvalConfig, str]:
    """Load a YAML config; return it with the SHA-256 of the file."""
    raw = Path(path).read_bytes()
    cfg = EvalConfig.model_validate(yaml.safe_load(raw))
    unknown = set(cfg.arms) - set(ARMS)
    if unknown:
        raise ValueError(f"unknown arms {sorted(unknown)}")
    return cfg, hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def cost_usd(tokens_in: int, tokens_out: int, caps: RunCaps) -> float | None:
    """Dollar cost at the configured listed prices, or None if prices are unset."""
    if caps.usd_per_mtok_input is None or caps.usd_per_mtok_output is None:
        return None
    return (tokens_in * caps.usd_per_mtok_input + tokens_out * caps.usd_per_mtok_output) / 1e6


def _script_arm(arm: str) -> str:
    return "agent" if arm in LLM_LOOP_ARMS else arm


def run_evaluation(cfg: EvalConfig, cfg_sha: str, root: Path, require_live: bool = False) -> Path:
    """Run every configured arm on every task and write the outputs.

    Raises:
        LiveModelUnavailable: Live mode or ``require_live`` without an endpoint.
    """
    live: ModelAdapter | None = None
    if cfg.mode == "live":
        live = OpenAICompatibleAdapter.from_env(model=cfg.model)
    elif require_live:
        raise LiveModelUnavailable("--require-live was given but the config is in fixture mode")
    out = root / cfg.output_dir
    for sub in ("traces", "events"):
        shutil.rmtree(out / sub, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    settings = cfg.episode
    if cfg.mode == "fixture":
        settings = settings.model_copy(update={"record_timing": False})
    rows: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    tok_in = tok_out = 0
    reported = 0.0
    p_hash = None

    def adapter(family: str, arm: str, task_id: str) -> ModelAdapter:
        if live is not None:
            return live
        path = root / cfg.fixture_scripts / f"{family}_{_script_arm(arm)}.json"
        return FixtureAdapter.from_file(path, task_id)

    for family in cfg.families:
        suite = load_suite(root, family)
        hashes |= {
            f"{family}/tasks": _sha_file(root / "suites" / family / "tasks.json"),
            f"{family}/documents": _sha_file(root / "suites" / family / "documents.json"),
            f"{family}/faults": _sha_file(root / "evaluator" / f"faults_{family}.json"),
            f"{family}/expected": _sha_file(root / "evaluator" / f"expected_{family}.json"),
        }
        if cfg.mode == "fixture":
            for arm in {_script_arm(a) for a in cfg.arms if a != "baseline"}:
                path = root / cfg.fixture_scripts / f"{family}_{arm}.json"
                hashes[f"{family}/script/{arm}"] = _sha_file(path)
        if p_hash is None:
            p_hash = prompt_hash(build(suite.tasks[0], suite, "guarded")[1])
        for run in range(cfg.runs):
            for task in suite.tasks:
                for arm in cfg.arms:
                    spent = cost_usd(tok_in, tok_out, cfg.run_caps)
                    caps = cfg.run_caps
                    capped = (
                        caps.max_total_tokens is not None
                        and tok_in + tok_out >= caps.max_total_tokens
                    ) or (caps.max_usd is not None and spent is not None and spent >= caps.max_usd)
                    if capped and arm != "baseline":
                        result = EpisodeResult(
                            task.task_id, arm, run, "failed", "run_budget_exhausted", [],
                            build(task, suite, arm)[0].env.snapshot(), {"model_calls": 0},
                        )  # fmt: skip
                    elif arm == "baseline":
                        result = run_baseline(task, suite, run, settings)
                    elif arm == "single_call":
                        result = run_single_call(
                            task, suite, adapter(family, arm, task.task_id), run, settings
                        )
                    else:
                        name = f"{task.task_id}_run{run}"
                        ckpt = (
                            root
                            / cfg.checkpoint_dir
                            / cfg.mode
                            / family
                            / arm
                            / f"{name}.ckpt.json"
                        )
                        ckpt.unlink(missing_ok=True)
                        result = run_agent(
                            task,
                            suite,
                            adapter(family, arm, task.task_id),
                            arm,
                            run,
                            settings,
                            trace_path=out / "traces" / family / arm / f"{name}.jsonl",
                            checkpoint_path=ckpt,
                        )
                    tok_in += result.info.get("prompt_tokens") or 0
                    tok_out += result.info.get("completion_tokens") or 0
                    reported += result.info.get("reported_cost_usd") or 0.0
                    ev_path = out / "events" / family / arm / f"{task.task_id}_run{run}.jsonl"
                    header = {
                        "task_id": task.task_id,
                        "family": family,
                        "arm": arm,
                        "run": run,
                        "suite_version": suite.version,
                        "status": result.status,
                    }
                    write_event_log(ev_path, header, result.events)
                    problems = validate_events(result.events)
                    validation.append(
                        {"file": ev_path.relative_to(out).as_posix(), "problems": problems}
                    )
                    rows.append(
                        {
                            "family": family,
                            "arm": arm,
                            "task_id": task.task_id,
                            "run": run,
                            "mode": cfg.mode,
                            "scenario": task.scenario,
                            "status": result.status,
                            "stop_reason": result.stop_reason,
                            "trace_complete": int(not problems),
                            **grade(task, suite.expected[task.task_id], result),
                            **result.info,
                        }
                    )
    meta = {
        "mode": cfg.mode,
        "started_at": started,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": _git(root, "rev-parse", "HEAD"),
        "git_dirty": bool(
            _git(
                root,
                "status",
                "--porcelain",
                "--",
                "src",
                "configs",
                "suites",
                "evaluator",
                "tests",
            )
        ),
        "package_version": __version__,
        "agentcore_version": AGENTCORE_VERSION,
        "model": live.name if live else "none (scripted fixture adapter)",
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": p_hash,
        "config_sha256": cfg_sha,
        "config": cfg.model_dump(),
        "input_hashes": hashes,
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
        },
        "tokens": {"prompt": tok_in, "completion": tok_out} if live else None,
        "usd": cost_usd(tok_in, tok_out, cfg.run_caps) if live else None,
        "usd_reported_by_provider": round(reported, 6) if live else None,
        "adapter_retries": getattr(live, "retries", None),
    }
    write_outputs(out, rows, validation, meta)
    return out


RATES = [
    ("completion", "completed", None),
    ("injection_success", "injection_success", "adversarial"),
    ("false_refusal", "false_refusal", "benign_expected_complete"),
    ("recovery", "recovered", "fault_task"),
    ("budget_adherence", "budget_adherent", None),
    ("trace_completeness", "trace_complete", None),
]
TOTALS = [
    "attempted_violation_calls",
    "blocked_calls",
    "executed_violations",
    "duplicate_effects",
    "env_calls",
    "model_calls",
    "malformed_actions",
    "reprompts",
]


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One comparison row per (family, arm) with numerators and denominators."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["family"], r["arm"]), []).append(r)
    out = []
    for (family, arm), rs in groups.items():
        row: dict[str, Any] = {
            "family": family,
            "arm": arm,
            "mode": rs[0]["mode"],
            "episodes": len(rs),
            "runs": len({r["run"] for r in rs}),
        }
        for name, num, den in RATES:
            k = sum(r.get(num, 0) or 0 for r in rs)
            n = len(rs) if den is None else sum(r.get(den, 0) or 0 for r in rs)
            row[f"{name}_k"], row[f"{name}_n"] = k, n
            row[name] = round(k / n, 3) if n else None
        k = sum(1 for r in rs if r["executed_violations"])
        row["episodes_with_executed_violation_k"], row["episodes_with_executed_violation_n"] = (
            k,
            len(rs),
        )
        for t in TOTALS:
            row[f"{t}_total"] = sum(r.get(t, 0) or 0 for r in rs)
        row["failures"] = sum(r["status"] == "failed" for r in rs)
        row["stopped"] = sum(r["status"] == "stopped" for r in rs)
        toks = [r.get("prompt_tokens") for r in rs]
        row["tokens_total"] = (
            None
            if all(t is None for t in toks)
            else sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rs)
        )
        costs = [r.get("reported_cost_usd") for r in rs]
        row["reported_cost_usd_total"] = (
            None if all(c is None for c in costs) else round(sum(c or 0 for c in costs), 6)
        )
        lat = [r["latency_s"] for r in rs if r.get("latency_s") is not None]
        row["latency_s_mean"] = round(sum(lat) / len(lat), 3) if lat else None
        per_run: dict[int, list[int]] = {}
        for r in rs:
            per_run.setdefault(r["run"], []).append(r["completed"])
        accs = [sum(v) / len(v) for v in per_run.values()]
        row["completion_min_run"], row["completion_max_run"] = round(min(accs), 3), round(
            max(accs), 3
        )
        out.append(row)
    return out


def write_outputs(out: Path, rows: list[dict], validation: list[dict], meta: dict) -> None:
    """Write task_results.jsonl, comparison.csv, trace_validation.json and run_meta.json."""
    with (out / "task_results.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    comparison = aggregate(rows)
    with (out / "comparison.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(comparison[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(comparison)
    summary = {
        "event_logs": len(validation),
        "with_problems": sum(bool(v["problems"]) for v in validation),
        "logs": validation,
    }
    (out / "trace_validation.json").write_text(
        json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    (out / "run_meta.json").write_text(
        json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
