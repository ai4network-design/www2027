#!/usr/bin/env python3
"""Merge the revision items of the two collections into the usable set (data/maintenance_revisions_merged_20261011.json):
usable = announcement with a clock time, update with a clock time or a cancellation, posted before the end; first
occurrence wins (the 2026-10-01 collection first). The item ids and order are stable across label changes.

    PYTHONPATH=src python scripts/merge_revisions.py data/maintenance_notices_update_20261001 data/maintenance_notices_20260929 --out data/maintenance_revisions_merged_20261011.json
"""
import argparse, importlib.util, json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_a = importlib.util.spec_from_file_location('anu', HERE / 'analyse_notice_updates.py'); anu = importlib.util.module_from_spec(_a); _a.loader.exec_module(anu)


def main():
    p = argparse.ArgumentParser(); p.add_argument('roots', nargs='+'); p.add_argument('--out', required=True); a = p.parse_args()
    rows = {}
    for root in a.roots:
        for r in anu.revisions(root):
            rows.setdefault(r['id'], r)
    use = [r for r in rows.values() if r['announcement_clock'] and (r['update_clock'] or r['kind'] == 'cancel') and r['phase'] != 'after']
    Path(a.out).write_text(json.dumps(use, ensure_ascii=False, indent=0))
    print('merged', len(rows), 'usable', len(use), dict(Counter((r['part'], r['kind']) for r in use)))
    print('label changed by the strict rule:', dict(Counter((r['site'], r['kind_selection'], r['kind']) for r in use if r['kind_selection'] != r['kind'])))


if __name__ == '__main__':
    main()
