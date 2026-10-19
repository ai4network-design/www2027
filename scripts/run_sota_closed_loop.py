#!/usr/bin/env python3
"""Closed-loop arms whose beliefs come from the published-tool baselines (contracts/sota_baselines_20261015.json step 3):
  maintenance  M_dateparser_glue / M_sutime_glue on the v2 worlds (scripts/run_maintenance_v2.py construction)
  affected     M_rapidfuzz / M_embed on the affected-object worlds (scripts/run_maintenance_affected.py construction)

    PYTHONPATH=src python scripts/run_sota_closed_loop.py --scenario maintenance --arm M_dateparser_glue --contract contracts/sota_baselines_20261015.json --out runs/sota_baselines_20261015/closed_loop/M_dateparser_glue [--shard i/n]
"""
import importlib.util, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
load = lambda name: (lambda s: (s.loader.exec_module(mod := importlib.util.module_from_spec(s)) or mod))(importlib.util.spec_from_file_location(name, HERE / f'{name}.py'))
scenario = sys.argv[sys.argv.index('--scenario') + 1]; del sys.argv[sys.argv.index('--scenario'):sys.argv.index('--scenario') + 2]
if scenario == 'maintenance':
    V2 = load('run_maintenance_v2'); V1 = V2.V1
    V1.COMPILED = 'runs/sota_baselines_20261015/time'
    V1.FILES = {'M_dateparser_glue': 'ann_test_dateparser_glue.jsonl', 'M_dateparser_raw': 'ann_test_dateparser_raw.jsonl', 'M_sutime_glue': 'ann_test_sutime_glue.jsonl', 'M_sutime_raw': 'ann_test_sutime_raw.jsonl'}
    V1.ARMS = V1.ARMS + list(V1.FILES); V2.main()
else:
    A = load('run_maintenance_affected')
    # keep the affected run's compilers (they define the 805 worlds) and add the tool outputs by relative path
    tool_files = {'rapidfuzz': '../../../sota_baselines_20261015/entity/test_rapidfuzz.jsonl', 'embed': '../../../sota_baselines_20261015/entity/test_embed_bge-small-en-v1.5.jsonl'}
    A.FILES.update({k: v for k, v in tool_files.items() if (A.m.ROOT / A.COMPILED / v).exists()})
    A.ARMS = A.ARMS + ['M_rapidfuzz', 'M_embed']
    belief0 = A.belief_for
    def belief_for(arm, w):     # entity tools give no window: the path from the tool, the window from the 7B extract outputs of the affected run
        it = w['item']; p_true, s, e, c = w['maintenance']
        comp = arm[2:]; r = A.compiled(comp)[it['id']]; reg = r['score']['region_resolved']
        if not reg:
            return None
        q = {json.loads(l)['id']: json.loads(l) for l in (A.m.ROOT / 'runs/affected_scenario_20261013/compile/test/qwen7b_extract.rescored.jsonl').read_text().splitlines() if l.strip()}[it['id']]
        sb, eb = A.window(w, q['pred'].get('start_utc'), q['pred'].get('end_utc'))
        return (A.path_of(it['site'], reg), sb, eb, c) if eb > sb else None
    import json
    A.belief_for = belief_for; A.main()
