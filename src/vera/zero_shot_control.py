"""Zero-shot parameter protocol and unfitted safety checks; no model/teacher imports."""
from collections import deque
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'contracts/zero_shot_parameter_control_v1.json'
CONTRACT_SHA = 'e74e160fd3cc4bbbadde8e47866069c8f031e1fd778ae622f7ff03ebae39ab7c'
PROMPT = ROOT / 'contracts/zero_shot_system_prompt_v1.txt'
PROMPT_SHA = '7e1611334f97474493d7e1f91410c01ff27599f49903268ce9412ef95d8ab5ca'
OBS_KEYS = ('fg_throughput','fg_delay','bg_throughput','bg_delay','loss','jitter','queue','current_path','throttle_state')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def contract():
    if sha(CONTRACT) != CONTRACT_SHA or sha(PROMPT) != PROMPT_SHA:
        raise ValueError('frozen zero-shot protocol/prompt changed')
    return json.loads(CONTRACT.read_text())

def finite(x, lo=0, hi=math.inf):
    return type(x) in (int,float) and math.isfinite(x) and lo <= x <= hi

def strict_json(text):
    def pairs(items):
        value = {}
        for k,v in items:
            if k in value: raise ValueError('duplicate JSON key')
            value[k] = v
        return value
    def bad(_): raise ValueError('nonfinite JSON')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad)

def parse(text):
    if not isinstance(text,str) or len(text.encode()) > 512: raise ValueError('bounded text required')
    action = strict_json(text)
    if type(action) is not dict or len(action) != 1: raise ValueError('one operation/key required')
    key, value = next(iter(action.items()))
    if key == 'hold':
        if value is not True: raise ValueError('hold must be true')
    elif key == 'path':
        if type(value) is not int or value not in (1,2,3): raise ValueError('available path id')
    elif key == 'rate_mbps':
        if value is not None:
            if not finite(value,1.05,5.7): raise ValueError('rate cap domain')
            raw = Decimal(str(value))*1000000
            if raw != raw.to_integral_value() or int(raw)%1000: raise ValueError('rate resolution 1000bps')
    else: raise ValueError('unknown operation')
    return action

def target(action, state):
    action = parse(json.dumps(action,allow_nan=False))
    path,cap = state['current_path'],state['bg_cap_bps']
    if 'path' in action: path = action['path']
    if 'rate_mbps' in action:
        value = action['rate_mbps']
        cap = 0 if value is None else int(Decimal(str(value))*1000000)
        if cap == 5700000: cap = 0
    return dict(current_path=path,bg_cap_bps=cap)

def canonical(action,state):
    after=target(action,state)
    return {'hold':True} if all(after[k]==state[k] for k in after) else action

def initial_history():
    return dict(edit_ticks=[],route_ticks=[],rate_ticks=[],limited_ticks=0)

def advance(history,before,after,tick):
    h=deepcopy(history)
    route=before['current_path'] != after['current_path']
    rate=before['bg_cap_bps'] != after['bg_cap_bps']
    if route and rate: raise ValueError('one edit per tick')
    if route or rate: h['edit_ticks'].append(tick)
    if route: h['route_ticks'].append(tick)
    if rate: h['rate_ticks'].append(tick)
    h['limited_ticks']=h['limited_ticks']+1 if after['bg_cap_bps'] else 0
    return h

