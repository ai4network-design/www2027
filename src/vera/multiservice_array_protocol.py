"""Reversible operation transport; original interpretation/control stays separate.

Arrays replace repeated object keys, not physical action choices. The decoded
packet is validated by the original ordered parser before any action is selected.
"""
from decimal import Decimal
import json
from . import multiservice_control as m
from . import multiservice_ordered as ordered
from . import multiservice_quota_protocol as quota
from . import multiservice_precondition as pre
from .multiservice_syntax import Builder,compile_tables as compile_original

view=quota.view
calibration_gate=quota.calibration_gate

def operation_text(operation):
    a=m.parse_action(m.canonical_json(operation))
    if 'hold' in a:return '["hold"]'
    field='path' if 'path' in a else 'rate_mbps';value=a[field]
    number='null' if value is None else format(Decimal(str(value)).normalize(),'f')
    return '['+json.dumps(a['service'])+','+json.dumps(field)+','+number+']'

def encode(packet):
    packet=ordered.parse(m.canonical_json(packet),'action')
    return pre.PREFIX+','.join(operation_text(a) for a in packet['steps'])+']}'

def parse(raw,phase):
    if phase=='intent':return quota.parse(raw,phase)
    if phase!='action':raise ValueError('phase')
    value=m.decode(raw)
    if set(value)!={'steps'} or type(value['steps']) is not list or not 1<=len(value['steps'])<=2:
        raise ValueError('one or two chronological operation arrays')
    operations=[]
    for row in value['steps']:
        if type(row) is not list:raise ValueError('operation array')
        if row==['hold']:operations.append({'hold':True})
        elif len(row)==3 and type(row[0]) is str and row[1] in ('path','rate_mbps'):
            operations.append(dict(service=row[0],**{row[1]:row[2]}))
        else:raise ValueError('hold or service/field/value array')
    # In particular, reject a bad later step before interpreting the first one.
    return ordered.parse(m.canonical_json(dict(steps=operations)),'action')

def grammar(phase):
    if phase=='intent':return quota.grammar(phase)
    if phase!='action':raise ValueError('phase')
    b=Builder()
    def operation():
        names=b.either(*(b.literal(json.dumps(n)) for n in m.NAMES))
        field=b.either(b.seq(b.literal(',"path",'),b.either(*(b.literal(str(i)) for i in (1,2,3)))),
            b.seq(b.literal(',"rate_mbps",'),b.either(b.literal('null'),b.number())))
        return b.either(b.literal('["hold"]'),b.seq(b.literal('['),names,field,b.literal(']')))
    return b.dfa(b.seq(b.literal(pre.PREFIX),operation(),b.either(b.literal(''),b.seq(b.literal(','),operation())),b.literal(']}')))

def compile_tables(tokenizer,output_vocab,denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)

def messages(v,frontend=False):
    result=quota.messages(v,frontend=frontend)
    if v['phase']=='action':
        _,body=result[0]['content'].split('\n',1)
        header=(m.ROOT/'contracts/multiservice_array_header_v1.txt').read_text().strip()
        result[0]['content']=header+'\n'+body
    return result
