#!/usr/bin/env python3
"""Closed-loop summary of the second tool-baseline arms beside the existing arms (contracts/tool_baselines_2_20261019.json step 2).

    python3 scripts/summarise_tool2.py runs/tool_baselines_2_20261019
"""
import importlib.util, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('smd', HERE / 'summarise_maintenance_diagnostic.py'); S = importlib.util.module_from_spec(_s); _s.loader.exec_module(S)
ROOT = Path(__file__).resolve().parents[1]


def link(t, src):
    if not t.exists() and (src / 'report.json').exists():
        os.symlink(src.resolve(), t)


def main():
    root = Path(sys.argv[1]); view = root / 'view_maint' / 'primary'; view.mkdir(parents=True, exist_ok=True)
    link(view / 'R0_reactive', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/R0_reactive'); link(view / 'M_oracle', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/M_oracle')
    link(view / 'M_rule', ROOT / 'runs/converter_fix_20261001/closed_loop/M_rule'); link(view / 'M_qwen7b_extract', ROOT / 'runs/converter_fix_20261001/closed_loop/M_qwen7b_extract')
    link(view / 'M_sutime_glue', ROOT / 'runs/sota_baselines_20261015/closed_loop/M_sutime_glue')
    new = [a for a in ('M_heideltime_glue', 'M_cmp_llm_local_qwen7b', 'M_cmp_llm_local_qwen7b_lenient', 'M_cmp_llm_gpt-6-luna', 'M_cmp_llm_gpt-6-luna_lenient', 'M_cmp_llm_gpt-5p6-terra', 'M_cmp_llm_gpt-5p6-terra_lenient') if (root / 'closed_loop' / a / 'report.json').exists()]
    for a in new:
        link(view / a, root / 'closed_loop' / a)
    S.PRIMARY = ['R0_reactive', 'M_sutime_glue'] + new + ['M_rule', 'M_qwen7b_extract', 'M_oracle']; S.LANG = 'M_qwen7b_extract'
    S.PAIRS = [('M_qwen7b_extract', a) for a in new] + [(a, 'R0_reactive') for a in new] + [('M_rule', a) for a in new] + [(a, 'M_sutime_glue') for a in new]
    sys.argv = ['x', str(root / 'view_maint'), '--part', 'primary']; S.main(); os.replace(root / 'view_maint' / 'summary_primary.json', root / 'summary_maintenance.json')


if __name__ == '__main__':
    main()
