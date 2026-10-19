"""Explicit model-owned quota MPC baseline; no private evaluator or native calls."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import math
import time
from . import multiservice_control as m
from . import multiservice_quota as q
from . import multiservice_quota_interface as interface
from . import multiservice_precondition as pre

C=json.loads((m.ROOT/'contracts/multiservice_quota_planner_20260913.json').read_text())


def forecast(s,h,target):
    """One nominal conservative-rate second, not a certified native byte bound.

    The original .01Mbps allowance is a surrogate, not proof against every
    packet/transit transient. All real quality must still be measured afterwards.
    """
    if [r['name'] for r in s['services']]!=list(m.NAMES):raise ValueError('ordered public service identity')
    out=dict(deepcopy(target),tick=s['tick']+1,physical_time=s['physical_time']+1,
        observation_kind='numerical_forecast_not_native',
        links=[dict(capacity_mbps=m.C['path_min_mbps'][i],queued_bytes=r['queued_bytes']) for i,r in enumerate(s['links'])],services=[])
    prediction=m.predict(out,target)
    for i,predicted in enumerate(prediction['services']):
        r=deepcopy(predicted)
        delivered=math.floor(r['throughput_mbps']*1000000/8)
        r['rx_bytes']=s['services'][i]['rx_bytes']+delivered
        out['services'].append(r)
    return out,m.advance(h,s,target)


def knots(request,s,h,ledger):
    """Public monotonic breakpoints, including preparation for each destination."""
    dynamic=q.current(request,s,ledger)
    if dynamic is None:raise ValueError('model-owned quota required')
    spec=dynamic['spec'];remaining=dynamic['remaining_quota_bits'];actions=[{'hold':True}]
    for i,name in enumerate(m.NAMES):
        rates={round(m.FLOOR[i]*1000)+10,int(m.DEMAND[i]*1000)}
        if spec['service']==name:
            minimum=int(((Decimal(str(spec['min_mbps']))+Decimal('.01'))*1000).to_integral_value(rounding=ROUND_CEILING))
            rates.add(minimum)
            for ticks in range(1,C['bounds']['forecast_steps']+1):
                k=int((Decimal(remaining)/Decimal(1000*ticks)+10).to_integral_value(rounding=ROUND_CEILING))
                if k>=minimum:rates.add(k)
        for p,capacity in enumerate(m.C['path_min_mbps'],1):
            others=sum((s['caps_bps'][j]//1000 or round(m.DEMAND[j]*1000)) for j in range(3) if j!=i and s['paths'][j]==p)
            rates.add(int((Decimal(str(capacity))*40000/41).to_integral_value(rounding=ROUND_FLOOR))-others)
        actions.extend(dict(service=name,path=p) for p in (1,2,3))
        actions.append(dict(service=name,rate_mbps=None))
        actions.extend(dict(service=name,rate_mbps=k/1000) for k in sorted(rates) if m.FLOOR[i]*1000<=k<=m.DEMAND[i]*1000)
    for a in actions:
        if 'rate_mbps' in a and a['rate_mbps']==m.DEMAND[m.NAMES.index(a['service'])]:a['rate_mbps']=None
    unique={m.canonical_json(m.canonical(a,s)):m.canonical(a,s) for a in actions}
    return [unique[k] for k in sorted(unique)]


def quality(s,spec):
    r=s['services'][m.NAMES.index(spec['service'])]
    return m.sla(s) and r['delay_ms']<=spec['delay_ms'] and r['throughput_mbps']>=spec['min_mbps']


def advance_objective(before,after,request,ledger,score,step,edited):
    old=q.current(request,before,ledger);new=q.current(request,after,ledger)
    delta=new['progress']['additional_payload_bits'][old['spec']['service']]-old['progress']['additional_payload_bits'][old['spec']['service']]
    credited=min(old['remaining_quota_bits'],delta)
    result=deepcopy(score);result['good_bits']+=credited if quality(after,old['spec']) else 0
    if 'release_expression' in ledger:
        # A completed byte quota does not revoke a still-active protection duty.
        # Preserve the old objective/score shape exactly for legacy quota-only ledgers.
        attained=score.get('protection_attained',ledger.get('protection_attained',quality(before,old['spec'])))
        result['active_protection_breaks']=score.get('active_protection_breaks',0)+int(
            (attained or old['quota_complete']) and old['stage']=='urgent' and not quality(after,old['spec']))
        result['protection_attained']=attained or quality(after,old['spec'])
    result['delivered_bits']+=credited;result['edits']+=int(edited)
    if new['stage']=='restore' and result['completion_step'] is None:result['completion_step']=step
    if new['stage']=='restore' and not any(after['caps_bps']) and m.sla(after) and result['restoration_step'] is None:result['restoration_step']=step
    result['postcompletion_capped_steps']+=int(old['stage']=='restore' and any(after['caps_bps']))
    return result


def rank(score):
    far=C['bounds']['forecast_steps']+1
    prefix=(score['active_protection_breaks'],) if 'active_protection_breaks' in score else ()
    return prefix+(-score['good_bits'],-score['delivered_bits'],
            far if score['completion_step'] is None else score['completion_step'],
            far if score['restoration_step'] is None else score['restoration_step'],
             score['postcompletion_capped_steps'],score['edits'])


def conditional_completion(request,s,h,ledger,budget):
    """Bounded rollout completion, not a deployed shortcut or a private schedule.

    The original two explicit actions may be WAIT, PREPARE; allow a subsequent
    already-safe QoS-achieving move in conditional forecasts. Legacy rollouts
    remain unchanged. An incomplete scan invalidates this forecast branch.
    """
    d=q.current(request,s,ledger)
    if 'release_expression' not in ledger or d['stage']!='urgent' or quality(s,d['spec']):
        return {'hold':True},0,True
    best=None;checks=0
    for a in knots(request,s,h,ledger):
        if budget():return None,checks,False
        v,sel,_=interface.select(request,a,s,h,ledger);checks+=1
        if sel['source']!='proposal':continue
        ns,_=forecast(s,h,v['target'])
        if not quality(ns,d['spec']):continue
        row=ns['services'][m.NAMES.index(d['spec']['service'])]
        key=(-row['throughput_mbps'],int(v['target']!=m.config(s)),m.canonical_json(a))
        if best is None or key<best[0]:best=(key,a)
    return ({'hold':True} if best is None else best[1]),checks,True


def decide(request,s,h,ledger,*,started=None,clock=time.perf_counter):
    start=clock() if started is None else started
    pre.validate_state(s)
    dynamic=q.current(request,s,ledger)
    if dynamic is None:raise ValueError('planner cannot manufacture an intent')
    domain=pre.build(s,h);domain_operations=set(domain['operations'])
    before=deepcopy((s,h,ledger));bounds=C['bounds'];branches=[];best=None;checks=0;timed_out=False
    budget=lambda:(clock()-start)*1000>=bounds['soft_search_ms']
    initial=dict(good_bits=0,delivered_bits=0,completion_step=0 if dynamic['stage']=='restore' else None,
        restoration_step=0 if dynamic['stage']=='restore' and not any(s['caps_bps']) and m.sla(s) else None,
        postcompletion_capped_steps=0,edits=0)
    def certify(a,state,history):
        nonlocal checks
        checks+=1
        v,sel,_=interface.select(request,a,state,history,ledger)
        return v if sel['source']=='proposal' else None
    def step(state,history,v,score,index):
        ns,nh=forecast(state,history,v['target'])
        return ns,nh,advance_objective(state,ns,request,ledger,score,index,v['target']!=m.config(state))
    first_candidates=[a for a in knots(request,s,h,ledger) if pre.text(a) in domain_operations]
    for first in first_candidates:
        if budget():timed_out=True;break
        v=certify(first,s,h)
        if v is None:continue
        ns,nh,one=step(s,h,v,initial,1)
        for second in knots(request,ns,nh,ledger):
            if budget() or len(branches)>=bounds['maximum_evaluated_branches']:timed_out=True;break
            w=certify(second,ns,nh)
            if w is None:continue
            state,history,score=step(ns,nh,w,one,2)
            trace=[dict(step=1,action=v['action'],bits=q.current(request,ns,ledger)['progress']['additional_payload_bits'],stage=q.current(request,ns,ledger)['stage']),
                   dict(step=2,action=w['action'],bits=q.current(request,state,ledger)['progress']['additional_payload_bits'],stage=q.current(request,state,ledger)['stage'])]
            complete=True
            for index in range(3,bounds['forecast_steps']+1):
                if budget():timed_out=True;complete=False;break
                d=q.current(request,state,ledger);a={'hold':True}
                if d['stage']=='restore':
                    certificate=m.escape(state,history)
                    if certificate['ok'] and certificate['actions']:a=certificate['actions'][0]
                elif 'release_expression' in ledger:
                    a,extra,completed=conditional_completion(request,state,history,ledger,budget)
                    checks+=extra
                    if not completed:timed_out=True;complete=False;break
                checks+=1;selected,sel,_=interface.select(request,a,state,history,ledger)
                state,history,score=step(state,history,selected,score,index)
                trace.append(dict(step=index,action=selected['action'],source=sel['source'],
                    bits=q.current(request,state,ledger)['progress']['additional_payload_bits'],stage=q.current(request,state,ledger)['stage']))
            if not complete:break
            row=dict(first=first,second=second,score=score,rank=rank(score))
            branches.append(row)
            if best is None or row['rank']<best['rank']:best=dict(row,forecast=trace)
        if timed_out:break
    if (s,h,ledger)!=before:raise ValueError('planner mutated actual feedback')
    elapsed=(clock()-start)*1000
    return (None if best is None or elapsed>=850 else deepcopy(best['first'])),dict(
        version=C['version'],model_spec=deepcopy(dynamic['spec']),model_stage=dynamic['stage'],
        actual_remaining_bits=dynamic['remaining_quota_bits'],first_candidates=first_candidates,
        public_first_domain={k:v for k,v in domain.items() if k!='operations'},
        public_first_operations=len(domain_operations),
        completed_branches=len(branches),certification_calls=checks,bounded_search=timed_out,
        search_ms=elapsed,proposal_admitted=best is not None and elapsed<850,
        best=best,branches=branches,forecast_only=True,actual_future_inputs=0,
        private_score_inputs=0,original_model_calls=0,optimizer=C['kind'])
