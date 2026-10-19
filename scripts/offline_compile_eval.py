"""Offline compilation evaluation (no ns-3): rule vs two-stage models (optionally with adapters) on phrasing families.

Corpus format: {"items": [{"id", "family", "messages": [str,...], "references": [{obligation_id, field, value} | null,...],
                            "tick": 2, "ledger": optional v2 ledger override}]}
A null reference means the message revises nothing (expected 'none' / abstain).

    PYTHONPATH=src python scripts/offline_compile_eval.py --corpus data/open_requests/explore_families_20260924.json \
        --out runs/explore_families_20260924 --model qwen7b --model qwen1p5b [--adapter name=path] [--mode standard|conditional]
"""
import argparse, json, time
from copy import deepcopy
from pathlib import Path
from vera import multiservice_control as m, obligation_ledger as L, revision_v2 as V
from vera.zero_shot_evidence import fresh, save

BASE = json.loads((m.ROOT / 'contracts/frozen_protocol_20260924_20261008.json').read_text())['base_ledger']

CONDITIONAL = '''You compile one administrator message about a three-service network (query, video, backup) into a conditional rule. Return JSON only:
{"trigger": {"kind": "time"|"event"|"none", "at_minute": INTEGER or null, "event": "conference_ended"|"conference_running_past"|null, "compare": ">"|"<="|null},
 "then": {"obligation_id": "<service>.qos"|"backup.deadline"|"recovery"|"priority"|"none", "field": FIELD or null, "value": VALUE or null},
 "else": {"obligation_id": "none"} or the same shape,
 "until": {"kind": "event"|"time"|"none", "event": "conference_ended"|null, "at_minute": INTEGER or null}}
Fields: <service>.qos delay_ms/min_mbps, backup.deadline deadline_tick/total_bytes, recovery max_intervals, priority order. "pause the backup" means backup.qos min_mbps set to the hard floor 1.0. Use null where nothing applies. No explanation.'''


class Chat:
    def __init__(self, model_dir, adapter=None, quant=None):
        """quant=None loads bf16 on cuda:0 (unchanged path); quant='nf4' loads 4-bit NF4 (bitsandbytes, double quant, bf16 compute)
        for models that do not fit in bf16 (used for Qwen2.5-72B on the 96 GB GPU)."""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch; self.tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        if quant == 'nf4':
            from transformers import BitsAndBytesConfig
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
            self.model = AutoModelForCausalLM.from_pretrained(model_dir, local_files_only=True, quantization_config=q, dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa').eval()
        else:
            assert quant is None, quant
            self.model = AutoModelForCausalLM.from_pretrained(model_dir, local_files_only=True, torch_dtype=torch.bfloat16, attn_implementation='sdpa').to('cuda:0').eval()
        self.soft = None; self.meta = None
        if adapter:
            from vera import lora_lite
            meta = json.loads((Path(adapter) / 'meta.json').read_text())
            if meta.get('lora'):
                lora_lite.inject_lora(self.model, **meta['lora'])
            self.meta, soft = lora_lite.load_adapter(adapter, self.model)
            if soft is not None:
                self.soft = soft.to('cuda:0')
            self.model.eval()
        self.calls = 0

    def __call__(self, messages, max_new_tokens=96):
        self.calls += 1; start = time.perf_counter()
        prompt = self.tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        ids = self.tok(prompt, return_tensors='pt', add_special_tokens=False).to('cuda:0')
        with self.torch.inference_mode():
            if self.soft is not None:
                emb = self.model.get_input_embeddings()(ids['input_ids'])
                emb = self.torch.cat([self.soft.unsqueeze(0).to(emb.dtype), emb], dim=1)
                att = self.torch.ones(emb.shape[:2], dtype=ids['attention_mask'].dtype, device='cuda:0')
                out = self.model.generate(inputs_embeds=emb, attention_mask=att, max_new_tokens=max_new_tokens, do_sample=False, num_beams=1, temperature=None, top_p=None, top_k=None, pad_token_id=self.tok.eos_token_id)
                gen = out[0].tolist()
            else:
                out = self.model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False, num_beams=1, temperature=None, top_p=None, top_k=None, pad_token_id=self.tok.eos_token_id)
                gen = out[0, ids['input_ids'].shape[1]:].tolist()
        return self.tok.decode(gen, skip_special_tokens=True), (time.perf_counter() - start) * 1000


def norm(v):
    return float(v) if isinstance(v, (int, float)) else v


def two_stage(chat, ledger, text, conversation, tick, lenient=True, types=None):
    s1, ms1 = chat(V.stage1_messages(ledger, text, conversation, tick, types))
    try:
        t = V.parse_stage1(s1, lenient=lenient, types=types)
    except ValueError as exc:
        return dict(type=None, request=None, error=f'stage1: {exc}', raw1=s1, raw2=None, ms=ms1)
    if t == 'none':
        return dict(type='none', request=None, error=None, raw1=s1, raw2=None, ms=ms1)
    s2, ms2 = chat(V.stage2_messages(ledger, text, conversation, tick, t))
    try:
        req, cand = V.assemble(ledger, t, s2)
        return dict(type=t, request=req, candidate=cand, error=None, raw1=s1, raw2=s2, ms=ms1 + ms2)
    except ValueError as exc:
        return dict(type=t, request=None, error=f'stage2: {exc}', raw1=s1, raw2=s2, ms=ms1 + ms2)


