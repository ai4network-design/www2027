#!/usr/bin/env python3
"""Revision items for the notice-update scenario: from a collection (collect_maintenance_notices.py output) list every
non-status update post that changes the timing of a scheduled maintenance (reschedule, postpone, extend, cancel, delay,
new window), together with the original announcement and the automatic references. No model call.

    PYTHONPATH=src python scripts/analyse_notice_updates.py data/maintenance_notices_update_20261001 [--out revisions.jsonl]
"""
import argparse, datetime as D, importlib.util, json, re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
STATUS = {'scheduled', 'in progress', 'completed', 'verifying', 'monitoring', 'resolved', 'investigating', 'identified'}
KW = [('cancel', r'cancel'), ('reschedule', r'resched|new (?:date|window|time)|moved to|pushed'), ('postpone', r'postpon|delayed|will now (?:start|begin)|later than'),
      ('extend', r'extend|prolong|additional (?:time|hour)|longer than (?:expected|planned|anticipated)|will continue|still (?:in progress|ongoing)')]
CLOCK = re.compile(r'\b\d{1,2}:\d{2}\b')


def iso(ms):
    return D.datetime.fromtimestamp(ms / 1000, D.timezone.utc).strftime('%Y-%m-%dT%H:%M')


BOILERPLATE = re.compile(r'(?:extended|cancell?ed|delayed|postponed)(?:\s*,)?\s+or\s+(?:extended|cancell?ed|delayed|postponed)')
CANCEL = re.compile(r'(?:is|are|was|were|been|being|be)\s+(?:now\s+)?cancel|cancell?ed\.|we are cancel|cancell?ing the|will not (?:take place|be (?:performed|carried out|happening|proceeding))|no longer (?:take place|be performed|proceed)')


def kind_of(body, strict=True):
    """Action keyword of an update. strict=True (amendment 2 of contracts/revision_scenario_20261011.json, 2026-10-13): the
    boilerplate 'if the maintenance is extended or cancelled' is removed first, a cancellation needs a cancelling phrase
    (not the bare word), moving/postponing takes precedence over extending, and a post that only restates a window with
    clock times counts as a move. strict=False is the original first-keyword rule (selection of the items)."""
    b = body.lower()
    if not strict:
        for k, pat in KW:
            if re.search(pat, b):
                return k
        return None
    b = BOILERPLATE.sub(' ', b)
    if CANCEL.search(b):
        return 'cancel'
    for k in ('reschedule', 'postpone', 'extend'):
        if re.search(dict(KW)[k], b):
            return k
    return 'reschedule' if CLOCK.search(b) else None


def revisions(root):
    notices = [json.loads(l) for l in (Path(root) / 'notices.jsonl').read_text().splitlines() if l.strip()]
    group = mce.provider_groups(notices); out = []
    for n in notices:
        ups = sorted(n['updates'], key=lambda u: u['unix_ms']); st = {(u.get('status') or '').lower(): u['unix_ms'] for u in ups}
        ann = next((u for u in ups if (u.get('status') or '').lower() == 'scheduled'), None)
        if not ann or not ann.get('body'):
            continue
        start, end = st.get('in progress'), st.get('completed')
        for u in ups:
            s = (u.get('status') or '').lower()
            if s in STATUS or not u.get('body'):
                continue
            kind = kind_of(u['body'], strict=False)      # selection: the original first-keyword rule
            if not kind:
                continue
            kind = kind_of(u['body']) or kind            # label: the strict rule (amendment 2)
            phase = 'before_start' if (start and u['unix_ms'] < start) else ('during' if (end and u['unix_ms'] < end) else 'after')
            out.append(dict(id=f"{n['site']}/{n['code']}/{u['unix_ms']}", notice=f"{n['site']}/{n['code']}", site=n['site'], group=group[n['site']], part=mce.part_of(group[n['site']]), kind_selection=kind_of(u['body'], strict=False),
                            title=n.get('title'), announcement=ann['body'], announced_utc=iso(ann['unix_ms']), ref_start=iso(start) if start else None, ref_end=iso(end) if end else None,
                            update=u['body'], update_utc=iso(u['unix_ms']), phase=phase, kind=kind, update_clock=bool(CLOCK.search(u['body'])), announcement_clock=bool(CLOCK.search(ann['body']))))
    return out


def main():
    a = argparse.ArgumentParser(); a.add_argument('root'); a.add_argument('--out'); a = a.parse_args()
    rows = revisions(a.root)
    print('notices', len({r['notice'] for r in rows}), 'revision posts', len(rows))
    for key in ('part', 'phase', 'kind', 'site'):
        print(key, dict(Counter(r[key] for r in rows).most_common()))
    print('usable (announcement clock, update clock or cancel, phase not after):', dict(Counter(r['part'] for r in rows if r['announcement_clock'] and (r['update_clock'] or r['kind'] == 'cancel') and r['phase'] != 'after')))
    if a.out:
        Path(a.out).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


if __name__ == '__main__':
    main()
