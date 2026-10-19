#!/usr/bin/env python3
"""Closed-loop summary of the published-tool arms (contracts/sota_baselines_20261015.json step 3) beside the existing arms.

    python3 scripts/summarise_sota.py runs/sota_baselines_20261015
"""
import importlib.util, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('smd', HERE / 'summarise_maintenance_diagnostic.py'); S = importlib.util.module_from_spec(_s); _s.loader.exec_module(S)
_a = importlib.util.spec_from_file_location('saf', HERE / 'summarise_affected_scenario.py'); SA = importlib.util.module_from_spec(_a); _a.loader.exec_module(SA)
ROOT = Path(__file__).resolve().parents[1]


def link(t, src):
    if not t.exists() and (src / 'report.json').exists():
        os.symlink(src.resolve(), t)


def main():
    root = Path(sys.argv[1])
    print('# published tools in the maintenance closed loop (v2 worlds)\n'); view = root / 'view_maint' / 'primary'; view.mkdir(parents=True, exist_ok=True)
    link(view / 'R0_reactive', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/R0_reactive'); link(view / 'M_oracle', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/M_oracle')
    link(view / 'M_rule', ROOT / 'runs/converter_fix_20261001/closed_loop/M_rule'); link(view / 'M_qwen7b_extract', ROOT / 'runs/converter_fix_20261001/closed_loop/M_qwen7b_extract')
    for a in ('M_dateparser_glue', 'M_sutime_glue'):
        link(view / a, root / 'closed_loop' / a)
    S.PRIMARY = [a for a in ('R0_reactive', 'M_dateparser_glue', 'M_sutime_glue', 'M_rule', 'M_qwen7b_extract', 'M_oracle') if (view / a).exists()]; S.LANG = 'M_qwen7b_extract'
    S.PAIRS = [p for p in [('M_qwen7b_extract', 'M_dateparser_glue'), ('M_rule', 'M_dateparser_glue'), ('M_dateparser_glue', 'R0_reactive'), ('M_qwen7b_extract', 'M_sutime_glue'), ('M_sutime_glue', 'R0_reactive')] if p[0] in S.PRIMARY and p[1] in S.PRIMARY]
    sys.argv = ['x', str(root / 'view_maint'), '--part', 'primary']; S.main(); os.replace(root / 'view_maint' / 'summary_primary.json', root / 'summary_maintenance.json')
    print('\n\n# published tools in the affected-object closed loop\n'); aroot = root / 'view_aff'; (aroot / 'closed_loop').mkdir(parents=True, exist_ok=True)
    for a in ('R0_reactive', 'M_rule', 'M_qwen7b', 'M_oracle', 'X_oracle_time_rule_path', 'X_oracle_time_qwen7b_path'):
        link(aroot / 'closed_loop' / a, ROOT / 'runs/affected_scenario_20261013/closed_loop' / a)
    for a in ('M_rapidfuzz', 'M_embed'):
        link(aroot / 'closed_loop' / a, root / 'closed_loop' / a)
    SA.ARMS = SA.ARMS + ['M_rapidfuzz', 'M_embed']; SA.PAIRS = [('M_qwen7b', 'M_rapidfuzz'), ('M_rapidfuzz', 'M_rule'), ('M_rapidfuzz', 'R0_reactive'), ('M_qwen7b', 'M_embed'), ('M_embed', 'M_rule'), ('M_oracle', 'M_rapidfuzz')]
    sys.argv = ['x', str(aroot)]; SA.main(); os.replace(aroot / 'summary_closed_loop_site.json', root / 'summary_affected_site.json')


if __name__ == '__main__':
    main()