def score(result, ref):
    if ref is None:
        return dict(type_ok=result['type'] == 'none' or (result['request'] is None and result['error'] is None), value_ok=result['type'] == 'none')
    want_type = f"{ref['obligation_id']}.{ref['field']}"
    type_ok = result['type'] == want_type
    r = result.get('request')
    value_ok = r is not None and (r['obligation_id'], r['field'], norm(r['value'])) == (ref['obligation_id'], ref['field'], norm(ref['value']))
    return dict(type_ok=type_ok, value_ok=value_ok)


def rule_eval(ledger, text, conversation, tick, ref):
    try:
        r = V.rule_request(ledger, text, tick); err = None
    except ValueError as exc:
        r = None; err = str(exc)
    if ref is None:
        return dict(request=r, error=err, type_ok=r is None, value_ok=r is None)
    ok = r is not None and (r['obligation_id'], r['field'], norm(r['value'])) == (ref['obligation_id'], ref['field'], norm(ref['value']))
    return dict(request=r, error=err, type_ok=r is not None and r['obligation_id'] == ref['obligation_id'] and r['field'] == ref['field'], value_ok=ok)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--corpus', required=True); p.add_argument('--out', required=True)
    p.add_argument('--model', action='append', default=[]); p.add_argument('--adapter', action='append', default=[], help='label=path (model taken from meta.json)')
    p.add_argument('--mode', choices=['standard', 'conditional'], default='standard'); p.add_argument('--no-rule', action='store_true')
    a = p.parse_args()
    corpus = json.loads(Path(a.corpus).read_text()); out = fresh(a.out)
    assets = json.loads((m.ROOT / '.runtime/ubuntu-pro6000/assets.json').read_text())['models']
    arms = {}
    if not a.no_rule and a.mode == 'standard':
        arms['rule'] = None
    for name in a.model:
        arms[f'{name}_two_stage_lenient'] = dict(model=name, adapter=None)
    for spec in a.adapter:
        label, path = spec.split('=', 1); meta = json.loads((Path(path) / 'meta.json').read_text())
        arms[label] = dict(model=meta['base_model'], adapter=path)
    results = {}
    for arm, cfg in arms.items():
        chat = Chat(assets[cfg['model']]['directory'], cfg['adapter']) if cfg else None
        rows = []
        for item in corpus['items']:
            ledger = L.upgrade(item.get('ledger') or BASE); conversation = []; tick = item.get('tick', 2)
            for k, text in enumerate(item['messages']):
                ref = item['references'][k]
                if a.mode == 'conditional':
                    raw, ms = chat([dict(role='system', content=CONDITIONAL), dict(role='user', content=json.dumps(dict(current_administrator_message=text, current_tick=tick, prior_administrator_messages=conversation), ensure_ascii=False))], max_new_tokens=320)
                    try:
                        parsed = V.decode(raw); err = None
                    except ValueError as exc:
                        parsed = None; err = str(exc)
                    exp = item.get('expected', [None])[k]
                    def subset(got, want):
                        if isinstance(want, dict):
                            return isinstance(got, dict) and all((got.get(k2) in (None, {}) if v2 is None else subset(got.get(k2), v2)) for k2, v2 in want.items())
                        return norm(got) == norm(want)
                    rows.append(dict(item=item['id'], family=item['family'], k=k, text=text, raw=raw, parsed=parsed, error=err, expected=exp,
                                     ms=ms, match=parsed is not None and exp is not None and subset(parsed, exp)))
                elif cfg is None:
                    r = rule_eval(ledger, text, conversation, tick, ref); rows.append(dict(item=item['id'], family=item['family'], k=k, text=text, ref=ref, **r))
                else:
                    r = two_stage(chat, ledger, text, conversation, tick); sc = score(r, ref)
                    rows.append(dict(item=item['id'], family=item['family'], k=k, text=text, ref=ref, type=r['type'], request=r['request'], error=r['error'], raw1=r['raw1'], raw2=r['raw2'], ms=r['ms'], **sc))
                # advance the ledger with the reference so later messages see the right state (annotation-based context)
                if ref is not None:
                    try:
                        ledger = L.propose(ledger, dict(base_version=ledger['version'], **ref))
                    except ValueError:
                        pass
                conversation.append(text)
        results[arm] = rows
        save(out / f'{arm}.json', rows)
        if chat is not None:
            del chat.model; import torch; torch.cuda.empty_cache()
    summary = {}
    for arm, rows in results.items():
        fams = sorted({r['family'] for r in rows})
        if a.mode == 'conditional':
            summary[arm] = dict(n=len(rows), match=sum(r['match'] for r in rows), parsed=sum(r['parsed'] is not None for r in rows),
                                by_family={f: dict(n=sum(r['family'] == f for r in rows), match=sum(r['match'] for r in rows if r['family'] == f)) for f in fams})
        else:
            summary[arm] = dict(n=len(rows), type_ok=sum(r['type_ok'] for r in rows), value_ok=sum(r['value_ok'] for r in rows),
                                mean_ms=(sum(r.get('ms', 0) for r in rows) / len(rows)) if rows else None,
                                by_family={f: dict(n=sum(r['family'] == f for r in rows), type_ok=sum(r['type_ok'] for r in rows if r['family'] == f), value_ok=sum(r['value_ok'] for r in rows if r['family'] == f)) for f in fams})
    save(out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
