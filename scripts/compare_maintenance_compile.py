#!/usr/bin/env python3
"""Paired comparison of maintenance-compile arms on the test split (contracts/model_scale_20261001.json step 1).

Per item: start_ok, window_ok = start_ok and (end_ok is not False). Pairs: wins/losses and the mean difference with a
provider-clustered bootstrap (10,000 resamples, seed 0, 95% percentile), the convention of the 2026-09-29 pilot.

    PYTHONPATH=src python scripts/compare_maintenance_compile.py --arm 7b_extract=runs/maintenance_compile_20260929/test_7b/qwen7b_extract.jsonl \
        --arm 32b_extract=runs/model_scale_20261001/compile_test/qwen32b_extract.jsonl --rule --pairs 32b_extract:7b_extract,32b_extract:rule
"""
import argparse, importlib.util, json, random, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
sys.path.insert(0, str(HERE)); import maintenance_rule as MR
from vera import multiservice_control as m
RES, SEED = 10000, 0


def flags(sc):
    return dict(start=bool(sc['start_ok']), window=bool(sc['start_ok']) and sc['end_ok'] is not False)


def main():
    a = argparse.ArgumentParser(); a.add_argument('--arm', action='append', default=[]); a.add_argument('--rule', action='store_true')
    a.add_argument('--pairs', default=''); a.add_argument('--root', default='data/maintenance_notices_20260929'); a.add_argument('--json')
    a = a.parse_args()
    items = {i['id']: i for i in mce.items(m.ROOT / a.root) if i['part'] == 'test'}
    arms = {}
    for spec in a.arm:
        lab, path = spec.split('=', 1)
        arms[lab] = {r['id']: flags(r['score']) for r in (json.loads(l) for l in (m.ROOT / path).read_text().splitlines() if l.strip()) if r['id'] in items}
    if a.rule:
        arms['rule'] = {k: flags(mce.score(MR.extract(i['text'], i['posted_utc']), i)) for k, i in items.items()}
    ids = sorted(set.intersection(*[set(v) for v in arms.values()])); sites = defaultdict(list)
    for k in ids:
        sites[items[k]['site']].append(k)
    out = dict(n=len(ids), providers=len(sites), arms={lab: {f: sum(v[k][f] for k in ids) for f in ('start', 'window')} for lab, v in arms.items()}, pairs={})
    print(f'{len(ids)} test items, {len(sites)} providers'); print('| arm | start | window |'); print('|---|---|---|')
    for lab, c in out['arms'].items():
        print(f"| {lab} | {c['start']} | {c['window']} |")
    print('\n| comparison | wins / losses | difference [95%] |'); print('|---|---|---|')
    keys = sorted(sites); rng = random.Random(SEED); draws = [[rng.choice(keys) for _ in keys] for _ in range(RES)]
    for pair in [p for p in a.pairs.split(',') if p]:
        x, y = pair.split(':')
        for f in ('start', 'window'):
            d = {k: int(arms[x][k][f]) - int(arms[y][k][f]) for k in ids}
            wins = sum(v > 0 for v in d.values()); losses = sum(v < 0 for v in d.values()); mean = sum(d.values()) / len(ids)
            vals = sorted(sum(d[k] for s in dr for k in sites[s]) / sum(len(sites[s]) for s in dr) for dr in draws)
            lo, hi = vals[int(.025 * RES)], vals[int(.975 * RES) - 1]
            out['pairs'][f'{x} vs {y}, {f}'] = dict(wins=wins, losses=losses, diff=mean, lo=lo, hi=hi)
            print(f'| {x} vs {y}, {f} | {wins} / {losses} | {mean:+.3f} [{lo:+.3f}, {hi:+.3f}] |')
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
