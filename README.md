# Reading the Status Page — artifact (anonymised, WWW 2027 submission)

Contents
- `release/` — benchmark item lists (identifiers, status-page URLs, splits, automatic references, T3 labels and candidate lists),
  the frozen prompts and the registered contracts. Notice text is not redistributed: fetch it from the URLs under each site's
  robots.txt (`scripts/collect_maintenance_notices.py` does exactly that).
- `scripts/` — crawl, measurement, the three compile evaluations (rule, open models, published tools, industry parser, closed
  models), the closed-loop runners (maintenance v2, controls, perturbation, seed replay, tool-belief arms), summaries and
  paired bootstrap comparisons, the live-snapshot pipeline.
- `src/vera/` — the controller: obligation ledger and planner, maintenance shield (invariant, escape, goal keeping),
  episode runner, native ns-3 driver, and the ns-3 scenario `ns3/ns3_multiservice_maint.cc` (ns-3.46).
- `contracts/` — one JSON per experiment, registered before each test run: pinned SHA-256 of scripts and data, arms, scoring,
  budgets, decision rules and amendments.

Reproduction sketch
1. Crawl: `python3 scripts/collect_maintenance_notices.py --sites <sites.json> --out data/notices` (robots-respecting, 1 req/s).
2. Compile benchmarks: `PYTHONPATH=src python scripts/maintenance_compile_eval.py models ... --model qwen7b` (Qwen2.5-7B-Instruct
   from Hugging Face), `revision_compile_eval.py`, `affected_compile_eval.py`; tools: `sota_baselines.py`, `tool_baselines_2.py`;
   comparisons: `compare_maintenance_compile.py`, `rescore_revision_compile.py`, `rescore_affected_compile.py`.
3. Closed loop: build the ns-3 scenario, then `run_maintenance_v2.py --contract contracts/maintenance_diagnostic_v2_20260930.json
   --arm M_qwen7b_extract ...`; summaries with `summarise_maintenance_diagnostic.py`.
Paths inside contracts that pointed to the authors' hosts are replaced by `<repo>` / `<local>` placeholders.
