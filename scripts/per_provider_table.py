#!/usr/bin/env python3
"""Per-provider window-correct counts on the announcement test split for the appendix of the WWW 2027 paper.

Reads stored compile outputs (jsonl rows with id, site, score.start_ok, score.end_ok) and the rule, prints a LaTeX
table body (provider, n, counts per arm). window_ok = start_ok and end_ok is not False (scripts/compare_maintenance_compile.py).

    PYTHONPATH=src python scripts/per_provider_table.py --arm "7B"=runs/converter_fix_20261001/compile_test/qwen7b_extract.jsonl \
        --arm SUTime=runs/sota_baselines_20261015/time/ann_test_sutime_glue.jsonl --arm HeidelTime=runs/tool_baselines_2_20261019/time/ann_test_heideltime_glue.jsonl --rule
"""
import argparse, importlib.util, json, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
sys.path.insert(0, str(HERE)); import maintenance_rule as MR
from vera import multiservice_control as m


def ok(sc):
    return bool(sc['start_ok']) and sc['end_ok'] is not False


def main():
    a = argparse.ArgumentParser(); a.add_argument('--arm', action='append', default=[]); a.add_argument('--rule', action='store_true'); a.add_argument('--tex'); a = a.parse_args()
    items = {i['id']: i for i in mce.items(m.ROOT / 'data/maintenance_notices_20260929') if i['part'] == 'test'}
    arms = {}
    if a.rule:
        arms['rule'] = {k: ok(mce.score(MR.extract(i['text'], i['posted_utc']), i)) for k, i in items.items()}
    for spec in a.arm:
        lab, path = spec.split('=', 1)
        arms[lab] = {r['id']: ok(r['score']) for r in (json.loads(l) for l in (m.ROOT / path).read_text().splitlines() if l.strip()) if r['id'] in items}
    sites = defaultdict(list)
    for k, i in items.items():
        sites[i['site']].append(k)
    labs = list(arms); lines = ['\\begin{tabular}{l r ' + 'r' * len(labs) + '}', '\\toprule', 'provider & n & ' + ' & '.join(labs) + ' \\\\', '\\midrule']
    for s, ks in sorted(sites.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{s.replace('_', '\\_')} & {len(ks)} & " + ' & '.join(str(sum(arms[l].get(k, False) for k in ks)) for l in labs) + ' \\\\')
    lines += ['\\midrule', f"all & {len(items)} & " + ' & '.join(str(sum(arms[l].get(k, False) for k in items)) for l in labs) + ' \\\\', '\\bottomrule', '\\end{tabular}']
    out = '\n'.join(lines); print(out)
    if a.tex:
        Path(a.tex).write_text(out + '\n')


if __name__ == '__main__':
    main()
