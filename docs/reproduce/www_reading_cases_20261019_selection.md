# Fixed selection rule for archived reading cases

Recorded at 2026-10-19 12:14 UTC, before inspecting the selected cases. Earlier feasibility checks inspected file schemas and the first stored record; they did not rank candidate examples. The selection rule below was assigned before this extraction.

Use only these archived T1 predictions:

- Post-converter-fix Qwen2.5 7B extract: `runs/converter_fix_20261001/compile_test/qwen7b_extract.jsonl`.
- SUTime with development-split glue: `runs/sota_baselines_20261015/time/ann_test_sutime_glue.jsonl`.

Join by the stored notice ID, check matching reference fields, and sort the common IDs lexicographically. Available-reference agreement is the stored `start_ok` and, when an end reference is available, `end_ok`; no boundary is newly annotated or rescored.

1. Select the first common ID where 7B agrees with all available references and SUTime does not.
2. Select the first common ID where 7B does not agree with all available references, regardless of SUTime's result.

Do not replace either example because its prose is awkward, the outcome is ambiguous, or another example is more favorable. These selections condition on observed scores and explain individual readings; they cannot estimate prevalence, accuracy, causal effects, or semantic-reference validity.

After selection, retain the source hashes, ID, posting time, original model output, extraction, converted UTC prediction, stored scores, and automatic references. The public case report contains only a short decisive prose excerpt (aiming at at most 40 words per case), not complete notices or the raw corpus. Excerpt ranges are recorded and checked against the archived text; any omissions are explicit. Explain uncertainty without relabeling the reference or claiming independent human validation. No model call, crawl, annotation, or simulation is authorized by this extraction.
