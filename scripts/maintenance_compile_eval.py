#!/usr/bin/env python3
"""Compile pilot on public maintenance notices: announcement text -> announced window in UTC.

Items: the first 'Scheduled' update of each notice (plus its title and posting time). Reference: the posting times
of the 'In progress' (start) and 'Completed' (end) updates, kept only when they sit within 20 s after a 5-minute
boundary (the pattern of automatic transitions at the announced times) and floored to that boundary; the end
reference is used only when it passes the same test. Items whose announcement states no clock time are excluded.
Providers are split into development and test halves by a hash of the domain; the rule arm is written on the
development half only; models run zero-shot; the test half is scored once.

    PYTHONPATH=src python scripts/maintenance_compile_eval.py split  data/maintenance_notices_20260929
    PYTHONPATH=src python scripts/maintenance_compile_eval.py rule   data/maintenance_notices_20260929 --part dev
    PYTHONPATH=src python scripts/maintenance_compile_eval.py models data/maintenance_notices_20260929 --part test --model qwen7b --model qwen1p5b
"""
import argparse, datetime as D, hashlib, json, re, sys
from pathlib import Path

TIME_RE = re.compile(r'\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b', re.I)
TOL_MIN = 2


def provider_groups(notices):
    """Sites that publish any identical announcement body (e.g. a parent company and its brand) form one group,
    so the development/test split never separates copies of the same text."""
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    owner = {}
    for n in notices:
        find(n['site'])
        for u in n['updates']:
            if (u['status'] or '').lower().startswith('scheduled') and u['body'] and len(u['body']) > 40:
                key = re.sub(r'\s+', ' ', u['body']).strip()
                if key in owner:
                    parent[find(n['site'])] = find(owner[key])
                else:
                    owner[key] = n['site']
    return {s: min(x for x in parent if find(x) == find(s)) for s in parent}


def part_of(group):
    return 'dev' if int(hashlib.sha256(group.encode()).hexdigest(), 16) % 2 == 0 else 'test'


def ref_time(unix_ms):
    if unix_ms is None:
        return None
    s = unix_ms // 1000
    if s % 300 > 20:
        return None
    return D.datetime.fromtimestamp(s - s % 300, D.timezone.utc)


