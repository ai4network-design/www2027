"""Independent multi-service operations, finite safety certificate and controllers.

No task strings, goal schedule, private task IDs, or raw-language parser here.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import json
import math
from .zero_shot_control import ROOT, sha

import os
CONTRACT=ROOT/os.environ.get('VERA_TOPOLOGY_CONTRACT','contracts/multiservice_comparison_20260911.json')   # second topology: contracts/topology_L_20261015.json
C=json.loads(CONTRACT.read_text())
NAMES=C['services']; DEMAND=C['demand_mbps']; FLOOR=C['hard_min_mbps']
N=len(NAMES); P=len(C['path_min_mbps']); PATHS=tuple(range(1,P+1))   # services and paths; service i's home path is i+1

def canonical_json(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):
    import hashlib
    return hashlib.sha256(canonical_json(x).encode()).hexdigest()
def finite(x): return type(x) in (int,float) and math.isfinite(x)
def decode(raw):
    if not isinstance(raw,str) or len(raw.encode())>1024:raise ValueError('bounded JSON')
    text=raw.strip()
    if text.startswith('```'):
        lines=text.splitlines()
        if len(lines)<3 or lines[0] not in ('```','```json') or lines[-1]!='```':raise ValueError('complete fence')
        text='\n'.join(lines[1:-1])
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('duplicate key')
            d[k]=v
        return d
    obj=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    if type(obj) is dict and set(obj) in ({'operation'},{'next_operation'}):obj=next(iter(obj.values()))
    if type(obj) is not dict:raise ValueError('object')
    return obj
def parse_action(raw):
    d=decode(raw)
    if set(d)=={'hold'} and d['hold'] is True:return d
    if set(d) not in ({'service','path'},{'service','rate_mbps'}) or d['service'] not in NAMES:raise ValueError('one service operation')
    if 'path' in d:
        p=d['path']
        if not finite(p) or int(p)!=p or p not in PATHS:raise ValueError('path')
        d['path']=int(p)
    else:
        r=d['rate_mbps'];i=NAMES.index(d['service'])
        if r is not None:
            if not finite(r) or not FLOOR[i]<=r<=DEMAND[i]:raise ValueError('rate domain')
            d['rate_mbps']=float(Decimal(str(r)).quantize(Decimal('.001'),rounding=ROUND_HALF_UP))
    return d
def parse_goal(raw):
    d=decode(raw)
    if set(d)!={'delay_ms','min_mbps'}:raise ValueError('two numeric goal maps')
    for k,m in d.items():
        if type(m) is not dict or any(n not in NAMES for n in m):raise ValueError('goal services')
        if any(not finite(v) or not 0<v<=(1000 if k=='delay_ms' else DEMAND[NAMES.index(n)]) for n,v in m.items()):raise ValueError('goal value')
    return d
def history():return dict(edit_ticks=[],route_ticks=[],rate_ticks=[[] for _ in range(N)],limited_ticks=[0]*N)
def config(s):return dict(paths=s['paths'][:],caps_bps=s['caps_bps'][:])
def target(a,s):
    a=parse_action(canonical_json(a));t=config(s)
    if 'hold' not in a:
        i=NAMES.index(a['service'])
        if 'path' in a:t['paths'][i]=a['path']
        else:t['caps_bps'][i]=0 if a['rate_mbps'] is None or a['rate_mbps']==DEMAND[i] else int(round(a['rate_mbps']*1e6))
    return t
def canonical(a,s):return {'hold':True} if target(a,s)==config(s) else a
def advance(h,s,t):
    h=deepcopy(h);tick=s['tick']
    changed=False
    for i in range(N):
        if s['paths'][i]!=t['paths'][i]:h['route_ticks'].append(tick);changed=True
        if s['caps_bps'][i]!=t['caps_bps'][i]:h['rate_ticks'][i].append(tick);changed=True
        h['limited_ticks'][i]=h['limited_ticks'][i]+1 if t['caps_bps'][i] else 0
    if changed:h['edit_ticks'].append(tick)
    return h
def predict(s,t,robust=False):
    offered=[min(DEMAND[i],t['caps_bps'][i]/1e6 or DEMAND[i]) for i in range(N)]
    loads=[sum(offered[i]*1.025 for i in range(N) if t['paths'][i]==p) for p in PATHS]
    rows=[]
    for i in range(N):
        p=t['paths'][i]-1;cap=C['path_min_mbps'][p] if robust else s['links'][p]['capacity_mbps']
        queued=0 if robust else s['links'][p]['queued_bytes']*8/1e6
        excess=max(0,loads[p]-cap);loss=excess/max(loads[p],1e-9)
        delay=2+C['path_delay_ms'][p]+.00984/cap*1000+2*.0984+queued/cap*1000
        rows.append(dict(name=NAMES[i],throughput_mbps=max(0,offered[i]*(1-loss)-.01),delay_ms=delay,loss=loss))
    return dict(services=rows,loads_mbps=loads,capacity_margin_mbps=[(C['path_min_mbps'][p] if robust else s['links'][p]['capacity_mbps'])-loads[p] for p in range(P)])
def sla(obs):
    return all(r['throughput_mbps']>=FLOOR[i]-1e-9 and r['delay_ms']<=50 and r['loss']<=.01 for i,r in enumerate(obs['services']))
def robust_ok(t):
    p=predict({},t,True)
    return sla(p) and min(p['capacity_margin_mbps'])>=-1e-9
def operational(a,s,h,emergency=False):
    t=target(a,s);tick=s['tick'];errors=[]
    routes=[i for i in range(N) if t['paths'][i]!=s['paths'][i]]
    rates=[i for i in range(N) if t['caps_bps'][i]!=s['caps_bps'][i]]
    if len(routes)+len(rates)>1:errors.append('one_field')
    active=lambda xs:sum(x>tick-60 for x in xs)
    if routes:
        if 20<=tick<30:errors.append('protected_route')
        if h['route_ticks'] and tick-h['route_ticks'][-1]<5:errors.append('route_cooldown')
        if active(h['route_ticks'])+1>2:errors.append('route_budget')
    if active(h['edit_ticks'])+bool(routes or rates)+sum(bool(x) for x in t['caps_bps'])>6:errors.append('edit_release_reserve')
    for i in rates:
        if h['rate_ticks'][i] and tick-h['rate_ticks'][i][-1]<2 and not emergency:errors.append('rate_dwell')
    for i in range(N):
        if t['caps_bps'][i] and h['limited_ticks'][i]+1>8:errors.append('rate_max_dwell')
    if not robust_ok(t):errors.append('robust_shared_capacity_or_sla')
    return t,errors
def successor(s,t,h):
    out=dict(t,tick=s['tick']+1,links=s['links'],services=predict(s,t)['services'])
    return out,advance(h,s,t)

@lru_cache(maxsize=8192)
def _escape(key):
    obj=json.loads(key);start=obj['state'];h0=obj['history'];count=0;seen=set()
    def search(s,h,depth):
        nonlocal count
        count+=1
        if count>512 or depth>12:return None
        if not any(s['caps_bps']) and robust_ok(s):return []
        k=canonical_json([s['tick'],config(s),h])
        if k in seen:return None
        seen.add(k)
        actions=[dict(service=NAMES[i],rate_mbps=None) for i in range(N) if s['caps_bps'][i]]
        actions += [dict(service=NAMES[i],path=i+1) for i in range(N) if s['paths'][i]!=i+1]
        actions += [dict(hold=True)]
        for a in actions:
            t,errors=operational(a,s,h)
            if errors:continue
            ns,nh=successor(s,t,h);tail=search(ns,nh,depth+1)
            if tail is not None:return [a]+tail
        return None
    result=search(start,h0,0)
    return dict(ok=result is not None,actions=result or [],nodes=min(count,513))
def escape(s,h):
    # Current queues/capacity are deliberately replaced by public worst-case bounds.
    minimal=dict(tick=s['tick'],**config(s),links=[dict(capacity_mbps=c,queued_bytes=0) for c in C['path_min_mbps']])
    return deepcopy(_escape(canonical_json(dict(state=minimal,history=h))))
def verify(a,s,h):
    try:
        a=canonical(parse_action(canonical_json(a)),s)
        t=target(a,s);p=predict(s,t)
        emergency=not sla(s) and sla(p) and 'rate_mbps' in a
        t,errors=operational(a,s,h,emergency)
        if not sla(p):errors.append('current_prediction_sla')
        ns,nh=successor(s,t,h)
        certificate=escape(ns,nh) if not errors else dict(ok=False,actions=[],nodes=0)
        if not errors and not certificate['ok']:errors.append('no_release_escape')
        return dict(ok=not errors,action=a,target=t,prediction=p,errors=errors,escape=certificate,
                    emergency_exemption=bool(emergency and any(h['rate_ticks'][i] and s['tick']-h['rate_ticks'][i][-1]<2 for i in range(N) if a.get('service')==NAMES[i])))
    except (ValueError,TypeError,KeyError) as exc:return dict(ok=False,errors=['format: '+str(exc)],action=None)
def select(a,s,h):
    v=verify(a,s,h) if a is not None else dict(ok=False,errors=['no_proposal'])
    if v['ok']:return v,dict(source='proposal',proposal=v)
    hold=verify(dict(hold=True),s,h)
    if hold['ok']:return hold,dict(source='safety_hold',proposal=v)
    certificate=escape(s,h)
    if certificate['ok'] and certificate['actions']:
        recovery=verify(certificate['actions'][0],s,h)
        if recovery['ok']:return recovery,dict(source='safety_recovery',proposal=v)
    raise ValueError('no certified action; keep failed prefix, do not actuate unchecked')
def goal_score(goal,obs):
    rows={r['name']:r for r in obs['services']};gaps=[]
    for n,v in sorted(goal['delay_ms'].items()):gaps.append(max(0,rows[n]['delay_ms']/v-1))
    for n,v in sorted(goal['min_mbps'].items()):gaps.append(max(0,1-rows[n]['throughput_mbps']/v))
    return (sum(x>1e-9 for x in gaps),sum(gaps))
def candidates(s,goal):
    result=[{'hold':True}];keys={canonical_json(result[0])}
    for i,n in enumerate(NAMES):
        rates={FLOOR[i]+.02,DEMAND[i]}
        if n in goal['min_mbps']:rates.add(goal['min_mbps'][n])
        for p,cap in enumerate(C['path_min_mbps'],1):
            others=sum(min(DEMAND[j],s['caps_bps'][j]/1e6 or DEMAND[j]) for j in range(N) if j!=i and s['paths'][j]==p)
            r=math.floor((cap/1.025-others)*1000)/1000
            if FLOOR[i]<=r<=DEMAND[i]:rates.add(r)
        actions=[dict(service=n,path=p) for p in PATHS]+[dict(service=n,rate_mbps=r) for r in sorted(rates)]+[dict(service=n,rate_mbps=None)]
        for a in actions:
            a=canonical(a,s);key=canonical_json(a)
            if key not in keys:keys.add(key);result.append(a)
    return result
def plan(s,h,goal,horizon=2):
    goal=parse_goal(canonical_json(goal));options=[];checked=0
    for index,a in enumerate(candidates(s,goal)):
        v=verify(a,s,h);checked+=1
        if not v['ok']:continue
        score=goal_score(goal,v['prediction']);edits=int(v['target']!=config(s))
        best=(*score,edits) if horizon==1 else None
        if horizon==2:
            ns,nh=successor(s,v['target'],h)
            for second in candidates(ns,goal):
                w=verify(second,ns,nh);checked+=1
                if w['ok']:
                    q=goal_score(goal,w['prediction']);rank=(score[0]+q[0],score[1]+q[1],edits+int(w['target']!=config(ns)))
                    if best is None or rank<best:best=rank
        if best is None:continue
        options.append((best,index,a,score))
    chosen=min(options,key=lambda r:(r[0],r[1])) if options else None
    return (chosen[2] if chosen else None),dict(goal=goal,checked_candidates=checked,horizon=horizon,
        chosen_rank=chosen[0] if chosen else None,immediate_score=chosen[3] if chosen else None)
def view(request,s,h):
    return dict(administrator_request=request,network=deepcopy(s),confirmed_history=deepcopy(h),
        public=dict(services=NAMES,demand_mbps=DEMAND,hard_min_mbps=FLOOR,hard_delay_ms=C['hard_delay_ms'],
        hard_loss_max=.01,path_min_mbps=C['path_min_mbps'],wire_factor=1.025,
        operational_limits=C['operational_limits'],default_goal=C['default_goal'],
        cap_resolution_mbps=.001,cap_domain='hard minimum through demand; null releases; demand canonicalizes to null',
        release_reserve='one edit per active cap; certified uncapped escape required; safe intentional rerouting allowed'))
def messages(v,frontend=False):
    name='multiservice_goal_prompt_v1.txt' if frontend else 'multiservice_system_prompt_v1.txt'
    return [dict(role='system',content=(ROOT/'contracts'/name).read_text().strip()),dict(role='user',content=canonical_json(v))]
