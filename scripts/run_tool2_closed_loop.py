#!/usr/bin/env python3
"""Closed-loop arms whose beliefs come from the second tool-baseline set (contracts/tool_baselines_2_20261019.json step 2):
M_heideltime_glue and M_cmp_llm_<model> on the v2 maintenance worlds (scripts/run_maintenance_v2.py construction).

    PYTHONPATH=src python scripts/run_tool2_closed_loop.py --arm M_heideltime_glue --contract contracts/tool_baselines_2_20261019.json --out runs/tool_baselines_2_20261019/closed_loop/M_heideltime_glue [--shard i/n]
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rv2', HERE / 'run_maintenance_v2.py'); V2 = importlib.util.module_from_spec(_s); _s.loader.exec_module(V2)
V1 = V2.V1
V1.COMPILED = 'runs/tool_baselines_2_20261019/time'
V1.FILES = {'M_heideltime_glue': 'ann_test_heideltime_glue.jsonl', 'M_heideltime_raw': 'ann_test_heideltime_raw.jsonl',
            'M_cmp_llm_local_qwen7b': 'ann_test_cmp_llm_local_qwen7b.jsonl', 'M_cmp_llm_local_qwen7b_lenient': 'ann_test_cmp_llm_local_qwen7b_lenient.jsonl',
            'M_cmp_llm_gpt-6-luna': 'ann_test_cmp_llm_gpt-6-luna.jsonl', 'M_cmp_llm_gpt-6-luna_lenient': 'ann_test_cmp_llm_gpt-6-luna_lenient.jsonl', 'M_cmp_llm_gpt-5p6-terra': 'ann_test_cmp_llm_gpt-5p6-terra.jsonl', 'M_cmp_llm_gpt-5p6-terra_lenient': 'ann_test_cmp_llm_gpt-5p6-terra_lenient.jsonl'}
V1.ARMS = V1.ARMS + list(V1.FILES)
V2.main()
