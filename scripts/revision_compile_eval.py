#!/usr/bin/env python3
"""Revision compile benchmark: a maintenance announcement plus a later post that changes its timing (cancelled,
rescheduled/postponed, extended). Items: data/maintenance_revisions_merged_20261011.json (scripts/analyse_notice_updates.py
over the 2026-09-29 and 2026-10-01 collections, usable = announcement with a clock time, update with a clock time or a
cancellation, posted before the end). Split by provider group as the announcement benchmark (dev / test).

Targets per item
* action: cancelled | moved (rescheduled or postponed) | extended — label derived from the selection keywords, so a rule
  that knows those keywords reproduces it; reported descriptively.
* new bound: for moved items the new start against the automatic In-progress time; for extended items the new end
  against the automatic Completed time; tolerance ±2 min (strict) and ±60 min (loose; the completion time is noisy).
  Cancelled items have no bound target. Automatic transitions are unreliable for cancelled events (most still fired).

Arms: rule = development rule (scripts/maintenance_rule.py extract) on the update text, no cancellation detection;
rule_kw = same plus the selection keywords for the action (disclosed upper bound for a keyword rule); model arms read
announcement + update with posting times; 'direct' outputs UTC, 'extract' copies date/times/zone and code converts.

    PYTHONPATH=src python scripts/revision_compile_eval.py rule --part dev
    PYTHONPATH=src python scripts/revision_compile_eval.py models --part test --model qwen7b --model qwen72b --mode extract --mode direct --max-calls 640 --out runs/revision_compile_<date>/test
"""
import argparse, datetime as D, importlib.util, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
_a = importlib.util.spec_from_file_location('anu', HERE / 'analyse_notice_updates.py'); anu = importlib.util.module_from_spec(_a); _a.loader.exec_module(anu)
sys.path.insert(0, str(HERE)); import maintenance_rule as MR
from vera import multiservice_control as m
ITEMS = 'data/maintenance_revisions_merged_20261011.json'
ACTION = {'cancel': 'cancelled', 'reschedule': 'moved', 'postpone': 'moved', 'extend': 'extended'}

DIRECT = ('You read a public scheduled-maintenance thread from a service provider: the original announcement and one later update that '
          'changes the plan. Decide what the update does to the maintenance window and give the resulting window in UTC. Return JSON only: '
          '{"action": "cancelled" | "moved" | "extended" | "unchanged", "start_utc": "YYYY-MM-DDTHH:MM" or null, "end_utc": "YYYY-MM-DDTHH:MM" or null}. '
          '"cancelled": the maintenance will not take place (start_utc and end_utc null). "moved": a new start time or date is given (give the new window). '
          '"extended": the maintenance is running longer than announced (keep the start, give the new end). Convert every time zone to UTC with daylight '
          'saving time for the stated date; a date without a year takes the year of the posting time; keep an unchanged bound from the announcement. No explanation.')
EXTRACT = ('You read a public scheduled-maintenance thread from a service provider: the original announcement and one later update that '
           'changes the plan. Decide what the update does and copy the resulting window exactly as written, without converting time zones. Return JSON only: '
           '{"action": "cancelled" | "moved" | "extended" | "unchanged", "date": "YYYY-MM-DD" (start date of the resulting window as written; no year -> year of the '
           'posting time), "start": "HH:MM" (24-hour, as written) or null, "end": "HH:MM" or null, "end_date": "YYYY-MM-DD" or null (only if the end is on another date), '
           '"tz": the zone abbreviation or name written next to the times (for example "UTC", "PDT", "CEST") or null}. "cancelled": no window (all null). '
           '"moved": the new start (and end if given). "extended": keep the announced start, give the new end. If both UTC and a local time are given, copy the UTC one. No explanation.')


def items():
    return json.loads((m.ROOT / ITEMS).read_text())


def messages(it, mode, target='upd'):
    if target == 'ann':   # the announcement alone, with the announcement benchmark's prompts (scripts/maintenance_compile_eval.py)
        return mce.messages(dict(posted_utc=it['announced_utc'], title=it.get('title'), text=it['announcement']), mode)
    user = json.dumps(dict(announcement_posted_at_utc=it['announced_utc'], announcement=it['announcement'], update_posted_at_utc=it['update_utc'], update=it['update']), ensure_ascii=False)
    return [dict(role='system', content=DIRECT if mode == 'direct' else EXTRACT), dict(role='user', content=user)]


def minute(v):
    return mce.parse_minute(v)


