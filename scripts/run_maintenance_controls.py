#!/usr/bin/env python3
"""Same-information controls and sensitivity on the v2 worlds (contracts/controls_20261014.json). Beliefs from the post-fix
7B extract outputs (runs/converter_fix_20261001/compile_test) or the oracle; shield with escape fallback as v2.

Arms (name = control_belief):
  C_lead{k}_<b>   fixed-lead heuristic: no goal keeping; k intervals before the believed start, if the proposed configuration is
                  not robust under the belief, take the first step of the shortest escape (k in 1, 2, 3)
  C_greedy_<b>    goal keeping with the search capped at one action ahead
  S_nodes{N}_<b>  goal keeping with the search node budget N (default 20,000)
  S_L{L}_<b>      every notice at lead L (interval 0 = reference start - L x 30 min) instead of the hash-drawn L in {4,5,6};
                  R0_L{L} is the reactive reference on those worlds
where <b> is qwen7b or oracle. Everything else as scripts/run_maintenance_v2.py.

    PYTHONPATH=src python scripts/run_maintenance_controls.py --contract contracts/controls_20261014.json --arm C_lead2_qwen7b --out runs/controls_20261014/closed_loop/C_lead2_qwen7b [--shard i/n]
"""
import argparse, datetime as D, importlib.util, json, math, re, sys
from pathlib import Path
from vera import maintenance_shield as MS, maintenance_episode as ME

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rv2', HERE / 'run_maintenance_v2.py'); V2 = importlib.util.module_from_spec(_s); _s.loader.exec_module(V2)
V1 = V2.V1
V1.COMPILED = 'runs/converter_fix_20261001/compile_test'; V1.FILES = {'M_qwen7b_extract': 'qwen7b_extract.jsonl'}
ARM = re.compile(r'^(C_lead(?P<k>[123])|C_greedy|S_nodes(?P<n>\d+)|S_L(?P<l>\d)|R0_L(?P<l0>\d))_?(?P<b>qwen7b|oracle)?$')


def main():
    arm = sys.argv[sys.argv.index('--arm') + 1]; mm = ARM.match(arm); assert mm, arm
    base = 'R0_reactive' if mm.group('l0') else ('M_qwen7b_extract' if mm.group('b') == 'qwen7b' else 'M_oracle')
    heuristic = int(mm.group('k')) if mm.group('k') else None; planning_on = heuristic is None and base != 'R0_reactive'
    if arm.startswith('C_greedy'):
        MS.SEARCH_DEPTH_CAP = 1
    if mm.group('n'):
        MS.SEARCH_MAX_NODES = int(mm.group('n'))
    L = int(mm.group('l') or mm.group('l0') or 0)
    worlds0 = V2.worlds
    def worlds():
        out = []
        for w in worlds0():
            if L:
                it = w['item']; dur = max(1, math.ceil((V1.ts(it['ref_end']) - V1.ts(it['ref_start'])) / V1.STEP)); S0 = V1.ts(it['ref_start']) - L * V1.STEP
                w = dict(w, S0=S0.isoformat(), maintenance=(w['maintenance'][0], L, min(12, L + dur), w['maintenance'][3]))
            out.append(w)
        return out
    V2.worlds = worlds
    run0 = ME.run
    def run(root, contract, world, belief, factory, shield=True, planning=False):
        return run0(root, contract, world, belief, factory, shield=shield, planning=planning and planning_on, heuristic_lead=heuristic)
    V2.ME.run = run
    # route the arm name to the belief source, keep the output name
    beliefs0 = V1.beliefs
    V1.beliefs = lambda a, ws: beliefs0(base, ws)
    V1.ARMS = V1.ARMS + [arm]
    V2.main()


if __name__ == '__main__':
    main()
