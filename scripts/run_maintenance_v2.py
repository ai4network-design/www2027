#!/usr/bin/env python3
"""Maintenance confirmatory run v2 (contracts/maintenance_diagnostic_v2_20260930.json).

Same 159 test notices, clock mapping (L, true window), ns-3 seed, beliefs, planner and metric as v1
(scripts/run_maintenance_diagnostic.py); two design corrections registered before any v2 result:
* every notice is played on path 3 at each remaining capacity in {1.5, 2, 3} Mbps (477 worlds per arm; in v1 only the
  59 path-3 worlds separated the arms);
* the registered shield fallback is the shortest escape (src/vera/maintenance_escape.py): when the believed window has
  opened, or the start can no longer be met, take the first step of the shortest legal path to a robust configuration
  within the rest of the believed window, so the timing-free comparator M_worst actually prepares.
This is a design-corrected replication on notices whose v1 results were already seen, not a fresh-sample test.

    PYTHONPATH=src python scripts/run_maintenance_v2.py --contract contracts/maintenance_diagnostic_v2_20260930.json --arm M_worst --out runs/maintenance_diagnostic_v2_20260930/primary/M_worst [--shard 0/3]
"""
import argparse, importlib.util, json
from pathlib import Path
from vera import maintenance_escape, multiservice_control as m, maintenance_episode as ME
from vera.maintenance_native import MaintNative

maintenance_escape.install()
HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rmd', HERE / 'run_maintenance_diagnostic.py'); V1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(V1)
CAPS = (1.5, 2.0, 3.0)
PATH = 3


def worlds():
    out = []
    for w in V1.worlds():
        _, L, e, _ = w['maintenance']
        for c in CAPS:
            out.append(dict(w, id=f"{w['id']}#c{c}", notice=w['id'], maintenance=(PATH, L, e, c)))
    return out


def main():
    a = argparse.ArgumentParser(); a.add_argument('--arm', required=True, choices=V1.ARMS); a.add_argument('--out', required=True)
    a.add_argument('--contract', required=True); a.add_argument('--shard', help='i/n: run only worlds with index % n == i (report assembled by an unsharded pass)')
    a.add_argument('--max-worlds', type=int, help='smoke only'); a = a.parse_args()
    mc = json.loads((m.ROOT / a.contract).read_text()); bad = [f for f, v in mc['sources'].items() if m.sha(m.ROOT / f) != v]
    assert not bad, f'source mismatch against the contract: {bad}'
    planning = a.arm != 'R0_reactive' and not a.arm.endswith('_noplan')
    c = json.loads((m.ROOT / 'contracts/frozen_protocol_20260924_20261008.json').read_text())
    build = json.loads((m.ROOT / 'runs/ns3_maintenance_build_20260930/native_maint.json').read_text())
    assert m.sha(build['binary']) == build['binary_sha256'] and m.sha(m.ROOT / build['source']) == build['source_sha256']
    ws = worlds()[:a.max_worlds]
    nb = V1.beliefs(a.arm, [dict(w, id=w['notice']) for w in ws])   # stored compile outputs are keyed by notice id
    bel = {w['id']: (None if nb[w['notice']] is None else nb[w['notice']][:3] + (w['maintenance'][3],)) for w in ws}
    root = Path(a.out); root.mkdir(parents=True, exist_ok=True)
    factory = lambda out, seed, n, mm: MaintNative(build, out, seed, n, mm)
    idx = range(len(ws))
    if a.shard:
        i, n = map(int, a.shard.split('/')); idx = [k for k in idx if k % n == i]
    cells = []
    for k in idx:
        w = ws[k]; d = root / w['id'].replace('/', '__')
        if (d / 'report.json').exists():
            rep = json.loads((d / 'report.json').read_text())
        else:
            rep, _ = ME.run(d, c, w, bel[w['id']], factory, shield=a.arm != 'R0_reactive', planning=planning)
            print(json.dumps(dict(w=w['id'][:48], belief=rep['belief'], sat=round(rep['metric']['satisfaction'], 3), hard=rep['metric']['hard_sla_violation_intervals'],
                                  unc=rep['uncertified_intervals'], goal=rep.get('goal_steps'), err=rep['error'])), flush=True)
        cells.append(dict(world=w['id'], notice=w['notice'], site=w['item']['site'], maintenance=list(w['maintenance']), belief=rep['belief'], ok=rep['ok'], error=rep['error'],
                          metric=rep['metric'], uncertified=rep['uncertified_intervals'], fallbacks=rep['fallbacks'], goal_steps=rep.get('goal_steps')))
    if not a.shard:
        (root / 'report.json').write_text(json.dumps(dict(arm=a.arm, planning=planning, cells=cells), ensure_ascii=False))


if __name__ == '__main__':
    main()
