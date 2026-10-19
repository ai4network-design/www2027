"""Public necessary preconditions, not a goal optimizer or full safety certificate.

Canonical first operations: HOLD or a changed setting. Every physical millirate
value remains representable; redundant no-op aliases use HOLD, release uses null.
No administrator text, declared/private goal, future state or escape plan is read.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import json
from . import multiservice_control as m
from .multiservice_syntax import advance

PREFIX='{"steps":['


def public_projection(s,h):
    return (dict(tick=s['tick'],paths=list(s['paths']),caps_bps=list(s['caps_bps']),
        links=[{k:r[k] for k in ('capacity_mbps','queued_bytes')} for r in s['links']],
        services=[{k:r[k] for k in ('name','throughput_mbps','delay_ms','loss')} for r in s['services']]),
        {k:deepcopy(h[k]) for k in ('edit_ticks','route_ticks','rate_ticks','limited_ticks')})


def from_view(v):
    n=deepcopy(v['network']);h=deepcopy(v['confirmed_history'])
    if 'service_config' in n:
        c=n.pop('service_config');n['paths']=[c[k]['path'] for k in m.NAMES];n['caps_bps']=[c[k]['cap_bps'] for k in m.NAMES]
        for k in ('rate_ticks','limited_ticks'):h[k]=[h[k][name] for name in m.NAMES]
    return public_projection(n,h)


def text(a):
    if 'hold' in a:return '{"hold":true}'
    head='{"service":'+json.dumps(a['service'])
    if 'path' in a:return head+',"path":'+str(a['path'])+'}'
    value=a['rate_mbps']
    number='null' if value is None else format(Decimal(str(value)).normalize(),'f')
    return head+',"rate_mbps":'+number+'}'


def necessary(a,s,h):
    """Reference first-operation predicate. Deliberately does not call escape()."""
    a=m.parse_action(m.canonical_json(a));t=m.target(a,s)
    if 'hold' in a:return dict(ok=True,errors=[],hold_requires_final_verifier=True)
    if t==m.config(s):return dict(ok=False,errors=['noop_alias_use_hold'])
    p=m.predict(s,t);emergency=not m.sla(s) and m.sla(p) and 'rate_mbps' in a
    _,errors=m.operational(a,s,h,emergency)
    if not m.sla(p):errors.append('current_prediction_sla')
    return dict(ok=not errors,errors=errors,emergency=bool(emergency))


def validate_state(s):
    # The interval proof relies on the existing public worst-case bounds. Reject
    # unsupported telemetry instead of silently discarding potentially safe rates.
    if len(s['paths'])!=3 or len(s['caps_bps'])!=3 or len(s['links'])!=3:raise ValueError('state shape')
    for i in range(3):
        if s['paths'][i] not in (1,2,3):raise ValueError('path identity')
        cap=s['caps_bps'][i]
        if type(cap) is not int or cap<0 or cap%1000 or cap>m.DEMAND[i]*1e6:raise ValueError('non-grid cap telemetry')
        link=s['links'][i]
        if not m.finite(link['capacity_mbps']) or link['capacity_mbps']<m.C['path_min_mbps'][i]:raise ValueError('capacity below guaranteed envelope')
        if not m.finite(link['queued_bytes']) or link['queued_bytes']<0:raise ValueError('invalid queue')


def build(s,h):
    """Exact physical grid domain under the public envelope, using interval bounds.

