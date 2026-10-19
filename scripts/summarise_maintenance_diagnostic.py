#!/usr/bin/env python3
"""Summary and pre-registered decision of the maintenance diagnostic (contracts/maintenance_diagnostic_20260930.json).

Per arm: mean satisfaction, window satisfaction (share of true-window intervals with every base obligation met),
hard-SLA violation intervals per episode, uncertified intervals, aborts. Paired differences over worlds; uncertainty by
cluster bootstrap over providers (status-page sites), 10,000 resamples, seed 20260930, 95% percentile intervals.

    python3 scripts/summarise_maintenance_diagnostic.py runs/maintenance_diagnostic_<date> [--part primary|secondary]
"""
import json, random, sys
from collections import defaultdict
from pathlib import Path

PRIMARY = ['R0_reactive', 'M_worst', 'M_rule', 'M_qwen7b_extract', 'M_qwen7b_direct', 'M_qwen1p5b_lora', 'M_oracle']
LANG = 'M_qwen7b_extract'
PAIRS = [(LANG, 'R0_reactive'), (LANG, 'M_worst'), (LANG, 'M_rule'), (LANG, 'M_qwen7b_direct'), ('M_qwen1p5b_lora', LANG),
         ('M_oracle', LANG), ('M_oracle', 'R0_reactive'), ('M_rule', 'R0_reactive'), ('M_worst', 'R0_reactive')]
RESAMPLES, SEED = 10000, 20260930


def load(root, arms):
    out = {}
    for a in arms:
        p = Path(root) / a / 'report.json'
        if p.exists():
            out[a] = {c['world']: c for c in json.loads(p.read_text())['cells']}
    return out


def val(c, k):
    mt = c['metric']
    if k == 'sat':
        return mt['satisfaction']
    if k == 'win':
        return mt['window_met'] / mt['window_intervals'] if mt['window_intervals'] else 1.0
    if k == 'hard':
        return mt['hard_sla_violation_intervals']
    if k == 'unc':
        return c['uncertified']


def boot(sites, fn):
    rng = random.Random(SEED); keys = sorted(sites); vals = []
    for _ in range(RESAMPLES):
        ws = [w for s in (rng.choice(keys) for _ in keys) for w in sites[s]]
        vals.append(fn(ws))
    vals.sort(); return vals[int(.025 * RESAMPLES)], vals[int(.975 * RESAMPLES) - 1]


def mean(xs):
    return sum(xs) / len(xs) if xs else float('nan')


def main():
    root = sys.argv[1]; part = sys.argv[sys.argv.index('--part') + 1] if '--part' in sys.argv else 'primary'
    arms = PRIMARY if part == 'primary' else [LANG + '_noplan', 'M_oracle_noplan']
    d = load(Path(root) / part, arms)
    if part == 'secondary':
        d.update({a: v for a, v in load(Path(root) / 'primary', ['R0_reactive', LANG, 'M_oracle']).items()})
    worlds = sorted(set.intersection(*[set(v) for v in d.values()]))
    any_arm = next(iter(d.values())); sites = defaultdict(list)
    for w in worlds:
        sites[any_arm[w]['site']].append(w)
    res = dict(part=part, worlds=len(worlds), providers=len(sites), arms={}, pairs={})
    lines = [f'# Maintenance diagnostic ({part})', '', f'{len(worlds)} worlds, {len(sites)} providers.', '',
             '| arm | satisfaction | window sat. | hard-SLA int./ep. | uncertified/ep. | aborted | goal steps |', '|---|---|---|---|---|---|---|']
    for a, cells in d.items():
        cs = [cells[w] for w in worlds]
        r = dict(sat=mean([val(c, 'sat') for c in cs]), win=mean([val(c, 'win') for c in cs]), hard=mean([val(c, 'hard') for c in cs]),
                 unc=mean([val(c, 'unc') for c in cs]), aborted=sum(not c['ok'] for c in cs), goal=sum(c.get('goal_steps') or 0 for c in cs))
        r['sat_ci'] = boot(sites, lambda ws: mean([val(cells[w], 'sat') for w in ws]))
        res['arms'][a] = r
        lines.append(f"| {a} | {r['sat']:.3f} [{r['sat_ci'][0]:.3f}, {r['sat_ci'][1]:.3f}] | {r['win']:.3f} | {r['hard']:.2f} | {r['unc']:.2f} | {r['aborted']} | {r['goal']} |")
    pairs = PAIRS if part == 'primary' else [(LANG, LANG + '_noplan'), ('M_oracle', 'M_oracle_noplan'), (LANG + '_noplan', 'R0_reactive')]
    lines += ['', '| pair | Δ satisfaction [95%] | Δ window sat. [95%] | Δ hard-SLA int./ep. [95%] |', '|---|---|---|---|']
    for x, y in pairs:
        if x not in d or y not in d:
            continue
        r = {}
        for k in ('sat', 'win', 'hard'):
            f = lambda ws: mean([val(d[x][w], k) - val(d[y][w], k) for w in ws])
            r[k] = (f(worlds),) + boot(sites, f)
        res['pairs'][f'{x} - {y}'] = r
        lines.append(f"| {x} − {y} | {r['sat'][0]:+.3f} [{r['sat'][1]:+.3f}, {r['sat'][2]:+.3f}] | {r['win'][0]:+.3f} [{r['win'][1]:+.3f}, {r['win'][2]:+.3f}] | {r['hard'][0]:+.2f} [{r['hard'][1]:+.2f}, {r['hard'][2]:+.2f}] |")
    if part == 'primary' and all(p in res['pairs'] for p in (f'{LANG} - R0_reactive', f'{LANG} - M_worst')):
        a = res['pairs'][f'{LANG} - R0_reactive']; b = res['pairs'][f'{LANG} - M_worst']
        g = dict(beats_reactive=a['sat'][1] > 0, beats_worst=b['sat'][1] > 0, hard_not_above_reactive=res['arms'][LANG]['hard'] <= res['arms']['R0_reactive']['hard'],
                 no_abort=res['arms'][LANG]['aborted'] == 0)
        g['verdict'] = 'advance' if all(g.values()) else ('narrow' if a['sat'][1] > 0 or b['sat'][1] > 0 else 'stop')
        res['decision'] = g
        lines += ['', f"Decision (pre-registered): {json.dumps(g)}"]
    # per (path, capacity) breakdown (descriptive)
    lines += ['', 'Satisfaction by (path, remaining Mbps), descriptive:', '', '| path, Mbps | n | ' + ' | '.join(d) + ' |', '|---' * (len(d) + 2) + '|']
    groups = defaultdict(list)
    for w in worlds:
        groups[tuple(any_arm[w]['maintenance'][i] for i in (0, 3))].append(w)
    for g_, ws in sorted(groups.items()):
        lines.append(f'| {g_[0]}, {g_[1]} | {len(ws)} | ' + ' | '.join(f"{mean([val(d[a][w], 'sat') for w in ws]):.3f}" for a in d) + ' |')
    print('\n'.join(lines))
    (Path(root) / f'summary_{part}.json').write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
