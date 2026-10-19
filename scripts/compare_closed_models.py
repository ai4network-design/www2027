#!/usr/bin/env python3
"""Paired comparisons for the closed reference models run with OUR prompts (contracts/closed_models_compile_20261019.json).

announcements  scripts/compare_maintenance_compile.py (provider-clustered) beside the post-fix 7B extract, 72B direct and the rule
revisions      scripts/rescore_revision_compile.py with the two new arms linked into a view directory (notice-clustered)
regions        scripts/rescore_affected_compile.py with the two new arms linked into a view directory (provider- and notice-clustered)

    PYTHONPATH=src python scripts/compare_closed_models.py runs/closed_models_compile_20261019
"""
import importlib.util, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
NEW = ['gpt-6-luna_extract', 'gpt-5p6-terra_extract']


def link_all(view, src_dirs, arms, new_dir):
    view.mkdir(parents=True, exist_ok=True)
    for a in arms:
        for d in src_dirs:
            src = ROOT / d / f'{a}.jsonl'
            if src.exists() and not (view / f'{a}.jsonl').exists():
                os.symlink(src.resolve(), view / f'{a}.jsonl'); break
    for a in NEW:
        src = new_dir / f'{a}.jsonl'
        if src.exists() and not (view / f'{a}.jsonl').exists():
            os.symlink(src.resolve(), view / f'{a}.jsonl')


def main():
    root = Path(sys.argv[1]); env = dict(os.environ, PYTHONPATH='src')
    print('# announcements\n'); sys.stdout.flush()
    arms = [f'{a}={root / "ann_test" / f"{a}.jsonl"}' for a in NEW if (root / 'ann_test' / f'{a}.jsonl').exists()]
    arms += ['7b_extract=runs/converter_fix_20261001/compile_test/qwen7b_extract.jsonl', '72b_direct=runs/model_scale_20261001/compile_test/qwen72b_direct.jsonl', '72b_extract=runs/converter_fix_20261001/compile_test/qwen72b_extract.jsonl']
    new = [a for a in NEW if (root / 'ann_test' / f'{a}.jsonl').exists()]
    pairs = [f'{a}:7b_extract' for a in new] + [f'{a}:rule' for a in new] + [f'{a}:72b_direct' for a in new] + (['gpt-6-luna_extract:gpt-5p6-terra_extract'] if len(new) == 2 else [])
    cmd = [sys.executable, str(HERE / 'compare_maintenance_compile.py'), '--rule', '--pairs', ','.join(pairs), '--json', str(root / 'announcement_comparison.json')]
    for a in arms:
        cmd += ['--arm', a]
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)
    print('\n# revisions\n'); sys.stdout.flush()
    _s = importlib.util.spec_from_file_location('rrc', HERE / 'rescore_revision_compile.py'); RR = importlib.util.module_from_spec(_s); _s.loader.exec_module(RR)
    view = root / 'rev_view'; link_all(view, ['runs/revision_scenario_20261011/compile/upd', 'runs/model_family_20261014/revision'], RR.ARMS, root / 'upd_test')
    RR.ARMS = RR.ARMS + NEW
    RR.PAIRS = [(a, 'qwen7b_extract') for a in NEW] + [(a, 'rule') for a in NEW] + [(a, 'rule_kw') for a in NEW] + [(a, 'qwen72b_direct') for a in NEW] + [('gpt-6-luna_extract', 'gpt-5p6-terra_extract')]
    sys.argv = ['x', str(view), '--json', str(root / 'revision_comparison.json')]; RR.main()
    print('\n# regions\n'); sys.stdout.flush()
    _a = importlib.util.spec_from_file_location('rac', HERE / 'rescore_affected_compile.py'); RA = importlib.util.module_from_spec(_a); _a.loader.exec_module(RA)
    aview = root / 'aff_view'; link_all(aview, ['runs/affected_scenario_20261013/compile/test', 'runs/model_family_20261014/affected', 'runs/sota_baselines_20261015/entity'], RA.ARMS, root / 'aff_test')
    RA.ARMS = RA.ARMS + NEW
    RA.PAIRS = [(a, 'qwen7b_extract') for a in NEW] + [(a, 'rule') for a in NEW] + [(a, 'qwen72b_extract') for a in NEW] + [(a, 'rapidfuzz') for a in NEW] + [('gpt-6-luna_extract', 'gpt-5p6-terra_extract')]
    sys.argv = ['x', str(aview), '--json', str(root / 'affected_comparison.json')]; RA.main()


if __name__ == '__main__':
    main()
