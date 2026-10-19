#!/usr/bin/env python3
"""Affected-object compile benchmark (contracts/affected_scenario_20261013.json): from one announcement, which region of
the provider's list is affected (top-1 exact match to the structured field) and the window (as the announcement
benchmark, extract mode: copy date / times / zone and the region entry; code converts the window).

Arms: rule (scripts/affected_rule.py, developed on the development provider only); models zero-shot, candidate list in
the prompt. Items: data/affected_items_20261013.json (scripts/affected_items.py).

    PYTHONPATH=src python scripts/affected_compile_eval.py rule --part dev
    PYTHONPATH=src python scripts/affected_compile_eval.py models --part test --model qwen7b --model qwen72b --max-calls 1500 --out runs/affected_scenario_20261013/compile/test
"""
import argparse, importlib.util, json, re, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
sys.path.insert(0, str(HERE)); import affected_rule as AR, maintenance_rule as MR
_i = importlib.util.spec_from_file_location('ai', HERE / 'affected_items.py'); AI = importlib.util.module_from_spec(_i); _i.loader.exec_module(AI)
from vera import multiservice_control as m
ITEMS = 'data/affected_items_20261013.json'

EXTRACT_REGION = (mce.EXTRACT.replace('"tz": the time-zone abbreviation', '"region": exactly one entry copied verbatim from the candidate list given in the input (the region, '
                  'data centre or point of presence the maintenance affects; null if none applies), "tz": the time-zone abbreviation'))


def items():
    return json.loads((m.ROOT / ITEMS).read_text())


def messages(it):
    user = json.dumps(dict(posted_at_utc=it['posted_utc'], title=it['title'], announcement=it['text'], candidate_regions=it['candidates']), ensure_ascii=False)
    return [dict(role='system', content=EXTRACT_REGION), dict(role='user', content=user)]


def norm(s):
    return ' '.join(str(s or '').lower().split())


def resolve(reg, candidates):
    """Map an answer to one candidate (applied to every arm): exact; else equal after dropping parenthesised codes; else the
    unique candidate that contains the answer; else the unique candidate whose parenthesised code equals the answer's
    leading letters ('sy4' -> 'Sydney (SY)'). None when nothing applies or several candidates apply."""
    if reg is None:
        return None
    reg = AI.region_of(None, str(reg))            # amendment 1: an answer copied from a 'ZONE: Product' component names the zone
    r = norm(reg); by = {norm(c): c for c in candidates}
    if r in by:
        return by[r]
    strip = lambda s: norm(re.sub(r'\s*\([^)]*\)', '', s))
    hits = [c for c in candidates if strip(c) == strip(r)] or [c for c in candidates if r and r in norm(c)]
    if len(hits) == 1:
        return hits[0]
    mm = re.match(r'^([a-z]{2,4})\d+$', r)
    if mm:
        hits = [c for c in candidates if any(code == mm.group(1) for code in AR.codes(c))]
        if len(hits) == 1:
            return hits[0]
    return None


def score(pred, it):
    reg = (pred or {}).get('region'); strict = norm(reg) == norm(it['region']); res = resolve(reg, it['candidates'])
    return dict(region_ok=strict, region_resolved_ok=res == it['region'], region_in_list=reg is not None and norm(reg) in {norm(c) for c in it['candidates']}, region_resolved=res, **mce.score(pred or {}, it))


def rule_pred(it):
    w = MR.extract(it['text'], it['posted_utc']); return dict(region=AR.pick(it['text'], it['candidates']), **w)


def summarise(rows):
    ends = [r for r in rows if r['score']['end_ok'] is not None]
    return dict(n=len(rows), region_ok=sum(r['score']['region_ok'] for r in rows), region_resolved_ok=sum(r['score']['region_resolved_ok'] for r in rows), region_in_list=sum(r['score']['region_in_list'] for r in rows),
                start_ok=sum(r['score']['start_ok'] for r in rows), end_ok=sum(bool(r['score']['end_ok']) for r in ends), end_n=len(ends),
                by_site={s: dict(n=sum(1 for r in rows if r['site'] == s), region_ok=sum(r['score']['region_ok'] for r in rows if r['site'] == s), region_resolved_ok=sum(r['score']['region_resolved_ok'] for r in rows if r['site'] == s)) for s in sorted({r['site'] for r in rows})})


def main():
    p = argparse.ArgumentParser(); p.add_argument('cmd', choices=['rule', 'models']); p.add_argument('--part', default='dev'); p.add_argument('--model', action='append', default=[])
    p.add_argument('--out'); p.add_argument('--max-calls', type=int, default=100); a = p.parse_args()
    sel = [i for i in items() if i['part'] == a.part]
    if a.cmd == 'rule':
        rows = [dict(i, pred=rule_pred(i)) for i in sel]
        for r in rows: r['score'] = score(r['pred'], r)
        print('rule', json.dumps(summarise(rows)))
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True); (Path(a.out) / 'rule.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        return
    spec = importlib.util.spec_from_file_location('oce', HERE / 'offline_compile_eval.py'); oce = importlib.util.module_from_spec(spec); spec.loader.exec_module(oce)
    assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); calls = 0; summary = {}
    for name in a.model:
        chat = oce.Chat(assets[name]['directory'], None, quant=assets[name].get('quant')); rows = []; label = f'{name}_extract'
        for i in sel:
            if calls >= a.max_calls:
                break
            raw, ms = chat(messages(i), max_new_tokens=120); calls += 1
            o = mce.decode(raw); pred = dict(region=o.get('region'), **mce.convert(o))
            rows.append(dict(i, raw=raw, extracted=o, pred=pred, ms=ms, score=score(pred, i)))
        (out / f'{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        summary[label] = summarise(rows); print(label, json.dumps(summary[label]), flush=True)
        del chat.model; import torch; torch.cuda.empty_cache()
    (out / 'summary.json').write_text(json.dumps(dict(part=a.part, calls=calls, arms=summary), indent=1))


if __name__ == '__main__':
    main()
