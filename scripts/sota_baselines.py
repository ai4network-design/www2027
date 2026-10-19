#!/usr/bin/env python3
"""Published-tool baselines for the compile benchmarks (contracts/sota_baselines_20261015.json).

time    maintenance window from an announcement (or a revision post) with a temporal-expression tool:
          dateparser   dateparser.search.search_dates with the posting time as relative base (pure Python)
          sutime       Stanford SUTime via the 'sutime' wrapper (needs Java; TIMEX3 values)
        Two conversions per tool, both designed on the development providers only:
          raw   the tool's own datetimes: the first clock-bearing expression is the start, the next one the end
          glue  the same, but a clock-only expression takes the date of the nearest date expression and the zone written
                next to it (our zone table), i.e. the glue the hand rule also has; the tool still finds the expressions
entity  affected region from an announcement with the candidate list:
          rapidfuzz    best candidate by max(partial_ratio, token_set_ratio) of the candidate name against the text
          embed        sentence-embedding similarity between the text and each candidate (sentence-transformers)

    PYTHONPATH=src python scripts/sota_baselines.py time --tool dateparser --part test --target ann --out runs/sota_baselines_20261015/time
    PYTHONPATH=src python scripts/sota_baselines.py entity --tool rapidfuzz --part test --out runs/sota_baselines_20261015/entity
"""
import argparse, datetime as D, importlib.util, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
_r = importlib.util.spec_from_file_location('rce', HERE / 'revision_compile_eval.py'); RCE = importlib.util.module_from_spec(_r); _r.loader.exec_module(RCE)
_a = importlib.util.spec_from_file_location('ace', HERE / 'affected_compile_eval.py'); ACE = importlib.util.module_from_spec(_a); _a.loader.exec_module(ACE)
sys.path.insert(0, str(HERE)); import maintenance_rule as MR
from vera import multiservice_control as m
CLOCK = re.compile(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b|\b\d{1,2}:\d{2}\b', re.I)
DATEISH = re.compile(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|\b20\d{2}-\d{2}-\d{2}\b|\b\d{1,2}[./]\d{1,2}[./]20\d{2}\b', re.I)


def expressions_dateparser(text, posted):
    import dateparser.search
    base = D.datetime.fromisoformat(posted)
    found = dateparser.search.search_dates(text, languages=['en'], settings={'RELATIVE_BASE': base, 'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': False}) or []   # naive output: the tool already shifts zoned expressions to the base zone (UTC)
    out = []; pos = 0
    for sub, dt in found:
        i = text.find(sub, pos); i = i if i >= 0 else text.find(sub); pos = max(pos, i + len(sub)) if i >= 0 else pos
        zoned = bool(re.search(r'\b(' + MR.TZ_RE[1:], sub, re.I))          # the tool saw a zone inside the expression and converted it
        out.append(dict(text=sub, pos=i, dt=dt.replace(tzinfo=None) if dt.tzinfo else dt, zoned=zoned, clock=bool(CLOCK.search(sub)), date=bool(DATEISH.search(sub)) or bool(re.search(r'20\d{2}', sub))))
    return out


def expressions_sutime(text, posted):
    from sutime import SUTime
    global _SU
    if '_SU' not in globals():
        import os
        _SU = SUTime(jars=os.environ.get('SUTIME_JARS'), mark_time_ranges=True, include_range=True)   # SUTIME_JARS: the Stanford CoreNLP directory; JAVA_HOME: the portable JRE
    ref = D.datetime.fromisoformat(posted)
    def parse_value(v):
        """TIMEX3 value -> (local datetime, offset hours or None, has_clock, has_date); None for durations and sets.
        Time-only values ('T04:00+0000', from ranges) take the posting date as the tool's reference does."""
        v = str(v)
        mm = re.match(r'^(\d{4}-\d{2}-\d{2})?T(\d{2}:\d{2})(?::\d{2})?(?:([+-]\d{2})(\d{2})?|(Z))?$', v)
        if mm:
            date = mm.group(1) or ref.strftime('%Y-%m-%d'); dt = D.datetime.fromisoformat(date + 'T' + mm.group(2))
            off = 0.0 if mm.group(5) else (int(mm.group(3)) + (int(mm.group(4) or 0) / 60) * (1 if int(mm.group(3)) >= 0 else -1) if mm.group(3) else None)
            return dt, off, True, bool(mm.group(1))
        md = re.match(r'^(\d{4}-\d{2}-\d{2})$', v)
        return (D.datetime.fromisoformat(md.group(1)), None, False, True) if md else None
    out = []
    for x in _SU.parse(text, reference_date=posted[:10]):
        v = x.get('value'); vals = []
        if isinstance(v, dict):
            vals = [v.get('begin'), v.get('end')]
        else:
            vals = [v]
        for k, vv in enumerate(vals):
            pv = parse_value(vv) if vv else None
            if pv is None:
                continue
            dt, off, has_clock, has_date = pv
            in_text = bool(DATEISH.search(x['text'])) or bool(re.search(r'20\d{2}', x['text'])) or x.get('type') == 'DATE'   # a clock-only expression gets the reference date from the tool; the text decides whether a date was written
            out.append(dict(text=x['text'], pos=x['start'] + k, dt=dt, off=off, zoned=off is not None, clock=has_clock, date=in_text))
    return out


def window_from(exprs, text, posted, glue):
    """start/end UTC from the tool's expressions. raw: tool datetimes as given (naive, taken as UTC). glue: clock-only
    expressions take the date of the nearest dated expression and the zone token written right after the clock."""
    posted_dt = D.datetime.fromisoformat(posted); clocks = [e for e in exprs if e['clock']]
    if not clocks:
        return dict(start_utc=None, end_utc=None)
    dated = [e for e in exprs if e['date']]
    def resolve(e):
        dt = e['dt']
        if glue and not e['date'] and dated:
            near = min(dated, key=lambda d: abs(d['pos'] - e['pos'])); dt = dt.replace(year=near['dt'].year, month=near['dt'].month, day=near['dt'].day)
        if e.get('off') is not None:                                  # the tool's own zone offset (SUTime values carry it)
            dt = dt - D.timedelta(hours=e['off'])
        if glue:
            if not e.get('zoned'):                                    # the tool converted zoned expressions itself; naive ones take the zone written near them
                tail = text[e['pos'] + len(e['text']):e['pos'] + len(e['text']) + 12]
                mz = re.match(r'\s*\(?\s*(' + MR.TZ_RE[1:], tail, re.I)
                off = MR.offset(mz.group(1).lower(), dt.date()) if mz else None
                if off is None:
                    mz2 = re.search(r'\b(' + MR.TZ_RE[1:], text[max(0, e['pos'] - 40):e['pos'] + 80], re.I); off = MR.offset(mz2.group(1).lower(), dt.date()) if mz2 else None
                dt = dt - D.timedelta(hours=off or 0)
        return dt
    start = resolve(clocks[0]); end = resolve(clocks[1]) if len(clocks) > 1 else None
    if end is not None and end <= start:
        end = end + D.timedelta(days=1)
    return dict(start_utc=start.strftime('%Y-%m-%dT%H:%M'), end_utc=end.strftime('%Y-%m-%dT%H:%M') if end else None)


def run_time(a):
    exprs = expressions_dateparser if a.tool == 'dateparser' else expressions_sutime
    if a.target == 'ann':
        items = [i for i in mce.items(m.ROOT / 'data/maintenance_notices_20260929') if i['part'] == a.part]
        text_of, posted_of = (lambda i: i['text']), (lambda i: i['posted_utc'])
    else:
        items = [dict(i, truth=RCE.ACTION[i['kind']]) for i in RCE.items() if i['part'] == a.part]
        text_of, posted_of = (lambda i: i['update']), (lambda i: i['update_utc'])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for glue in (False, True):
        rows = []
        for it in items:
            try:
                ex = exprs(text_of(it), posted_of(it)); w = window_from(ex, text_of(it), posted_of(it), glue)
            except Exception as exc:
                ex, w = [], dict(start_utc=None, end_utc=None, error=repr(exc)[:120])
            if a.target == 'ann':
                rows.append(dict(it, pred=w, expressions=[dict(text=e['text'], clock=e['clock'], date=e['date']) for e in ex], score=mce.score(w, it)))
            else:
                act = RCE.ACTION.get(RCE.anu.kind_of(it['update']) or '', 'unchanged'); pred = dict(action=act, **w)   # action from the selection keywords (as rule_kw)
                rows.append(dict(it, pred=pred, expressions=[dict(text=e['text'], clock=e['clock'], date=e['date']) for e in ex], score=RCE.score(pred, it)))
        label = f"{a.tool}_{'glue' if glue else 'raw'}"
        (out / f'{a.target}_{a.part}_{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False, default=str) + '\n' for r in rows))
        summ = mce.summarise(rows) if a.target == 'ann' else RCE.summarise(rows); print(a.target, a.part, label, json.dumps(summ)[:300], flush=True)


def run_entity(a):
    items = [i for i in ACE.items() if i['part'] == a.part]; out = Path(a.out); out.mkdir(parents=True, exist_ok=True); rows = []
    if a.tool == 'rapidfuzz':
        from rapidfuzz import fuzz
        def pick(it):
            t = it['text'].lower(); best = None; bs = -1
            for c in it['candidates']:
                s = max(fuzz.partial_ratio(c.lower(), t), fuzz.token_set_ratio(c.lower(), t))
                if s > bs:
                    best, bs = c, s
            return best, bs
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(a.model, device='cuda'); cache = {}
        def pick(it):
            key = tuple(it['candidates'])
            if key not in cache:
                cache[key] = model.encode(list(key), normalize_embeddings=True)
            q = model.encode([it['text']], normalize_embeddings=True)[0]; sims = cache[key] @ q; j = int(sims.argmax())
            return it['candidates'][j], float(sims[j])
    for it in items:
        reg, sc = pick(it); pred = dict(region=reg, start_utc=None, end_utc=None)
        rows.append(dict(it, pred=pred, match_score=sc, score=ACE.score(pred, it)))
    label = a.tool if a.tool == 'rapidfuzz' else 'embed_' + Path(a.model).name
    (out / f'{a.part}_{label}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    print('entity', a.part, label, json.dumps(ACE.summarise(rows))[:400])


def main():
    p = argparse.ArgumentParser(); p.add_argument('cmd', choices=['time', 'entity']); p.add_argument('--tool', required=True); p.add_argument('--part', default='dev')
    p.add_argument('--target', choices=['ann', 'upd'], default='ann'); p.add_argument('--out', required=True); p.add_argument('--model', default='BAAI/bge-small-en-v1.5'); a = p.parse_args()
    (run_time if a.cmd == 'time' else run_entity)(a)


if __name__ == '__main__':
    main()
