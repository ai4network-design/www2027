"""Model-origin, request-versioned goals; hard safety always takes priority."""
from copy import deepcopy
import json
from . import multiservice_control as m
from . import multiservice_numeric as numeric
CONTRACT=json.loads((m.ROOT/'contracts/multiservice_ledger_20260912.json').read_text())

def parse_packet(raw):
    d=m.decode(raw)
    if set(d) not in ({'a'},{'a','g'}):raise ValueError('packet a, optional g')
    d['a']=m.parse_action(m.canonical_json(d['a']))
    if 'g' in d:
        d['g']=m.parse_goal(m.canonical_json(d['g']))
        if not any(d['g'].values()):raise ValueError('empty declared goal')
    return d

def current(request,ledger):
    return deepcopy(ledger) if ledger is not None and ledger['request_sha256']==m.digest(request) else None

def accept(request,ledger,packet,tick,call):
    ledger=current(request,ledger)
    if packet is None:return ledger,None,'no_admitted_packet'
    if ledger is None:
        if 'g' not in packet:return None,None,'goal_required'
        ledger=dict(request_sha256=m.digest(request),goal=deepcopy(packet['g']),origin_tick=tick,origin_call=call)
    elif 'g' in packet and packet['g']!=ledger['goal']:return ledger,None,'same_request_goal_drift'
    return ledger,deepcopy(packet['a']),None

def view(request,s,h,variant,previous=None,ledger=None):
    if variant!='L':raise ValueError('frozen L variant')
    v=numeric.view(request,s,h,'D',previous);ledger=current(request,ledger)
    v['model_declared_goal']=None if ledger is None else deepcopy(ledger['goal'])
    v['last_proposed_path_numeric']=None
    if previous is not None:
        a=previous['raw_proposal']
        if a is not None and 'path' in a:
            i=m.NAMES.index(a['service']);old=previous['before'];row=numeric.numeric(old,previous['history_before'])['rows'][a['service']][a['path']-1]
            v['last_proposed_path_numeric']=dict(tick=previous['tick'],service=a['service'],path=a['path'],
                offered_mbps=min(m.DEMAND[i],old['caps_bps'][i]/1e6 or m.DEMAND[i]),max_rate_mbps=row[1],delay_estimate_ms=row[2])
    return v

def original_view(v):
    v=deepcopy(v);v.pop('model_declared_goal',None);v.pop('last_proposed_path_numeric',None)
    return numeric.original_view(v)

def messages(v,frontend=False):
    if frontend:raise ValueError('direct packet only')
    return [dict(role='system',content=(m.ROOT/'contracts/multiservice_ledger_prompt_v1.txt').read_text().strip()),
            dict(role='user',content=m.canonical_json(v))]

def select(a,s,h,ledger):
    selected,selection=m.select(a,s,h);detail=dict(checked=False,blocked=False)
    if ledger is not None and selection['source']=='proposal' and selected['target']!=m.config(s) and m.sla(s):
        goal=ledger['goal'];before=m.goal_score(goal,s);after=m.goal_score(goal,selected['prediction'])
        detail.update(checked=True,before_score=before,proposed_score=after)
        if before[0]==0 and after[0]>0:
            hold=m.verify({'hold':True},s,h);detail['hold_admitted']=hold['ok']
            if hold['ok']:
                selected=hold;selection=dict(source='intent_hold',proposal=selection['proposal']);detail['blocked']=True
    return selected,selection,detail
