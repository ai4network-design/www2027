"""Lossless named observation and strictly causal last-action feedback."""
from copy import deepcopy
import json
from . import multiservice_control as m
CONTRACT=json.loads((m.ROOT/'contracts/multiservice_abc_20260912.json').read_text())
def named_config(s):
    return {n:dict(path=s['paths'][i],cap_bps=s['caps_bps'][i]) for i,n in enumerate(m.NAMES)}
def feedback(row):
    if row is None:return None
    g=row.get('generation')
    return dict(tick=row['tick'],proposed_operation=deepcopy(row['raw_proposal']),
        parse_error=g.get('parse_error') if g else None,
        rejection_reasons=deepcopy(row['selection'].get('proposal',{}).get('errors',[])),
        selection_source=row['selection']['source'],
        late_proposal_discarded=bool(row['selection'].get('late_proposal_discarded')),
        deployed_operation=deepcopy(row['selected']['action']),
        deployed_config=named_config(row['after']),actual_changed=row['edited'],
        realised_services=[{k:s[k] for k in ('name','throughput_mbps','delay_ms','loss')} for s in row['after']['services']])
def view(request,s,h,variant,previous=None):
    if variant not in ('A','B','C'):raise ValueError('frozen variant')
    v=deepcopy(m.view(request,s,h))
    if variant=='A':return v
    v['network']['service_config']=named_config(s)
    del v['network']['paths'];del v['network']['caps_bps']
    for key in ('rate_ticks','limited_ticks'):
        v['confirmed_history'][key]={n:deepcopy(h[key][i]) for i,n in enumerate(m.NAMES)}
    p=v['public']
    p['service_bounds']={n:{k:p[k][i] for k in ('demand_mbps','hard_min_mbps','hard_delay_ms')} for i,n in enumerate(m.NAMES)}
    for key in ('demand_mbps','hard_min_mbps','hard_delay_ms'):del p[key]
    p['path_min_mbps']={str(i+1):c for i,c in enumerate(p['path_min_mbps'])}
    if variant=='C':
        if previous is not None and (previous['tick']+1!=s['tick'] or previous['after']!=s or previous['history_after']!=h):
            raise ValueError('feedback must be immediately preceding own deployed state')
        v['previous_action_feedback']=feedback(previous)
    return v
def original_view(v):
    """Inverse B/C representation for tests/audit; no prediction or control."""
    v=deepcopy(v);v.pop('previous_action_feedback',None)
    if 'service_config' not in v['network']:return v
    configs=v['network'].pop('service_config')
    v['network']['paths']=[configs[n]['path'] for n in m.NAMES]
    v['network']['caps_bps']=[configs[n]['cap_bps'] for n in m.NAMES]
    for k in ('rate_ticks','limited_ticks'):v['confirmed_history'][k]=[v['confirmed_history'][k][n] for n in m.NAMES]
    p=v['public'];bounds=p.pop('service_bounds')
    for k in ('demand_mbps','hard_min_mbps','hard_delay_ms'):p[k]=[bounds[n][k] for n in m.NAMES]
    p['path_min_mbps']=[p['path_min_mbps'][str(i+1)] for i in range(3)]
    return v
