#!/usr/bin/env python3
"""Measurement of public status-page maintenance notices as a Web channel (WWW 2027 paper, measurement section).

Reads the three local, robots-respecting collections (notices.jsonl: site, code, url, title, affected, updates[status,
unix_ms, body]) and reports, over the union of unique notices (site, code):
  * providers, notices, announcements with a clock time, English share;
  * lead time from the announcement post to the automatic 'In progress' transition (the announced start) and the
    announced window length, as quantiles overall and per provider;
  * how the time is written: zone tokens, city-name zones, notices with a clock time but no zone token;
  * wording: distinct wording shapes per provider (digits, codes and month names masked);
  * revisions: later posts that reschedule, extend or cancel, and whether they came before, during or after the window;
  * affected objects: whether the structured region is named verbatim in the text, by a city token, or neither;
  * start hour (UTC) and weekday histograms.
No model call, no network. Output: summary JSON and a Markdown report.

    python3 scripts/measure_status_pages.py data/maintenance_notices_20260929 data/maintenance_notices_update_20261001 \
        data/maintenance_notices_fresh_20261013 --out runs/status_page_measurement_20261019
"""
import argparse, datetime as D, json, re, statistics
from collections import Counter, defaultdict
from pathlib import Path

MONTHS = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
TZ = re.compile(r'\b(UTC|GMT|PDT|PST|PT|EDT|EST|ET|CDT|CST|CT|MDT|MST|CEST|CET|BST|IST|JST|AEST|AEDT|SGT|HKT|KST|MSK|BRT|WIB|NZST|NZDT|UTC\s?[+-]\s?\d{1,2}(?::\d{2})?|GMT\s?[+-]\s?\d{1,2}(?::\d{2})?)\b')
CITYTZ = re.compile(r'\b(Paris|London|Berlin|Amsterdam|Frankfurt|Tokyo|Sydney|Singapore|Pacific|Eastern|Central|Mountain|Europe/\w+|America/\w+|Asia/\w+)\s+(?:Time|time|local time|Standard Time|Daylight Time)\b')
TIME = re.compile(r'\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b', re.I)
RESCHED = re.compile(r'resched|postpon|moved to|new date|new time|delayed', re.I)
EXTEND = re.compile(r'extend|extension|longer than expected|additional time|overrun', re.I)
CANCEL = re.compile(r'cancel', re.I)
CJK = re.compile(r'[一-鿿぀-ヿ]')


def shape(text):
    t = re.sub(MONTHS, 'M', text or '')
    t = re.sub(r'\d+', '#', t)
    t = re.sub(r'\b[A-Z][A-Z0-9\-]{1,}\b', 'X', t)
    return re.sub(r'\s+', ' ', t)[:160]


def ref_time(unix_ms):
    """Automatic transition at the announced time: within 20 s after a 5-minute boundary (as the compile benchmark)."""
    if unix_ms is None:
        return None
    s = unix_ms // 1000
    if s % 300 > 20:
        return None
    return D.datetime.fromtimestamp(s - s % 300, D.timezone.utc)


def quantiles(xs, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    if not xs:
        return {}
    xs = sorted(xs); n = len(xs)
    out = {f'p{int(q * 100)}': xs[min(n - 1, int(q * (n - 1)))] for q in qs}
    out['n'] = n; out['mean'] = statistics.fmean(xs)
    return out


def region_names(affected):
    """'Regions (US-ORD (Chicago), EU-West (London))' -> ['US-ORD (Chicago)', 'EU-West (London)']; one region only."""
    if not affected:
        return []
    m = re.match(r'^\s*Regions?\s*\((.*)\)\s*$', affected, re.S)
    if not m:
        return []
    inner = m.group(1); out = []; depth = 0; cur = ''
    for ch in inner:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur.strip()); cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def naming_class(region, text):
    """verbatim: the region string appears in the text; city: a word of the parenthesised city appears; code: the code token appears; else neither."""
    t = text.lower()
    if region.lower() in t:
        return 'verbatim'
    code = re.sub(r'\s*\(.*\)\s*$', '', region).strip()
    city = re.search(r'\(([^()]*)\)\s*$', region)
    if code and code.lower() in t:
        return 'code'
    if city and any(len(w) > 3 and w.lower() in t for w in re.split(r'[\s,/]+', city.group(1))):
        return 'city'
    return 'neither'


