#!/usr/bin/env python3
"""Paired comparisons for the second tool-baseline set (contracts/tool_baselines_2_20261019.json step 1).

announcements  scripts/compare_maintenance_compile.py with the new arms beside the post-fix 7B extract, SUTime glue and the rule
revisions      scripts/rescore_revision_compile.py with the new arms linked into a view directory under the names it expects

    PYTHONPATH=src python scripts/compare_tool2.py runs/tool_baselines_2_20261019
"""
import importlib.util, json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
NEW = ['heideltime_raw', 'heideltime_glue', 'cmp_rules', 'cmp_llm_local_qwen7b', 'cmp_llm_local_qwen7b_lenient', 'cmp_llm_gpt-6-luna', 'cmp_llm_gpt-6-luna_lenient', 'cmp_llm_gpt-5p6-terra', 'cmp_llm_gpt-5p6-terra_lenient']


def main():
    root = Path(sys.argv[1]); time = root / 'time'
    ann = [a for a in NEW if (time / f'ann_test_{a}.jsonl').exists()]
    arms = [f'{a}={time / f"ann_test_{a}.jsonl"}' for a in ann]
    arms += ['7b_extract=runs/converter_fix_20261001/compile_test/qwen7b_extract.jsonl', 'sutime_glue=runs/sota_baselines_20261015/time/ann_test_sutime_glue.jsonl']
    pairs = [f'7b_extract:{a}' for a in ann] + [f'{a}:rule' for a in ann] + [f'{a}:sutime_glue' for a in ann]
    cmd = [sys.executable, str(HERE / 'compare_maintenance_compile.py'), '--rule', '--pairs', ','.join(pairs), '--json', str(root / 'announcement_comparison.json')]
    for a in arms:
        cmd += ['--arm', a]
    print('# announcements\n'); sys.stdout.flush(); subprocess.run(cmd, check=True, cwd=ROOT, env=dict(os.environ, PYTHONPATH='src'))
    print('\n# revisions\n'); view = root / 'rev_view'; view.mkdir(exist_ok=True)
    _s = importlib.util.spec_from_file_location('rrc', HERE / 'rescore_revision_compile.py'); RR = importlib.util.module_from_spec(_s); _s.loader.exec_module(RR)
    for a in RR.ARMS:
        src = ROOT / 'runs/revision_scenario_20261011/compile/upd' / f'{a}.jsonl'
        if not src.exists():
            src = ROOT / 'runs/sota_baselines_20261015/time' / f'upd_test_{a}.jsonl'
        if src.exists() and not (view / f'{a}.jsonl').exists():
            os.symlink(src.resolve(), view / f'{a}.jsonl')
    rev = [a for a in NEW if (time / f'upd_test_{a}.jsonl').exists()]
    for a in rev:
        if not (view / f'{a}.jsonl').exists():
            os.symlink((time / f'upd_test_{a}.jsonl').resolve(), view / f'{a}.jsonl')
    RR.ARMS = RR.ARMS + rev
    RR.PAIRS = [('qwen7b_extract', a) for a in rev] + [(a, 'rule') for a in rev] + [(a, 'rule_kw') for a in rev] + [(a, 'sutime_glue') for a in rev]
    sys.argv = ['x', str(view), '--json', str(root / 'revision_comparison.json')]; RR.main()


if __name__ == '__main__':
    main()
