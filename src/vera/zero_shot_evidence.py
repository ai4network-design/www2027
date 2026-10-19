"""Bounded exclusive evidence writes and run/source identities."""
import json
import os
from pathlib import Path
import subprocess
import time
from .zero_shot_control import ROOT, sha, CONTRACT, PROMPT

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True,allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())

def text(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f: f.write(value);f.flush();os.fsync(f.fileno())

def sources():
    paths=sorted((ROOT/'src/vera').glob('zero_shot*.py'))+[ROOT/'src/vera/ns3/ns3_parameter_online.cc',CONTRACT,PROMPT]
    paths += [ROOT/'scripts'/name for name in ('prepare_zero_shot_models.py','verify_zero_shot_model_transfer.py','run_zero_shot_control.py') if (ROOT/'scripts'/name).exists()]
    return {str(p.relative_to(ROOT)):sha(p) for p in paths}

def identity():
    return dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                record_sha256=sha(ROOT/'docs/coordination/record.md'),sources=sources())

def guard(original):
    if identity()!=original: raise RuntimeError('source/record identity changed during scientific stage')

def fresh(out):
    out=Path(out).resolve()
    if ROOT/'runs' not in out.parents: raise ValueError('new ignored output required')
    out.mkdir(parents=True,exist_ok=False)
    return out

def tree(root):
    return {str(p.relative_to(root)):dict(bytes=p.stat().st_size,sha256=sha(p))
            for p in sorted(Path(root).rglob('*')) if p.is_file()}
