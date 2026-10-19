#!/usr/bin/env python3
"""Re-score stored affected-object compile outputs against the current items (truth and candidate list), no model call,
and compare arms: region accuracy (strict / resolved) per provider, paired wins/losses with cluster bootstrap over
providers and over notices.

    PYTHONPATH=src python scripts/rescore_affected_compile.py runs/affected_scenario_20261013/compile/test --json out.json
"""
import argparse, importlib.util, json, random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('ace', HERE / 'affected_compile_eval.py'); ACE = importlib.util.module_from_spec(_s); _s.loader.exec_module(ACE)
ARMS = ['rule', 'qwen7b_extract', 'qwen72b_extract', 'llama70b_extract', 'llama8b_extract', 'rapidfuzz', 'embed_bge-small-en-v1.5']
PAIRS = [('qwen7b_extract', 'rule'), ('qwen72b_extract', 'rule'), ('qwen72b_extract', 'qwen7b_extract'), ('llama70b_extract', 'rule'), ('llama8b_extract', 'rule'), ('llama70b_extract', 'qwen72b_extract'), ('llama8b_extract', 'qwen7b_extract'), ('qwen7b_extract', 'rapidfuzz'), ('rapidfuzz', 'rule'), ('qwen7b_extract', 'embed_bge-small-en-v1.5'), ('embed_bge-small-en-v1.5', 'rule')]
RES, SEED = 10000, 0


def boot(d, clusters, label):
    keys = sorted(clusters); rng = random.Random(SEED); vals = []
    for _ in range(RES):
        ks = [k for s in (rng.choice(keys) for _ in keys) for k in clusters[s]]
        vals.append(sum(d[k] for k in ks) / len(ks))
    vals.sort(); return vals[int(.025 * RES)], vals[int(.975 * RES) - 1]


def main():
    a = argparse.ArgumentParser(); a.add_argument('root'); a.add_argument('--part', default='test'); a.add_argument('--json'); a = a.parse_args()
    items = {i['id']: i for i in ACE.items() if i['part'] == a.part}
    preds = {'rule': {k: ACE.rule_pred(i) for k, i in items.items()}}
    for arm in ARMS[1:]:
        f = Path(a.root) / f'{arm}.jsonl'
        if f.exists():
            preds[arm] = {r['id']: r['pred'] for r in (json.loads(l) for l in f.read_text().splitlines() if l.strip()) if r['id'] in items}
            for k, r in preds[arm].items():                 # keep the model's raw region answer; the window is unchanged
                pass
    ids = sorted(set.intersection(*[set(v) for v in preds.values()])); sc = {arm: {k: ACE.score(preds[arm][k], items[k]) for k in ids} for arm in preds}
    # write re-scored rows for the closed loop (region_resolved under the current items)
    for arm in preds:
        rows = [dict(items[k], pred=preds[arm][k], score=sc[arm][k]) for k in ids]
        (Path(a.root) / f'{arm}.rescored.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    sites = sorted({items[k]['site'] for k in ids}); out = dict(n=len(ids), arms={}, pairs={})
    print(f'{len(ids)} {a.part} items, {len(sites)} providers'); print('| arm | region strict | region resolved | ' + ' | '.join(s.replace('status.', '').replace('.com', '') for s in sites) + ' | start ok | end ok |'); print('|---' * (5 + len(sites)) + '|')
    for arm, s in sc.items():
        r = dict(strict=sum(s[k]['region_ok'] for k in ids), resolved=sum(s[k]['region_resolved_ok'] for k in ids), by_site={st: f"{sum(s[k]['region_resolved_ok'] for k in ids if items[k]['site'] == st)}/{sum(1 for k in ids if items[k]['site'] == st)}" for st in sites},
                 start_ok=sum(s[k]['start_ok'] for k in ids), end_ok=sum(bool(s[k]['end_ok']) for k in ids))
        out['arms'][arm] = r
        print(f"| {arm} | {r['strict']} | {r['resolved']} | " + ' | '.join(r['by_site'][st] for st in sites) + f" | {r['start_ok']} | {r['end_ok']} |")
    by_site = defaultdict(list); by_notice = defaultdict(list)
    for k in ids:
        by_site[items[k]['site']].append(k); by_notice[k].append(k)
    print('\n| comparison (resolved region) | wins / losses | difference [95% by provider] | [95% by notice] |'); print('|---|---|---|---|')
    for x, y in PAIRS:
        if x not in sc or y not in sc:
            continue
        d = {k: int(sc[x][k]['region_resolved_ok']) - int(sc[y][k]['region_resolved_ok']) for k in ids}
        wins = sum(v > 0 for v in d.values()); losses = sum(v < 0 for v in d.values()); mean = sum(d.values()) / len(ids)
        lo1, hi1 = boot(d, by_site, 'site'); lo2, hi2 = boot(d, by_notice, 'notice')
        out['pairs'][f'{x} vs {y}'] = dict(wins=wins, losses=losses, diff=mean, site_ci=[lo1, hi1], notice_ci=[lo2, hi2])
        print(f'| {x} vs {y} | {wins} / {losses} | {mean:+.3f} [{lo1:+.3f}, {hi1:+.3f}] | [{lo2:+.3f}, {hi2:+.3f}] |')
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