def validate_snapshot(state,obs,health,outcomes,tick):
    expected={'schema','tick','physical_tick','current_path','throttle_state','fg_offered_bps',
              'bg_offered_bps','bg_cap_bps','bg_demand_bps','paths'}
    if type(state) is not dict or set(state)!=expected or state['schema']!='vera.parameter_network_state/1':
        raise ValueError('complete parameter state required')
    if state['tick']!=tick or state['physical_tick']!=tick+1: raise ValueError('state clock')
    if type(state['current_path']) is not int or state['current_path'] not in (1,2,3): raise ValueError('state path')
    cap=state['bg_cap_bps']
    if type(cap) is not int or not (cap==0 or 1050000<=cap<5700000 and cap%1000==0): raise ValueError('state cap')
    if state['throttle_state']!=int(bool(cap)): raise ValueError('state limited bit')
    if state['bg_demand_bps'] not in (3000000,5700000): raise ValueError('actual demand envelope')
    if state['bg_offered_bps']!=min(state['bg_demand_bps'],cap or 5700000): raise ValueError('cap not applied')
    if not finite(state['fg_offered_bps'],2500000,3000000): raise ValueError('foreground envelope')
    if set(obs)!=set(OBS_KEYS) or any(not finite(x) for x in obs.values()): raise ValueError('complete finite telemetry')
    if obs['loss']>1 or any(obs[k]!=state[k] for k in ('current_path','throttle_state')): raise ValueError('monitor config')
    if len(state['paths'])!=3: raise ValueError('three actual paths')
    for i,p in enumerate(state['paths']):
        if p['id']!=i+1 or not finite(p['rate_bps'],[6000000,10000000,10000000][i],10000000):
            raise ValueError('path service envelope')
        if p['delay_ms']!=[10,24,40][i] or not finite(p['packet_error_rate'],0,.08): raise ValueError('path physical attributes')
        if any(not finite(p[k]) for k in ('qdisc_bytes','device_queue_bytes')): raise ValueError('queue bytes')
    if health['schema']!='vera.path_health/2' or health['tick']!=tick or health['physical_tick']!=tick+1:
        raise ValueError('health clock')
    since=health['b_healthy_since']
    if type(since) is not int or since < -1 or since>tick+1 or health['b_spike'] and since!=-1:
        raise ValueError('causal recovery observation')
    if bool(state['paths'][1]['packet_error_rate']) != health['b_spike']: raise ValueError('health mismatch')
    if outcomes['tick']!=tick or outcomes['physical_tick']!=tick+1: raise ValueError('outcomes clock')
    if outcomes['accounting_errors'] or outcomes['fg_tx']!=sum(outcomes[k] for k in ('fg_rx','fg_dropped','fg_pending')):
        raise ValueError('packet accounting')

def predict(state,configuration):
    p=state['paths'][configuration['current_path']-1]
    fg=state['fg_offered_bps'];bg=min(state['bg_demand_bps'],configuration['bg_cap_bps'] or 5700000)
    offered=(fg+bg)*1.025;buffer_bits=100*1230*8
    queued=8*(p['qdisc_bytes']+p['device_queue_bytes'])
    excess=max(0,queued+offered-p['rate_bps'])
    overflow=min(1,max(0,excess-buffer_bits)/offered)
    loss=1-(1-p['packet_error_rate'])*(1-overflow)
    qdelay=max(queued,min(buffer_bits,excess))/p['rate_bps']*1000
    delay=1+p['delay_ms']+9840/p['rate_bps']*1000+9840/1e8*1000+qdelay
    return dict(fg_delay=delay,fg_throughput=fg/1e6*(1-loss),bg_throughput=bg/1e6*(1-loss),
                loss=loss,jitter=qdelay,current_payload_bg_bps=bg,
                approximation='unfitted current fluid queues; not packet safety theorem')

def sla_margins(q):
    return dict(delay_ms=50-q['fg_delay'],loss=.01-q['loss'],throughput_mbps=q['fg_throughput']-2.5)

def sla_ok(q): return all(x>=0 for x in sla_margins(q).values())

def service(configuration,health,tick):
    path,cap=configuration['current_path'],configuration['bg_cap_bps']
    end=None if health['b_healthy_since']==-1 else health['b_healthy_since']+8
    lease=path!=2 or not health['b_spike'] and end is not None and tick<end
    margin=[6000000,10000000,10000000][path-1]-(3000000+min(5700000,cap or 5700000))*1.025
    return dict(ok=bool(margin>=0 and lease),margin_bps=margin,lease_ok=bool(lease),lease_end_tick=end)

def operational(action,state,history,health,tick,*,current_qos=None,new_qos=None):
    after=target(action,state);violations=[]
    route=after['current_path']!=state['current_path'];rate=after['bg_cap_bps']!=state['bg_cap_bps']
    recent=lambda name:sum(t>tick-60 for t in history[name])
    if route:
        if 20<=tick<30: violations.append('protected_route')
        if history['route_ticks'] and tick-history['route_ticks'][-1]<5: violations.append('route_cooldown')
        if recent('route_ticks')+1>2: violations.append('route_budget')
        if current_qos is not None and sla_ok(current_qos):
            unbounded=dict(current_path=state['current_path'],bg_cap_bps=0)
            escape=after['current_path']==3 and (state['current_path']==2 or not service(unbounded,health,tick)['ok'])
            if not escape: violations.append('preserve_route')
    exemption=False
    if rate and history['rate_ticks'] and tick-history['rate_ticks'][-1]<2:
        exemption=(current_qos is not None and new_qos is not None and not sla_ok(current_qos) and sla_ok(new_qos))
        if not exemption: violations.append('rate_dwell')
    if (route or rate) and recent('edit_ticks')+1+int(bool(after['bg_cap_bps']))>6:
        violations.append('edit_budget_with_release_reserve')
    if after['bg_cap_bps'] and history['limited_ticks']+1>8: violations.append('rate_max_dwell')
    return after,violations,dict(used=exemption,current_margins=sla_margins(current_qos) if current_qos else None,
                                proposed_margins=sla_margins(new_qos) if new_qos else None)

