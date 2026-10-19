"""Fresh model-owned numerical quota -> original public MPC and hard safety."""
import json,time
from copy import deepcopy
from decimal import Decimal
from . import multiservice_control as m, multiservice_service_bound as public
from . import multiservice_quota as quota, multiservice_quota_planner as planner
from . import multiservice_quota_interface as interface
from .multiservice_syntax import Builder,compile_tables as compile_original
HEADER='contracts/multiservice_quota_program_v1.txt'

def parse(raw):
    if type(raw) is not str:raise ValueError('complete wire string')
    wrapped=m.decode('{"value":'+raw+'}')
    if set(wrapped)!={'value'}:raise ValueError('one complete array')
    row=wrapped['value']
    if type(row) is not list or len(row)!=4:raise ValueError('four numerical objective fields')
    name,mb,delay,minimum=row
    if any(not m.finite(x) for x in (mb,delay,minimum)):raise ValueError('finite numerical fields')
    bits=Decimal(str(mb))*8000000
    if bits!=bits.to_integral_value():raise ValueError('exact integer payload quota')
    # Unit conversion of the MODEL output, not interpretation of natural language.
    return quota.parse(m.canonical_json(dict(service=name,quota_mbit=float(Decimal(str(mb))*8),
        delay_ms=delay,min_mbps=minimum)))

def encode(value):
    spec=quota.parse(m.canonical_json(value))
    return json.dumps([spec['service'],float(Decimal(str(spec['quota_mbit']))/8),
        spec['delay_ms'],spec['min_mbps']],separators=(',',':'),allow_nan=False)

def grammar(phase):
    if phase=='intent':return public.grammar(phase)
    if phase!='action':raise ValueError('phase')
    b=Builder();lit,seq,alt=b.literal,b.seq,b.either
    def number():
        first,zero,end=b.node(),b.node(),b.node()
        b.edge(first,'0',zero);b.eps[zero].add(end)
        ints=[b.node() for _ in range(7)]
        for digit in '123456789':b.edge(first,digit,ints[0])
        for i,node in enumerate(ints):
            b.eps[node].add(end)
            if i<6:
                for digit in '0123456789':b.edge(node,digit,ints[i+1])
        dot=b.node()
        for node in [zero]+ints:b.edge(node,'.',dot)
        node=dot
        for _ in range(3):
            nxt=b.node()
            for digit in '0123456789':b.edge(node,digit,nxt)
            b.eps[nxt].add(end);node=nxt
        return first,end
    return b.dfa(seq(lit('['),alt(*(lit('"'+n+'"') for n in m.NAMES)),lit(','),
        number(),lit(','),number(),lit(','),number(),lit(']')))

def compile_tables(tokenizer,output_vocab,denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)

def view(request,s,h,epoch):
    v=public.view(request,s,h,epoch);v['epoch']=deepcopy(epoch)
    return v

def messages(v,frontend=False):
    msg=public.messages(v,frontend=frontend)
    if v['phase']=='action':
        expected=public.view(v['payload']['administrator_request'],v['network'],v['confirmed_history'],v['epoch'])
        if expected!={k:x for k,x in v.items() if k!='epoch'}:raise ValueError('immutable epoch and actual view')
        msg[0]['content']=(m.ROOT/HEADER).read_text().strip()
    return msg

def accept(value,g):
    if not g['proposal_admitted'] or not g['complete_json_stop'] or value is None:return None
    if g['phase']!='program' or g['parsed']!=value or not g['deployment_allowed'] or g['diagnostic_only']:
        raise ValueError('fresh deployable model objective identity')
    parsed=parse(g['raw_text'])
    if parsed!=value:raise ValueError('complete model-owned wire identity')
    return parsed

def select(value,v,*,started=None,call=0,clock=time.perf_counter):
    start=clock() if started is None else started
    selection_start=clock()
    messages(v);request=v['payload']['administrator_request'];s=v['network'];h=v['confirmed_history']
    ledger=None;plan=None;action=None
    if value is not None:
        value=quota.parse(m.canonical_json(value))
        ledger=quota.establish(request,value,v['epoch'],s['tick'],call)
        # The unchanged 650ms soft search starts at the entire decision's start.
        # Generation does not buy a second 650ms allowance.
        action,plan=planner.decide(request,s,h,ledger,started=start,clock=clock)
    parsed=clock()
    selected,selection,retention=interface.select(request,action,s,h,ledger)
    done=clock()
    return selected,selection,dict(method='fresh_model_quota_plus_original_mpc',
        model_spec=deepcopy(value),ledger=ledger,planner=plan,operation=action,retention=retention,
        proposal_admitted=bool(plan and plan['proposal_admitted']),
        charged_proposal_ms=(parsed-start)*1000,planning_and_interpretation_ms=(parsed-selection_start)*1000,
        hard_safety_ms=(done-parsed)*1000,private_program_used=False)
