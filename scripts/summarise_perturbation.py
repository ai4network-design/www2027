#!/usr/bin/env python3
"""Summary of the belief-perturbation arms (contracts/perturbation_20261019.json) beside the unperturbed arms.

timing/path arms are paired with the unperturbed arm of the same belief source and with reactive control on the 477 v2
worlds; the phantom arms (worlds without an event) are paired with R0_phantom on the same phantom worlds.

    python3 scripts/summarise_perturbation.py runs/perturbation_20261019
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
    root = Path(sys.argv[1]); cl = root / 'closed_loop'
    print('# belief perturbations on the v2 worlds\n'); view = root / 'view_timing' / 'primary'; view.mkdir(parents=True, exist_ok=True)
    link(view / 'R0_reactive', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/R0_reactive'); link(view / 'M_oracle', ROOT / 'runs/maintenance_diagnostic_v2_20260930/primary/M_oracle')
    link(view / 'M_qwen7b_extract', ROOT / 'runs/converter_fix_20261001/closed_loop/M_qwen7b_extract')
    timing = [a for a in ('P_early1_qwen7b', 'P_early2_qwen7b', 'P_late1_qwen7b', 'P_late2_qwen7b', 'P_path_qwen7b', 'P_early1_oracle', 'P_early2_oracle', 'P_late1_oracle', 'P_late2_oracle', 'P_path_oracle') if (cl / a / 'report.json').exists()]
    for a in timing:
        link(view / a, cl / a)
    S.PRIMARY = ['R0_reactive', 'M_qwen7b_extract', 'M_oracle'] + timing; S.LANG = 'M_qwen7b_extract'
    S.PAIRS = [(a, 'M_qwen7b_extract' if a.endswith('qwen7b') else 'M_oracle') for a in timing] + [(a, 'R0_reactive') for a in timing]
    sys.argv = ['x', str(root / 'view_timing'), '--part', 'primary']; S.main(); os.replace(root / 'view_timing' / 'summary_primary.json', root / 'summary_timing.json')
    print('\n\n# false notice: phantom worlds (no event)\n'); pv = root / 'view_phantom' / 'primary'; pv.mkdir(parents=True, exist_ok=True)
    phantom = [a for a in ('R0_phantom', 'P_phantom_qwen7b', 'P_phantom_oracle') if (cl / a / 'report.json').exists()]
    for a in phantom:
        link(pv / a, cl / a)
    S.PRIMARY = phantom; S.LANG = 'P_phantom_qwen7b'; S.PAIRS = [(a, 'R0_phantom') for a in phantom if a != 'R0_phantom']
    sys.argv = ['x', str(root / 'view_phantom'), '--part', 'primary']; S.main(); os.replace(root / 'view_phantom' / 'summary_primary.json', root / 'summary_phantom.json')


if __name__ == '__main__':
    main()
