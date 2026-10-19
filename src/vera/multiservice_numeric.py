"""Request-independent public arithmetic; no goal interpretation or action selection."""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import json
from . import multiservice_control as m
from . import multiservice_abc as abc
CONTRACT=json.loads((m.ROOT/'contracts/multiservice_numeric_20260912.json').read_text())

def numeric(s,h):
    offered=[min(Decimal(str(d)),Decimal(str(s['caps_bps'][i]))/1000000 or Decimal(str(d))) for i,d in enumerate(m.DEMAND)]
    rows={}
    for i,n in enumerate(m.NAMES):
        values=[]
        for p,capacity in enumerate(m.C['path_min_mbps'],1):
            others=sum((offered[j] for j in range(3) if j!=i and s['paths'][j]==p),Decimal(0))
            bound=min(Decimal(str(m.DEMAND[i])),max(Decimal(0),Decimal(str(capacity))/Decimal('1.025')-others))
            rate=float(bound.quantize(Decimal('.001'),rounding=ROUND_FLOOR))
            delay=round(2+m.C['path_delay_ms'][p-1]+9.84/capacity+.1968,3)
            values.append([p,rate,delay])
        rows[n]=values
    tick=s['tick'];next_route=max(tick,h['route_ticks'][-1]+5 if h['route_ticks'] else tick)
    if 20<=next_route<30:next_route=30
    return dict(rows=rows,payload_allowance_mbps=.01,
        route_edits_left=max(0,2-sum(x>tick-60 for x in h['route_ticks'])),
        next_route_tick=next_route,
        cap_ticks_left={n:8-h['limited_ticks'][i] for i,n in enumerate(m.NAMES)})

def view(request,s,h,variant,previous=None):
    if variant not in ('C','D'):raise ValueError('frozen variant')
    v=abc.view(request,s,h,'C',previous)
    if variant=='D':v['public_numeric']=numeric(s,h)
    return v

def original_view(v):
    v=deepcopy(v);v.pop('public_numeric',None)
    return abc.original_view(v)

def messages(v,frontend=False):
    if frontend:raise ValueError('direct action only')
    result=m.messages(v)
    if 'public_numeric' in v:result[0]['content']+='\n'+(m.ROOT/'contracts/multiservice_numeric_prompt_v1.txt').read_text().strip()
    return result
