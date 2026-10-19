#!/usr/bin/env python3
"""Closed reference models with OUR prompts on the three compile benchmarks (contract contracts/closed_models_compile_20261019.json).

Same prompts, decoding, conversion and scoring as the Qwen runs (scripts/maintenance_compile_eval.py extract mode,
scripts/revision_compile_eval.py extract mode, scripts/affected_compile_eval.py); the chat call goes to the OpenAI API
(chat.completions, temperature 0 where the model accepts it, one call per item, resumable so no item is paid twice).
Outputs use the same row format and file names as the local runs (<model>_extract.jsonl) so the existing comparison
scripts read them unchanged. Key: PARSER_OPENAI_API_KEY or ~/.config/vera/openai_key (never printed or stored in outputs).

    PYTHONPATH=src python scripts/closed_model_compile.py --model gpt-6-luna --task ann --part test --out runs/closed_models_compile_20261019/ann_test [--max-items 10]
"""
import argparse, datetime as D, importlib.util, json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('mce', HERE / 'maintenance_compile_eval.py'); mce = importlib.util.module_from_spec(_s); _s.loader.exec_module(mce)
_r = importlib.util.spec_from_file_location('rce', HERE / 'revision_compile_eval.py'); RCE = importlib.util.module_from_spec(_r); _r.loader.exec_module(RCE)
_a = importlib.util.spec_from_file_location('ace', HERE / 'affected_compile_eval.py'); ACE = importlib.util.module_from_spec(_a); _a.loader.exec_module(ACE)
from vera import multiservice_control as m


class OpenAIChat:
    def __init__(self, model):
        from openai import OpenAI
        key = os.environ.get('PARSER_OPENAI_API_KEY') or Path(os.path.expanduser('~/.config/vera/openai_key')).read_text().strip()
        self.client = OpenAI(api_key=key); self.model = model; self.calls = 0; self.temperature_ok = True; self.version = None

    def __call__(self, messages, max_new_tokens=160):
        t0 = time.perf_counter()
        for attempt in range(4):
            try:
                kw = dict(model=self.model, messages=messages, max_completion_tokens=max_new_tokens)
                if self.temperature_ok:
                    kw['temperature'] = 0
                r = self.client.chat.completions.create(**kw)
                self.calls += 1; self.version = getattr(r, 'model', None)
                return (r.choices[0].message.content or ''), (time.perf_counter() - t0) * 1000
            except Exception as exc:
                msg = str(exc)
                if 'temperature' in msg and self.temperature_ok:
                    self.temperature_ok = False; continue
                if 'max_completion_tokens' in msg or 'max_tokens' in msg:
                    kw.pop('max_completion_tokens', None); kw['max_tokens'] = max_new_tokens
                    try:
                        r = self.client.chat.completions.create(**kw); self.calls += 1; self.version = getattr(r, 'model', None)
                        return (r.choices[0].message.content or ''), (time.perf_counter() - t0) * 1000
                    except Exception as exc2:
                        msg = str(exc2)
                if attempt == 3:
                    return f'ERROR: {msg[:200]}', (time.perf_counter() - t0) * 1000
                time.sleep(2 * (attempt + 1))


def main():
    p = argparse.ArgumentParser(); p.add_argument('--model', required=True); p.add_argument('--task', required=True, choices=['ann', 'upd', 'aff'])
    p.add_argument('--part', default='test'); p.add_argument('--out', required=True); p.add_argument('--max-items', type=int); a = p.parse_args()
    if a.task == 'ann':
        sel = [i for i in mce.items(m.ROOT / 'data/maintenance_notices_20260929') if i['part'] == a.part]
    elif a.task == 'upd':
        sel = [dict(i, truth=RCE.ACTION[i['kind']]) for i in RCE.items() if i['part'] == a.part]
    else:
        sel = [i for i in ACE.items() if i['part'] == a.part]
    sel = sel[:a.max_items] if a.max_items else sel
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); label = f"{a.model.replace('.', 'p')}_extract"; f = out / f'{label}.jsonl'
    done = {}
    if f.exists():
        done = {json.loads(l)['id']: json.loads(l) for l in f.read_text().splitlines() if l.strip()}
    chat = OpenAIChat(a.model); rows = []
    with f.open('a') as fh:
        for i in sel:
            if i['id'] in done:
                rows.append(done[i['id']]); continue
            if a.task == 'ann':
                raw, ms = chat(mce.messages(i, 'extract'), 120); o = mce.decode(raw); pred = mce.convert(o); sc = mce.score(pred, i)
            elif a.task == 'upd':
                raw, ms = chat(RCE.messages(i, 'extract', 'upd'), 120); o = mce.decode(raw); pred = RCE.convert_rev(o); sc = RCE.score(pred, i)
            else:
                raw, ms = chat(ACE.messages(i), 140); o = mce.decode(raw); pred = dict(region=o.get('region'), **mce.convert(o)); sc = ACE.score(pred, i)
            row = dict(i, raw=raw, extracted=o, pred=pred, ms=ms, score=sc, model_version=chat.version, run_utc=D.datetime.now(D.timezone.utc).strftime('%Y-%m-%dT%H:%M'))
            rows.append(row); fh.write(json.dumps(row, ensure_ascii=False) + '\n'); fh.flush()
    summ = mce.summarise(rows) if a.task == 'ann' else (RCE.summarise(rows) if a.task == 'upd' else ACE.summarise(rows))
    if a.task == 'aff':
        summ.pop('by_site', None)
    print(a.task, a.part, label, 'calls', chat.calls, 'version', chat.version, json.dumps(summ)[:400], flush=True)


if __name__ == '__main__':
    main()
