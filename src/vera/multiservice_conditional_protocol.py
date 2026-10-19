"""Raw-language action protocol: no private condition, stage or goal compiler."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_conditional_public as public
from . import multiservice_sendcap_protocol as transport
from . import multiservice_ordered as ordered

HEADER='contracts/multiservice_conditional_action_v2.txt'
parse=transport.parse
grammar=transport.grammar
compile_tables=transport.compile_tables
calibration_gate=transport.calibration_gate

def view(request,s,h,epoch):
    payload=public.view(request,s,h,epoch)
    constraints=payload['public']
    constraints['uncapped_source_demand_mbps']=dict(zip(m.NAMES,constraints.pop('demand_mbps')))
    return dict(phase='action',payload=payload,network=deepcopy(s),confirmed_history=deepcopy(h))

def messages(v,frontend=False):
    if frontend:raise ValueError('raw-language direct action only')
    # Original four paid calibration calls remain the original intent task;
    # they are not conditional-task demonstrations or cached generated answers.
    if v['phase']=='intent':return transport.messages(v)
    if v['phase']!='action':raise ValueError('phase')
    if v['network']!=v['payload']['network'] or v['confirmed_history']!=v['payload']['confirmed_history']:
        raise ValueError('model and safety public inputs differ')
    return [dict(role='system',content=(m.ROOT/HEADER).read_text().strip()),
            dict(role='user',content=m.canonical_json(v['payload']))]

def select(packet,s,h):
    """Original hard safety only; no private semantic retention/correction."""
    action,interpretation=(ordered.interpret(packet,s) if packet is not None else
        (None,dict(reason='no_admitted_packet',selected_index=None,skipped_indices=[])))
    selected,selection=m.select(action,s,h)
    return selected,selection,dict(action=action,interpretation=interpretation,
        semantic_retention_applied=False,private_reference_used=False)
