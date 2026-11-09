"""Draw the README figures from saved run outputs only.

Reads reports/live (v1 pilot 1), reports/live_v2 (v1 pilot 2),
reports/live_v3 (v2 round, gpt-4.1-mini) and reports/live_v3_sonnet (v2
round, second model): task_results.jsonl, the saved event logs and the saved
traces. Writes each figure to docs/figures/ as SVG and as a 300 dpi PNG, plus
figure_stats.json with every plotted value and interval. No model is called
and nothing is estimated; running it twice gives byte-identical files.

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
import numpy as np  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# (output dir, task family, label, second model)
ROUNDS = [
    ("live", "heldout", "v1 pilot 1", False),
    ("live_v2", "heldout2", "v1 pilot 2", False),
    ("live_v3", "heldout3", "v2", False),
    ("live_v3_sonnet", "heldout3", "v2", True),
]
ARMS = ["baseline", "single_call", "guarded", "naive"]
ARM_TYPE = {
    "baseline": "baseline",
    "single_call": "single",
    "guarded": "agent",
    "naive": "ablation",
}
ARM_NAME = {
    "baseline": "Rule-based (no model)",
    "single_call": "Single call",
    "guarded": "Guarded agent",
    "naive": "Naive agent",
}
FAMILY = {
    "injected_export": "Injection",
    "injected_read": "Injection",
    "path_traversal": "Injection",
    "alias_escape": "Injection",
    "forged_approval": "Forged approval",
    "timeout_after_commit": "Transient fault",
    "timeout_before_commit": "Transient fault",
    "rate_limit": "Transient fault",
    "duplicate_search": "Transient fault",
    "ambiguous": "Ambiguous request",
    "benign": "Benign",
}
STEMS = ["fig1_main", "figS1_failure_modes", "figS2_tokens"]
STATS: dict[str, dict] = {}


def load(name: str) -> list[dict]:
    path = ROOT / "reports" / name / "task_results.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def events(name: str, family: str, arm: str, task: str, run: int) -> list[dict]:
    path = ROOT / "reports" / name / "events" / family / arm / f"{task}_run{run}.jsonl"
    return [e for e in read_jsonl(path) if "header" not in e]


def episode_tokens(name: str, family: str, arm: str, task: str, run: int) -> int:
    """Prompt plus completion tokens of one episode; trace counts are cumulative."""
    path = ROOT / "reports" / name / "traces" / family / arm / f"{task}_run{run}.jsonl"
    steps = [e for e in read_jsonl(path) if e.get("event") == "step" and e.get("tokens")]
    last = steps[-1]["tokens"]
    return int(last["prompt"] + last["completion"])


def by_arm(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["arm"]].append(r)
    return out


def rate(rows: list[dict], key: str, tag: str) -> tuple[float, float, float]:
    k = sum(int(bool(r[key])) for r in rows)
    n = len(rows)
    p, lo, hi = fs.wilson(k, n)
    STATS[tag] = {"k": k, "n": n, "rate": p, "wilson95": [lo, hi]}
    return p, lo, hi


def per_task(rows: list[dict], key: str) -> dict[str, float]:
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        acc[r["task_id"]].append(float(bool(r[key])))
    return {t: float(np.mean(v)) for t, v in acc.items()}


def paired(rows_a: list[dict], rows_b: list[dict], key: str, tag: str) -> None:
    a, b = per_task(rows_a, key), per_task(rows_b, key)
    tasks = sorted(set(a) & set(b))
    m, lo, hi = fs.paired_bootstrap([a[t] for t in tasks], [b[t] for t in tasks])
    STATS[tag] = {"mean_difference": m, "bootstrap95": [lo, hi], "n_tasks": len(tasks)}


def unit_axis(ax, axis: str = "x", step: float = 0.5) -> None:
    ticks = list(np.arange(0, 1 + 1e-9, step))
    names = [f"{t:g}" for t in ticks]
    if axis == "x":
        ax.set_xlim(-0.04, 1.04)
        ax.spines["bottom"].set_bounds(0, 1)
        ax.set_xticks(ticks, names)
        ax.xaxis.set_minor_locator(MultipleLocator(0.1))
    else:
        ax.set_ylim(-0.04, 1.04)
        ax.spines["left"].set_bounds(0, 1)
        ax.set_yticks(ticks, names)


# Figure 1 -------------------------------------------------------------------


def rate_rows() -> list[tuple[float, str, str, bool, str]]:
    """Rows for the arm x round dot plots: (y, arm, dir, second model, sub-label)."""
    rows, y = [], 0.0
    for arm in ARMS:
        for name, _, label, second in ROUNDS:
            if arm in ("single_call", "naive") and second:
                continue
            rows.append((y, arm, name, second, "v2, Sonnet" if second else label))
            y += 1
        y += 0.6
    return rows


def panel_tradeoff(fig, data, top: float) -> None:
    metrics = [
        ("completed", "Tasks completed", 1.0),
        ("executed_violations", "Executed forbidden\neffect", 0.5),
    ]
    rows = rate_rows()
    height = (rows[-1][0] + 1) * 2.75
    left0, width, gap = 41.0, 28.0, 5.0
    for j, (key, title, xmax) in enumerate(metrics):
        ax = fs.axes_mm(fig, left0 + j * (width + gap), top, width, height)
        for y, arm, name, second, _ in rows:
            tag = "completion" if key == "completed" else "executed_episodes"
            p, lo, hi = rate(data[name][arm], key, f"{tag}/{name}/{arm}")
            fs.dot_interval(ax, y, p, lo, hi, ARM_TYPE[arm], second)
        ax.set_xlim(-0.04 * xmax, xmax * 1.04)
        ax.spines["bottom"].set_bounds(0, xmax)
        ax.set_xticks([0, xmax / 2, xmax])
        ax.xaxis.set_minor_locator(MultipleLocator(0.1 if xmax == 1 else 0.05))
        fs.tidy_ticks(ax, "x")
        ax.set_ylim(rows[-1][0] + 0.6, -0.6)
        ax.set_yticks([r[0] for r in rows])
        if j == 0:
            ax.set_yticklabels([r[4] for r in rows])
            for arm in ARMS:
                ys = [r[0] for r in rows if r[1] == arm]
                ax.text(
                    -0.62,
                    (ys[0] + ys[-1]) / 2,
                    ARM_NAME[arm].replace(" (", "\n("),
                    ha="right",
                    va="center",
                    transform=ax.get_yaxis_transform(),
                )
        else:
            ax.set_yticklabels([])
        ax.set_title(title)
        ax.set_xlabel("Fraction of episodes")
    fs.panel_label(fig, "a", 1.5, top - 6.0)


def panel_accounting(fig, data, top: float) -> None:
    rows, labels, heads, y = [], [], [], 0.0
    for arm in ARMS:
        heads.append((y, ARM_NAME[arm]))
        y += 1.0
        for name, _, label, second in ROUNDS:
            if arm in data[name]:
                rows.append((y, name, arm))
                labels.append("v2, Sonnet" if second else label)
                y += 1
        y += 0.35
    left, width, height = 143.0, 36.0, 58.0
    ax = fs.axes_mm(fig, left, top, width, height)
    segs = [
        ("correct", "Blocked by the gate"),
        ("partial", "Not blocked, no effect"),
        ("wrong", "Executed"),
    ]
    for yy, name, arm in rows:
        rs = data[name][arm]
        a = sum(r["attempted_violation_calls"] for r in rs)
        b = sum(r["blocked_calls"] for r in rs)
        e = sum(r["executed_violations"] for r in rs)
        STATS[f"violation_calls/{name}/{arm}"] = {"attempted": a, "blocked": b, "executed": e}
        start = 0
        for v, (key, _) in zip([b, max(a - b - e, 0), e], segs, strict=True):
            if v:
                ax.barh(
                    yy,
                    v,
                    left=start,
                    height=0.66,
                    color=fs.OUTCOME[key],
                    edgecolor="white",
                    linewidth=0.4,
                )
            start += v
        ax.text(start + 0.15, yy, str(a), va="center", ha="left", color=fs.MUTED)
    for yy, txt in heads:
        ax.text(
            -0.95,
            yy,
            txt,
            va="center",
            ha="left",
            color=fs.MUTED,
            transform=ax.get_yaxis_transform(),
        )
    ax.set_yticks([r[0] for r in rows], labels)
    ax.set_ylim(y - 0.35 - 0.4, -0.6)
    ax.set_xlim(0, 7)
    ax.xaxis.set_major_locator(MultipleLocator(2))
    ax.xaxis.set_minor_locator(MultipleLocator(1))
    ax.set_xlabel("Flagged tool calls (count)")
    key = fs.axes_mm(fig, left - 34, top + height + 9.0, width + 34, 4)
    key.set_axis_off()
    key.legend(
        handles=fs.outcome_handles(segs),
        loc="center",
        ncol=3,
        handlelength=1.0,
        columnspacing=0.8,
        handletextpad=0.3,
    )
    fs.panel_label(fig, "b", 103.0, top - 3.0)


def panel_families(fig, data, top: float) -> None:
    cols = [("live_v3", arm, ARM_NAME[arm].replace(" (no model)", "")) for arm in ARMS]
    cols += [
        ("live_v3_sonnet", arm, ARM_NAME[arm].replace(" (no model)", ""))
        for arm in ("baseline", "guarded")
    ]
    fams = sorted(set(FAMILY.values()))
    totals = {}
    for fam in fams:
        k = n = 0
        for name, arm, _ in cols:
            sub = [r for r in data[name][arm] if FAMILY[r["scenario"]] == fam]
            k += sum(r["completed"] for r in sub)
            n += len(sub)
        totals[fam] = (k, n)
    fams.sort(key=lambda f: (-totals[f][0] / totals[f][1], f))
    tile_w, tile_h, left = 8.6, 5.6, 33.0
    width, height = len(cols) * tile_w, len(fams) * tile_h
    ax = fs.axes_mm(fig, left, top + 8.0, width, height)
    fs.bare(ax)
    for j, (name, arm, _) in enumerate(cols):
        for i, fam in enumerate(fams):
            sub = [r for r in data[name][arm] if FAMILY[r["scenario"]] == fam]
            k, n = sum(r["completed"] for r in sub), len(sub)
            share = k / n
            STATS[f"completion_by_family/{name}/{arm}/{fam}"] = {"k": k, "n": n}
            ax.add_patch(
                fs.plt.Rectangle(
                    (j * tile_w, i * tile_h),
                    tile_w,
                    tile_h,
                    facecolor=fs.SEQUENTIAL(share),
                    edgecolor="white",
                    linewidth=0.8,
                )
            )
            ax.text(
                (j + 0.5) * tile_w,
                (i + 0.5) * tile_h,
                f"{k}/{n}",
                ha="center",
                va="center",
                color="white" if share > 0.7 else fs.INK,
            )
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.set_yticks([(i + 0.5) * tile_h for i in range(len(fams))], fams)
    ax.set_xticks(
        [(j + 0.5) * tile_w for j in range(len(cols))],
        [c[2] for c in cols],
        rotation=40,
        ha="right",
        rotation_mode="anchor",
    )
    for txt, a, b in (("gpt-4.1-mini\n2 runs", 0, 4), ("claude-sonnet-4.6\n1 run", 4, 6)):
        ax.plot(
            [a * tile_w + 0.6, b * tile_w - 0.6],
            [-1.2, -1.2],
            color=fs.MUTED,
            linewidth=0.6,
            clip_on=False,
        )
        ax.text((a + b) / 2 * tile_w, -1.9, txt, ha="center", va="bottom", color=fs.MUTED)
    mx = fs.axes_mm(fig, left + width + 2.0, top + 8.0, 12, height)
    for i, fam in enumerate(fams):
        k, n = totals[fam]
        p, lo, hi = fs.wilson(k, n)
        STATS[f"completion_by_family/all_arms/{fam}"] = {
            "k": k,
            "n": n,
            "rate": p,
            "wilson95": [lo, hi],
        }
        mx.plot([lo, hi], [(i + 0.5) * tile_h] * 2, color=fs.MUTED, linewidth=0.8)
        mx.plot(
            [p], [(i + 0.5) * tile_h], marker="o", markersize=3.2, color=fs.INK, linestyle="none"
        )
    mx.set_ylim(height, 0)
    mx.set_yticks([])
    mx.spines["left"].set_visible(False)
    mx.set_xlim(0, 1)
    mx.set_xticks([0, 1])
    fs.tidy_ticks(mx, "x")
    mx.set_xlabel("Completed,\nall columns\n(fraction)")
    fs.panel_label(fig, "c", 1.5, top - 1.5)


def panel_dumbbell(fig, data, top: float) -> None:
    left, width, height = 128.0, 38.0, 36.0
    ax = fs.axes_mm(fig, left, top + 8.0, width, height)
    for i, arm in enumerate(ARMS):
        t = ARM_TYPE[arm]
        p1, lo1, hi1 = rate(data["live_v2"][arm], "completed", f"completion/live_v2/{arm}")
        p2, lo2, hi2 = rate(data["live_v3"][arm], "completed", f"completion/live_v3/{arm}")
        y1, y2 = i - 0.17, i + 0.17
        ax.plot(
            [p1, p2], [y1, y2], color=fs.CONTEXT, linewidth=1.6, zorder=1, solid_capstyle="butt"
        )
        ax.plot([lo1, hi1], [y1, y1], color=fs.MUTED, linewidth=0.7, zorder=2)
        ax.plot([lo2, hi2], [y2, y2], **fs.line_kw(t, width=0.7))
        grey = fs.marker_kw(t)
        grey.update(markerfacecolor=fs.CONTEXT, markeredgecolor=fs.MUTED)
        ax.plot([p1], [y1], **{**grey, "clip_on": False})
        ax.plot([p2], [y2], **{**fs.marker_kw(t), "clip_on": False})
        k1 = STATS[f"completion/live_v2/{arm}"]["k"]
        k2 = STATS[f"completion/live_v3/{arm}"]["k"]
        ax.text(
            1.06,
            i,
            f"{k1} to {k2}",
            transform=ax.get_yaxis_transform(),
            va="center",
            ha="left",
            color=fs.MUTED,
        )
        paired(
            data["live_v3"][arm],
            data["live_v3"]["baseline"],
            "completed",
            f"paired_vs_rule_based/live_v3/{arm}",
        )
    paired(
        data["live_v3_sonnet"]["guarded"],
        data["live_v3_sonnet"]["baseline"],
        "completed",
        "paired_vs_rule_based/live_v3_sonnet/guarded",
    )
    ax.set_yticks(range(len(ARMS)), [ARM_NAME[a] for a in ARMS])
    ax.set_ylim(len(ARMS) - 0.5, -0.5)
    unit_axis(ax, "x", 0.25)
    fs.tidy_ticks(ax, "x")
    ax.set_xlabel("Tasks completed (fraction)")
    ax.text(
        0,
        1.03,
        "Upper: v1 pilot 2 (grey). Lower: v2.",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        color=fs.MUTED,
    )
    fs.panel_label(fig, "d", 103.0, top - 1.5)


def figure1(data, out: Path) -> list[Path]:
    fig = fs.figure(fs.FULL_WIDTH, 140)
    panel_tradeoff(fig, data, top=9.0)
    panel_accounting(fig, data, top=6.0)
    panel_families(fig, data, top=85.0)
    panel_dumbbell(fig, data, top=85.0)
    return fs.save(fig, out, "fig1_main")


# Supplementary --------------------------------------------------------------

MODES = [
    ("correct", "Completed"),
    ("wrong", "Finished, task incomplete"),
    ("failed", "Read a guessed path, then abstained"),
    ("abstained", "Abstained for another reason"),
]


def failure_mode(name: str, family: str, r: dict) -> str:
    if r["completed"]:
        return "correct"
    ev = events(name, family, r["arm"], r["task_id"], r["run"])
    guessed = any(e["tool"] == "read_document" and e["error"] == "not_found" for e in ev)
    if r["status"] == "abstained":
        return "failed" if guessed else "abstained"
    return "wrong"


def agent_rows(data) -> list[tuple[float, str, str, str, bool, str, str | None]]:
    """(y, dir, family, arm, second, arm label, group header or None)."""
    rows, y = [], 0.0
    for name, family, label, second in ROUNDS:
        model = "claude-sonnet-4.6, 1 run" if second else "gpt-4.1-mini, 2 runs"
        first = True
        for arm in ("guarded", "naive"):
            if arm in data[name]:
                head = f"{label}, {model}" if first else None
                if first:
                    y += 1.0
                rows.append((y, name, family, arm, second, ARM_NAME[arm], head))
                first = False
                y += 1
        y += 0.3
    return rows


def figure_s1(data, out: Path) -> list[Path]:
    rows = agent_rows(data)
    fig = fs.figure(fs.FULL_WIDTH, 62)
    height = (rows[-1][0] + 0.7) * 3.4
    ax = fs.axes_mm(fig, 40, 5, 92, height)
    cx = fs.axes_mm(fig, 138, 5, 28, height)
    for a in (ax, cx):
        a.set_ylim(rows[-1][0] + 0.6, -0.4)
    for y, name, family, arm, second, _, head in rows:
        rs = data[name][arm]
        counts: dict[str, int] = defaultdict(int)
        for r in rs:
            counts[failure_mode(name, family, r)] += 1
        STATS[f"agent_outcomes/{name}/{arm}"] = dict(counts) | {"n": len(rs)}
        start = 0.0
        for key, _ in MODES:
            c = counts[key]
            if c:
                ax.barh(
                    y,
                    c / len(rs),
                    left=start,
                    height=0.62,
                    color=fs.OUTCOME[key],
                    edgecolor="white",
                    linewidth=0.4,
                )
                ax.text(
                    start + c / len(rs) / 2,
                    y,
                    str(c),
                    ha="center",
                    va="center",
                    color=fs.OUTCOME_TEXT[key],
                )
            start += c / len(rs)
        if second:
            ax.add_patch(
                fs.plt.Rectangle(
                    (0, y - 0.31),
                    1,
                    0.62,
                    fill=False,
                    edgecolor=fs.INK,
                    linewidth=0.75,
                    linestyle=(0, (2.2, 1.2)),
                    zorder=3,
                )
            )
        p, lo, hi = fs.wilson(counts["correct"], len(rs))
        fs.dot_interval(cx, y, p, lo, hi, ARM_TYPE[arm], second)
        cx.text(
            1.08, y, f"n = {len(rs)}", va="center", ha="left", transform=cx.get_yaxis_transform()
        )
        if head:
            ax.text(
                -0.39,
                y - 1.0,
                head,
                va="center",
                ha="left",
                color=fs.MUTED,
                transform=ax.get_yaxis_transform(),
            )
    ax.set_yticks([r[0] for r in rows], [r[5] for r in rows])
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    unit_axis(ax, "x", 0.25)
    fs.tidy_ticks(ax, "x")
    ax.set_xlabel("Episodes (fraction)")
    cx.set_yticks([])
    cx.spines["left"].set_visible(False)
    unit_axis(cx, "x", 0.5)
    cx.set_xlabel("Completed (fraction)")
    fs.panel_label(fig, "a", 1.5, 1.5)
    fs.panel_label(fig, "b", 133.0, 1.5)
    key = fs.axes_mm(fig, 0, 5 + height + 11, fs.FULL_WIDTH, 4)
    key.set_axis_off()
    key.legend(
        handles=fs.outcome_handles(MODES), loc="center", ncol=4, handlelength=1.0, columnspacing=1.2
    )
    return fs.save(fig, out, "figS1_failure_modes")


def figure_s2(data, out: Path) -> list[Path]:
    rows = [(r[0] * 1.3, *r[1:]) for r in agent_rows(data)]
    fig = fs.figure(fs.FULL_WIDTH, 90)
    height = (rows[-1][0] + 1.3) * 5.0
    ax = fs.axes_mm(fig, 40, 3, 112, height)
    ax.set_ylim(rows[-1][0] + 0.75, -0.75)
    for y, name, family, arm, second, _, head in rows:
        vals = [
            episode_tokens(name, family, arm, r["task_id"], r["run"]) / 1000
            for r in sorted(data[name][arm], key=lambda r: (r["task_id"], r["run"]))
        ]
        q1, med, q3 = np.percentile(vals, [25, 50, 75])
        STATS[f"tokens_per_episode_thousands/{name}/{arm}"] = {
            "n": len(vals),
            "median": float(med),
            "q1": float(q1),
            "q3": float(q3),
            "min": float(min(vals)),
            "max": float(max(vals)),
        }
        fs.raincloud(ax, y, vals, ARM_TYPE[arm], second)
        if head:
            ax.text(
                -0.32,
                y - 1.3 * 0.85,
                head,
                va="center",
                ha="left",
                color=fs.MUTED,
                transform=ax.get_yaxis_transform(),
            )
    ax.set_yticks([r[0] for r in rows], [r[5] for r in rows])
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 35)
    ax.xaxis.set_major_locator(MultipleLocator(5))
    ax.xaxis.set_minor_locator(MultipleLocator(1))
    ax.set_xlabel("Model tokens per episode (thousands)")
    return fs.save(fig, out, "figS2_tokens")


def main(argv: list[str] | None = None) -> list[Path]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "figures")
    args = parser.parse_args(argv)
    fs.apply()
    data = {name: by_arm(load(name)) for name, *_ in ROUNDS}
    paths = figure1(data, args.out)
    paths += figure_s1(data, args.out)
    paths += figure_s2(data, args.out)
    stats = args.out / "figure_stats.json"
    rounded = {
        k: {f: round(v, 4) if isinstance(v, float) else v for f, v in d.items()}
        for k, d in sorted(STATS.items())
    }
    for d in rounded.values():
        for f, v in d.items():
            if isinstance(v, list):
                d[f] = [round(x, 4) for x in v]
    meta = {
        "interval": "Wilson 95% for rates; paired percentile bootstrap 95% for differences",
        "bootstrap_resamples": fs.N_BOOT,
        "seed": fs.SEED,
        "resampling_unit": "task",
    }
    text = json.dumps({"method": meta, "values": rounded}, indent=1)
    stats.write_text(text + "\n", encoding="utf-8")
    return paths + [stats]


if __name__ == "__main__":
    for p in main():
        print(f"wrote {p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p}")
