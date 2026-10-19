#!/usr/bin/env python3
"""Maintenance capacity-drop diagnostic runner (design: docs/reproduce/maintenance_scenario_design_20260930.md).

Worlds: test-split public maintenance notices whose announcement states a clock time and that have both an
automatic start and end reference. Clock: one interval = 30 minutes; interval 0 = reference start - L x 30 min with
L in {4, 5, 6} by hash; true window [L, L + ceil(duration / 30 min)) clipped to the 12 intervals; path in {1, 2, 3}
and remaining capacity in {1.5, 2, 3} Mbps by hash. Beliefs map a compiled UTC window with the same clock; a missing
start compiles to 'may start now' (interval 2 to 12), a missing end to 'until the episode ends'. Compile outputs are
reused from runs/maintenance_compile_20260929 (same prompts, greedy decoding): no model call here.

    PYTHONPATH=src python scripts/run_maintenance_diagnostic.py --arm M_qwen7b_extract --out runs/maintenance_diagnostic_<date>/M_qwen7b_extract --native real
"""
import argparse, datetime as D, hashlib, importlib.util, json, math
from pathlib import Path
from vera import multiservice_control as m, maintenance_episode as ME
from vera.maintenance_native import MaintNative
from vera.zero_shot_evidence import save

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
import sys; sys.path.insert(0, str(HERE)); import maintenance_rule as MR

NOTICES = 'data/maintenance_notices_20260929'
COMPILED = 'runs/maintenance_compile_20260929'
STEP = D.timedelta(minutes=30)
ARMS = ['R0_reactive', 'M_worst', 'M_rule', 'M_qwen7b_extract', 'M_qwen7b_direct', 'M_qwen1p5b_lora', 'M_oracle', 'M_qwen7b_extract_noplan', 'M_oracle_noplan']
FILES = {'M_qwen7b_extract': 'test_7b/qwen7b_extract.jsonl', 'M_qwen7b_direct': 'test_7b/qwen7b_direct.jsonl', 'M_qwen1p5b_lora': 'test_lora/qwen1p5b_lora_extract.jsonl'}


def h(x, mod):
    return int(hashlib.sha256(x.encode()).hexdigest()[:8], 16) % mod


def ts(v):
    return D.datetime.fromisoformat(v)


def worlds(part='test'):
    out = []
    for i in mce.items(m.ROOT / NOTICES):
        if i['part'] != part or not i['ref_end']:
            continue
        L = 4 + h(i['id'] + 'L', 3); S0 = ts(i['ref_start']) - L * STEP
        dur = max(1, math.ceil((ts(i['ref_end']) - ts(i['ref_start'])) / STEP))
        p = 1 + h(i['id'] + 'p', 3); c = [1.5, 2.0, 3.0][h(i['id'] + 'c', 3)]
        out.append(dict(id=i['id'], item=i, seed=h(i['id'] + 'seed', 2 ** 31), S0=S0.isoformat(), maintenance=(p, L, min(12, L + dur), c)))
    return out


def belief_from_utc(world, start_utc, end_utc):
    p, _, _, c = world['maintenance']; S0 = ts(world['S0'])
    try:
        sb = math.floor((ts(start_utc) - S0) / STEP) if start_utc else None
    except (TypeError, ValueError):
        sb = None
    try:
        eb = math.ceil((ts(end_utc) - S0) / STEP) if end_utc else None
    except (TypeError, ValueError):
        eb = None
    if sb is None:
        return (p, ME.NOTICE_TICK, 12, c)          # timing unknown: may start now, until the end
    return (p, sb, min(12, eb) if eb is not None and eb > sb else 12, c)


def beliefs(arm, ws):
    arm = arm.replace('_noplan', '')
    if arm == 'R0_reactive':
        return {w['id']: None for w in ws}
    if arm == 'M_worst':
        return {w['id']: (w['maintenance'][0], ME.NOTICE_TICK, 12, w['maintenance'][3]) for w in ws}
    if arm == 'M_oracle':
        return {w['id']: tuple(w['maintenance']) for w in ws}
    if arm == 'M_rule':
        return {w['id']: belief_from_utc(w, *[MR.extract(w['item']['text'], w['item']['posted_utc'])[k] for k in ('start_utc', 'end_utc')]) for w in ws}
    rows = {json_['id']: json_ for json_ in (json.loads(l) for l in (m.ROOT / COMPILED / FILES[arm]).read_text().splitlines() if l.strip())}
    return {w['id']: belief_from_utc(w, (rows[w['id']]['pred'] or {}).get('start_utc'), (rows[w['id']]['pred'] or {}).get('end_utc')) for w in ws}


def main():
    a = argparse.ArgumentParser(); a.add_argument('--arm', required=True, choices=ARMS); a.add_argument('--out', required=True)
    a.add_argument('--native', choices=['real'], default='real'); a.add_argument('--max-worlds', type=int)
    a.add_argument('--planning', action='store_true', help='forecast-aware goal keeping on top of the shield'); a.add_argument('--path', type=int, help='only worlds whose maintenance is on this path')
    a.add_argument('--contract', help='formal run: verify the pinned source SHA-256 of the maintenance contract; planning follows the arm')
    a = a.parse_args()
    if a.contract:
        mc = json.loads((m.ROOT / a.contract).read_text()); bad = [f for f, v in mc['sources'].items() if m.sha(m.ROOT / f) != v]
        assert not bad, f'source mismatch against the contract: {bad}'
        assert a.max_worlds is None and a.path is None, 'formal run uses every test world'
        a.planning = not a.arm.endswith('_noplan')
    c = json.loads((m.ROOT / 'contracts/frozen_protocol_20260924_20261008.json').read_text())
    build = json.loads((m.ROOT / 'runs/ns3_maintenance_build_20260930/native_maint.json').read_text())
    assert m.sha(build['binary']) == build['binary_sha256'] and m.sha(m.ROOT / build['source']) == build['source_sha256']
    ws = [w for w in worlds() if a.path is None or w['maintenance'][0] == a.path][:a.max_worlds]; bel = beliefs(a.arm, ws); root = Path(a.out); root.mkdir(parents=True, exist_ok=True)
    factory = lambda out, seed, n, mm: MaintNative(build, out, seed, n, mm)
    cells = []
    for w in ws:
        d = root / w['id'].replace('/', '__')
        if (d / 'report.json').exists():
            rep = json.loads((d / 'report.json').read_text())
        else:
            rep, _ = ME.run(d, c, w, bel[w['id']], factory, shield=a.arm != 'R0_reactive', planning=a.planning and a.arm != 'R0_reactive')
        cells.append(dict(world=w['id'], site=w['item']['site'], maintenance=list(w['maintenance']), belief=rep['belief'], ok=rep['ok'], error=rep['error'],
                          metric=rep['metric'], uncertified=rep['uncertified_intervals'], fallbacks=rep['fallbacks'], goal_steps=rep.get('goal_steps')))
        print(json.dumps(dict(w=w['id'][:40], maint=w['maintenance'], belief=rep['belief'], sat=round(rep['metric']['satisfaction'], 3), win=f"{rep['metric']['window_met']}/{rep['metric']['window_intervals']}",
                              hard=rep['metric']['hard_sla_violation_intervals'], unc=rep['uncertified_intervals'], goal=rep.get('goal_steps'), err=rep['error'])), flush=True)
    (root / 'report.json').write_text(json.dumps(dict(arm=a.arm, cells=cells), ensure_ascii=False))


if __name__ == '__main__':
    main()
