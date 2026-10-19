#!/usr/bin/env python3
"""Post-hoc provider sensitivity from frozen WWW summary counts only.

No model calls, simulations, raw-label edits, resampling, or significance tests.
The fixed input hashes intentionally reject even a changed source-table layout;
review a source change before creating a separately dated analysis.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import statistics


ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "t1": {
        "path": "paper_www2027/tables/per_provider_body.tex",
        "sha256": "548d5c31bc17a42d59d9c1703352dc7622a3a3865b324aa296f1726c923782c9",
    },
    "t3": {
        "path": "docs/reproduce/sota_baselines_20261015_affected_comparison.json",
        "sha256": "e0d3c2566608743f90c82b7a6f87966933c68e498b59e42fbbd3beb02838ded0",
    },
}
OUTPUT_STEM = ROOT / "docs/reproduce/www_provider_sensitivity_20261019"
FIGURE = ROOT / "paper_www2027/figures/provider_sensitivity.pdf"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_inputs() -> tuple[list[dict], list[dict]]:
    for spec in INPUTS.values():
        actual = digest(ROOT / spec["path"])
        if actual != spec["sha256"]:
            raise ValueError(f"Frozen input changed: {spec['path']} ({actual})")
    t1 = []
    row = re.compile(
        r"^([a-z0-9.]+)\s*&\s*(\d+)\s*&\s*(\d+)\s*&\s*(\d+)"
        r"\s*&\s*(\d+)\s*&\s*(\d+)\s*&\s*(\d+)\s*\\\\\s*$"
    )
    for line in (ROOT / INPUTS["t1"]["path"]).read_text().splitlines():
        match = row.match(line)
        if not match or match[1] == "all":
            continue
        n, rule, sutime, _heidel, qwen7b, _qwen72b = map(int, match.groups()[1:])
        t1.append(dict(provider=match[1], n=n, qwen7b=qwen7b, rule=rule, sutime=sutime))
    if len(t1) != 15 or sum(x["n"] for x in t1) != 240:
        raise ValueError("T1 parsing did not recover the frozen 15-provider, 240-item table")
    for arm, total in (("qwen7b", 173), ("rule", 154), ("sutime", 105)):
        if sum(x[arm] for x in t1) != total:
            raise ValueError(f"T1 total mismatch: {arm}")

    t3_source = json.loads((ROOT / INPUTS["t3"]["path"]).read_text())
    qsites = t3_source["arms"]["qwen7b_extract"]["by_site"]
    rsites = t3_source["arms"]["rapidfuzz"]["by_site"]
    if qsites.keys() != rsites.keys():
        raise ValueError("T3 provider sets differ between the paired methods")
    t3 = []
    for provider, fraction in qsites.items():
        qwen, n = map(int, fraction.split("/"))
        rapidfuzz, other_n = map(int, rsites[provider].split("/"))
        if n != other_n:
            raise ValueError(f"T3 denominator mismatch: {provider}")
        t3.append(dict(provider=provider.removeprefix("status."), n=n,
                       qwen7b=qwen, rapidfuzz=rapidfuzz))
    t3.sort(key=lambda r: (-r["n"], r["provider"]))
    if len(t3) != 5 or sum(x["n"] for x in t3) != t3_source["n"]:
        raise ValueError("T3 parsing did not recover the frozen provider/item totals")
    for arm, original in (("qwen7b", "qwen7b_extract"), ("rapidfuzz", "rapidfuzz")):
        if sum(x[arm] for x in t3) != t3_source["arms"][original]["resolved"]:
            raise ValueError(f"T3 resolved total mismatch: {arm}")
    return t1, t3


def summarize(rows: list[dict], baseline: str, task: str) -> dict:
    n = sum(r["n"] for r in rows)
    qtotal = sum(r["qwen7b"] for r in rows)
    btotal = sum(r[baseline] for r in rows)
    provider_rows, omissions = [], []
    for r in rows:
        if not 0 <= r["qwen7b"] <= r["n"] or not 0 <= r[baseline] <= r["n"]:
            raise ValueError("Invalid source count")
        delta_count = r["qwen7b"] - r[baseline]
        provider_rows.append(dict(provider=r["provider"], n=r["n"],
                                  qwen7b_correct=r["qwen7b"], baseline_correct=r[baseline],
                                  qwen7b_rate=r["qwen7b"] / r["n"],
                                  baseline_rate=r[baseline] / r["n"],
                                  delta_count=delta_count, delta=delta_count / r["n"]))
        omissions.append(dict(omitted_provider=r["provider"], omitted_n=r["n"],
                              remaining_n=n-r["n"],
                              remaining_qwen7b_correct=qtotal-r["qwen7b"],
                              remaining_baseline_correct=btotal-r[baseline],
                              delta=(qtotal-btotal-delta_count) / (n-r["n"])))
    minimum = min(x["delta"] for x in omissions)
    maximum = max(x["delta"] for x in omissions)
    return dict(task=task, comparator=baseline, n=n, providers=len(rows),
                qwen7b_correct=qtotal, baseline_correct=btotal,
                micro_qwen7b=qtotal/n, micro_baseline=btotal/n,
                micro_delta=(qtotal-btotal)/n,
                macro_qwen7b=statistics.mean(r["qwen7b_rate"] for r in provider_rows),
                macro_baseline=statistics.mean(r["baseline_rate"] for r in provider_rows),
                macro_delta=statistics.mean(r["delta"] for r in provider_rows),
                provider_signs=dict(positive=sum(r["delta_count"] > 0 for r in provider_rows),
                                    zero=sum(r["delta_count"] == 0 for r in provider_rows),
                                    negative=sum(r["delta_count"] < 0 for r in provider_rows)),
                per_provider=provider_rows, leave_one_provider_out=omissions,
                leave_one_provider_out_range=dict(
                    min=minimum, max=maximum,
                    min_omitted_providers=[r["omitted_provider"] for r in omissions if r["delta"] == minimum],
                    max_omitted_providers=[r["omitted_provider"] for r in omissions if r["delta"] == maximum]))


def pp(value: float, decimals: int = 2) -> str:
    return f"{100*value:+.{decimals}f}"


def markdown(result: dict) -> str:
    lines = [
        "# WWW provider sensitivity: post-hoc descriptive analysis (2026-10-19)", "",
        "This analysis reuses frozen summary counts only. It makes no model calls or simulations, changes no labels or predictions, and adds no bootstrap or significance test. All differences below are Qwen2.5-7B extract minus the named comparator, in percentage points (pp).", "",
        "## Inputs and scoring", "",
        "T1 retains the published available-boundary score: start and end where both references exist, otherwise start only (240 notices, 15 providers). T3 retains the resolved-region score (805 notices, 5 providers). Exact paired-item wins/losses cannot be recovered from marginal provider counts and are not inferred here.", "",
    ]
    for key, spec in result["inputs"].items():
        lines += [f"- {key}: `{spec['path']}`, SHA-256 `{spec['sha256']}`."]
    lines += ["", f"Analysis script SHA-256: `{result['script_sha256']}`.", "",
              "## Definitions", "",
              "- Micro: sum of correct-count differences divided by all notices; this is the original notice-weighted estimand.",
              "- Macro: unweighted mean of each provider's accuracy difference; every provider has equal weight, including providers with one notice.",
              "- Leave one provider out: remove each provider once without retraining or re-prompting, then recompute the remaining micro difference. The range is descriptive, not a confidence interval, new cross-validation, or a statistical test.", "",
              "## Summary", "",
              "| Task and comparator | n / providers | Micro delta (pp) | Macro delta (pp) | Provider signs + / 0 / - | Omit-one range (pp) | Minimum after omitting | Maximum after omitting |",
              "|---|---:|---:|---:|---:|---:|---|---|"]
    for c in result["comparisons"]:
        limits = c["leave_one_provider_out_range"]
        signs = c["provider_signs"]
        lines.append(f"| {c['task']} vs {c['comparator']} | {c['n']} / {c['providers']} | {pp(c['micro_delta'])} | {pp(c['macro_delta'])} | {signs['positive']} / {signs['zero']} / {signs['negative']} | {pp(limits['min'])} to {pp(limits['max'])} | {', '.join(limits['min_omitted_providers'])} | {', '.join(limits['max_omitted_providers'])} |")
    for c in result["comparisons"]:
        lines += ["", f"## {c['task']}: 7B extract minus {c['comparator']}", "",
                  "All providers and all omission results are shown; positive, zero, and negative rows are retained.", "",
                  "| Provider | n | 7B correct | Comparator correct | Provider delta (pp) | Remaining n if omitted | Remaining micro delta (pp) |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        omissions = {r["omitted_provider"]: r for r in c["leave_one_provider_out"]}
        for r in c["per_provider"]:
            omit = omissions[r["provider"]]
            lines.append(f"| {r['provider']} | {r['n']} | {r['qwen7b_correct']} | {r['baseline_correct']} | {pp(r['delta'])} | {omit['remaining_n']} | {pp(omit['delta'])} |")
    lines += ["", "## Interpretation and limitations", "",
              "The summaries assess dependence on the observed provider composition. A positive omission result is not evidence that the difference is statistically nonzero or generalizes to an unseen provider. Macro averages can be sensitive to tiny providers, so n is always displayed. The T1 and T3 populations and scoring rules differ and are not pooled.", "",
              "The analysis is post-hoc and does not replace the original registered estimates. It cannot validate the automatic timestamp references, selection-derived labels, component references, or the latent correctness of the prose. Provider robustness and reference validity are separate questions.", "",
              "## Validation and outputs", "",
              "The script checks exact source hashes, provider counts, denominators, count bounds, and original aggregate totals before writing outputs. The figure is designed for full text width (7.0 inches), with all plotted text at least 8 pt at native size; omission ranges are explicitly labelled as non-CIs. Its PDF embeds TrueType fonts and records timezone-aware UTC metadata.", "",
              "Outputs: `docs/reproduce/www_provider_sensitivity_20261019.json`, this report, and `paper_www2027/figures/provider_sensitivity.pdf`.", ""]
    return "\n".join(lines)


def plot(result: dict, png: Path | None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "axes.labelsize": 8, "xtick.labelsize": 8,
                         "ytick.labelsize": 8, "pdf.fonttype": 42, "ps.fonttype": 42,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False})
    blue, orange = "#206095", "#B65D16"
    fig = plt.figure(figsize=(7.0, 3.3))
    left = fig.add_axes([0.19, 0.16, 0.395, 0.69])
    right = fig.add_axes([0.77, 0.59, 0.215, 0.26])
    rule, sutime, rapid = result["comparisons"]

    def name(provider: str) -> str:
        return provider.removesuffix(".com").removesuffix(".net")

    def base_axis(ax, rows, xlim, ticks):
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([f"{name(r['provider'])} ({r['n']})" for r in rows])
        ax.set_ylim(len(rows)-0.45, -0.55)
        ax.set_xlim(*xlim)
        ax.set_xticks(ticks)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:+g}" if x else "0"))
        ax.axvline(0, color="#555555", linewidth=0.7, zorder=0)
        ax.grid(axis="x", color="#E2E2E2", linewidth=0.5)
        ax.tick_params(axis="y", length=0, pad=5)
        ax.tick_params(axis="x", length=2.5, color="#999999")
        ax.spines["bottom"].set_color("#999999")

    base_axis(left, rule["per_provider"], (-105, 105), [-100, -50, 0, 50, 100])
    for comp, offset, color, marker in ((rule, -0.13, blue, "o"), (sutime, 0.13, orange, "^")):
        for i, r in enumerate(comp["per_provider"]):
            x, y = 100*r["delta"], i+offset
            left.plot([0, x], [y, y], color=color, alpha=0.45, linewidth=0.8)
            left.plot(x, y, marker=marker, color=color, markersize=3.5, linestyle="None")
    left.set_xlabel("Accuracy difference (percentage points)", labelpad=4)
    base_axis(right, rapid["per_provider"], (-21, 34), [-20, 0, 20, 30])
    for i, r in enumerate(rapid["per_provider"]):
        x = 100*r["delta"]
        right.plot([0, x], [i, i], color=blue, alpha=0.45, linewidth=0.8)
        right.plot(x, i, "o", color=blue, markersize=3.5)
    right.set_xlabel("Difference (pp)", labelpad=3)

    fig.text(0.19, 0.96, "(a) Announced boundaries (T1)", fontsize=9, weight="bold")
    fig.text(0.65, 0.96, "(b) Affected region (T3)", fontsize=9, weight="bold")
    fig.legend(handles=[Line2D([], [], color=blue, marker="o", linestyle="None", markersize=3.5, label="7B - rule"),
                        Line2D([], [], color=orange, marker="^", linestyle="None", markersize=3.5, label="7B - SUTime")],
               loc="upper left", bbox_to_anchor=(0.18, 0.935), ncol=2, frameon=False,
               fontsize=8, handletextpad=0.3, columnspacing=1.0, borderpad=0)
    fig.text(0.77, 0.90, "7B - rapidfuzz", color=blue, fontsize=8)
    fig.text(0.65, 0.435, "Micro / macro difference (pp)", fontsize=8, weight="bold")
    labels = ["T1 - rule", "T1 - SUTime", "T3 - rapidfuzz"]
    for y, label, comp in zip([0.39, 0.35, 0.31], labels, result["comparisons"]):
        fig.text(0.65, y, f"{label}: {pp(comp['micro_delta'], 1)} / {pp(comp['macro_delta'], 1)}", fontsize=8)
    fig.text(0.65, 0.245, "Omit-one micro range (pp)", fontsize=8, weight="bold")
    for y, label, comp in zip([0.20, 0.16, 0.12], labels, result["comparisons"]):
        limits = comp["leave_one_provider_out_range"]
        fig.text(0.65, y, f"{label}: {pp(limits['min'], 1)} to {pp(limits['max'], 1)}", fontsize=8)
    fig.text(0.02, 0.025, "Provider labels show n. Post-hoc omission ranges are descriptive, not confidence intervals.", fontsize=8)
    timestamp = dt.datetime.fromisoformat(result["generated_at_utc"])
    FIGURE.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE, metadata={"Title": "WWW provider sensitivity (post-hoc)",
                                 "Author": "", "Creator": Path(__file__).name,
                                 "CreationDate": timestamp, "ModDate": timestamp,
                                 "Subject": "Frozen-count provider sensitivity; omission ranges are not confidence intervals."})
    if png:
        png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(png, dpi=200)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", type=Path, help="Optional temporary preview path")
    args = parser.parse_args()
    t1, t3 = checked_inputs()
    result = dict(schema_version=1, analysis_date="2026-10-19", status="post-hoc descriptive sensitivity",
                  generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), inputs=INPUTS,
                  script_sha256=digest(Path(__file__)),
                  definitions=dict(micro="notice-weighted accuracy difference",
                                   macro="equal-provider mean accuracy difference",
                                   omission="remaining micro difference after omitting one provider; not a CI or new CV"),
                  comparisons=[summarize(t1, "rule", "T1"), summarize(t1, "sutime", "T1"),
                               summarize(t3, "rapidfuzz", "T3")])
    plot(result, args.png)
    OUTPUT_STEM.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    OUTPUT_STEM.with_suffix(".md").write_text(markdown(result))
    for c in result["comparisons"]:
        limits = c["leave_one_provider_out_range"]
        print(f"{c['task']} vs {c['comparator']}: micro {pp(c['micro_delta'])} pp; macro {pp(c['macro_delta'])} pp; omit-one {pp(limits['min'])} to {pp(limits['max'])} pp")


if __name__ == "__main__":
    main()
