"""Explicit intent-establishment tick followed by original-LM direct action ticks."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_ledger as l
current=l.current
select=l.select


def view(request,s,h,variant,previous=None,ledger=None):
    v=l.view(request,s,h,'L',previous,ledger)
    # Remove a misleading fallback SOFT goal, never remove hard SLA/budget data.
    v['public'].pop('default_goal')
    v['phase']='intent' if v['model_declared_goal'] is None else 'action'
    return v


def messages(v,frontend=False):
    if frontend:raise ValueError('no planner frontend')
    system=(m.ROOT/('contracts/multiservice_phase_'+v['phase']+'_v1.txt')).read_text().strip()
    return [dict(role='system',content=system),dict(role='user',content=m.canonical_json(v))]


def parse(raw,phase):
    if phase=='action':return m.parse_action(raw)
    if phase!='intent':raise ValueError('phase')
    goal=m.parse_goal(raw)
    if not any(goal.values()):raise ValueError('empty goal')
    return goal


def accept(request,ledger,value,tick,call):
    ledger=current(request,ledger)
    if value is None:return ledger,None,'no_admitted_packet'
    if ledger is None:
        ledger=dict(request_sha256=m.digest(request),goal=deepcopy(value),origin_tick=tick,origin_call=call)
        # An interpretation is NOT a model action. Safety supplies this tick's
        # actual hold/recovery, counted in latency, edit, SLA and intent metrics.
        return ledger,None,None
    return ledger,deepcopy(value),None


def packet_gate(records):
    if len(records)!=4:return False
    return all(r.get('proposal_admitted') and r.get('parse_error') is None
        and not r.get('generation_deadline_hit',True) and r.get('proposal_elapsed_ms',float('inf'))<1000
        and r.get('phase')=='intent' and r.get('parsed') is not None for r in records[1:])
