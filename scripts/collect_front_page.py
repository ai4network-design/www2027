#!/usr/bin/env python3
"""Front-page pass for the live snapshot (contract live_snapshot_20261019 amendment 1): the history pages list only started or
finished maintenances, so upcoming ones are taken from each site's front page, which links scheduled maintenances as
/incidents/<code>. Same robots check, delay and parser as scripts/collect_maintenance_notices.py; records are appended to the
snapshot's notices.jsonl (duplicates skipped) and the requests are logged in manifest_front.json.

    python3 scripts/collect_front_page.py --sites data/maintenance_notices_20260929/sites.json --out runs/live_snapshot_20261019/notices --per-site 10 --delay 1.0
"""
import argparse, gzip, importlib.util, json, re, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_c = importlib.util.spec_from_file_location('cmn', HERE / 'collect_maintenance_notices.py'); C = importlib.util.module_from_spec(_c); _c.loader.exec_module(C)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--sites', required=True); p.add_argument('--out', required=True); p.add_argument('--per-site', type=int, default=10); p.add_argument('--delay', type=float, default=1.0)
    a = p.parse_args(); out = Path(a.out); (out / 'raw').mkdir(parents=True, exist_ok=True)
    sites = json.loads(Path(a.sites).read_text()); f = C.Fetcher(a.delay)
    done = {json.loads(l)['site'] + '/' + json.loads(l)['code'] for l in (out / 'notices.jsonl').read_text().splitlines() if l.strip()} if (out / 'notices.jsonl').exists() else set()
    log = []
    with (out / 'notices.jsonl').open('a') as fh:
        for site in sites:
            entry = dict(site=site, front=0, codes=0, fetched=0, errors=[])
            try:
                h = f.get(site, f'https://{site}/'); entry['front'] = 1
            except Exception as exc:
                entry['errors'].append(f'front: {type(exc).__name__}'); log.append(entry); print(json.dumps(entry), flush=True); continue
            codes = list(dict.fromkeys(re.findall(r'/incidents/([a-z0-9]{10,14})', h)))[:a.per_site]; entry['codes'] = len(codes)
            (out / 'raw' / site).mkdir(exist_ok=True)
            for code in codes:
                if f'{site}/{code}' in done:
                    continue
                url = f'https://{site}/incidents/{code}'
                try:
                    page = f.get(site, url)
                except Exception as exc:
                    entry['errors'].append(f'{url}: {type(exc).__name__}'); continue
                (out / 'raw' / site / f'{code}.html.gz').write_bytes(gzip.compress(page.encode()))
                rec = dict(site=site, code=code, url=url, fetched_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), source='front', **C.parse_incident(page))
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n'); fh.flush(); entry['fetched'] += 1; done.add(f'{site}/{code}')
            log.append(entry); print(json.dumps(entry), flush=True)
    (out / 'manifest_front.json').write_text(json.dumps(dict(user_agent=C.UA, delay_s=a.delay, requests=f.count, sites=log, created=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), indent=1))


if __name__ == '__main__':
    main()
