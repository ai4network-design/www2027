#!/usr/bin/env python3
"""Select two fixed archival cases, or rebuild their displays from the public case JSON.

Original selection requires the two archived JSONL inputs. Display rebuilding and
the mechanical checks on saved outputs need only the short public case JSON. No
model, crawler, corpus annotation, simulator, or statistical estimation is run.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SPECS = {
    "qwen7b_extract": {
        "path": "runs/converter_fix_20261001/compile_test/qwen7b_extract.jsonl",
        "sha256": "255a0d615c2e314eaffe2e8a92dc600b1518ca59d9a5bf890d1b30b945809e7c",
    },
    "sutime_glue": {
        "path": "runs/sota_baselines_20261015/time/ann_test_sutime_glue.jsonl",
        "sha256": "bc59a37f002dbb13bab25742ac0d69cfa2813b06339a978b1d7abd28a9232cb9",
    },
}
SELECTION_PATH = "docs/reproduce/www_reading_cases_20261019_selection.md"
SELECTION_SHA = "4eea5cf25992361a79f2f2581125765885f000456994f9a60572000ae6e7442c"
CASE_SPECS = [
    {
        "role": "7B reference agreement; SUTime reference mismatch",
        "id": "status.bunny.net/p0b83ch7b6rc",
        "excerpts": [
            "this maintenance is scheduled to begin at 03:30 UTC on the 5th of November",
            "We expect the maintenance  to result in no more than 2 hours of downtime for API services.",
        ],
        "interpretation": "The saved 7B output supplies 05:30, two hours after the stated start. The prose gives an expected downtime upper bound, not an explicit end clock. Matching the platform's end reference therefore does not establish exact transcription or semantic correctness.",
        "paper_note": "The supplied end equals start plus the stated downtime upper bound; no end clock is explicit. Reference agreement is not exact transcription.",
    },
    {
        "role": "7B reference mismatch",
        "id": "status.bunny.net/hqm95hckjf66",
        "excerpts": [
            "you may experience downtime on the Magic Containers service between 10:00 and 11:00 CEST on Thursday, 30th of April",
        ],
        "interpretation": "The saved 7B output copies 10:00-11:00 CEST, which converts to 08:00-09:00 UTC. The platform references are one hour later. The discrepancy cannot establish a model reading error or adjudicate the provider's intended schedule; the automatic references remain unchanged.",
        "paper_note": "The quoted CEST clocks convert to 08:00--09:00 UTC; the platform references are one hour later. This mismatch alone does not identify a reading error.",
    },
]


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def available_match(score):
    assert isinstance(score["start_ok"], bool)
    assert score["end_ok"] is None or isinstance(score["end_ok"], bool)
    return score["start_ok"] and score["end_ok"] is not False


def read_archive(path, key):
    raw = path.read_bytes()
    if sha256(raw) != SOURCE_SPECS[key]["sha256"]:
        raise ValueError(f"Frozen source hash mismatch: {key}")
    rows = {}
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        assert row["id"] not in rows, f"Duplicate ID: {row['id']}"
        rows[row["id"]] = (row, line_number, sha256(line))
    return rows, len(raw)


def convert_case_extraction(extracted):
    """Check only the UTC/CEST fields in these two saved cases, not a general reader."""
    zone = extracted["tz"]
    assert zone in ("UTC", "CEST"), "Case verification supports only the two stored zones"
    offset = dt.timedelta(hours={"UTC": 0, "CEST": 2}[zone])
    start = dt.datetime.fromisoformat(extracted["date"] + "T" + extracted["start"]) - offset
    end = dt.datetime.fromisoformat((extracted.get("end_date") or extracted["date"]) + "T" + extracted["end"]) - offset
    if end <= start:
        end += dt.timedelta(days=1)
    return {"start_utc": start.isoformat(timespec="minutes"), "end_utc": end.isoformat(timespec="minutes")}


def check_saved_score(pred, reference):
    """Mechanically verify the two retained scores at the frozen two-minute tolerance."""
    result = {}
    for bound in ("start", "end"):
        ref = reference[bound + "_utc"]
        value = pred[bound + "_utc"]
        result[bound + "_ok"] = None if ref is None else (
            value is not None and abs((dt.datetime.fromisoformat(value) - dt.datetime.fromisoformat(ref)).total_seconds()) <= 120
        )
    return result


def verify_public_cases(report):
    assert report["schema"] == "www.reading_cases/1"
    assert report["selection"]["common_ids"] == 240
    assert report["selection"]["rule_sha256"] == SELECTION_SHA
    assert len(report["cases"]) == 2
    for key, source in SOURCE_SPECS.items():
        assert report["sources"][key]["sha256"] == source["sha256"]
    for index, case in enumerate(report["cases"]):
        assert case["id"] == CASE_SPECS[index]["id"]
        assert case["common_id_rank"] == (4 if index == 0 else 3)
        assert sum(len(span["text"].split()) for span in case["excerpt"]["spans"]) <= 40
        assert case["excerpt"]["text"] == " … ".join(span["text"] for span in case["excerpt"]["spans"])
        for span in case["excerpt"]["spans"]:
            assert span["end"] - span["start"] == len(span["text"])
        qwen = case["arms"]["qwen7b_extract"]
        assert json.loads(qwen["raw"]) == qwen["extracted"]
        assert convert_case_extraction(qwen["extracted"]) == qwen["pred"]
        for arm in case["arms"].values():
            assert check_saved_score(arm["pred"], case["reference"]) == arm["score"]
            assert available_match(arm["score"]) == arm["available_reference_match"]
        assert qwen["available_reference_match"] is (index == 0)
        if index == 0:
            assert not case["arms"]["sutime_glue"]["available_reference_match"]


def extract_cases(qwen_path, sutime_path):
    assert sha256((ROOT / SELECTION_PATH).read_bytes()) == SELECTION_SHA
    archives = {}
    sources = {}
    for key, path in (("qwen7b_extract", qwen_path), ("sutime_glue", sutime_path)):
        archives[key], size = read_archive(path, key)
        sources[key] = {**SOURCE_SPECS[key], "bytes": size}
    common = sorted(set(archives["qwen7b_extract"]) & set(archives["sutime_glue"]))
    assert len(common) == 240
    for ident in common:
        a = archives["qwen7b_extract"][ident][0]
        b = archives["sutime_glue"][ident][0]
        for field in ("text", "posted_utc", "ref_start", "ref_end", "site", "part"):
            assert a[field] == b[field], f"Unmatched input field {field}: {ident}"
    selected = [
        next(i for i in common if available_match(archives["qwen7b_extract"][i][0]["score"]) and not available_match(archives["sutime_glue"][i][0]["score"])),
        next(i for i in common if not available_match(archives["qwen7b_extract"][i][0]["score"])),
    ]
    assert selected == [spec["id"] for spec in CASE_SPECS]
    cases = []
    for spec, ident in zip(CASE_SPECS, selected):
        item = archives["qwen7b_extract"][ident][0]
        spans = []
        for excerpt in spec["excerpts"]:
            assert item["text"].count(excerpt) == 1
            start = item["text"].index(excerpt)
            spans.append({"start": start, "end": start + len(excerpt), "text": excerpt})
        arms = {}
        for arm, archive in archives.items():
            row, line_number, row_sha = archive[ident]
            arms[arm] = {key: row.get(key) for key in ("raw", "extracted", "pred", "score")}
            arms[arm].update({"source_line": line_number, "source_row_sha256": row_sha, "available_reference_match": available_match(row["score"])})
            if "expressions" in row:
                arms[arm]["expressions"] = row["expressions"]
        cases.append({
            "role": spec["role"], "id": ident, "common_id_rank": common.index(ident) + 1,
            "site": item["site"], "posted_utc": item["posted_utc"],
            "text_sha256": sha256(item["text"].encode()), "text_characters": len(item["text"]),
            "excerpt": {"text": " … ".join(span["text"] for span in spans), "spans": spans,
                        "offset_unit": "zero-based Unicode characters in the archived text; end exclusive"},
            "reference": {"start_utc": item["ref_start"], "end_utc": item["ref_end"]},
            "arms": arms, "interpretation": spec["interpretation"], "paper_note": spec["paper_note"],
        })
    report = {
        "schema": "www.reading_cases/1",
        "purpose": "Two condition-selected archival readings explaining reference agreement and mismatch, not semantic labels or accuracy estimates.",
        "sources": sources,
        "selection": {"rule_document": SELECTION_PATH, "rule_sha256": SELECTION_SHA, "recorded_at_utc": "2026-10-19T12:14:00Z",
                      "common_ids": len(common), "order": "lexicographic common notice ID",
                      "rules": ["first 7B available-reference match and SUTime mismatch", "first 7B available-reference mismatch, regardless of SUTime"],
                      "outcome_based": True, "replacements": 0},
        "limitations": [
            "Examples are selected conditional on saved reference agreement; they do not estimate error frequency, accuracy, or causal effects.",
            "Both first qualifying IDs happen to be from the same provider; no provider representativeness is claimed.",
            "Automatic references are unchanged and not independently validated. A match need not imply correct transcription; a mismatch need not imply a reading error.",
            "Only short original-text excerpts and saved model/tool outputs are public. Repeating selection and checking excerpt offsets against full text requires the original archived inputs.",
            "Public-case verification checks only saved JSON, deterministic UTC/CEST conversion and frozen reference scoring; it does not rerun either reader or infer model reasoning.",
            "SUTime has no stored model raw/extracted fields; their null values denote absent fields, not failed model calls.",
        ],
        "cases": cases,
    }
    verify_public_cases(report)
    return report


def tex_escape(text):
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "…": r"\ldots{}"}
    return "".join(replacements.get(char, char) for char in text)


def window_text(pred):
    start, end = pred["start_utc"], pred["end_utc"]
    if start and end and start[:10] == end[:10]:
        return start.replace("T", " ") + "--" + end[11:]
    return (start or "null").replace("T", " ") + " / " + (end or "null").replace("T", " ")


def write_displays(report, md_path, tex_path):
    verify_public_cases(report)
    md = ["# Archived reading cases", "", report["purpose"], "", "Selection was fixed before inspecting the selected cases; no example was replaced.", ""]
    for case in report["cases"]:
        md += [f"## {case['role']}", "", f"ID: `{case['id']}`; common-ID position {case['common_id_rank']}; posted UTC `{case['posted_utc']}`.", "", "> " + case["excerpt"]["text"], "", "Ellipses indicate omitted prose; excerpt offsets and source hashes are retained in JSON.", "", f"Automatic references (UTC): `{window_text(case['reference'])}`.", "", "| Saved reader | Converted UTC | Start/end reference match |", "|---|---|---|"]
        for name, arm in case["arms"].items():
            md.append(f"| {name} | `{window_text(arm['pred'])}` | {arm['score']['start_ok']} / {arm['score']['end_ok']} |")
        md += ["", "Saved 7B model output, verbatim:", "", "```json", case["arms"]["qwen7b_extract"]["raw"], "```", "", case["interpretation"], ""]
    md += ["## Provenance and limits", ""]
    md += [f"- `{source['path']}`: SHA-256 `{source['sha256']}`." for source in report["sources"].values()]
    md += [f"- {line}" for line in report["limitations"]]
    md += ["", "## Rebuilding", "", "Use `--case-json` with this public JSON to mechanically verify the two saved outputs and rebuild this report and the LaTeX table. Use `--qwen7b` and `--sutime` with the original archived JSONL files to repeat the fixed selection and excerpt checks. Input paths are arguments; no author machine or network access is required by the script.", ""]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md))

    cases = report["cases"]
    cells = [
        ("Notice ID", [r"\texttt{" + tex_escape(c["id"].split("/")[-1]) + "}" for c in cases]),
        ("Posted (UTC)", [tex_escape(c["posted_utc"].replace("T", " ")) for c in cases]),
        ("Prose excerpt", [r"\emph{" + tex_escape(" ".join(c["excerpt"]["text"].split())) + "}" for c in cases]),
        ("7B fields", [tex_escape(", ".join([c["arms"]["qwen7b_extract"]["extracted"]["date"], c["arms"]["qwen7b_extract"]["extracted"]["start"] + "--" + c["arms"]["qwen7b_extract"]["extracted"]["end"], c["arms"]["qwen7b_extract"]["extracted"]["tz"]])) for c in cases]),
        ("7B UTC", [tex_escape(window_text(c["arms"]["qwen7b_extract"]["pred"])) for c in cases]),
        ("SUTime UTC", [tex_escape(window_text(c["arms"]["sutime_glue"]["pred"])) for c in cases]),
        ("Reference UTC", [tex_escape(window_text(c["reference"])) for c in cases]),
        ("Interpretation", [tex_escape(c["paper_note"]) for c in cases]),
    ]
    tex = [
        "% Generated by scripts/extract_www_reading_cases.py from archived outputs.",
        r"\begin{table*}[t]", r"\centering",
        r"\caption{Two archived T1 readings, both from Bunny. In lexicographic common-ID order, select the first 7B reference match with a SUTime mismatch, and the first 7B mismatch. No example was replaced. Agreement is with automatic references, not independently verified semantics; these score-conditioned examples do not estimate prevalence.}",
        r"\label{tab:reading-cases}", r"\footnotesize", r"\setlength{\tabcolsep}{3pt}",
        r"\begin{tabular}{@{}p{0.12\textwidth}p{0.425\textwidth}p{0.425\textwidth}@{}}",
        r"\toprule", r" & \textbf{7B reference agreement} & \textbf{7B reference mismatch} \\", r"\midrule",
    ]
    for label, values in cells:
        tex.append(tex_escape(label) + " & " + " & ".join(values) + r" \\")
    tex += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.write_text("\n".join(tex) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen7b", type=Path, help="archived post-fix 7B JSONL")
    parser.add_argument("--sutime", type=Path, help="archived SUTime-glue JSONL")
    parser.add_argument("--case-json", type=Path, help="public case JSON; verify/rebuild displays without private inputs")
    parser.add_argument("--json-out", type=Path, default=ROOT / "docs/reproduce/www_reading_cases_20261019.json")
    parser.add_argument("--md-out", type=Path, default=ROOT / "docs/reproduce/www_reading_cases_20261019.md")
    parser.add_argument("--tex-out", type=Path, default=ROOT / "paper_www2027/tables/reading_cases.tex")
    args = parser.parse_args()
    if args.case_json:
        if args.qwen7b or args.sutime:
            parser.error("--case-json is separate from selection using archived inputs")
        report = json.loads(args.case_json.read_text())
    else:
        if not args.qwen7b or not args.sutime:
            parser.error("supply --qwen7b and --sutime, or --case-json")
        report = extract_cases(args.qwen7b, args.sutime)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    write_displays(report, args.md_out, args.tex_out)
    print("Verified two saved reading cases and wrote Markdown/LaTeX displays; no reader or experiment ran.")


if __name__ == "__main__":
    main()
