#!/usr/bin/env python3
"""Seed-replay summary (contracts/seed_replay_20261019.json): per arm, the mean over the three replays of each world, the
replay-to-replay spread, and paired differences on world means with a provider-clustered bootstrap (10,000, seed 20260930).

    python3 scripts/summarise_seeds.py runs/seed_replay_20261019
"""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIG = {'R0_reactive': 'runs/maintenance_diagnostic_v2_20260930/primary/R0_reactive', 'M_oracle': 'runs/maintenance_diagnostic_v2_20260930/primary/M_oracle',
        'M_rule': 'runs/converter_fix_20261001/closed_loop/M_rule', 'M_qwen7b_extract': 'runs/converter_fix_20261001/closed_loop/M_qwen7b_extract'}
ARMS = list(ORIG); PAIRS = [('M_qwen7b_extract', 'R0_reactive'), ('M_qwen7b_extract', 'M_rule'), ('M_oracle', 'M_qwen7b_extract'), ('M_rule', 'R0_reactive')]
RES, SEED = 10000, 20260930


def load(path):
    p = ROOT / path / 'report.json'
    return {c['world']: c for c in json.loads(p.read_text())['cells']} if p.exists() else None


def main():
    root = Path(sys.argv[1]); replays = {}
    for a in ARMS:
        reps = [load(ORIG[a])] + [load(f'{root}/closed_loop/{a}_s{k}') for k in (1, 2)]
        replays[a] = [r for r in reps if r]
    worlds = sorted(set.intersection(*[set(r) for a in ARMS for r in replays[a]]))
    site = {w: next(iter(replays['R0_reactive']))[w]['site'] for w in worlds}; sites = defaultdict(list)
    for w in worlds: sites[site[w]].append(w)
    keys = sorted(sites); rng = random.Random(SEED); draws = [[rng.choice(keys) for _ in keys] for _ in range(RES)]
    def boot(fn):
        vals = sorted(sum(fn(w) * 1 for s in dr for w in sites[s]) / sum(len(sites[s]) for s in dr) for dr in draws); return vals[int(.025 * RES)], vals[int(.975 * RES) - 1]
    out = dict(worlds=len(worlds), providers=len(sites), arms={}, pairs={})
    lines = [f'# seed replay: {len(worlds)} worlds x {len(replays["R0_reactive"])} replays, {len(sites)} providers', '', '| arm | replays | mean of world means | per-replay means | within-world SD (mean) | worlds with any change | hard-SLA (mean) |', '|---|---|---|---|---|---|---|']
    wm = {}
    for a in ARMS:
        reps = replays[a]; sat = {w: [r[w]['metric']['satisfaction'] for r in reps] for w in worlds}; wm[a] = {w: statistics.fmean(v) for w, v in sat.items()}
        per = [statistics.fmean(r[w]['metric']['satisfaction'] for w in worlds) for r in reps]
        sd = statistics.fmean(statistics.pstdev(v) for v in sat.values()); changed = sum(1 for v in sat.values() if max(v) - min(v) > 1e-9)
        hard = statistics.fmean(statistics.fmean(r[w]['metric']['hard_sla_violation_intervals'] for r in reps) for w in worlds)
        out['arms'][a] = dict(replays=len(reps), mean=statistics.fmean(wm[a].values()), per_replay=per, within_sd=sd, changed=changed, hard=hard)
        lines.append(f"| {a} | {len(reps)} | {out['arms'][a]['mean']:.3f} | {', '.join(f'{x:.3f}' for x in per)} | {sd:.3f} | {changed} | {hard:.2f} |")
    lines += ['', '| pair | Δ on world means [95%] | Δ at replay 0 only |', '|---|---|---|']
    for x, y in PAIRS:
        f = lambda w: wm[x][w] - wm[y][w]; d = statistics.fmean(f(w) for w in worlds); lo, hi = boot(f)
        d0 = statistics.fmean(replays[x][0][w]['metric']['satisfaction'] - replays[y][0][w]['metric']['satisfaction'] for w in worlds)
        out['pairs'][f'{x} - {y}'] = dict(diff=d, lo=lo, hi=hi, replay0=d0)
        lines.append(f'| {x} − {y} | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] | {d0:+.3f} |')
    print('\n'.join(lines)); (root / 'summary_seeds.json').write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
