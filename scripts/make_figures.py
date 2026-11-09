"""Draw the README figures from saved run outputs only.

Reads reports/live (v1 pilot 1), reports/live_v2 (v1 pilot 2),
reports/live_v3 (v2 round, gpt-4.1-mini) and reports/live_v3_sonnet (v2
round, second model): task_results.jsonl and the saved event logs. No model
is called and nothing is estimated; running it twice gives identical files.

Usage: python scripts/make_figures.py [--out docs/figures]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figure_style as fs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# (output dir, task family, short group label, second model?)
ROUNDS = [
    ("live", "heldout", "v1 pilot 1\nheldout", False),
    ("live_v2", "heldout2", "v1 pilot 2\nheldout2", False),
    ("live_v3", "heldout3", "v2\nheldout3", False),
    ("live_v3_sonnet", "heldout3", "v2, second model\nheldout3", True),
]
SAMPLE = (
    "Live. v1 pilots and v2: gpt-4.1-mini, 10 tasks x 2 runs.\n"
    "Second model: claude-sonnet-4.6, 10 tasks x 1 run (hatched)."
)
KEY = (
    "rule: rule-based baseline, no model.  single: single-call planner.\n"
    "guarded: iterative agent, gates enforced.  naive: the same agent, gates only logged."
)
ARMS = ["baseline", "single_call", "guarded", "naive"]
ARM_TYPE = {
    "baseline": "baseline",
    "single_call": "single",
    "guarded": "agent",
    "naive": "ablation",
}
ARM_LABEL = {"baseline": "rule", "single_call": "single", "guarded": "guarded", "naive": "naive"}
FAMILY = {
    "injected_export": "injection",
    "injected_read": "injection",
    "path_traversal": "injection",
    "alias_escape": "injection",
    "forged_approval": "forged approval",
    "timeout_after_commit": "transient fault",
    "timeout_before_commit": "transient fault",
    "rate_limit": "transient fault",
    "duplicate_search": "transient fault",
    "ambiguous": "ambiguous request",
    "benign": "benign",
}
FIGURES = [
    "completion_and_violations.png",
    "violation_accounting.png",
    "v2_completion_by_fault_family.png",
    "agent_failure_modes.png",
]


def load(name: str) -> list[dict]:
    path = ROOT / "reports" / name / "task_results.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def events(name: str, family: str, arm: str, task: str, run: int) -> list[dict]:
    path = ROOT / "reports" / name / "events" / family / arm / f"{task}_run{run}.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [e for e in lines if "header" not in e]


def by_arm(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["arm"]].append(r)
    return out


def layout(arms_wanted: tuple[str, ...] = tuple(ARMS)) -> list[tuple[float, str, str, bool, str]]:
    """Bar positions: (x, round dir, family, second model, arm)."""
    pos, x = [], 0.0
    for name, family, _, second in ROUNDS:
        present = by_arm(load(name))
        for arm in arms_wanted:
            if arm in present:
                pos.append((x, name, family, second, arm))
                x += 1
        x += 0.9
    return pos


def label_groups(ax, pos, y: float = -0.2) -> None:
    """Arm names as rotated ticks, round names centred under each group."""
    ax.set_xticks([p[0] for p in pos], [ARM_LABEL[p[4]] for p in pos], rotation=30, ha="right")
    for name, _, title, _ in ROUNDS:
        xs = [p[0] for p in pos if p[1] == name]
        if xs:
            ax.text(
                sum(xs) / len(xs),
                y,
                title,
                ha="center",
                va="top",
                fontsize=10,
                transform=ax.get_xaxis_transform(),
            )
    ax.set_xlim(pos[0][0] - 0.7, pos[-1][0] + 1.3)


def per_run_rate(rs: list[dict], key: str) -> list[float]:
    runs = sorted({r["run"] for r in rs})
    return [
        sum(r[key] for r in rs if r["run"] == i) / sum(1 for r in rs if r["run"] == i) for i in runs
    ]


def completion_and_violations(out: Path) -> None:
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(fs.WIDTH, 8.2), sharex=True, gridspec_kw={"height_ratios": [1.6, 1]}
    )
    pos = layout()
    for x, name, _, second, arm in pos:
        rs = by_arm(load(name))[arm]
        k, n = sum(r["completed"] for r in rs), len(rs)
        runs = per_run_rate(rs, "completed")
        top.bar(x, k / n, width=0.7, **fs.bar_kwargs(ARM_TYPE[arm], second))
        if len(runs) > 1:
            fs.points(top, x, runs)
        top.text(x, max(k / n, *runs) + 0.03, f"{k}/{n}", ha="center", va="bottom", fontsize=10)
        v = sum(r["executed_violations"] for r in rs)
        bottom.bar(x, v, width=0.7, **fs.bar_kwargs(ARM_TYPE[arm], second))
        bottom.text(x, v + 0.1, str(v), ha="center", va="bottom", fontsize=10)
    top.set_ylim(0, 1.18)
    top.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    top.set_ylabel("Share of tasks completed")
    bottom.set_ylim(0, 4.5)
    bottom.yaxis.set_major_locator(MaxNLocator(integer=True))
    bottom.set_ylabel("Executed forbidden\neffects (count)")
    label_groups(bottom, pos, y=-0.36)
    top.set_title(
        "Task completion (top) and executed forbidden effects (bottom) by arm\n"
        f"{SAMPLE}\nBars: all episodes. Points: individual runs.",
        loc="left",
        fontsize=11,
    )
    fig.text(0.01, 0.01, KEY, fontsize=10, color=fs.MUTED, va="bottom")
    fig.subplots_adjust(left=0.11, right=0.99, top=0.86, bottom=0.21, hspace=0.12)
    fs.save(fig, out / "completion_and_violations.png")


def violation_accounting(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(fs.WIDTH, 5.8))
    segments = [
        ("blocked by the monitor", fs.OUTCOME["correct"]),
        ("not blocked, no forbidden effect", fs.OUTCOME["abstained"]),
        ("executed forbidden effect", fs.OUTCOME["wrong"]),
    ]
    pos = layout()
    for x, name, _, second, arm in pos:
        rs = by_arm(load(name))[arm]
        a = sum(r["attempted_violation_calls"] for r in rs)
        b = sum(r["blocked_calls"] for r in rs)
        e = sum(r["executed_violations"] for r in rs)
        bottom = 0
        for v, (_, colour) in zip([b, max(a - b - e, 0), e], segments, strict=True):
            if v:
                ax.bar(
                    x,
                    v,
                    width=0.7,
                    bottom=bottom,
                    color=colour,
                    edgecolor=fs.EDGE,
                    lw=0.8,
                    hatch=fs.SECOND_MODEL_HATCH if second else None,
                )
                ax.text(
                    x,
                    bottom + v / 2,
                    str(v),
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if colour == fs.OUTCOME["wrong"] else fs.TEXT,
                )
            bottom += v
        ax.text(x, bottom + 0.12, str(a), ha="center", va="bottom", fontsize=10, color=fs.MUTED)
    label_groups(ax, pos, y=-0.25)
    ax.set_ylabel("Tool calls flagged by the monitor")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylim(0, 7.2)
    ax.legend(
        handles=[Patch(facecolor=c, edgecolor=fs.EDGE, label=k) for k, c in segments],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.42),
        ncol=3,
        frameon=False,
    )
    ax.set_title(
        "Attempted, blocked and executed contract violations by arm\n"
        f"{SAMPLE}\nBar height and grey number: attempted calls the monitor flagged.",
        loc="left",
        fontsize=11,
    )
    fig.subplots_adjust(left=0.08, right=0.99, top=0.8, bottom=0.36)
    fs.save(fig, out / "violation_accounting.png")


def completion_by_family(out: Path) -> None:
    families = ["benign", "injection", "forged approval", "transient fault", "ambiguous request"]
    cols = [("live_v3", arm, f"{ARM_LABEL[arm]}\nmini") for arm in ARMS] + [
        ("live_v3_sonnet", arm, f"{ARM_LABEL[arm]}\nsonnet") for arm in ("baseline", "guarded")
    ]
    fig, ax = plt.subplots(figsize=(fs.WIDTH, 4.8))
    for j, (name, arm, _) in enumerate(cols):
        rs = by_arm(load(name))[arm]
        for i, fam in enumerate(families):
            sub = [r for r in rs if FAMILY[r["scenario"]] == fam]
            k, n = sum(r["completed"] for r in sub), len(sub)
            share = k / n
            ax.add_patch(
                plt.Rectangle(
                    (j, i),
                    1,
                    1,
                    facecolor=plt.cm.Blues(0.12 + 0.75 * share),
                    edgecolor="white",
                    linewidth=2,
                )
            )
            ax.text(
                j + 0.5,
                i + 0.5,
                f"{k}/{n}",
                ha="center",
                va="center",
                fontsize=10,
                color="white" if share > 0.55 else fs.TEXT,
            )
    ax.set_xlim(0, len(cols))
    ax.set_ylim(len(families), 0)
    ax.set_xticks([j + 0.5 for j in range(len(cols))], [c[2] for c in cols], fontsize=10)
    ax.set_yticks([i + 0.5 for i in range(len(families))], families)
    ax.axvline(4, color=fs.EDGE, linewidth=1.5)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(
        "Tasks completed by scenario family, live, v2 held-out family heldout3 (10 tasks)\n"
        "mini: gpt-4.1-mini, 2 runs. sonnet: claude-sonnet-4.6, 1 run. "
        "Cell: completed / episodes.",
        loc="left",
        fontsize=11,
    )
    fig.text(0.01, 0.01, KEY, fontsize=10, color=fs.MUTED, va="bottom")
    fig.subplots_adjust(left=0.18, right=0.99, top=0.85, bottom=0.25)
    fs.save(fig, out / "v2_completion_by_fault_family.png")


MODES = [
    ("completed", fs.OUTCOME["correct"]),
    ("finished, task incomplete", fs.OUTCOME["wrong"]),
    ("read a guessed path, then abstained", fs.OUTCOME["failed"]),
    ("abstained for another reason", fs.OUTCOME["abstained"]),
]


def failure_mode(name: str, family: str, r: dict) -> str:
    if r["completed"]:
        return "completed"
    ev = events(name, family, r["arm"], r["task_id"], r["run"])
    guessed = any(e["tool"] == "read_document" and e["error"] == "not_found" for e in ev)
    if r["status"] == "abstained":
        return "read a guessed path, then abstained" if guessed else "abstained for another reason"
    return "finished, task incomplete"


def agent_failure_modes(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(fs.WIDTH, 5.8))
    pos = layout(("guarded", "naive"))
    for x, name, family, second, arm in pos:
        counts: dict[str, int] = defaultdict(int)
        for r in by_arm(load(name))[arm]:
            counts[failure_mode(name, family, r)] += 1
        bottom = 0
        for mode, colour in MODES:
            v = counts[mode]
            if v:
                ax.bar(
                    x,
                    v,
                    width=0.7,
                    bottom=bottom,
                    color=colour,
                    edgecolor=fs.EDGE,
                    lw=0.8,
                    hatch=fs.SECOND_MODEL_HATCH if second else None,
                )
                ax.text(
                    x,
                    bottom + v / 2,
                    str(v),
                    ha="center",
                    va="center",
                    fontsize=10,
                    color=fs.TEXT if mode == "abstained for another reason" else "white",
                )
            bottom += v
    label_groups(ax, pos, y=-0.25)
    ax.set_ylabel("Episodes")
    ax.set_ylim(0, 21)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(
        handles=[Patch(facecolor=c, edgecolor=fs.EDGE, label=k) for k, c in MODES],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.42),
        ncol=2,
        frameon=False,
    )
    ax.set_title(
        "Agent episode outcomes before (v1) and after (v2) the search-first rule\n" f"{SAMPLE}",
        loc="left",
        fontsize=11,
    )
    fig.subplots_adjust(left=0.08, right=0.99, top=0.82, bottom=0.4)
    fs.save(fig, out / "agent_failure_modes.png")


def main(argv: list[str] | None = None) -> list[Path]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "figures")
    args = parser.parse_args(argv)
    fs.apply()
    args.out.mkdir(parents=True, exist_ok=True)
    completion_and_violations(args.out)
    violation_accounting(args.out)
    completion_by_family(args.out)
    agent_failure_modes(args.out)
    return [args.out / f for f in FIGURES]


if __name__ == "__main__":
    for p in main():
        print(f"wrote {p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p}")
