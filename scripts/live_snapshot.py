#!/usr/bin/env python3
"""Live snapshot (contract contracts/live_snapshot_20261019.json): read today's UPCOMING maintenance notices end to end and
register the compiled windows for a prospective check at camera-ready.

Step 1 (crawl, separate command): scripts/collect_maintenance_notices.py with the registered site list into <root>/notices.
Step 2 (this script): select notices whose thread has a Scheduled post and no In-progress/Completed transition yet, compile
each with the frozen 7B extract prompt (transcribe, then convert) and the hand rule, and write
  <root>/predictions.jsonl   one row per upcoming notice (identifier, URL, posting time, both predicted windows, lead time)
  <root>/registration.json   counts, the SHA-256 of predictions.jsonl and the snapshot time (goes into docs/reproduce).
No notice text is written to the registration; predictions.jsonl keeps the text locally for the later check.

    PYTHONPATH=src python scripts/live_snapshot.py --root runs/live_snapshot_20261019
"""
import argparse, datetime as D, hashlib, importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
_o = importlib.util.spec_from_file_location('oce', HERE / 'offline_compile_eval.py'); oce = importlib.util.module_from_spec(_o); _o.loader.exec_module(oce)
sys.path.insert(0, str(HERE)); import maintenance_rule as MR
from vera import multiservice_control as m


def upcoming(root):
    out = []
    for l in (root / 'notices' / 'notices.jsonl').read_text().splitlines():
        if not l.strip():
            continue
        n = json.loads(l); ups = n['updates']
        ann = next((u for u in ups if (u['status'] or '').lower().startswith('scheduled') and u['body']), None)
        started = any((u['status'] or '').lower().startswith(('in progress', 'completed', 'verifying')) for u in ups)
        if not ann or started or not mce.TIME_RE.search(ann['body']):
            continue
        posted = D.datetime.fromtimestamp(ann['unix_ms'] // 1000, D.timezone.utc)
        out.append(dict(id=f"{n['site']}/{n['code']}", site=n['site'], url=n['url'], title=n.get('title'), text=ann['body'], posted_utc=posted.strftime('%Y-%m-%dT%H:%M'), fetched_at=n.get('fetched_at')))
    return out


def main():
    a = argparse.ArgumentParser(); a.add_argument('--root', required=True); a.add_argument('--model', default='qwen7b'); a = a.parse_args()
    root = Path(a.root); items = upcoming(root); now = D.datetime.now(D.timezone.utc)
    assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
    chat = oce.Chat(assets[a.model]['directory'], None, quant=assets[a.model].get('quant')); rows = []
    for it in items:
        raw, ms = chat(mce.messages(it, 'extract'), max_new_tokens=80); o = mce.decode(raw); pred = mce.convert(o); rule = MR.extract(it['text'], it['posted_utc'])
        lead = None
        if pred.get('start_utc'):
            lead = round((D.datetime.fromisoformat(pred['start_utc']).replace(tzinfo=D.timezone.utc) - now).total_seconds() / 3600, 1)
        rows.append(dict(it, raw=raw, extracted=o, pred=pred, rule=rule, lead_hours_from_snapshot=lead, agree=(pred.get('start_utc') == rule.get('start_utc'))))
    f = root / 'predictions.jsonl'; f.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    public = [dict(id=r['id'], url=r['url'], posted_utc=r['posted_utc'], pred_start_utc=r['pred']['start_utc'], pred_end_utc=r['pred']['end_utc'], rule_start_utc=r['rule']['start_utc'], rule_end_utc=r['rule']['end_utc'], lead_hours=r['lead_hours_from_snapshot']) for r in rows]
    reg = dict(snapshot_utc=now.strftime('%Y-%m-%dT%H:%M'), model=a.model, upcoming=len(rows), providers=len({r['site'] for r in rows}), with_window=sum(1 for r in rows if r['pred']['start_utc']),
               rule_agrees_on_start=sum(1 for r in rows if r['agree']), lead_hours_quantiles=sorted(x for x in (r['lead_hours_from_snapshot'] for r in rows) if x is not None),
               predictions_sha256=hashlib.sha256(f.read_bytes()).hexdigest(), predictions=public)
    (root / 'registration.json').write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    q = reg['lead_hours_quantiles']; print(json.dumps(dict(upcoming=reg['upcoming'], providers=reg['providers'], with_window=reg['with_window'], rule_agrees=reg['rule_agrees_on_start'], lead_h_min=q[:1], lead_h_median=q[len(q) // 2:len(q) // 2 + 1], lead_h_max=q[-1:], sha=reg['predictions_sha256'][:16])))


if __name__ == '__main__':
    main()
