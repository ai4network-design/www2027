#!/usr/bin/env python3
"""Re-score stored revision compile outputs against the current item labels (no model call) and compare arms: action
accuracy by kind, strict/loose bound accuracy, paired wins/losses and mean differences with a cluster bootstrap over
notices (10,000 resamples, seed 0).

    PYTHONPATH=src python scripts/rescore_revision_compile.py runs/revision_scenario_20261011/compile/upd --json out.json
"""
import argparse, importlib.util, json, random
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rce', HERE / 'revision_compile_eval.py'); RCE = importlib.util.module_from_spec(_s); _s.loader.exec_module(RCE)
ARMS = ['qwen7b_extract', 'qwen7b_direct', 'qwen72b_extract', 'qwen72b_direct', 'llama70b_extract', 'llama70b_direct', 'llama8b_extract', 'llama8b_direct', 'dateparser_raw', 'dateparser_glue', 'sutime_raw', 'sutime_glue']
PAIRS = [('qwen7b_extract', 'rule'), ('qwen7b_extract', 'rule_kw'), ('qwen72b_direct', 'rule'), ('qwen72b_direct', 'rule_kw'), ('qwen72b_direct', 'qwen7b_extract'), ('qwen7b_direct', 'qwen7b_extract'), ('llama70b_extract', 'rule_kw'), ('llama70b_direct', 'rule_kw'), ('llama8b_extract', 'rule_kw'), ('llama70b_direct', 'qwen72b_direct'), ('llama8b_extract', 'qwen7b_extract'), ('qwen7b_extract', 'dateparser_glue'), ('dateparser_glue', 'rule_kw'), ('qwen7b_extract', 'sutime_glue'), ('sutime_glue', 'rule_kw')]
RES, SEED = 10000, 0


def main():
    a = argparse.ArgumentParser(); a.add_argument('root'); a.add_argument('--part', default='test'); a.add_argument('--json'); a = a.parse_args()
    items = {i['id']: dict(i, truth=RCE.ACTION[i['kind']]) for i in RCE.items() if i['part'] == a.part}
    preds = {}
    for arm in ARMS:
        f = Path(a.root) / f'{arm}.jsonl'
        if f.exists():
            preds[arm] = {r['id']: r['pred'] for r in (json.loads(l) for l in f.read_text().splitlines() if l.strip()) if r['id'] in items}
    preds['rule'] = {k: RCE.rule_pred(i, False) for k, i in items.items()}; preds['rule_kw'] = {k: RCE.rule_pred(i, True) for k, i in items.items()}
    ids = sorted(set.intersection(*[set(v) for v in preds.values()])); sc = {arm: {k: RCE.score(preds[arm][k], items[k]) for k in ids} for arm in preds}
    out = dict(n=len(ids), arms={}, pairs={})
    print(f'{len(ids)} {a.part} items; truth kinds: ' + json.dumps({t: sum(1 for k in ids if items[k]["truth"] == t) for t in ("cancelled", "moved", "extended")}))
    print('| arm | action ok | cancelled | moved | extended | bound strict | bound loose |'); print('|---|---|---|---|---|---|---|')
    for arm, s in sc.items():
        b = [k for k in ids if s[k]['bound_ok'] is not None]
        r = dict(action_ok=sum(s[k]['action_ok'] for k in ids), by_kind={t: sum(s[k]['action_ok'] for k in ids if items[k]['truth'] == t) for t in ('cancelled', 'moved', 'extended')},
                 bound_n=len(b), bound_ok=sum(s[k]['bound_ok'] for k in b), bound_ok_loose=sum(s[k]['bound_ok_loose'] for k in b))
        out['arms'][arm] = r
        print(f"| {arm} | {r['action_ok']}/{len(ids)} | {r['by_kind']['cancelled']} | {r['by_kind']['moved']} | {r['by_kind']['extended']} | {r['bound_ok']}/{r['bound_n']} | {r['bound_ok_loose']}/{r['bound_n']} |")
    notices = defaultdict(list)
    for k in ids:
        notices[items[k]['notice']].append(k)
    keys = sorted(notices); rng = random.Random(SEED); draws = [[rng.choice(keys) for _ in keys] for _ in range(RES)]
    print('\n| comparison | wins / losses | difference [95%] |'); print('|---|---|---|')
    for x, y in PAIRS:
        if x not in sc or y not in sc:
            continue
        for f, lab in (('action_ok', 'action'), ('bound_ok', 'bound strict'), ('bound_ok_loose', 'bound loose')):
            d = {k: int(bool(sc[x][k][f])) - int(bool(sc[y][k][f])) for k in ids if f == 'action_ok' or sc[x][k]['bound_ok'] is not None}
            if not d:
                continue
            wins = sum(v > 0 for v in d.values()); losses = sum(v < 0 for v in d.values()); mean = sum(d.values()) / len(d)
            vals = sorted((lambda ks: sum(d[k] for k in ks) / len(ks))([k for s in dr for k in notices[s] if k in d]) for dr in draws if any(k in d for s in dr for k in notices[s]))
            lo, hi = vals[int(.025 * len(vals))], vals[int(.975 * len(vals)) - 1]
            out['pairs'][f'{x} vs {y}, {lab}'] = dict(wins=wins, losses=losses, diff=mean, lo=lo, hi=hi)
            print(f'| {x} vs {y}, {lab} | {wins} / {losses} | {mean:+.3f} [{lo:+.3f}, {hi:+.3f}] |')
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
