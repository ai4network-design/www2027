"""Read-only proposed progress telemetry; not a job model or deployed controller."""
from . import multiservice_control as m

def counters(s):
    rows={r['name']:r for r in s['services']}
    if set(rows)!=set(m.NAMES) or len(s['services'])!=3:raise ValueError('service identities')
    result={}
    for name in m.NAMES:
        value=rows[name]['rx_bytes']
        if type(value) is not int or value<0:raise ValueError('integer received payload counter')
        result[name]=value
    return result

def epoch(request,s):
    if type(request) is not str or type(s['tick']) is not int or s['tick']<0:raise ValueError('epoch identity')
    return dict(request_sha256=m.digest(request),origin_tick=s['tick'],received_bytes=counters(s))

def observe(request,s,origin):
    if type(s['tick']) is not int or type(origin['origin_tick']) is not int or origin['origin_tick']<0:
        raise ValueError('integer epoch ticks')
    if origin['request_sha256']!=m.digest(request) or s['tick']<origin['origin_tick']:
        raise ValueError('explicit new request epoch required')
    now=counters(s);old=origin['received_bytes']
    if set(old)!=set(m.NAMES) or any(type(v) is not int or v<0 for v in old.values()):raise ValueError('origin counters')
    if any(now[n]<old[n] for n in m.NAMES):raise ValueError('counter reset or invalid origin')
    return dict(origin_tick=origin['origin_tick'],sample_tick=s['tick'],
        additional_payload_bits={n:(now[n]-old[n])*8 for n in m.NAMES},
        semantics='received UDP payload since request epoch; not remaining job bytes or completion',
        source='native cumulative rx_bytes; no goal, threshold or hidden stage input')
