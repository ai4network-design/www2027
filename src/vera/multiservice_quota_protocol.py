"""Quota language transport and syntax only; no action selection or private goal."""
from copy import deepcopy
from decimal import Decimal
import json
from . import multiservice_control as m
from . import multiservice_quota as q
from . import multiservice_quota_interface as interface
from . import multiservice_ordered as ordered
from .multiservice_syntax import Builder,compile_tables as compile_original

def parse(raw,phase):
    if phase=='intent':return q.parse(raw)
    return ordered.parse(raw,phase)

def intent_text(spec):
    spec=q.parse(m.canonical_json(spec))
    return '{"service":'+json.dumps(spec['service'])+''.join(',"'+k+'":'+format(Decimal(str(spec[k])),'f') for k in ('quota_mbit','delay_ms','min_mbps'))+'}'

def grammar(kind):
    if kind=='action':return ordered.grammar(kind)
    if kind!='intent':raise ValueError('phase')
    b=Builder()
    return b.dfa(b.seq(b.literal('{"service":'),b.either(*(b.literal(json.dumps(n)) for n in m.NAMES)),
        b.literal(',"quota_mbit":'),b.number(),b.literal(',"delay_ms":'),b.number(),
        b.literal(',"min_mbps":'),b.number(),b.literal('}')))

def compile_tables(tokenizer,output_vocab,denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)

def view(request,s,h,epoch,ledger=None,previous=None):
    payload=interface.view(request,s,h,epoch,ledger,previous)
    dynamic=payload.pop('dynamic_intent');payload.pop('model_declared_goal')
    raw=payload['request_progress']
    payload['request_progress']={k:deepcopy(raw[k]) for k in ('origin_tick','sample_tick','additional_payload_bits')}
    payload['model_declared_quota']=None if dynamic is None else deepcopy(dynamic['spec'])
    payload['stage']=None if dynamic is None else dynamic['stage']
    payload['remaining_quota_bits']=None if dynamic is None else dynamic['remaining_quota_bits']
    # network/history are for the unchanged public necessary-domain builder,
    # never extra private inputs to the language model.
    return dict(phase=payload['phase'],payload=payload,network=deepcopy(s),confirmed_history=deepcopy(h))

def messages(v,frontend=False):
    if frontend:raise ValueError('direct quota packet only')
    kind=v['phase']
    if kind not in ('intent','action'):raise ValueError('phase')
    payload={'administrator_request':v['payload']['administrator_request']} if kind=='intent' else deepcopy(v['payload'])
    return [dict(role='system',content=(m.ROOT/f'contracts/multiservice_quota_{kind}_v1.txt').read_text().strip()),
            dict(role='user',content=m.canonical_json(payload))]

def calibration_gate(rows):
    return len(rows)==4 and all(r['proposal_admitted'] and r['complete_json_stop'] and r['parsed'] is not None
        and not r['generation_deadline_hit'] and r['proposal_elapsed_ms']<=850 for r in rows[1:])