def items(root):
    out = []; notices = [json.loads(l) for l in (Path(root) / 'notices.jsonl').read_text().splitlines() if l.strip()]
    group = provider_groups(notices); seen = set()
    for n in notices:
        ups = n['updates']
        ann = next((u for u in ups if (u['status'] or '').lower().startswith('scheduled') and u['body']), None)
        ip = next((u for u in ups if (u['status'] or '').lower().startswith('in progress')), None)
        done = next((u for u in ups if (u['status'] or '').lower().startswith('completed')), None)
        if not ann or not TIME_RE.search(ann['body']):
            continue
        key = (re.sub(r'\s+', ' ', ann['body']).strip(), ann['unix_ms'] // 60000)
        if key in seen:   # the same announcement published on two sites: keep one copy
            continue
        seen.add(key)
        start = ref_time(ip and ip['unix_ms'])
        if start is None:
            continue
        end = ref_time(done and done['unix_ms'])
        if end is not None and end <= start:
            end = None
        posted = D.datetime.fromtimestamp(ann['unix_ms'] // 1000, D.timezone.utc)
        out.append(dict(id=f"{n['site']}/{n['code']}", site=n['site'], group=group[n['site']], part=part_of(group[n['site']]), title=n.get('title'), text=ann['body'],
                        posted_utc=posted.strftime('%Y-%m-%dT%H:%M'), ref_start=start.strftime('%Y-%m-%dT%H:%M'), ref_end=end.strftime('%Y-%m-%dT%H:%M') if end else None))
    return out


def parse_minute(v):
    if not isinstance(v, str):
        return None
    try:
        t = D.datetime.fromisoformat(v.strip().replace('Z', '').replace(' ', 'T')[:16])
        return t.replace(tzinfo=D.timezone.utc)
    except ValueError:
        return None


def score(pred, item):
    s, e = parse_minute(pred.get('start_utc')), parse_minute(pred.get('end_utc'))
    rs, re_ = D.datetime.fromisoformat(item['ref_start']).replace(tzinfo=D.timezone.utc), item['ref_end'] and D.datetime.fromisoformat(item['ref_end']).replace(tzinfo=D.timezone.utc)
    ok_s = s is not None and abs((s - rs).total_seconds()) <= TOL_MIN * 60
    ok_e = None if not re_ else (e is not None and abs((e - re_).total_seconds()) <= TOL_MIN * 60)
    return dict(start_ok=ok_s, end_ok=ok_e)


PROMPT = ('You read one public scheduled-maintenance announcement from a service provider. Return JSON only: '
          '{"start_utc": "YYYY-MM-DDTHH:MM", "end_utc": "YYYY-MM-DDTHH:MM"} giving the announced maintenance window converted '
          'to UTC (24-hour clock). Convert every time zone to UTC, taking daylight saving time into account for the stated date. '
          'If the announcement gives a date without a year, use the year of the posting time. Use null for a bound the text does not state. No explanation.')


EXTRACT = ('You read one public scheduled-maintenance announcement from a service provider. Copy the announced window exactly '
           'as written, without converting any time zone. Return JSON only: {"date": "YYYY-MM-DD" (the start date as written; '
           'if no year is given use the year of the posting time; for a weekday such as "this Saturday" give the next such date '
           'after the posting time), "start": "HH:MM" (24-hour, as written), "end": "HH:MM" or null, "end_date": "YYYY-MM-DD" or '
           'null (only if the text gives a different end date), "tz": the time-zone abbreviation or name written next to the start '
           'time (for example "UTC", "PDT", "Pacific", "CEST"), or null}. If the text gives both a UTC time and a local time, copy '
           'the UTC one. No explanation.')


def messages(item, mode='direct'):
    user = json.dumps(dict(posted_at_utc=item['posted_utc'], title=item['title'], announcement=item['text']), ensure_ascii=False)
    return [dict(role='system', content=PROMPT if mode == 'direct' else EXTRACT), dict(role='user', content=user)]


def convert(o):
    """Engine-side conversion of an extracted local window to UTC (same zone table as the rule arm)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent)); import maintenance_rule as MR
    try:
        d = D.date.fromisoformat(str(o.get('date'))[:10])
        sh, sm = [int(x) for x in str(o.get('start'))[:5].split(':')]
    except (ValueError, TypeError):
        return {}
    off = MR.offset(str(o.get('tz') or 'utc').strip().split()[0].lower().strip('()'), d)
    off = 0 if off is None else off
    start = D.datetime(d.year, d.month, d.day, sh, sm) - D.timedelta(hours=off); end = None
    try:
        eh, em = [int(x) for x in str(o.get('end'))[:5].split(':')]
        ed = D.date.fromisoformat(str(o.get('end_date'))[:10]) if o.get('end_date') else d
        end = D.datetime(ed.year, ed.month, ed.day, eh, em) - D.timedelta(hours=off)
        if end <= start:
            end += D.timedelta(days=1)
    except (ValueError, TypeError):
        end = None
    return dict(start_utc=start.strftime('%Y-%m-%dT%H:%M'), end_utc=end.strftime('%Y-%m-%dT%H:%M') if end else None)


def decode(raw):
    m = re.search(r'\{.*\}', raw or '', re.S)
    if not m:
        return {}
    try:
        o = json.loads(m.group(0))
        return o if isinstance(o, dict) else {}
    except ValueError:
        return {}


def summarise(rows):
    n = len(rows); ends = [r for r in rows if r['score']['end_ok'] is not None]
    return dict(n=n, start_ok=sum(r['score']['start_ok'] for r in rows), end_n=len(ends), end_ok=sum(bool(r['score']['end_ok']) for r in ends),
                providers=len({r['site'] for r in rows}))


def main():
    p = argparse.ArgumentParser(); p.add_argument('cmd', choices=['split', 'rule', 'models']); p.add_argument('root'); p.add_argument('--part', default='dev')
    p.add_argument('--model', action='append', default=[]); p.add_argument('--out'); p.add_argument('--max-calls', type=int, default=800)
    p.add_argument('--mode', action='append', default=[], help='direct (model outputs UTC) and/or extract (model copies, code converts)')
    p.add_argument('--adapter', action='append', default=[], help='label=path: LoRA adapter (base model from meta.json), run in extract mode')
    a = p.parse_args(); its = items(a.root)
    if a.cmd == 'split':
        from collections import Counter
        c = Counter((i['part'], i['site']) for i in its)
        groups = {}
        for i in its: groups.setdefault(i['group'], set()).add(i['site'])
        print('multi-site groups:', {g: sorted(s) for g, s in groups.items() if len(s) > 1})
        for part in ('dev', 'test'):
            sites = {s: n for (pp, s), n in c.items() if pp == part}
            print(part, 'items', sum(sites.values()), 'providers', len(sites), dict(sorted(sites.items(), key=lambda kv: -kv[1])))
        print('with end reference:', sum(1 for i in its if i['ref_end']))
        return
    sel = [i for i in its if i['part'] == a.part]
    if a.cmd == 'rule':
        sys.path.insert(0, str(Path(__file__).resolve().parent)); import maintenance_rule as MR
        rows = [dict(i, pred=MR.extract(i['text'], i['posted_utc']), score=None) for i in sel]
        for r in rows: r['score'] = score(r['pred'], r)
        print(json.dumps(summarise(rows)))
        for r in rows:
            if not r['score']['start_ok'] or r['score']['end_ok'] is False:
                print('MISS', r['id'], r['ref_start'], r['ref_end'], r['pred'], '|', r['text'][:160].replace('\n', ' '))
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location('oce', Path(__file__).with_name('offline_compile_eval.py')); oce = importlib.util.module_from_spec(spec); spec.loader.exec_module(oce)
    from vera import multiservice_control as m
    assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); calls = 0; summary = {}
    modes = a.mode or ['direct']
    runs = [(name, None, modes, None) for name in a.model]
    for spec_ in a.adapter:
        lab, path = spec_.split('=', 1); meta = json.loads((Path(path) / 'meta.json').read_text())
        runs.append((meta['base_model'], path, ['extract'], lab))
    for name, adapter, run_modes, adapter_label in runs:
        chat = oce.Chat(assets[name]['directory'], adapter, quant=assets[name].get('quant'))
        for mode in run_modes:
            rows = []; label = adapter_label or f'{name}_{mode}'
            for i in sel:
                if calls >= a.max_calls:
                    break
                raw, ms = chat(messages(i, mode), max_new_tokens=80); calls += 1
                o = decode(raw); pred = o if mode == 'direct' else convert(o)
                rows.append(dict(i, raw=raw, extracted=o if mode == 'extract' else None, pred=pred, ms=ms, score=score(pred, i)))
            (out / f'{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
            summary[label] = summarise(rows); print(label, json.dumps(summary[label]), flush=True)
        del chat.model; import torch; torch.cuda.empty_cache()
    (out / 'summary.json').write_text(json.dumps(dict(part=a.part, calls=calls, arms=summary), indent=1))


if __name__ == '__main__':
    main()
