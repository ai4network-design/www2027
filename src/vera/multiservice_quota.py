"""Parameter semantics and read-only model-owned dynamic goal; not a controller."""
from copy import deepcopy
from decimal import Decimal
from . import multiservice_control as m
from . import multiservice_progress_signals as progress

FIELDS={'service','quota_mbit','delay_ms','min_mbps'}

def parse(raw):
    spec=m.decode(raw)
    if set(spec)!=FIELDS or spec['service'] not in m.NAMES:raise ValueError('complete quota intent fields')
    if any(not m.finite(spec[k]) for k in ('quota_mbit','delay_ms','min_mbps')):raise ValueError('finite numerical intent')
    if not 0<spec['quota_mbit']<=1000000 or not 0<spec['delay_ms']<=1000:
        raise ValueError('positive bounded quota/delay')
    if not 0<spec['min_mbps']<=m.DEMAND[m.NAMES.index(spec['service'])]:raise ValueError('throughput intent domain')
    bits=Decimal(str(spec['quota_mbit']))*1000000
    if bits!=bits.to_integral_value():raise ValueError('integer quota bits required; no rounding')
    return spec

def quota_bits(spec):return int(Decimal(str(parse(m.canonical_json(spec))['quota_mbit']))*1000000)

def establish(request,spec,epoch,tick,call,*,release_expression=None):
    spec=parse(m.canonical_json(spec))
    if epoch['request_sha256']!=m.digest(request) or tick<epoch['origin_tick']:raise ValueError('request epoch')
    result=dict(request_sha256=m.digest(request),spec=deepcopy(spec),epoch=deepcopy(epoch),origin_tick=tick,origin_call=call)
    if release_expression is not None:
        from .multiservice_conditional_quota import validate_release
        validate_release(release_expression)
        result['release_expression']=release_expression
    return result

def current(request,s,ledger):
    if ledger is None or ledger['request_sha256']!=m.digest(request):return None
    spec=parse(m.canonical_json(ledger['spec']));obs=progress.observe(request,s,ledger['epoch'])
    received=obs['additional_payload_bits'][spec['service']];urgent=received<quota_bits(spec)
    extra={}
    if 'release_expression' in ledger:
        from .multiservice_condition_program import evaluate_expression
        counts={n:v//8 for n,v in obs['additional_payload_bits'].items()}
        released=evaluate_expression(ledger['release_expression'],counts)
        extra=dict(quota_complete=not urgent,release_condition_met=released,
                   release_expression=ledger['release_expression'])
        urgent=not released
    return dict(spec=deepcopy(spec),progress=obs,stage='urgent' if urgent else 'restore',
        remaining_quota_bits=max(0,quota_bits(spec)-received),require_all_caps_released=not urgent,
        goal={'delay_ms':{spec['service']:spec['delay_ms']},'min_mbps':{spec['service']:spec['min_mbps']}} if urgent else
             {'delay_ms':{},'min_mbps':{}},
        origin_tick=ledger['origin_tick'],origin_call=ledger['origin_call'],**extra)
