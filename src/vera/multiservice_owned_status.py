"""Read-only actual predicates of the model's own declaration, no policy."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_quota as q

KEY = 'owned_goal_status'

def status(v):
    if v['phase'] != 'action':
        raise ValueError('owned status only after admitted intent')
    payload = v['payload']
    spec = q.parse(m.canonical_json(payload['model_declared_quota']))
    received = payload['request_progress']['additional_payload_bits'][spec['service']]
    if type(received) is not int or received < 0:
        raise ValueError('actual integer payload progress')
    remaining = max(0, q.quota_bits(spec) - received)
    urgent = remaining > 0
    if payload['remaining_quota_bits'] != remaining or payload['stage'] != ('urgent' if urgent else 'restore'):
        raise ValueError('owned quota phase/progress mismatch')
    s = v['network']
    rows = {row['name']: row for row in s['services']}
    if set(rows) != set(m.NAMES) or len(s['services']) != len(m.NAMES):
        raise ValueError('actual services')
    observed = rows[spec['service']]
    if not all(m.finite(observed[k]) for k in ('delay_ms','throughput_mbps')):
        raise ValueError('finite actual telemetry')
    if len(s['caps_bps']) != len(m.NAMES) or any(type(c) is not int or c < 0 for c in s['caps_bps']):
        raise ValueError('actual cap configuration')
    capped = [n for n,c in zip(m.NAMES,s['caps_bps']) if c]
    goal = dict(delay_ms={spec['service']:spec['delay_ms']},min_mbps={spec['service']:spec['min_mbps']})
    return dict(qos_required=urgent,
        qos_satisfied=(m.goal_score(goal,s)[0] == 0) if urgent else None,
        delay_margin_ms=round(spec['delay_ms']-observed['delay_ms'],6) if urgent else None,
        throughput_margin_mbps=round(observed['throughput_mbps']-spec['min_mbps'],6) if urgent else None,
        restoration_required=not urgent,capped_services=capped,all_caps_released=not capped)

def enrich(v):
    result = deepcopy(v)
    if v['phase'] == 'action':
        expected = status(v)
        if KEY in v['payload'] and v['payload'][KEY] != expected:
            raise ValueError('stale or forged owned status')
        result['payload'][KEY] = expected
    elif v['phase'] != 'intent':
        raise ValueError('phase')
    return result
