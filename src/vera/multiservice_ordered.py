"""Fresh chronological operations: skip only achieved leading settings, never unsafe ones."""
from copy import deepcopy
import json
from . import multiservice_named as named
from . import multiservice_control as m
from .multiservice_syntax import Builder,compile_tables as compile_original
current=named.current
select=named.select
packet_gate=named.packet_gate


def view(request,s,h,variant,previous=None,ledger=None):
    v=named.view(request,s,h,variant,previous,ledger)
    v['previous_model_plan']=None
    if v['phase']=='action' and previous is not None:
        packet=previous.get('controller_detail',{}).get('packet')
        if packet is not None and set(packet)=={'steps'}:
            v['previous_model_plan']=dict(origin_tick=previous['tick'],
                origin_call=previous['generation']['call'],steps=deepcopy(packet['steps']))
    return v


def parse(raw,phase):
    if phase=='intent':return named.parse(raw,phase)
    if phase!='action':raise ValueError('phase')
    p=m.decode(raw)
    if set(p)!={'steps'} or type(p['steps']) is not list or not 1<=len(p['steps'])<=2:
        raise ValueError('one or two chronological operations')
    # Full packet validation precedes any interpretation, including later steps.
    return dict(steps=[m.parse_action(m.canonical_json(a)) for a in p['steps']])


def interpret(packet,state):
    packet=parse(m.canonical_json(packet),'action');skipped=[]
    for i,a in enumerate(packet['steps']):
        if 'hold' in a:
            return deepcopy(a),dict(skipped_indices=skipped,selected_index=i,reason='explicit_hold')
        if m.target(a,state)==m.config(state):
            skipped.append(i);continue
        # No optimizer, safety predicate, or target-goal input here. Even an
        # unsafe first changed operation is returned to the original safety gate.
        return deepcopy(a),dict(skipped_indices=skipped,selected_index=i,reason='first_changed_setting')
    return dict(hold=True),dict(skipped_indices=skipped,selected_index=None,reason='all_settings_achieved')


def accept(request,ledger,value,tick,call,state):
    ledger=current(request,ledger)
    if ledger is None or value is None:
        l,a,e=named.accept(request,ledger,value,tick,call)
        return l,a,e,None
    a,execution=interpret(value,state)
    return ledger,a,None,execution


def messages(v,frontend=False):
    if frontend:raise ValueError('direct ordered packet only')
    if v['phase']=='intent':return named.messages(v)
    payload=named.action_input(v);payload['previous_model_plan']=deepcopy(v['previous_model_plan'])
    return [dict(role='system',content=(m.ROOT/'contracts/multiservice_ordered_action_v1.txt').read_text().strip()),
            dict(role='user',content=m.canonical_json(payload))]


def grammar(kind):
    if kind=='intent':return named.grammar(kind)
    if kind!='action':raise ValueError('phase')
    b=Builder()
    def op():
        names=b.either(*(b.literal(json.dumps(n)) for n in m.NAMES))
        field=b.either(b.seq(b.literal(',"path":'),b.either(*(b.literal(str(i)) for i in (1,2,3)))),
                      b.seq(b.literal(',"rate_mbps":'),b.either(b.literal('null'),b.number())))
        return b.either(b.literal('{"hold":true}'),b.seq(b.literal('{"service":'),names,field,b.literal('}')))
    return b.dfa(b.seq(b.literal('{"steps":['),op(),b.either(b.literal(''),b.seq(b.literal(','),op())),b.literal(']}')))


def compile_tables(tokenizer,output_vocab,denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)