def score(pred, it):
    truth = ACTION[it['kind']]; act = (pred or {}).get('action')
    out = dict(action_ok=act == truth, truth=truth, bound_ok=None, bound_ok_loose=None)
    ref = it['ref_start'] if truth == 'moved' else (it['ref_end'] if truth == 'extended' else None)
    if ref:
        key = 'start_utc' if truth == 'moved' else 'end_utc'; v = minute((pred or {}).get(key))
        r = D.datetime.fromisoformat(ref).replace(tzinfo=D.timezone.utc)
        if v is None:
            out['bound_ok'] = out['bound_ok_loose'] = False
        else:
            dt = abs((v - r).total_seconds()); out['bound_ok'] = dt <= mce.TOL_MIN * 60; out['bound_ok_loose'] = dt <= 3600
    return out


def rule_pred(it, keywords=False):
    p = MR.extract(it['update'], it['update_utc'])
    act = 'unchanged'
    if keywords:
        act = ACTION.get(anu.kind_of(it['update']) or '', 'unchanged')
        if act == 'cancelled':
            p = dict(start_utc=None, end_utc=None)
        elif act == 'extended':
            p = dict(start_utc=MR.extract(it['announcement'], it['announced_utc'])['start_utc'], end_utc=p['start_utc'] if p['end_utc'] is None else p['end_utc'])
    return dict(action=act, **p)


def convert_rev(o):
    act = (o or {}).get('action'); w = mce.convert(o or {}) if act not in (None, 'cancelled') else {}
    return dict(action=act, start_utc=w.get('start_utc'), end_utc=w.get('end_utc'))


def summarise(rows):
    b = [r for r in rows if r['score']['bound_ok'] is not None]
    return dict(n=len(rows), action_ok=sum(r['score']['action_ok'] for r in rows), bound_n=len(b), bound_ok=sum(r['score']['bound_ok'] for r in b), bound_ok_loose=sum(r['score']['bound_ok_loose'] for r in b),
                by_kind={k: dict(n=sum(1 for r in rows if r['truth'] == k), action_ok=sum(r['score']['action_ok'] for r in rows if r['truth'] == k)) for k in ('cancelled', 'moved', 'extended')})


def main():
    p = argparse.ArgumentParser(); p.add_argument('cmd', choices=['rule', 'models']); p.add_argument('--part', default='dev'); p.add_argument('--model', action='append', default=[])
    p.add_argument('--mode', action='append', default=[]); p.add_argument('--out'); p.add_argument('--max-calls', type=int, default=100)
    p.add_argument('--target', choices=['upd', 'ann'], default='upd', help='upd: announcement + update (revision prompts); ann: the announcement alone (announcement prompts)'); a = p.parse_args()
    sel = [dict(i, truth=ACTION[i['kind']]) for i in items() if i['part'] == a.part]
    if a.cmd == 'rule':
        for kw in (False, True):
            rows = [dict(i, pred=rule_pred(i, kw)) for i in sel]
            for r in rows: r['score'] = score(r['pred'], r)
            print('rule_kw' if kw else 'rule', json.dumps(summarise(rows)))
            if a.out:
                Path(a.out).mkdir(parents=True, exist_ok=True); (Path(a.out) / ('rule_kw' if kw else 'rule') + '.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        return
    spec = importlib.util.spec_from_file_location('oce', HERE / 'offline_compile_eval.py'); oce = importlib.util.module_from_spec(spec); spec.loader.exec_module(oce)
    assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); calls = 0; summary = {}
    for name in a.model:
        chat = oce.Chat(assets[name]['directory'], None, quant=assets[name].get('quant'))
        for mode in (a.mode or ['direct']):
            rows = []; label = f'{name}_{mode}'
            for i in sel:
                if calls >= a.max_calls:
                    break
                raw, ms = chat(messages(i, mode, a.target), max_new_tokens=96); calls += 1
                o = mce.decode(raw)
                if a.target == 'ann':
                    pred = o if mode == 'direct' else mce.convert(o); sc = None
                else:
                    pred = dict(action=o.get('action'), start_utc=o.get('start_utc'), end_utc=o.get('end_utc')) if mode == 'direct' else convert_rev(o); sc = score(pred, i)
                rows.append(dict(i, raw=raw, extracted=o if mode == 'extract' else None, pred=pred, ms=ms, score=sc))
            (out / f'{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
            summary[label] = summarise(rows) if a.target == 'upd' else dict(n=len(rows), parsed=sum(bool((r['pred'] or {}).get('start_utc')) for r in rows)); print(a.target, label, json.dumps(summary[label]), flush=True)
        del chat.model; import torch; torch.cuda.empty_cache()
    (out / 'summary.json').write_text(json.dumps(dict(part=a.part, calls=calls, arms=summary), indent=1))


if __name__ == '__main__':
    main()
