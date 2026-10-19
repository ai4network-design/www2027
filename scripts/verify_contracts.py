#!/usr/bin/env python3
"""Contract registry: for every contract JSON with a 'sources' map, check each pinned SHA-256 against the working tree and,
when it differs, find the newest commit whose version of the file has the pinned hash. Files that are not tracked
(local data, run outputs) are checked against the working tree only. Prints a Markdown table.

    python3 scripts/verify_contracts.py [--since 20260923] > docs/reproduce/contract_registry_<date>.md
"""
import argparse, hashlib, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def tracked(f):
    return subprocess.run(['git', 'ls-files', '--error-unmatch', f], cwd=ROOT, capture_output=True).returncode == 0


def commit_with_hash(f, want):
    commits = subprocess.run(['git', 'log', '--format=%H', '--', f], cwd=ROOT, capture_output=True, text=True).stdout.split()
    for c in commits:
        blob = subprocess.run(['git', 'show', f'{c}:{f}'], cwd=ROOT, capture_output=True).stdout
        if hashlib.sha256(blob).hexdigest() == want:
            return c[:8]
    return None


def main():
    a = argparse.ArgumentParser(); a.add_argument('--since', default='20260923'); a = a.parse_args()
    rows = []
    for cp in sorted((ROOT / 'contracts').glob('*.json')):
        digits = ''.join(ch for ch in cp.stem if ch.isdigit())[:8]
        if not digits or digits < a.since:
            continue
        c = json.loads(cp.read_text()); src = c.get('sources') or {}
        if not src:
            continue
        head = pinned = local_ok = local_missing = 0; pinned_at = set()
        for f, want in src.items():
            p = ROOT / f; cur = sha(p)
            if not tracked(f):
                if cur == want:
                    local_ok += 1
                else:
                    local_missing += 1
                continue
            if cur == want:
                head += 1
            else:
                c0 = commit_with_hash(f, want); pinned += 1; pinned_at.add(c0 or '?')
        rows.append(dict(contract=cp.name, status=str(c.get('status', ''))[:70], n=len(src), head=head, pinned=pinned, pinned_at=sorted(pinned_at), local_ok=local_ok, local_missing=local_missing, amendments=len(c.get('amendments', []))))
    print('| contract | pinned files | match HEAD | pinned to earlier commits | local files (match / absent here) | amendments | status |')
    print('|---|---|---|---|---|---|---|')
    for r in rows:
        print(f"| {r['contract']} | {r['n']} | {r['head']} | {r['pinned']} ({', '.join(r['pinned_at']) or '–'}) | {r['local_ok']} / {r['local_missing']} | {r['amendments']} | {r['status']} |")


if __name__ == '__main__':
    main()
