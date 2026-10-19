#!/usr/bin/env python3
"""Offline CPU verification of six saved-result presentations; no experiments.

Run from an exported artifact: python3 scripts/reproduce_www_saved_results.py
Only the standard library is required unless --figures is requested.
"""
import argparse
import csv
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("attribution", "provider", "trace", "metric_sensitivity",
          "control_conditions", "reading_cases")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(actual, expected, where="result"):
    """Compare structure and full-precision values; tolerate floating arithmetic only."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), where + ": keys differ")
        for key in expected:
            same(actual[key], expected[key], where + "." + str(key))
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), where + ": length differs")
        for i, (a, b) in enumerate(zip(actual, expected)):
            same(a, b, where + f"[{i}]")
    elif isinstance(expected, float):
        require(type(actual) in (int, float) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12), where + ": number differs")
    else:
        require(actual == expected, where + ": value differs")


def helper(name):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location("saved_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_manifest():
    path = ROOT / "saved_results/manifest.json"
    expected_digest = (path.with_suffix(".json.sha256")).read_text().split()[0]
    require(sha(path) == expected_digest, "Manifest checksum mismatch")
    manifest = read(path)
    require(manifest["schema"] == "www.saved_results/1", "Unexpected manifest schema")
    require(manifest["groups"] == list(GROUPS), "All six result groups are required")
    for relative, entry in manifest["files"].items():
        p = ROOT / relative
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "Unsafe manifest path")
        require(p.is_file() and not p.is_symlink(), "Missing/nonregular input: " + relative)
        require(p.stat().st_size == entry["bytes"] and sha(p) == entry["sha256"],
                "Input checksum mismatch: " + relative)
    return manifest


def report(name):
    return read(ROOT / "docs/reproduce" / ("www_" + name + "_20261019.json"))


def source_json(spec):
    p = ROOT / spec["path"]
    require(p.is_file() and sha(p) == spec["sha256"], "Source provenance mismatch: " + spec["path"])
    return read(p)


def attribution():
    d = report("attribution")
    sources = {key: source_json(value) for key, value in d["sources"].items()}
    rows = []
    for saved in d["rows"]:
        source = sources[saved["source"]]
        a, b = saved["arms"]
        delta, low, high = source["pairs"][saved["source_pair"]]["sat"]
        if saved["sign_reversed"]:
            delta, low, high = -delta, -high, -low
        means = [source["arms"][arm]["sat"] for arm in (a, b)]
        same(means[0] - means[1], delta, "attribution mean difference")
        require(low <= delta <= high, "Attribution interval order")
        row = dict(saved, means=means, delta=delta, ci95=[low, high], worlds=source["worlds"])
        same(row, saved, "attribution saved row")
        rows.append(row)
    return rows


def provider():
    module = helper("analyze_www_provider_sensitivity")
    t1, t3 = module.checked_inputs()
    result = [module.summarize(t1, "rule", "T1"), module.summarize(t1, "sutime", "T1"),
              module.summarize(t3, "rapidfuzz", "T3")]
    same(result, report("provider_sensitivity")["comparisons"], "provider summaries")
    return result


def trace():
    d = report("control_trace")
    require(d["schema"] == "www.control_trace/1", "Unexpected trace schema")
    module = helper("extract_www_control_trace")
    frozen_path = "contracts/frozen_protocol_20260924_20261008.json"
    topology_path = "contracts/multiservice_comparison_20260911.json"
    contracts = {}
    manifest_files = read(ROOT / "saved_results/manifest.json")["files"]
    for relative in [frozen_path, topology_path, *d["contract_source_pins"]]:
        source = d["source_manifest"][relative]
        exported = manifest_files[relative]
        require(exported["source_sha256"] == source["sha256"], "Trace contract provenance: " + relative)
        if relative in (frozen_path, topology_path):
            require(sha(ROOT / relative) == source["sha256"], "Scoring contract original checksum: " + relative)
        else:
            require(exported["transformation"] in ("none", "redacted identified host metadata: host"),
                    "Unexpected historical contract transformation")
        contracts[relative] = read(ROOT / relative)
        if relative in d["contract_source_pins"]:
            same(contracts[relative]["sources"], d["contract_source_pins"][relative], "historical source pins")
    frozen, topology = contracts[frozen_path], contracts[topology_path]
    same(d["scoring_parameters"], {
        "base_ledger": frozen["base_ledger"], "release_tick": frozen["release_tick"],
        "topology": {key: topology[key] for key in ("services", "hard_min_mbps", "hard_delay_ms", "hard_loss_max")},
    }, "trace scoring contracts")
    result = {}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for arm, data in d["arms"].items():
        require(len(data["raw_ticks"]) == 12, "Incomplete saved trace")
        archived = data["archived_report"]
        require(archived["ok"] and archived["world"] == d["world"], "Trace report identity/success")
        identity = data["launch_identity"]
        for key in ("seed", "binary_sha256", "native_source", "native_source_sha256"):
            same(identity[key], d["matching"][key], "trace launch " + key)
        same(identity["maintenance"], [d["matching"]["maintenance"]], "trace event")
        initial = canonical(data["raw_ticks"][0]["before"]).encode()
        require(hashlib.sha256(initial).hexdigest() == d["matching"]["initial_observation_sha256"],
                "Trace initial state differs")
        belief = None if arm == "reactive" else d["matching"]["predictive_belief"]
        require(arm == "reactive" or bool(belief), "Missing predictive belief")
        same(archived["belief"], belief, "trace report belief")
        for i, row in enumerate(data["raw_ticks"]):
            require(row["tick"] == row["before"]["tick"] == i and row["after"]["tick"] == i + 1,
                    "Trace tick sequence differs")
            if i:
                same(row["before"], data["raw_ticks"][i - 1]["after"], "realised-state continuity")
            for key in ("paths", "caps_bps"):
                same(row["after"][key], row["selected"]["target"][key], "installed target")
            same(row["shield"]["belief"], belief if i >= 2 else None, "trace tick belief")
        if arm == "lookahead":
            count = sum("goal_step" in row["shield"] for row in data["raw_ticks"])
            require(count > 0 and count == archived["goal_steps"], "Trace intervention mismatch")
        decoded, score = module.score_ticks(data["raw_ticks"], frozen, topology,
                                           data["archived_report"])
        same(decoded, data["decoded_ticks"], "trace decoded " + arm)
        same(score, data["score_validation"], "trace score " + arm)
        result[arm] = {"decoded_ticks": decoded, "score": score}
    require(set(result) == {"reactive", "fixed_lead", "lookahead"}, "Missing trace arm")
    return result


def metric_sensitivity():
    d = report("metric_sensitivity")
    sources = {key: source_json(value) for key, value in d["sources"].items()}
    count_keys = {"start only": "start", "all available boundaries": "window",
                  "strict boundary": "bound_ok", "loose boundary": "bound_ok_loose"}
    rows = []
    for saved in d["rows"]:
        source = sources[saved["task"]]
        a, b = saved["arms"]
        counts = [source["arms"][arm][count_keys[saved["metric"]]] for arm in (a, b)]
        n = source["n"] if saved["task"] == "T1" else source["arms"][a]["bound_n"]
        if saved["task"] == "T2":
            require(n == source["arms"][b]["bound_n"], "Boundary denominators differ")
        pair = source["pairs"][saved["source_pair"]]
        require(all(0 <= c <= n for c in counts), "Metric count outside denominator")
        require(counts[0] - counts[1] == pair["wins"] - pair["losses"], "Paired wins do not match counts")
        same((counts[0] - counts[1]) / n, pair["diff"], "metric difference")
        row = dict(saved, n=n, counts=counts, wins=pair["wins"], losses=pair["losses"],
                   delta=pair["diff"], ci95=[pair["lo"], pair["hi"]])
        same(row, saved, "metric saved row")
        rows.append(row)
    return rows


def control_conditions():
    d = report("control_conditions")
    capacity = d["sources"]["capacity_report"]
    raw = capacity["text"].encode("utf-8")
    require(hashlib.sha256(raw).hexdigest() == capacity["sha256"]
            and len(raw) == capacity["bytes"], "Embedded capacity report checksum mismatch")
    sources = {}
    for key, spec in d["sources"].items():
        if key == "capacity_report":
            continue
        p = ROOT / spec["path"]
        require(p.is_file() and sha(p) == spec["sha256"], "Condition source mismatch: " + spec["path"])
        if p.suffix == ".json":
            sources[key] = read(p)
    module = helper("build_www_control_conditions")
    rows = module.capacity_rows(capacity["text"])
    same(rows, d["capacity"], "capacity printed rows")
    for arm in module.CAPACITY_ARMS:
        same(sources["main"]["arms"][arm], sources["controls"]["arms"][arm], "condition shared arm")
    diagnostics = []
    for saved in d["diagnostics"]:
        stats = sources["controls"]["arms"][saved["arm"]]
        row = dict(saved, worlds=sources["controls"]["worlds"], window_satisfaction=stats["win"],
                   uncertified_per_episode=stats["unc"], saved_fields=stats)
        same(row, saved, "condition diagnostic")
        diagnostics.append(row)
    require("35 informative test items" in sources["revision_contract"]["relation"], "Revision item count missing")
    units = dict(items=35, notice_clusters=sources["revision"]["providers"],
                 worlds=sources["revision"]["worlds"], legacy_cluster_field="providers", cluster_unit="notice")
    same(units, d["revision_units"], "revision units")
    return dict(capacity=rows, diagnostics=diagnostics, revision_units=units)


def reading_cases():
    d = report("reading_cases")
    require(d["schema"] == "www.reading_cases/1", "Unexpected reading-case schema")
    selection = d["selection"]
    require(sha(ROOT / selection["rule_document"]) == selection["rule_sha256"], "Case selection rule checksum")
    require(selection["common_ids"] == 240 and selection["replacements"] == 0 and len(d["cases"]) == 2,
            "Case selection metadata differs")
    expected_ids = ["status.bunny.net/p0b83ch7b6rc", "status.bunny.net/hqm95hckjf66"]
    for i, case in enumerate(d["cases"]):
        require(case["id"] == expected_ids[i] and case["common_id_rank"] == (4 if i == 0 else 3), "Case identity")
        spans = case["excerpt"]["spans"]
        require(sum(len(s["text"].split()) for s in spans) <= 40, "Case excerpt exceeds limit")
        require(case["excerpt"]["text"] == " … ".join(s["text"] for s in spans), "Case excerpt join")
        for span in spans:
            require(0 <= span["start"] < span["end"] <= case["text_characters"]
                    and span["end"] - span["start"] == len(span["text"]), "Case excerpt offset bounds")
        qwen = case["arms"]["qwen7b_extract"]
        extracted = json.loads(qwen["raw"])
        same(extracted, qwen["extracted"], "case raw JSON")
        require(extracted["tz"] in ("UTC", "CEST"), "Unsupported case timezone")
        offset = timedelta(hours={"UTC": 0, "CEST": 2}[extracted["tz"]])
        start = datetime.fromisoformat(extracted["date"] + "T" + extracted["start"]) - offset
        end = datetime.fromisoformat((extracted.get("end_date") or extracted["date"]) + "T" + extracted["end"]) - offset
        if end <= start:
            end += timedelta(days=1)
        same({"start_utc": start.isoformat(timespec="minutes"), "end_utc": end.isoformat(timespec="minutes")},
             qwen["pred"], "case deterministic conversion")
        for name, arm in case["arms"].items():
            score = {}
            for bound in ("start", "end"):
                ref, pred = case["reference"][bound + "_utc"], arm["pred"][bound + "_utc"]
                score[bound + "_ok"] = None if ref is None else (pred is not None and
                    abs((datetime.fromisoformat(pred) - datetime.fromisoformat(ref)).total_seconds()) <= 120)
            same(score, arm["score"], "case saved score " + name)
            same(bool(score["start_ok"] and score["end_ok"] is not False),
                 arm["available_reference_match"], "case available-reference score")
        require(qwen["available_reference_match"] is (i == 0), "Case reference classification")
    return case_fields(d)


def case_fields(d):
    return {"selection": d["selection"], "cases": [
        {key: c[key] for key in ("id", "role", "common_id_rank", "posted_utc", "excerpt", "reference", "arms", "interpretation")}
        for c in d["cases"]]}


def expected_from_reports():
    """Package-time snapshot of previously reviewed outputs, not computed expectations."""
    t = report("control_trace")
    c = report("control_conditions")
    return {
        "attribution": report("attribution")["rows"],
        "provider": report("provider_sensitivity")["comparisons"],
        "trace": {arm: {"decoded_ticks": data["decoded_ticks"], "score": data["score_validation"]}
                  for arm, data in t["arms"].items()},
        "metric_sensitivity": report("metric_sensitivity")["rows"],
        "control_conditions": {key: c[key] for key in ("capacity", "diagnostics", "revision_units")},
        "reading_cases": case_fields(report("reading_cases")),
    }


def write_outputs(result, out):
    out.mkdir(parents=True, exist_ok=False)
    (out / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    lines = ["# Saved-result verification", "", "PASS: all six groups match the frozen expected results.", "",
             "No network requests, model inference, ns-3 execution, bootstrap resampling or new experiment occurred.",
             "Intervals below are reused from saved summaries. The source manifest checks integrity, not scientific truth.", ""]
    tables = {}
    tables["attribution"] = (["Contrast", "Worlds", "A", "B", "Difference", "CI low", "CI high"],
        [[r["label"], r["worlds"], *r["means"], r["delta"], *r["ci95"]] for r in result["attribution"]])
    tables["provider"] = (["Task", "Comparator", "n", "Providers", "Micro delta", "Macro delta", "Omit-one minimum", "Omit-one maximum"],
        [[r["task"], r["comparator"], r["n"], r["providers"], r["micro_delta"], r["macro_delta"],
          r["leave_one_provider_out_range"]["min"], r["leave_one_provider_out_range"]["max"]] for r in result["provider"]])
    tables["trace"] = (["Arm", "Met ticks", "Intervals", "Satisfaction", "Hard-SLA failed ticks"],
        [[arm, d["score"]["met"], d["score"]["intervals"], d["score"]["satisfaction"],
          d["score"]["hard_sla_violation_intervals"]] for arm, d in result["trace"].items()])
    tables["metric_sensitivity"] = (["Task", "Metric", "n", "7B", "Rule", "Difference", "CI low", "CI high", "Cluster unit"],
        [[r["task"], r["metric"], r["n"], *r["counts"], r["delta"], *r["ci95"], r["cluster_unit"]]
         for r in result["metric_sensitivity"]])
    arms = ["R0_reactive", "M_rule", "M_qwen7b_extract", "M_oracle"]
    tables["capacity"] = (["Remaining Mbps", "Worlds", *arms],
        [[r["remaining_mbps"], r["worlds"], *[r["satisfaction_printed"][a] for a in arms]]
         for r in result["control_conditions"]["capacity"]])
    tables["diagnostics"] = (["Arm", "Worlds", "Window satisfaction", "Uncertified per episode"],
        [[r["label"], r["worlds"], r["window_satisfaction"], r["uncertified_per_episode"]]
         for r in result["control_conditions"]["diagnostics"]])
    for title, (headers, rows) in tables.items():
        with (out / (title + ".csv")).open("w", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(headers); writer.writerows(rows)
        lines += ["## " + title.replace("_", " ").title(), "", "| " + " | ".join(headers) + " |",
                  "|" + "---|" * len(headers)]
        for row in rows:
            values = [f"{v:.6f}" if isinstance(v, float) else str(v) for v in row]
            lines.append("| " + " | ".join(values) + " |")
        lines.append("")
    lines += ["## Reading cases", "", "Case outputs and references were checked; reference disagreement is not a semantic error verdict.",
              "See reading_cases.md and results.json. Case selection cannot be repeated without the original archived inputs.", "",
              "## Limits", "", "- Attribution rows are separate contrasts, not an additive decomposition.",
              "- Provider omission ranges are descriptive, not confidence intervals or new-provider validation.",
              "- Automatic timestamps are imperfect references; this check does not validate ground truth.",
              "- Trace selection is fixed and illustrative, not an estimate of effect prevalence.",
              "- Capacity means retain their original three-decimal precision; uncertified does not mean realised violation.",
              "- CPU display reproduction does not establish original experiment reproduction or realtime feasibility."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    case_module = helper("extract_www_reading_cases")
    case_module.write_displays(report("reading_cases"), out / "reading_cases.md", out / "reading_cases.tex")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reproduced_saved_results")
    parser.add_argument("--figures", action="store_true", help="Also render provider and trace PDFs; requires matplotlib")
    args = parser.parse_args()
    require(not args.output.exists(), "Output must be a fresh directory: " + str(args.output))
    manifest = verify_manifest()
    actual = {name: globals()[name]() for name in GROUPS}
    same(actual, read(ROOT / "saved_results/expected.json"), "frozen expected results")
    write_outputs(actual, args.output)
    if args.figures:
        module = helper("analyze_www_provider_sensitivity")
        module.FIGURE = args.output / "provider_sensitivity.pdf"
        module.plot(dict(report("provider_sensitivity"), comparisons=actual["provider"]), None)
        subprocess.run([sys.executable, str(ROOT / "scripts/plot_www_control_trace.py"),
                        "--input", str(ROOT / "docs/reproduce/www_control_trace_20261019.json"),
                        "--output", str(args.output / "control_trace.pdf")], check=True)
    (args.output / "verification.json").write_text(json.dumps({
        "status": "PASS", "groups": list(GROUPS), "verified_files": len(manifest["files"]),
        "figures_rendered": args.figures, "scope": "saved-result arithmetic and display only",
    }, indent=2) + "\n")
    print("PASS: six saved-result groups; output: " + str(args.output))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        sys.exit(2)
