#!/usr/bin/env python3
"""Export the anonymised release package of the WWW 2027 benchmark: item identifiers, URLs, splits, references and labels
WITHOUT any notice text (the pages are public; the text is not redistributed), plus the frozen prompts and the contracts.

    PYTHONPATH=src python scripts/export_release_package.py --out runs/release_20261019
"""
import argparse, importlib.util, json, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
_r = importlib.util.spec_from_file_location('rce', HERE / 'revision_compile_eval.py'); RCE = importlib.util.module_from_spec(_r); _r.loader.exec_module(RCE)
_a = importlib.util.spec_from_file_location('ace', HERE / 'affected_compile_eval.py'); ACE = importlib.util.module_from_spec(_a); _a.loader.exec_module(ACE)
from vera import multiservice_control as m
TEXT_KEYS = {'text', 'announcement', 'update', 'title', 'body', 'raw'}


def strip(it):
    return {k: v for k, v in it.items() if k not in TEXT_KEYS}


def main():
    a = argparse.ArgumentParser(); a.add_argument('--out', required=True); a = a.parse_args()
    out = Path(a.out); (out / 'contracts').mkdir(parents=True, exist_ok=True); (out / 'prompts').mkdir(exist_ok=True)
    ann = [dict(strip(i), url=f"https://{i['site']}/incidents/{i['id'].split('/')[-1]}") for i in mce.items(m.ROOT / 'data/maintenance_notices_20260929')]
    (out / 'items_announcement.json').write_text(json.dumps(ann, ensure_ascii=False, indent=1))
    rev = [strip(i) for i in RCE.items()]; (out / 'items_revision.json').write_text(json.dumps(rev, ensure_ascii=False, indent=1))
    aff = [strip(i) for i in ACE.items()]; (out / 'items_affected.json').write_text(json.dumps(aff, ensure_ascii=False, indent=1))
    fresh = ROOT / 'data/maintenance_fresh_items_20261013'
    if fresh.exists():
        for f in fresh.glob('*.json'):
            try:
                rows = json.loads(f.read_text()); rows = [strip(i) for i in rows] if isinstance(rows, list) else rows
                (out / f'items_fresh_{f.stem}.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1))
            except Exception:
                pass
    (out / 'prompts' / 'announcement_extract.txt').write_text(mce.EXTRACT + '\n'); (out / 'prompts' / 'announcement_direct.txt').write_text(mce.PROMPT + '\n')
    (out / 'prompts' / 'revision_extract.txt').write_text(RCE.EXTRACT + '\n'); (out / 'prompts' / 'revision_direct.txt').write_text(RCE.DIRECT + '\n')
    (out / 'prompts' / 'affected_extract.txt').write_text(ACE.EXTRACT_REGION + '\n')
    for c in ROOT.glob('contracts/*2026*.json'):
        if any(k in c.name for k in ('maintenance', 'revision', 'affected', 'fresh', 'controls', 'model_', 'topology_L', 'sota', 'tool_baselines', 'perturbation', 'closed_models', 'converter', 'calibration', 'frozen')):
            shutil.copy(c, out / 'contracts' / c.name)
    (out / 'README.md').write_text('# Status-page maintenance notices: benchmark release (anonymised)\n\n'
        'Items carry the notice identifier and URL on the provider\'s public status page, the development/test split, the automatic\n'
        'references (platform In-progress / Completed transitions on the five-minute grid) and, for T3, the structured component label and\n'
        'the candidate list. Notice text is not redistributed: fetch it from the URL under the site\'s robots.txt. prompts/ holds the frozen\n'
        'prompts; contracts/ the registered contracts with pinned hashes and amendments. Scripts: see the code release.\n')
    sizes = {p.name: p.stat().st_size for p in out.rglob('*') if p.is_file()}
    print(json.dumps(dict(announcement=len(ann), revision=len(rev), affected=len(aff), files=len(sizes)), indent=1))


if __name__ == '__main__':
    main()