def residence(configuration,history,health,tick):
    """Independent finite continuous-cap witness; numeric cap retained until released."""
    queue=deque([(configuration,history,[])])
    seen=set();edges=0
    while queue:
        state,h,rows=queue.popleft()
        if state==dict(current_path=3,bg_cap_bps=0):
            return dict(ok=True,witness=rows,terminal=state,search_edges=edges,conditional=True)
        if len(rows)>=16: continue
        at=tick+len(rows)
        key=(at,state['current_path'],state['bg_cap_bps'],tuple(h['route_ticks']),tuple(h['rate_ticks']),tuple(h['edit_ticks']),h['limited_ticks'])
        if key in seen: continue
        seen.add(key)
        actions=[]
        if state['current_path']!=3: actions.append({'path':3})
        if state['bg_cap_bps']: actions.append({'rate_mbps':None})
        if state['current_path']==1 and not service(state,health,at)['ok']:
            actions.append({'rate_mbps':1.05})
        actions.append({'hold':True})
        for action in actions:
            after,errors,exemption=operational(action,state,h,health,at)
            proof=service(after,health,at);edges+=1
            if errors or not proof['ok']: continue
            nxt=advance(h,state,after,at)
            queue.append((after,nxt,rows+[dict(tick=at,action=action,state=after,history=nxt,service=proof)]))
    return dict(ok=False,witness=[],terminal=None,search_edges=edges,conditional=True)

def delivery_ceiling(state,configuration,outcomes):
    path=configuration['current_path'];fg=state['fg_offered_bps']
    latency=1000000+[10,24,40][path-1]*1000000+9840*1000000000//100000000+9840*1000000000//10000000
    spacing=9600*1000000000//fg
    fresh=max(0,(1000000000-latency)//spacing+1)
    upper=outcomes['fg_pending']+fresh
    return dict(upper_packets=upper,required_packets=math.ceil(2500000/(1228*8)),
                possible=upper>=math.ceil(2500000/(1228*8)),is_service_guarantee=False)

def verify(action,state,history,health,outcomes):
    tick=state['tick'];action=canonical(action,state);after=target(action,state)
    current=predict(state,dict(current_path=state['current_path'],bg_cap_bps=state['bg_cap_bps']))
    proposed=predict(state,after)
    after,errors,exemption=operational(action,state,history,health,tick,current_qos=current,new_qos=proposed)
    if proposed['loss']>.01: errors.append('current_predicted_loss')
    current_support=service(after,health,tick)
    if not current_support['lease_ok']: errors.append('B_lease')
    next_history=advance(history,state,after,tick)
    witness=residence(after,next_history,health,tick+1) if not errors else dict(ok=False,witness=[])
    if not witness['ok']: errors.append('no_post_deploy_escape')
    ceiling=delivery_ceiling(state,after,outcomes)
    full=not errors and sla_ok(proposed) and ceiling['possible']
    return dict(action=action,target=after,hard_ok=not errors,full_ok=full,violations=errors,
                prediction=proposed,sla_margins=sla_margins(proposed),packet_ceiling=ceiling,
                current_service=current_support,residence=witness,emergency_exemption=exemption)

def select(action,state,history,health,outcomes):
    original=None if action is None else verify(action,state,history,health,outcomes)
    if original is not None and original['full_ok']:
        return original,dict(source='model',proposal_verdict=original,repair_checks=[])
    checks=[]
    for a in ({'hold':True},{'path':3},{'rate_mbps':None}):
        v=verify(a,state,history,health,outcomes);checks.append(v)
        if v['full_ok']:
            return v,dict(source='safety_repair',proposal_verdict=original,repair_checks=checks)
    hard=next((v for v in checks if v['hard_ok']),None)
    if hard is None: raise ValueError('no certified deployable recovery; do not silently hold')
    return hard,dict(source='hard_safe_recovery_sla_unmet',proposal_verdict=original,repair_checks=checks)

def check_ack(before,after,ack,next_state,tick,history):
    if ack['schema']!='vera.parameter_ack/1' or ack['tick']!=tick: raise ValueError('ACK identity')
    if any(ack[k]!=after[k] or next_state[k]!=after[k] for k in after): raise ValueError('parameter not actuated')
    expected=min(before['bg_demand_bps'],after['bg_cap_bps'] or 5700000)
    if ack['bg_applied_bps']!=expected: raise ValueError('actual application rate mismatch')
    h=advance(history,before,after,tick)
    if ack['route_changes']!=len(h['route_ticks']) or ack['rate_changes']!=len(h['rate_ticks']):
        raise ValueError('native/confirmed edit count mismatch')
    return h
