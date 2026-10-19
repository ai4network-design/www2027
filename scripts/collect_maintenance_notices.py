#!/usr/bin/env python3
"""Collect public scheduled-maintenance notices from Atlassian Statuspage sites, respecting robots.txt.

Only HTML paths that robots.txt allows for this user agent are fetched (/history?page=N and /incidents/<code>);
the /api/ paths that most Statuspage sites disallow are never requested. At most one request per
``--delay`` seconds overall. Raw HTML goes to <out>/raw/<site>/ (git-ignored data), parsed records to
<out>/notices.jsonl, and a manifest with SHA-256 and the robots decisions to <out>/manifest.json.

Parsed record per maintenance: site, code, title, affected components, updates [{status, unix_ms, body}] in
chronological order. Text is kept verbatim (HTML tags removed, entities unescaped). The notices are public
announcements by the providers; they are used for research only and not redistributed.

    python3 scripts/collect_maintenance_notices.py --sites sites.json --out data/maintenance_notices_20260929
"""
import argparse, gzip, hashlib, html as H, json, re, time, urllib.request, urllib.robotparser
from pathlib import Path

UA = 'status-notice-research-probe/0.1 (academic study of maintenance notices; read-only)'


class Fetcher:
    def __init__(self, delay):
        self.delay = delay; self.last = 0.0; self.count = 0; self.robots = {}
    def allowed(self, site, url):
        if site not in self.robots:
            rp = urllib.robotparser.RobotFileParser(); rp.set_url(f'https://{site}/robots.txt')
            try:
                self.wait(); rp.read()
            except Exception:
                rp = None
            self.robots[site] = rp
        rp = self.robots[site]
        return rp is not None and rp.can_fetch(UA, url)
    def wait(self):
        dt = time.monotonic() - self.last
        if dt < self.delay:
            time.sleep(self.delay - dt)
        self.last = time.monotonic(); self.count += 1
    def get(self, site, url):
        if '/api/' in url or not self.allowed(site, url):
            raise PermissionError(f'robots.txt disallows {url}')
        self.wait()
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=25) as r:
            return r.read().decode('utf-8', 'replace')


def maintenance_codes(history_html):
    u = H.unescape(history_html)
    codes = re.findall(r'"code":"([a-z0-9]{10,14})"[^{}]*?"impact":"maintenance"', u) + re.findall(r'"impact":"maintenance"[^{}]*?"code":"([a-z0-9]{10,14})"', u)
    months = len(re.findall(r'"incidents":\[', u))
    return list(dict.fromkeys(codes)), months


def text(fragment):
    return re.sub(r'[ \t]+', ' ', H.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def parse_incident(page):
    title = re.search(r'<title>(.*?)</title>', page, re.S)
    title = re.sub(r'^.*? Status - ', '', text(title.group(1))) if title else None
    affected = re.search(r'This scheduled maintenance affected:(.*?)</div>', page, re.S)
    updates = []
    for m in re.finditer(r'<div class="row update-row">(.*?)<div class="update-timestamp[^"]*">(.*?)</div>', page, re.S):
        head, stamp = m.group(1), m.group(2)
        status = re.search(r'update-title[^>]*>(.*?)</h2>', head, re.S)
        body = re.search(r'<span class="whitespace-pre-wrap">(.*?)</span>\s*</div>', head, re.S)
        unix = re.search(r'data-datetime-unix="(\d+)"', stamp)
        updates.append(dict(status=text(status.group(1)) if status else None, unix_ms=int(unix.group(1)) if unix else None,
                            body=H.unescape(re.sub(r'<br\s*/?>', '\n', re.sub(r'<(?!br)[^>]+>', '', body.group(1)))).strip() if body else None))
    updates.sort(key=lambda u: u['unix_ms'] or 0)
    return dict(title=title, affected=text(affected.group(1)).rstrip('.') if affected else None, updates=updates)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--sites', required=True); p.add_argument('--out', required=True)
    p.add_argument('--pages', type=int, default=8); p.add_argument('--per-site', type=int, default=60); p.add_argument('--delay', type=float, default=1.0)
    a = p.parse_args()
    out = Path(a.out); (out / 'raw').mkdir(parents=True, exist_ok=True)
    sites = json.loads(Path(a.sites).read_text()); f = Fetcher(a.delay)
    done = {json.loads(l)['site'] + '/' + json.loads(l)['code'] for l in (out / 'notices.jsonl').read_text().splitlines()} if (out / 'notices.jsonl').exists() else set()
    log = []
    with (out / 'notices.jsonl').open('a') as fh:
        for site in sites:
            entry = dict(site=site, pages=0, months=0, codes=0, fetched=0, errors=[])
            codes = []
            for page in range(1, a.pages + 1):
                url = f'https://{site}/history?page={page}'
                try:
                    h = f.get(site, url)
                except Exception as exc:
                    entry['errors'].append(f'{url}: {type(exc).__name__}'); break
                c, months = maintenance_codes(h); entry['pages'] += 1; entry['months'] += months
                if not months:
                    break
                codes += [x for x in c if x not in codes]
                if len(codes) >= a.per_site:
                    break
            codes = codes[:a.per_site]; entry['codes'] = len(codes)
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
                rec = dict(site=site, code=code, url=url, fetched_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **parse_incident(page))
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n'); fh.flush(); entry['fetched'] += 1
            log.append(entry); print(json.dumps(entry), flush=True)
    data = (out / 'notices.jsonl').read_bytes()
    (out / 'manifest.json').write_text(json.dumps(dict(user_agent=UA, delay_s=a.delay, requests=f.count, sites=log, notices=len(data.splitlines()),
                                                      notices_sha256=hashlib.sha256(data).hexdigest(), created=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                                      policy='robots.txt checked per URL; /api/ never requested; research use only; raw text not redistributed'), indent=1))


if __name__ == '__main__':
    main()