def main():
    a = argparse.ArgumentParser(); a.add_argument('roots', nargs='+'); a.add_argument('--out', required=True); a = a.parse_args()
    notices = {}; per_collection = {}
    for r in a.roots:
        rows = [json.loads(l) for l in (Path(r) / 'notices.jsonl').read_text().splitlines() if l.strip()]
        per_collection[r] = dict(notices=len(rows), providers=len({n['site'] for n in rows}))
        for n in rows:
            notices.setdefault((n['site'], n['code']), n)
    by = defaultdict(list)
    for n in notices.values():
        by[n['site']].append(n)
    tot = Counter(); tz_all = Counter(); citytz = Counter(); lead_h = []; win_h = []; lead_by = defaultdict(list); start_hour = Counter(); weekday = Counter()
    shapes_by = {}; rev = Counter(); rev_when = Counter(); rev_by = Counter(); naming = Counter(); naming_by = defaultdict(Counter); prov_rows = []
    rev_notices = 0
    for site, ns in sorted(by.items(), key=lambda kv: -len(kv[1])):
        c = Counter(); shapes = set(); tzs = Counter(); leads = []
        for n in ns:
            ups = n['updates']
            ann = next((u for u in ups if (u['status'] or '').lower().startswith('scheduled') and u['body']), None)
            if not ann:
                c['no_announcement'] += 1; continue
            c['announced'] += 1; body = ann['body']; shapes.add(shape(body))
            if TIME.search(body):
                c['clock'] += 1
                zt = [z for z in TZ.findall(body)]; ct = CITYTZ.findall(body)
                for z in zt: tzs[re.sub(r'\s+', '', z.upper())] += 1
                for z in ct: citytz[z] += 1
                if not zt and not ct:
                    c['clock_no_zone'] += 1
                if ct and not zt:
                    c['city_zone_only'] += 1
            if len(body.split()) >= 8 and not CJK.search(body):
                c['english'] += 1
            ip = next((u for u in ups if (u['status'] or '').lower().startswith('in progress')), None)
            done = next((u for u in ups if (u['status'] or '').lower().startswith('completed')), None)
            s = ref_time(ip and ip['unix_ms']); e = ref_time(done and done['unix_ms'])
            posted = D.datetime.fromtimestamp(ann['unix_ms'] // 1000, D.timezone.utc)
            if s is not None:
                c['start_ref'] += 1; lh = (s - posted).total_seconds() / 3600; leads.append(lh); lead_h.append(lh); lead_by[site].append(lh)
                start_hour[s.hour] += 1; weekday[s.strftime('%a')] += 1
                if e is not None and e > s:
                    c['window_ref'] += 1; win_h.append((e - s).total_seconds() / 3600)
            # revisions: later posts (any status) that change timing
            kinds = set(); when = None
            for u in ups:
                if u is ann or not u['body']:
                    continue
                b = u['body']; k = 'cancel' if CANCEL.search(b) else 'reschedule' if RESCHED.search(b) else 'extend' if EXTEND.search(b) else None
                if k:
                    kinds.add(k)
                    if when is None and u['unix_ms'] and s is not None:
                        t = D.datetime.fromtimestamp(u['unix_ms'] // 1000, D.timezone.utc)
                        when = 'before_start' if t < s else ('after_end' if e is not None and t > e else 'during')
            if kinds:
                rev_notices += 1; rev_by[site] += 1; c['revised'] += 1
                for k in kinds: rev[k] += 1
                if when: rev_when[when] += 1
            regs = region_names(n.get('affected'))
            if len(regs) == 1 and TIME.search(body):
                k = naming_class(regs[0], body + ' ' + (n.get('title') or '')); naming[k] += 1; naming_by[site][k] += 1; c['single_region'] += 1
        tot.update(c); tz_all.update(tzs); shapes_by[site] = len(shapes)
        prov_rows.append(dict(site=site, notices=len(ns), announced=c['announced'], clock=c['clock'], clock_no_zone=c['clock_no_zone'], shapes=len(shapes),
                              start_ref=c['start_ref'], lead=quantiles(leads, (0.5,)), revised=c['revised'], single_region=c['single_region'], naming=dict(naming_by[site]), zones=dict(tzs.most_common(3))))
    summary = dict(collections=per_collection, unique_notices=len(notices), providers=len(by), totals=dict(tot), zones=dict(tz_all.most_common(20)), city_zones=dict(citytz.most_common(10)),
                   lead_hours=quantiles(lead_h), window_hours=quantiles(win_h), lead_lt_1h=sum(1 for x in lead_h if x < 1), lead_lt_24h=sum(1 for x in lead_h if x < 24), lead_ge_7d=sum(1 for x in lead_h if x >= 168),
                   start_hour_utc=dict(sorted(start_hour.items())), weekday=dict(weekday), revisions=dict(rev), revised_notices=rev_notices, revision_timing=dict(rev_when),
                   providers_with_revisions=len(rev_by), naming=dict(naming), shapes_median_per_announcement=statistics.median([shapes_by[s] / max(1, len(by[s])) for s in by]) if by else None,
                   providers_ge5_ge3shapes=sum(1 for s in by if len(by[s]) >= 5 and shapes_by[s] >= 3), providers_=prov_rows)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=float))
    (out / 'distributions.json').write_text(json.dumps(dict(lead_hours=lead_h, window_hours=win_h, lead_by_provider={k: v for k, v in lead_by.items()}), default=float))
    L = [f"# Status pages as a Web channel: measurement\n", f"Collections: {per_collection}\n", f"Unique notices {len(notices)} from {len(by)} providers; announced {tot['announced']}, with a clock time {tot['clock']}, English {tot['english']}, with an automatic start reference {tot['start_ref']}, with a window reference {tot['window_ref']}.\n",
         f"Lead time (hours, announcement post to start): {summary['lead_hours']}; under 1 h {summary['lead_lt_1h']}, under 24 h {summary['lead_lt_24h']}, 7 days or more {summary['lead_ge_7d']}.\n", f"Window length (hours): {summary['window_hours']}\n",
         f"Zone tokens: {summary['zones']}\nCity-name zones: {summary['city_zones']}\nClock time but no zone token: {tot['clock_no_zone']}; city-name zone only: {tot['city_zone_only']}\n",
         f"Revisions: {rev_notices} notices revised in {len(rev_by)} providers; kinds {dict(rev)}; timing {dict(rev_when)}\n", f"Affected region naming (single-region notices with a clock time, n={tot['single_region']}): {dict(naming)}\n",
         f"Wording shapes: median distinct shapes per notice within a provider {summary['shapes_median_per_announcement']:.2f}; providers with >=5 notices and >=3 shapes: {summary['providers_ge5_ge3shapes']}\n",
         f"Start hour (UTC): {summary['start_hour_utc']}\nWeekday: {summary['weekday']}\n", '| provider | notices | announced | clock | no zone | shapes | start ref | median lead h | revised | single region | naming | zones |', '|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in prov_rows:
        L.append(f"| {r['site']} | {r['notices']} | {r['announced']} | {r['clock']} | {r['clock_no_zone']} | {r['shapes']} | {r['start_ref']} | {r['lead'].get('p50', '')} | {r['revised']} | {r['single_region']} | {r['naming']} | {r['zones']} |")
    (out / 'report.md').write_text('\n'.join(L) + '\n'); print('\n'.join(L[:10]))


if __name__ == '__main__':
    main()
