#!/usr/bin/env python3
"""Closed-loop summary of the revision scenario (contracts/revision_scenario_20261011.json step 2): the v2 summariser with
the revision arms, clustered by notice (the cells' 'notice' field stands in for the provider cluster), plus a breakdown
by truth kind (cancelled / moved / extended).

    python3 scripts/summarise_revision_scenario.py runs/revision_scenario_20261011
"""
import importlib.util, json, os, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('smd', HERE / 'summarise_maintenance_diagnostic.py'); S = importlib.util.module_from_spec(_s); _s.loader.exec_module(S)
ARMS = ['R0_reactive', 'M_initial_rule', 'M_update_rule', 'M_update_rule_kw', 'M_initial_qwen7b_extract', 'M_update_qwen7b_extract', 'M_initial_qwen72b_direct', 'M_update_qwen72b_direct', 'M_oracle']
PAIRS = [('M_update_rule', 'M_initial_rule'), ('M_update_rule_kw', 'M_initial_rule'), ('M_update_qwen7b_extract', 'M_initial_qwen7b_extract'), ('M_update_qwen72b_direct', 'M_initial_qwen72b_direct'),
         ('M_update_qwen7b_extract', 'M_update_rule'), ('M_update_qwen7b_extract', 'M_update_rule_kw'), ('M_update_qwen72b_direct', 'M_update_qwen7b_extract'),
         ('M_update_qwen7b_extract', 'R0_reactive'), ('M_initial_qwen7b_extract', 'R0_reactive'), ('M_oracle', 'M_update_qwen7b_extract'), ('M_oracle', 'R0_reactive')]


def main():
    root = Path(sys.argv[1]); view = root / 'view' / 'primary'; view.mkdir(parents=True, exist_ok=True)
    for a in ARMS:
        t = view / a
        if not t.exists() and (root / 'closed_loop' / a / 'report.json').exists():
            os.symlink((root / 'closed_loop' / a).resolve(), t)
    present = [a for a in ARMS if (view / a).exists()]
    load0 = S.load
    def load(r, arms=None):
        d = load0(r, arms)
        for cells in d.values():
            for c in cells.values():
                c['site'] = c['notice']
        return d
    kinds_arg = sys.argv[2].split(',') if len(sys.argv) > 2 else None     # e.g. cancelled,moved (amendment 1 scope)
    load1 = load
    def load(r, arms=None):
        d = load1(r, arms)
        if kinds_arg:
            for a in d:
                d[a] = {w: c for w, c in d[a].items() if c['truth'] in kinds_arg}
        return d
    S.load = load; S.PRIMARY = present; S.LANG = 'M_update_qwen7b_extract'; S.PAIRS = [p for p in PAIRS if p[0] in present and p[1] in present]
    sys.argv = ['x', str(root / 'view'), '--part', 'primary']
    S.main()
    os.replace(root / 'view' / 'summary_primary.json', root / ('summary_closed_loop.json' if not kinds_arg else 'summary_closed_loop_' + '_'.join(kinds_arg) + '.json'))
    d = load0(root / 'view' / 'primary', present); worlds = sorted(set.intersection(*[set(v) for v in d.values()]))
    kinds = defaultdict(list)
    for w in worlds:
        kinds[d[present[0]][w]['truth']].append(w)
    print('\nSatisfaction by truth kind (descriptive):\n\n| kind | n | ' + ' | '.join(present) + ' |\n' + '|---' * (len(present) + 2) + '|')
    for k, ws in sorted(kinds.items()):
        print(f'| {k} | {len(ws)} | ' + ' | '.join(f"{S.mean([S.val(d[a][w], 'sat') for w in ws]):.3f}" for a in present) + ' |')
    print('\nHard-SLA intervals per episode by truth kind:\n\n| kind | ' + ' | '.join(present) + ' |\n' + '|---' * (len(present) + 1) + '|')
    for k, ws in sorted(kinds.items()):
        print(f'| {k} | ' + ' | '.join(f"{S.mean([S.val(d[a][w], 'hard') for w in ws]):.2f}" for a in present) + ' |')


if __name__ == '__main__':
    main()
