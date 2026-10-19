#!/usr/bin/env python3
"""Plot the archived, deterministically selected WWW trace without new analysis.

    python3 scripts/plot_www_control_trace.py

Input embeds all original tick observations and an independently validated
per-tick decoding. Colours depict realised outcomes, not certification status.
Action letters identify actual configuration changes; dots denote holds.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
ARM_ORDER = ["reactive", "fixed_lead", "lookahead"]
ARM_LABELS = ["Reactive", "Fixed lead", "Lookahead"]
COLOURS = {"met": "#d9ead3", "obligation": "#fce5cd", "hard": "#f4cccc"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=ROOT / "docs/reproduce/www_control_trace_20261019.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "paper_www2027/figures/control_trace.pdf")
    args = parser.parse_args()
    result = json.loads(args.input.read_text())
    if result["schema"] != "www.control_trace/1":
        raise ValueError("Unexpected trace schema")
    for arm in ARM_ORDER:
        if not result["arms"][arm]["score_validation"]["matched_archived_report"]:
            raise ValueError("Plot requires scores validated against archived reports")

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 7,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    fig = plt.figure(figsize=(3.35, 2.1), dpi=250)
    ax = fig.add_axes([0.275, 0.47, 0.705, 0.42])
    letters = {}
    next_letter = ord("a")
    ylabels = []
    for i, arm in enumerate(ARM_ORDER):
        row_y = 2 - i
        data = result["arms"][arm]
        ylabels.append(f"{ARM_LABELS[i]} {data['score_validation']['met']}/12")
        for tick in data["decoded_ticks"]:
            status = "met" if tick["satisfied"] else ("obligation" if tick["hard_sla"] else "hard")
            ax.add_patch(Rectangle((tick["tick"], row_y), 1, 0.8,
                                   facecolor=COLOURS[status], edgecolor="white", linewidth=0.6))
            if tick["action"] == "hold":
                ax.plot(tick["tick"] + 0.5, row_y + 0.4, ".", color="#555555", markersize=2.0)
            else:
                letter = chr(next_letter)
                next_letter += 1
                letters[letter] = tick["action"]
                ax.text(tick["tick"] + 0.5, row_y + 0.4, letter,
                        ha="center", va="center", fontsize=7.5, fontweight="bold")
    # The printed action key is intentionally tied to the stored case; fail
    # rather than silently reuse its labels if a different case is supplied.
    expected = {
        "a": "route query P3->P1", "b": "cap backup 1.02 Mbps",
        "c": "cap query 1.82 Mbps", "d": "cap backup 4.39 Mbps",
        "e": "route backup P3->P1",
    }
    if letters != expected:
        raise ValueError("Stored actions differ from this figure's explicit key")
    _, start, end, _ = result["matching"]["maintenance"]
    ax.add_patch(Rectangle((start, 3.07), end - start, 0.14,
                           facecolor="#777777", edgecolor="none"))
    ax.text((start + end) / 2, 3.32, "event", ha="center", va="bottom", fontsize=7)
    ax.axvline(2, color="#555555", linewidth=0.65, linestyle=(0, (2, 2)), ymax=0.88)
    ax.text(2, 3.32, "notice", ha="center", va="bottom", fontsize=7)
    ax.set_xlim(0, 12)
    ax.set_ylim(-0.1, 3.85)
    ax.set_xticks([t + 0.5 for t in range(12)], [str(t) for t in range(12)])
    ax.set_yticks([2.4, 1.4, 0.4], ylabels)
    ax.tick_params(axis="both", length=0, pad=2, labelsize=7)
    ax.set_xlabel("Control tick", fontsize=7, labelpad=2)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.02, 0.96, "Realised outcomes; satisfied ticks / 12", fontsize=7.2, va="top")
    legend_handles = [
        Patch(facecolor=COLOURS["met"], label="All met"),
        Patch(facecolor=COLOURS["obligation"], label="QoS fail"),
        Patch(facecolor=COLOURS["hard"], label="Hard SLA fail"),
    ]
    fig.legend(handles=legend_handles, loc="center", bbox_to_anchor=(0.50, 0.285),
               ncol=3, frameon=False, fontsize=7, handlelength=0.9,
               handletextpad=0.35, columnspacing=0.9, borderaxespad=0)
    fig.text(0.02, 0.19, "a: Q to P1    b: cap B 1.02", fontsize=7)
    fig.text(0.02, 0.115, "c: cap Q 1.82    d: cap B 4.39    e: B to P1", fontsize=7)
    fig.text(0.02, 0.04, "Q=query; B=backup; caps in Mbps; dot=hold", fontsize=7)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, metadata={
        "Title": "Archived control trace: deterministic intervention case",
        "Author": "",
        "Subject": result["world"],
        "CreationDate": datetime.now(timezone.utc),
    })
    plt.close(fig)
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
