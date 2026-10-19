#!/usr/bin/env python3
"""What a wrong notice costs: belief perturbations on the v2 worlds (WWW 2027 paper; contract contracts/perturbation_20261019.json).

Beliefs from the post-fix 7B extract outputs (runs/converter_fix_20261001/compile_test) or the oracle, then perturbed
before the episode; shield with escape fallback and goal keeping exactly as scripts/run_maintenance_v2.py.

Arms (name = P_<kind>_<b>, <b> in qwen7b | oracle):
  P_early{k}_<b>   believed start k intervals too early (s_b - k, floor 0), end unchanged
  P_late{k}_<b>    believed start k intervals too late (s_b + k, cap 12), end pushed to stay after the start
  P_path_<b>       believed path wrong (the next path), timing unchanged
  P_phantom_<b>    the world has NO maintenance (window emptied), the belief claims the arm's usual window: a false notice
  R0_phantom       reactive control on the same phantom worlds (reference: what an unprepared controller scores with no event)
  M_oracle_phantom alias of P_phantom_oracle
Everything else (477 worlds, metric, ns-3 build) as v2; 0 model calls.

    PYTHONPATH=src python scripts/run_maintenance_perturbed.py --contract contracts/perturbation_20261019.json --arm P_early1_qwen7b --out runs/perturbation_20261019/closed_loop/P_early1_qwen7b [--shard i/n]
"""
import importlib.util, re, sys
from pathlib import Path
from vera import maintenance_episode as ME

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rv2', HERE / 'run_maintenance_v2.py'); V2 = importlib.util.module_from_spec(_s); _s.loader.exec_module(V2)
V1 = V2.V1
V1.COMPILED = 'runs/converter_fix_20261001/compile_test'; V1.FILES = {'M_qwen7b_extract': 'qwen7b_extract.jsonl'}
ARM = re.compile(r'^(?:P_(?P<kind>early[12]|late[12]|path|phantom)_(?P<b>qwen7b|oracle)|(?P<r0>R0_phantom)|(?P<mo>M_oracle_phantom))$')


def perturb(kind, b):
    if b is None:
        return None
    p, sb, eb, c = b
    if kind.startswith('early'):
        k = int(kind[-1]); return (p, max(0, sb - k), eb, c)
    if kind.startswith('late'):
        k = int(kind[-1]); sb2 = min(12, sb + k); return (p, sb2, min(12, max(eb, sb2 + 1)), c)
    if kind == 'path':
        return (p % 3 + 1, sb, eb, c)
    return b          # phantom: the belief is the arm's usual one; the world loses its event


def main():
    arm = sys.argv[sys.argv.index('--arm') + 1]; mm = ARM.match(arm); assert mm, arm
    kind = mm.group('kind') or 'phantom'
    base = 'R0_reactive' if mm.group('r0') else ('M_oracle' if (mm.group('mo') or mm.group('b') == 'oracle') else 'M_qwen7b_extract')
    worlds0 = V2.worlds
    if kind == 'phantom':
        def worlds():
            out = []
            for w in worlds0():
                p, s, e, c = w['maintenance']
                out.append(dict(w, id=w['id'] + '#phantom', maintenance=(p, s, s, c)))     # e <= s: the native run gets no event; the metric's window is empty
            return out
        V2.worlds = worlds
    beliefs0 = V1.beliefs
    def beliefs(a, ws):
        if kind == 'phantom' and base == 'M_oracle':     # the false notice claims the ORIGINAL window (the phantom world's own tuple is empty)
            orig = {w['notice']: tuple(w['maintenance']) for w in worlds0()}
            return {w['id']: orig[w['id']] for w in ws}
        src = beliefs0(base, ws)
        if base == 'R0_reactive':
            return src
        return {k: perturb(kind, v) for k, v in src.items()}
    V1.beliefs = beliefs
    V1.ARMS = V1.ARMS + [arm]
    V2.main()


if __name__ == '__main__':
    main()
