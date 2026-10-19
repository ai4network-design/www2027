#!/usr/bin/env python3
"""Extract one deterministically selected WWW control trace from existing runs.

Standard-library post-processing only: no runner imports, model calls, simulator
execution, population statistics, or changes to historical runs. Output must be
a fresh directory beneath the selected repository's ignored runs/ directory.

Example on the experiment server (the script may also be supplied on stdin):
    python3 scripts/extract_www_control_trace.py --repo-root /path/to/repo \
        --out-dir /path/to/repo/runs/www_control_trace_20261019

Selection: common 2 Mbps worlds in lexicographic world-ID order, first complete
successful triple with identical event, initial observation, seed and native
binary, identical beliefs in the two 7B arms, no reactive belief, and a recorded
lookahead goal intervention. No score or performance difference selects a case.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ARMS = {
    "reactive": "runs/maintenance_diagnostic_v2_20260930/primary/R0_reactive",
    "fixed_lead": "runs/controls_20261014/closed_loop/C_lead1_qwen7b",
    "lookahead": "runs/converter_fix_20261001/closed_loop/M_qwen7b_extract",
}
CONTRACTS = [
    "contracts/maintenance_diagnostic_v2_20260930.json",
    "contracts/controls_20261014.json",
    "contracts/converter_fix_20261001.json",
]
FROZEN = "contracts/frozen_protocol_20260924_20261008.json"
TOPOLOGY = "contracts/multiservice_comparison_20260911.json"
STEM = "www_control_trace_20261019"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def action_description(row, names):
    before, target = row["before"], row["selected"]["target"]
    changes = []
    for i, name in enumerate(names):
        if before["paths"][i] != target["paths"][i]:
            changes.append(f"route {name} P{before['paths'][i]}->P{target['paths'][i]}")
        if before["caps_bps"][i] != target["caps_bps"][i]:
            cap = target["caps_bps"][i]
            changes.append(f"cap {name} {cap / 1e6:g} Mbps" if cap else f"release {name} cap")
    if len(changes) > 1:
        raise ValueError("Selected target changes more than one field")
    return changes[0] if changes else "hold"


def score_ticks(rows, frozen, topology, report):
    """Decode the frozen metric from saved observations, without calling control code.

    Mirrors maintenance_episode.metric, multiservice_control.sla, and
    frozen_protocol.obligation_outcomes: hard SLA at every tick; QoS clauses
    when unreleased; backup only when after.tick equals the deadline; recovery
    only after its end event. Results must exactly match the archived report.
    """
    names = topology["services"]
    obligations = frozen["base_ledger"]["obligations"]
    epoch = {s["name"]: s["rx_bytes"] for s in rows[0]["before"]["services"]}
    decoded = []
    for row in rows:
        actual = {s["name"]: s for s in row["after"]["services"]}
        hard_parts = {
            name: (actual[name]["throughput_mbps"] >= topology["hard_min_mbps"][i] - 1e-9
                   and actual[name]["delay_ms"] <= topology["hard_delay_ms"][i]
                   and actual[name]["loss"] <= topology["hard_loss_max"])
            for i, name in enumerate(names)
        }
        hard = all(hard_parts.values())
        if hard != row["actual_hard_sla"]:
            raise ValueError("Decoded hard SLA differs from saved actual_hard_sla")
        outcomes = {}
        for name, clause in obligations.items():
            if name.endswith(".qos") and not row["public_events"].get(clause["release_event"], False):
                service = actual[clause["service"]]
                outcomes[name] = not (
                    service["delay_ms"] > clause["delay_ms"] + 1e-9
                    or service["throughput_mbps"] < clause["min_mbps"] - 1e-9
                )
        deadline = obligations["backup.deadline"]
        if row["after"]["tick"] == deadline["deadline_tick"]:
            outcomes["backup.deadline"] = (
                actual["backup"]["rx_bytes"] - epoch["backup"] >= deadline["total_bytes"]
            )
        recovery = obligations["recovery"]
        if (row["public_events"]["ended"]
                and row["tick"] >= frozen["release_tick"] + recovery["max_intervals"] - 1):
            outcomes["recovery"] = not any(row["after"]["caps_bps"])
        decoded.append({
            "tick": row["tick"],
            "action": action_description(row, names),
            "selection_source": row["selection"]["source"],
            "hard_sla": hard,
            "hard_sla_by_service": hard_parts,
            "obligations": outcomes,
            "failed_obligations": [k for k, v in outcomes.items() if not v],
            "satisfied": hard and all(outcomes.values()),
            "goal_intervention": "goal_step" in row["shield"],
        })
    met = sum(t["satisfied"] for t in decoded)
    hard_fail = sum(not t["hard_sla"] for t in decoded)
    stored = report["metric"]
    if (met != stored["met"] or hard_fail != stored["hard_sla_violation_intervals"]
            or stored["intervals"] != 12 or stored["missing"] != 0
            or abs(met / 12 - stored["satisfaction"]) > 1e-12):
        raise ValueError("Decoded tick score does not match archived world report")
    return decoded, {
        "matched_archived_report": True,
        "met": met, "intervals": 12, "satisfaction": stored["satisfaction"],
        "hard_sla_violation_intervals": hard_fail,
        "recovery_ever_active": any("recovery" in t["obligations"] for t in decoded),
    }


def make_report(result):
    lines = [
        "# WWW control trace: deterministic case extraction (2026-10-19)", "",
        "This is a display of archived observations, not a new control experiment. No model or ns-3 calls were made.", "",
        "## Selection fixed before inspecting outcomes", "",
        result["selection_rule"], "",
        f"Selected world: `{result['world']}`. Notice: `{result['notice']}`.",
        f"Event: `{result['matching']['maintenance']}`; seed `{result['matching']['seed']}`.",
        f"Native binary SHA-256: `{result['matching']['binary_sha256']}`.",
        "Reactive receives no belief. Fixed lead and lookahead receive the same stored 7B belief.",
        "The selected case is conditional on a goal intervention and is illustrative; it is not an estimate of prevalence or effect size.", "",
        "## Archived score validation", "",
        "The extractor independently decodes existing observations using the frozen ledger: hard SLA and active QoS at every tick; "
        "the 4,000,000-byte backup deadline only at state tick 8; recovery only if its end event activates. "
        "It compares the decoded satisfied-tick count and hard-violation count to each original report. "
        "No control decision, predictor, simulation, bootstrap, or population estimate is rerun.", "",
        "| Arm | Met ticks / 12 | Satisfaction | Hard-SLA failed ticks | Report match |",
        "|---|---:|---:|---:|---|",
    ]
    for arm, data in result["arms"].items():
        score = data["score_validation"]
        lines.append(f"| {arm} | {score['met']} | {score['satisfaction']:.3f} | "
                     f"{score['hard_sla_violation_intervals']} | yes |")
    lines += ["", "## Actual actions and realised failures", "",
              "Rows are logged executed actions, not invented rejected proposals. A hold is shown when no configuration field changed.", "",
              "| Arm | Tick | Executed action | Source | Hard SLA | Failed active obligations |",
              "|---|---:|---|---|---|---|"]
    for arm, data in result["arms"].items():
        for tick in data["decoded_ticks"]:
            failures = ", ".join(tick["failed_obligations"]) or "none"
            lines.append(f"| {arm} | {tick['tick']} | {tick['action']} | {tick['selection_source']} | "
                         f"{'met' if tick['hard_sla'] else 'failed'} | {failures} |")
    lines += ["", "## Selection audit", "",
              f"{result['selection_audit']['common_worlds']} common world IDs; "
              f"{len(result['selection_audit']['capacity_candidates'])} common 2-Mbps candidates.",
              "Capacity-excluded IDs, every checked candidate, and unexamined candidates after selection are retained in the JSON.", "",
              "## Provenance and reconstruction", "",
              "The JSON embeds all 36 original tick records, the three original world reports, sanitised native launch identities, "
              "frozen scoring parameters, and a SHA-256/byte-count manifest of every input read. "
              "Input paths are repository-relative; private host paths and notice prose are not exported. "
              "Historical contract source pins are retained separately from the hashes of files read for this extraction. "
              "The three archived runs use their registered code versions, including the later fixed-lead wrapper; matching checks "
              "verify shared physical inputs and beliefs, not byte-identical controller implementations.", "",
              "The full embedded tick records make this figure and case score reconstructible from the small Git artifact. "
              "Reselecting the case independently requires the historical run reports identified in the manifest. "
              "Original runs are unchanged; this extraction does not create or claim a new backup of those historical runs.", "",
              "Rebuild the figure with `python3 scripts/plot_www_control_trace.py`.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--extractor-sha256")
    args = parser.parse_args()
    root, out = args.repo_root.resolve(), args.out_dir.resolve()
    out.relative_to(root / "runs")
    if out.exists():
        raise FileExistsError(f"Fresh output directory required: {out}")
    sources = {}

    def read(relative):
        data = (root / relative).read_bytes()
        sources[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        return json.loads(data)

    contracts = {name: read(name) for name in CONTRACTS}
    frozen, topology = read(FROZEN), read(TOPOLOGY)
    summaries = {arm: read(path + "/report.json") for arm, path in ARMS.items()}
    cells = {arm: {c["world"]: c for c in report["cells"]} for arm, report in summaries.items()}
    common = set.intersection(*(set(d) for d in cells.values()))
    union = set.union(*(set(d) for d in cells.values()))
    capacity_candidates = sorted(w for w in common if all(cells[a][w]["maintenance"][3] == 2 for a in ARMS))
    audit = {
        "common_worlds": len(common),
        "not_common": sorted(union - common),
        "capacity_excluded": sorted(common - set(capacity_candidates)),
        "capacity_candidates": capacity_candidates,
        "checked": [],
    }
    selected = None
    selection_rule = (
        "From common 2-Mbps worlds in lexicographic world-ID order, choose the first successful complete triple "
        "with identical event, initial observation, seed and native binary, identical 7B beliefs in the two predictive arms, "
        "no reactive belief, and a recorded lookahead goal intervention. Scores, wins and performance gaps are not selection criteria."
    )
    for world in capacity_candidates:
        reasons = []
        for arm in ARMS:
            if not cells[arm][world]["ok"]:
                reasons.append(arm + ": archived run not ok")
        if cells["lookahead"][world].get("goal_steps", 0) <= 0:
            reasons.append("lookahead: no recorded goal intervention")
        if reasons:
            audit["checked"].append({"world": world, "status": "excluded", "reasons": reasons})
            continue
        triple = {}
        try:
            for arm, prefix in ARMS.items():
                directory = prefix + "/" + world.replace("/", "__")
                report = read(directory + "/report.json")
                launch = read(directory + "/native/launch.json")
                rows = [read(directory + f"/tick_{tick:03d}.json") for tick in range(12)]
                seed = next(int(s.split("=", 1)[1]) for s in launch["argv"] if s.startswith("--seed="))
                if not report["ok"] or report["world"] != world:
                    reasons.append(arm + ": report identity or success mismatch")
                if [row["tick"] for row in rows] != list(range(12)):
                    reasons.append(arm + ": missing or unordered ticks")
                for i, row in enumerate(rows):
                    if row["before"]["tick"] != i or row["after"]["tick"] != i + 1:
                        reasons.append(arm + ": state tick mismatch")
                    if i and row["before"] != rows[i - 1]["after"]:
                        reasons.append(arm + ": broken realised-state continuity")
                    if any(row["after"][key] != row["selected"]["target"][key] for key in ["paths", "caps_bps"]):
                        reasons.append(arm + ": executed target differs from realised configuration")
                if launch["maintenance"] != [cells[arm][world]["maintenance"]]:
                    reasons.append(arm + ": launch/summary event mismatch")
                if report["belief"] != cells[arm][world]["belief"]:
                    reasons.append(arm + ": report/summary belief mismatch")
                triple[arm] = {
                    "source_directory": directory,
                    "archived_report": report,
                    "launch_identity": {
                        "seed": seed, "maintenance": launch["maintenance"],
                        "binary_sha256": launch["build"]["binary_sha256"],
                        "native_source": Path(launch["build"]["source"]).name,
                        "native_source_sha256": launch["build"]["source_sha256"],
                    },
                    "raw_ticks": rows,
                }
            events = {canonical(t["launch_identity"]["maintenance"]) for t in triple.values()}
            seeds = {t["launch_identity"]["seed"] for t in triple.values()}
            binaries = {t["launch_identity"]["binary_sha256"] for t in triple.values()}
            initials = {canonical(t["raw_ticks"][0]["before"]) for t in triple.values()}
            if not all(len(v) == 1 for v in [events, seeds, binaries, initials]):
                reasons.append("physical inputs mismatch: event, seed, native binary, or initial observation")
            belief = triple["lookahead"]["archived_report"]["belief"]
            if not belief or triple["fixed_lead"]["archived_report"]["belief"] != belief:
                reasons.append("predictive arms have different or absent beliefs")
            if triple["reactive"]["archived_report"]["belief"] is not None:
                reasons.append("reactive has a belief")
            for arm, data in triple.items():
                expected = None if arm == "reactive" else belief
                if any(row["shield"]["belief"] != (expected if row["tick"] >= 2 else None)
                       for row in data["raw_ticks"]):
                    reasons.append(arm + ": tick belief does not match the stated information condition")
            goal_count = sum("goal_step" in row["shield"] for row in triple["lookahead"]["raw_ticks"])
            if goal_count <= 0 or goal_count != triple["lookahead"]["archived_report"]["goal_steps"]:
                reasons.append("lookahead intervention missing or inconsistent")
        except (OSError, KeyError, ValueError, StopIteration) as exc:
            reasons.append("unreadable or invalid archived triple: " + type(exc).__name__)
        if reasons:
            audit["checked"].append({"world": world, "status": "excluded", "reasons": reasons})
            continue
        audit["checked"].append({"world": world, "status": "selected", "reasons": []})
        audit["unexamined_after_selection"] = capacity_candidates[capacity_candidates.index(world) + 1:]
        selected = (world, triple)
        break
    out.mkdir(parents=True, exist_ok=False)
    if selected is None:
        (out / "selection_failed.json").write_text(json.dumps(audit, indent=2) + "\n")
        raise RuntimeError("No matching world; filters were not relaxed")
    world, triple = selected
    # Validation happens after selection. A failure aborts; it does not move to
    # a more favourable candidate or silently alter the archived score.
    for arm, data in triple.items():
        data["decoded_ticks"], data["score_validation"] = score_ticks(
            data["raw_ticks"], frozen, topology, data["archived_report"]
        )
    identity = triple["lookahead"]["launch_identity"]
    result = {
        "schema": "www.control_trace/1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "kind": "postprocessing_of_archived_runs_only",
        "extractor_sha256": args.extractor_sha256,
        "selection_rule": selection_rule,
        "selection_audit": audit,
        "world": world,
        "notice": cells["lookahead"][world]["notice"],
        "site": cells["lookahead"][world]["site"],
        "matching": {
            **identity,
            "maintenance": identity["maintenance"][0],
            "initial_observation_sha256": hashlib.sha256(canonical(triple["lookahead"]["raw_ticks"][0]["before"]).encode()).hexdigest(),
            "predictive_belief": triple["lookahead"]["archived_report"]["belief"],
            "reactive_belief": None,
        },
        "scoring_parameters": {
            "base_ledger": frozen["base_ledger"], "release_tick": frozen["release_tick"],
            "topology": {key: topology[key] for key in ["services", "hard_min_mbps", "hard_delay_ms", "hard_loss_max"]},
        },
        "arms": triple,
        "contract_source_pins": {name: c["sources"] for name, c in contracts.items()},
        "source_manifest": sources,
        "limitations": [
            "One deterministic intervention case; not population evidence or an unbiased random sample.",
            "Two forecast arms share a belief; reactive intentionally has no future belief.",
            "30-minute notice-calendar ticks map to one-second packet simulation ticks.",
            "No rejected proposal is inferred when it is absent from the archived record.",
            "Different historical controller code versions are identified by their registered contracts.",
            "Original run preservation is unchanged; this is a reconstructible derived asset, not a new historical-run backup.",
        ],
    }
    (out / f"{STEM}.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (out / f"{STEM}.md").write_text(make_report(result))
    print(json.dumps({
        "selected_world": world, "checked_candidates": len(audit["checked"]),
        "output_directory": str(out),
        "score_validation": {a: t["score_validation"] for a, t in triple.items()},
        "source_files": len(sources),
    }, indent=2))


if __name__ == "__main__":
    main()
