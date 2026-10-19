#!/usr/bin/env python3
"""Items for the affected-object scenario (contracts/affected_scenario_20261013.json): single-region maintenance notices
of the providers whose Statuspage components are regions, data centres or points of presence, with a clock-time
announcement and both automatic references. Truth = the structured affected component; candidates = every region
component the provider ever listed in the collections. Provider split as the announcement benchmark.

    PYTHONPATH=src python scripts/affected_items.py --out data/affected_items_20261013.json
"""
import argparse, datetime as D, importlib.util, json, re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
from vera import multiservice_control as m
COLLECTIONS = ['data/maintenance_notices_20260929', 'data/maintenance_notices_update_20261001', 'data/maintenance_notices_fresh_20261013']
REGION_GROUPS = {'status.linode.com': {'Regions'}, 'status.imperva.com': {'EMEA PoPs', 'North American PoPs', 'APAC PoPs', 'LATAM PoPs'}, 'status.upcloud.com': None,
                 'status.wasabi.com': None, 'status.packet.net': None, 'status.scaleway.com': {'Elements - AZ', 'Dedibox - Datacenters'}}
CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')


def split_top(inner):
    parts = []; depth = 0; cur = ''
    for ch in inner:
        depth += (ch == '(') - (ch == ')')
        if ch == ',' and depth == 0:
            parts.append(cur.strip()); cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def parse_affected(site, s):
    """'Group (c1, c2) and Group2 (c3)' -> [(group, [components])]; Imperva components are 'City, Country (CODE)'."""
    out = []; i = 0; s = s.strip()
    while i < len(s):
        mm = re.match(r'\s*(?:and\s+|,\s*)?([^()]+?)\s*\(', s[i:])
        if not mm:
            break
        g = mm.group(1).strip(); j = i + mm.end(); depth = 1; k = j
        while k < len(s) and depth > 0:
            depth += (s[k] == '(') - (s[k] == ')'); k += 1
        parts = split_top(s[j:k - 1])
        if site == 'status.imperva.com':
            merged = []
            for p in parts:
                if merged and '(' not in merged[-1] and '(' in p:
                    merged[-1] = merged[-1] + ', ' + p
                else:
                    merged.append(p)
            parts = merged
        out.append((g, parts)); i = k
    return out


def region_of(site, component):
    """Amendment 1 (2026-10-13, after the test compile, before the closed loop): a component written as 'ZONE: Product'
    (UpCloud) names the zone; the product is not a place. The zone is the region; identical zones collapse."""
    return component.split(':')[0].strip() if ':' in component else component


def iso(ms):
    return D.datetime.fromtimestamp(ms / 1000, D.timezone.utc).strftime('%Y-%m-%dT%H:%M')


def build():
    rows = {}
    for d in COLLECTIONS:
        for l in (m.ROOT / d / 'notices.jsonl').read_text().splitlines():
            if l.strip():
                r = json.loads(l); rows.setdefault((r['site'], r['code']), r)
    notices = list(rows.values()); group = mce.provider_groups(notices)
    candidates = defaultdict(set); parsed = {}
    for r in notices:
        site = r['site']
        if site not in REGION_GROUPS or not r.get('affected'):
            continue
        rg = REGION_GROUPS[site]; regs = sorted({region_of(site, c) for g, cs in parse_affected(site, r['affected']) if (rg is None or g in rg) for c in cs})
        parsed[(site, r['code'])] = regs; candidates[site].update(regs)
    items = []
    for (site, code), regs in parsed.items():
        if len(regs) != 1:
            continue
        r = rows[(site, code)]; ups = sorted(r['updates'], key=lambda u: u['unix_ms'])
        ann = next((u for u in ups if (u.get('status') or '').lower() == 'scheduled'), None); st = {(u.get('status') or '').lower(): u['unix_ms'] for u in ups}
        if not ann or not ann.get('body') or not CLOCK.search(ann['body']) or not st.get('in progress') or not st.get('completed'):
            continue
        items.append(dict(id=f'{site}/{code}', site=site, group=group[site], part=mce.part_of(group[site]), title=r.get('title'), text=ann['body'], posted_utc=iso(ann['unix_ms']),
                          ref_start=iso(st['in progress']), ref_end=iso(st['completed']), region=regs[0], candidates=sorted(candidates[site])))
    items.sort(key=lambda i: i['id'])
    return items


def main():
    a = argparse.ArgumentParser(); a.add_argument('--out', required=True); a = a.parse_args()
    items = build(); Path(a.out).write_text(json.dumps(items, ensure_ascii=False, indent=0))
    print('items', len(items), dict(Counter((i['part'], i['site']) for i in items)))
    print('candidates per provider', {s: len(items[[i['site'] for i in items].index(s)]['candidates']) for s in sorted({i['site'] for i in items})})


if __name__ == '__main__':
    main()
