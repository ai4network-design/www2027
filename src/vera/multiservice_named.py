"""Named goal transport and non-optimizing, unit-labelled public observation."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_phase as phase
from .multiservice_syntax import Builder,grammar as original_grammar,compile_tables as compile_original

view=phase.view
accept=phase.accept
current=phase.current
select=phase.select
packet_gate=phase.packet_gate
parse=phase.parse


def grammar(kind):
    if kind=='action':return original_grammar(kind)
    if kind!='intent':raise ValueError('phase')
    b=Builder()
    def mapping():
        # Canonical order, every subset including empty. No requested values.
        parts=[]
        for mask in range(8):
            names=[n for i,n in enumerate(m.NAMES) if mask & (1<<i)]
            seq=[b.literal('{')]
            for i,n in enumerate(names):
                seq += [b.literal((',' if i else '')+'"'+n+'":'),b.number()]
            seq += [b.literal('}')]
            parts.append(b.seq(*seq))
        return b.either(*parts)
    return b.dfa(b.seq(b.literal('{"delay_ms":'),mapping(),b.literal(',"min_mbps":'),mapping(),b.literal('}')))


def compile_tables(tokenizer,output_vocab,denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)


def action_input(v):
    """Remove duplicated raw packet counters, never insert target decisions."""
    n=v['network'];p=v['public_numeric']
    services={}
    for s in n['services']:
        name=s['name'];cfg=n['service_config'][name]
        services[name]=dict(path=cfg['path'],cap_mbps=None if cfg['cap_bps']==0 else cfg['cap_bps']/1e6,
            demand_mbps=s['demand_mbps'],offered_mbps=s['offered_mbps'],
            delivered_mbps=round(s['throughput_mbps'],6),mean_delay_ms=round(s['delay_ms'],6),loss=s['loss'])
    paths={name:[dict(path=row[0],max_offered_mbps=row[1],zero_queue_mean_delay_ms=row[2]) for row in rows]
           for name,rows in p['rows'].items()}
    feedback=deepcopy(v['previous_action_feedback'])
    if feedback is not None:
        # These are already represented by current actual services/config.
        feedback.pop('realised_services');feedback.pop('deployed_config')
    return dict(administrator_request=v['administrator_request'],model_declared_goal=deepcopy(v['model_declared_goal']),
        tick=n['tick'],physical_time=n['physical_time'],services=services,
        links=deepcopy(n['links']),hard_and_operational_constraints=deepcopy(v['public']),
        confirmed_history=deepcopy(v['confirmed_history']),
        hypothetical_paths_with_other_services_unchanged=paths,
        remaining=dict(route_edits=p['route_edits_left'],next_route_tick=p['next_route_tick'],cap_ticks=deepcopy(p['cap_ticks_left'])),
        delivered_payload_allowance_mbps=p['payload_allowance_mbps'],previous_action_feedback=feedback,
        last_proposed_path_numeric=deepcopy(v['last_proposed_path_numeric']))


def messages(v,frontend=False):
    if frontend:raise ValueError('no planner frontend')
    kind=v['phase']
    payload=dict(administrator_request=v['administrator_request']) if kind=='intent' else action_input(v)
    return [dict(role='system',content=(m.ROOT/('contracts/multiservice_named_'+kind+'_v1.txt')).read_text().strip()),
            dict(role='user',content=m.canonical_json(payload))]
