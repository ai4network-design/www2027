# Reading the Status Page — anonymous WWW 2027 artifact

This artifact provides two distinct evidence levels. The **offline CPU saved-results check** below is self-contained and verifies six presentations from archived outputs. The historical model/network experiment sources are also supplied, but an end-to-end rerun of those experiments has **not** been validated from this release. CPU verification does not establish model accuracy, reference truth, original experiment reproduction, or real-time deployment feasibility.

## Offline CPU check: the supported entry point

Requirements: Python 3.9 or newer. No third-party packages, network connection, model weights, API credentials, GPU, ns-3, private repository, or Git history are required. Python 3.9.6 was used for the release check.

From the root of this downloaded repository, run:

```sh
python3 scripts/reproduce_www_saved_results.py --output reproduced_saved_results
```

The output directory must not already exist. The command verifies the SHA-256 manifest before reading inputs, rechecks saved arithmetic and scores, compares full-precision results with `saved_results/expected.json`, and writes `verification.json`, `results.json`, `report.md`, CSV tables and `reading_cases.md`. Success prints `PASS: six saved-result groups`; any missing group, altered input, or inconsistent value fails with a nonzero exit status. The SHA manifest detects accidental changes; it is not a signature or a proof of scientific validity.

The six groups are:

| Group | What is checked or rebuilt | Boundary |
|---|---|---|
| Attribution | Four saved controlled contrasts, mean differences and interval direction | Existing paired intervals are reused; contrasts are not additive |
| Provider sensitivity | Per-provider counts, micro/macro differences and all single-provider omissions | Omission ranges are descriptive, not confidence intervals or new-provider validation |
| Control trace | All 36 saved ticks, state continuity, installed targets, matching physical inputs/beliefs, contract scoring parameters and three archived episode reports | Reuses the published trace decoder against original report anchors; this case does not exercise deadline failure or recovery activation |
| Metric sensitivity | Four stored T1/T2 metric contrasts, counts, denominators, paired wins/losses and intervals | No threshold sweep, relabelling or bootstrap is performed |
| Control conditions | Capacity-stratum printed means, window satisfaction, uncertified intervals and revision sampling units | Printed capacity means have only the original three-decimal precision; uncertified differs from realised violation |
| Reading cases | Two fixed real outputs, deterministic conversions, reference comparisons and their display | Short excerpts do not permit independent full-corpus selection or new inference; a reference mismatch is not a semantic-error verdict |

All numeric differences are fractions unless a table explicitly says percentage points. T1 intervals cluster by provider; T2 intervals cluster by notice. The 35 cancelled/moved revision items belong to 30 notice clusters and yield 210 worlds. Older summaries use the field name `providers` for those notice clusters; the check preserves the source and labels its meaning explicitly.

### Optional scientific figures

The arithmetic check above needs no installation. To additionally rebuild the provider and control-trace PDFs, use Python 3.9–3.12 and a separate environment. Installing the two pinned plotting packages requires package access once; figure generation itself is offline:

```sh
python3 -m venv .venv-saved-results
.venv-saved-results/bin/python -m pip install -r requirements-saved-results-plots.txt
.venv-saved-results/bin/python scripts/reproduce_www_saved_results.py --output reproduced_saved_results_figures --figures
```

PDF bytes can differ with font/rendering environments and creation timestamps. The numeric outputs and input checksums, rather than PDF byte equality, are the verification target.

## Files and provenance

- `saved_results/manifest.json` locks the CPU inputs, helper scripts and expected numeric outputs; its adjacent checksum checks manifest integrity.
- `docs/reproduce/www_*_20261019.json` contains reviewed saved-result presentations; the required underlying summaries and counts are included beside them. The control trace embeds its original observations and reports; control conditions embeds the archived capacity-report text.
- `PROVENANCE.json` records the origin and exported SHA-256 of every file except itself. For copied or transformed files, `source_sha256` identifies the original source bytes. Generated files instead have `source_kind: generated` and `source_sha256: null`; their `generator` records the actual construction-script hash, and the expected-results snapshot also lists all six input-report hashes. The construction script is not included or needed for the supported CPU entry point; its identity is build provenance. Source-code completeness additions are listed in `saved_results/source_closure.json`.
- `release/` contains identifiers, public URLs, splits, references, prompts and experiment contracts. Full notice text and historical model/run archives are not redistributed. The two reading cases contain only bounded excerpts and saved model outputs.
- `scripts/` and `src/vera/` contain the saved-result tools and historical experiment code. The CPU entry point imports only bounded standard-library presentation helpers, never model or network runners.

The reports retain historical source hashes even when those original raw runs are not bundled. A hash identifies an input; it does not claim that every historical input is downloadable here. Reselecting the trace or reading cases requires the archived inputs named in their provenance. Automatic platform timestamps and component fields remain imperfect references.

Two historical contract host-location fields are redacted in the export. Provenance records both the original-source and exported-file hashes and the exact transformed field. The frozen scoring and topology contracts are bundled byte-for-byte and checked against their original hashes; the redacted historical contract is checked for its unchanged scientific source-pin map. No scientific parameter is inferred from a host path.

## Historical experiment path: supplied source, not a validated full rerun

Static missing-module dependencies in the original source package have been added. This does not validate external dependencies, protocol inputs, pretrained checkpoints, native binaries, API models or original latency budgets. The retained historical formatter `scripts/per_provider_table.py` uses Python 3.12 syntax; it is not imported by the supported CPU entry point. Do not interpret the following prerequisites as a completed reproduction claim.

| Stage | Supplied entry points | Inputs that must be provisioned explicitly |
|---|---|---|
| Public-page collection | `collect_maintenance_notices.py`, `live_snapshot.py` | Provider URL list, output directory, retrieval date, current access/robots rules; current pages may differ from archived pages |
| T1/T2/T3 reading | `maintenance_compile_eval.py`, `revision_compile_eval.py`, `affected_compile_eval.py` | Exact frozen item text and split, prompt mode, checkpoint/tokenizer/revision, generation settings, output path and model dependencies |
| Published parsers | `sota_baselines.py`, `tool_baselines_2.py` | Original parser/package versions and their runtimes/resources; Java resources for SUTime/HeidelTime as applicable |
| Closed-loop network execution | `run_maintenance_v2.py`, `run_maintenance_controls.py`, `src/vera/ns3/ns3_multiservice_maint.cc` | Built ns-3.46 scenario and libraries, correct contract tree and hashes, saved beliefs, arm/world/seed selection, fresh output directory and per-host native configuration |
| Historical population summaries | `summarise_maintenance_diagnostic.py` and related scripts | Complete original world reports with the correct protocol version and cluster keys |

Contract paths replaced by `<repo>` or `<local>` are provenance placeholders and require deliberate local configuration. The historical `verify_contracts.py` may need the original Git history; it is not the portable verification entry point. Network ticks advance one simulated second while action selection pauses that simulator clock; the saved satisfaction results do not measure a real-time LLM deadline.