Within the robust capacity bound there is no predicted loss: payload throughput
is rate minus .01 Mbps; delay is rate-independent. For changed positive caps,
edit/dwell/reserve flags are identical throughout each interval. We check both
endpoints against the original predicate, and separately handle null release.
Full escape certification and own-goal retention are NOT moved into this code.
"""
    validate_state(s);ops=[{'hold':True}];ranges={};rejected=Counter();checks=0
    for i,name in enumerate(m.NAMES):
        for a in [dict(service=name,path=p) for p in (1,2,3)]+[dict(service=name,rate_mbps=None)]:
            verdict=necessary(a,s,h);checks+=1
            if verdict['ok']:ops.append(a)
            else:rejected.update(verdict['errors'])
        p=s['paths'][i]-1
        others=sum((s['caps_bps'][j]//1000 or int(m.DEMAND[j]*1000)) for j in range(3) if j!=i and s['paths'][j]==p+1)
        capacity_k=int((Decimal(str(m.C['path_min_mbps'][p]))*40000/41).to_integral_value(rounding=ROUND_FLOOR))-others
        lo=int((Decimal(str(m.FLOOR[i]))*1000).to_integral_value(rounding=ROUND_CEILING))+10
        hi=min(int(m.DEMAND[i]*1000)-1,capacity_k)
        current=s['caps_bps'][i]//1000
        intervals=[] if lo>hi else [(lo,hi)]
        if lo<=current<=hi:
            intervals=[(a,b) for a,b in [(lo,current-1),(current+1,hi)] if a<=b]
        accepted=[]
        for low,high in intervals:
            v=necessary(dict(service=name,rate_mbps=low/1000),s,h)
            w=necessary(dict(service=name,rate_mbps=high/1000),s,h);checks+=2
            if v['ok']!=w['ok']:raise ValueError('precondition interval proof violated')
            if v['ok']:
                accepted.append([low,high])
                ops.extend(dict(service=name,rate_mbps=k/1000) for k in range(low,high+1))
            else:rejected.update(v['errors'])
        ranges[name]=accepted
    strings=sorted({text(a) for a in ops})
    return dict(version='public-first-precondition-v1',operations=strings,
        domain_sha256=m.digest(strings),rate_intervals_kbps=ranges,
        reference_predicate_checks=checks,rejected_boundary_reasons=dict(rejected),
        goal_inputs=0,optimizer_calls=0,escape_calls=0,hold_preserved=True,
        aliases='canonical millirates; all no-op settings use HOLD; full demand uses null',
        certificate=False)


class PrefixGate:
    """Intersect existing ordered syntax with a canonical first-operation trie.

    Once the first operation ends, the original syntax alone governs the optional
    second operation: it is merely a declared future step and is NOT deployed.
    The unchanged executor still validates the full packet and final safety.
    """
    def __init__(self,domain):
        self.nodes=[{}];self.ends=set()
        for operation in domain['operations']:
            state=0
            for char in operation:
                if char not in self.nodes[state]:
                    self.nodes[state][char]=len(self.nodes);self.nodes.append({})
                state=self.nodes[state][char]
            self.ends.add(state)
    def viable(self,prefix):
        if len(prefix)<=len(PREFIX):return PREFIX.startswith(prefix)
        if not prefix.startswith(PREFIX):return False
        state=0
        for char in prefix[len(PREFIX):]:
            if state in self.ends:return True
            state=self.nodes[state].get(char)
            if state is None:return False
        return True
    def filter_ids(self,prefix,ids,token_text):
        return [i for i in ids if self.viable(prefix+token_text[str(i)])]


class Processor:
    """Existing syntax intersection plus public first-operation prefix viability."""
    def __init__(self,base):
        self.base=base;self.gate=None;self.rows=[];self.cache={}
    def bind(self,domain):
        self.gate=None if domain is None else PrefixGate(domain)
        self.cache={};self.rows=[]
    def begin(self,phase,input_size):self.base.begin(phase,input_size);self.rows=[]
    def __call__(self,input_ids,scores):
        b=self.base
        if b.phase=='intent':return b(input_ids,scores)
        if self.gate is None:raise ValueError('action domain not bound')
        prefix=b.tokenizer.decode(input_ids[0,b.input_size:].tolist(),skip_special_tokens=False,clean_up_tokenization_spaces=False)
        d=b.compiled['tables'][b.phase];state=advance(d,0,prefix)
        if state is None or d['accepting'][state]:raise ValueError('invalid syntax prefix')
        original=d['allowed_ids'][state];allowed=self.gate.filter_ids(prefix,original,b.compiled['token_text'])
        if not allowed:raise ValueError('empty syntax/precondition intersection')
        key=tuple(allowed)
        if allowed==original:indices=b.indices[b.phase][state]
        else:
            if key not in self.cache:self.cache[key]=b.torch.tensor(allowed,dtype=b.torch.long,device=scores.device)
            indices=self.cache[key]
        self.rows.append(dict(prefix=prefix,syntax_allowed=original,allowed=allowed,
            removed=[i for i in original if i not in set(allowed)]))
        result=b.torch.full_like(scores,float('-inf'))
        return result.index_copy(-1,indices,scores.index_select(-1,indices))
