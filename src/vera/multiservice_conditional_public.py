"""Public received-byte feedback only: no goal program, labels or hidden stage."""
from copy import deepcopy
from . import multiservice_control as m,multiservice_progress_signals as progress

def view(request,s,h,epoch):
    value=m.view(request,s,h)
    value['public'].pop('default_goal')
    observed=progress.observe(request,s,epoch)
    byte_counts={k:v//8 for k,v in observed['additional_payload_bits'].items()}
    value['request_progress']=dict(origin_tick=observed['origin_tick'],sample_tick=observed['sample_tick'],
        additional_payload_bytes=byte_counts,additional_decimal_megabytes={k:v/1000000 for k,v in byte_counts.items()},
        semantics='Received payload since this administrator request; 1 decimal megabyte = 1000000 bytes. Not a private goal evaluation.')
    return deepcopy(value)
