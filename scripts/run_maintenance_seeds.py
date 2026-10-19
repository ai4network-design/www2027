#!/usr/bin/env python3
"""Seed replay of the v2 maintenance worlds (contract contracts/seed_replay_20261019.json): the same 477 worlds and beliefs,
with the ns-3 seed re-drawn, so that every world is played three times (the registered seed is replay 0, reused).

Arms: R0_reactive, M_rule, M_qwen7b_extract (post-fix beliefs, runs/converter_fix_20261001/compile_test), M_oracle.
Replay k >= 1 draws the seed as h(id + 'seed' + str(k)); everything else as scripts/run_maintenance_v2.py.

    PYTHONPATH=src python scripts/run_maintenance_seeds.py --contract contracts/seed_replay_20261019.json --arm M_qwen7b_extract --replay 1 --out runs/seed_replay_20261019/closed_loop/M_qwen7b_extract_s1 [--shard i/n]
"""
import importlib.util, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('rv2', HERE / 'run_maintenance_v2.py'); V2 = importlib.util.module_from_spec(_s); _s.loader.exec_module(V2)
V1 = V2.V1
V1.COMPILED = 'runs/converter_fix_20261001/compile_test'; V1.FILES = {'M_qwen7b_extract': 'qwen7b_extract.jsonl'}


def main():
    k = int(sys.argv[sys.argv.index('--replay') + 1]); del sys.argv[sys.argv.index('--replay'):sys.argv.index('--replay') + 2]
    assert k >= 1, 'replay 0 is the registered run; reuse it'
    worlds0 = V2.worlds
    def worlds():
        return [dict(w, seed=V1.h(w['notice'] + 'seed' + str(k), 2 ** 31)) for w in worlds0()]
    V2.worlds = worlds
    V2.main()


if __name__ == '__main__':
    main()
