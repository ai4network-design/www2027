"""Typed single-field transactions; no language parser or controller changes.

The full-rebuild arm is a correct deterministic strong baseline. Both arms are
subject to an independent scope check before the shared feasibility screen.
"""
from copy import deepcopy
import math

SERVICES = ('query', 'video', 'backup')
ALLOWED = {(f'{s}.qos', f) for s in SERVICES for f in ('delay_ms', 'min_mbps')} | {('backup.deadline', 'deadline_tick')}

def validate(ledger):
    if set(ledger) != {'version', 'epoch_tick', 'obligations'} or type(ledger['version']) is not int or ledger['version'] < 1 or ledger['epoch_tick'] != 0:
        raise ValueError('ledger identity')
    o = ledger['obligations']
    if set(o) != {f'{s}.qos' for s in SERVICES} | {'backup.deadline', 'recovery'}:
        raise ValueError('missing or extra obligation')
    for s in SERVICES:
        q = o[f'{s}.qos']
        if set(q) != {'service', 'delay_ms', 'min_mbps', 'release_event'} or q['service'] != s or q['release_event'] != 'ended':
            raise ValueError('qos binding or condition')
        for k in ('delay_ms', 'min_mbps'):
            if type(q[k]) not in (int, float) or not math.isfinite(q[k]) or q[k] <= 0:
                raise ValueError('numeric qos')
    d = o['backup.deadline']
    if set(d) != {'service', 'total_bytes', 'deadline_tick'} or d['service'] != 'backup' or type(d['total_bytes']) is not int or d['total_bytes'] <= 0 or type(d['deadline_tick']) is not int or d['deadline_tick'] <= 0:
        raise ValueError('deadline')
    r = o['recovery']
    if r != {'target': 'all_caps_removed', 'condition_events': ['ended'], 'max_intervals': 2}:
        raise ValueError('recovery definition outside frozen scope')

def request_check(old, request):
    validate(old)
    if set(request) != {'base_version', 'obligation_id', 'field', 'value'} or request['base_version'] != old['version'] or type(request['base_version']) is not int:
        raise ValueError('stale version or request schema')
    if (request['obligation_id'], request['field']) not in ALLOWED:
        raise ValueError('unsupported revision scope')

def propose(old, request, arm):
    request_check(old, request)
    key, field = request['obligation_id'], request['field']
    if arm == 'patch':
        new = deepcopy(old)
        new['obligations'][key][field] = deepcopy(request['value'])
        new['version'] += 1
    elif arm == 'full_rebuild':
        # Serialize every field from the previous snapshot plus the amendment.
        obligations = {name: {k: deepcopy(request['value'] if (name, k) == (key, field) else v)
                             for k, v in fields.items()} for name, fields in old['obligations'].items()}
        new = dict(version=old['version'] + 1, epoch_tick=old['epoch_tick'], obligations=obligations)
    else:
        raise ValueError('unknown arm')
    verify_scope(old, new, request)
    return new

def verify_scope(old, new, request):
    request_check(old, request)
    validate(new)
    if new['version'] != old['version'] + 1 or new['epoch_tick'] != old['epoch_tick']:
        raise ValueError('version or epoch changed')
    key, field = request['obligation_id'], request['field']
    for name, fields in old['obligations'].items():
        for k, value in fields.items():
            expected = request['value'] if (name, k) == (key, field) else value
            if new['obligations'][name][k] != expected:
                raise ValueError('unrelated obligation changed or amendment omitted')

def commit(old, new, request, accepted):
    verify_scope(old, new, request)
    if type(accepted) is not bool:
        raise ValueError('explicit admission decision required')
    return deepcopy(new if accepted else old)

def goals(ledger, events):
    active = [q for name, q in ledger['obligations'].items() if name.endswith('.qos') and not events[q['release_event']]]
    return {field: {q['service']: q[field] for q in active} for field in ('delay_ms', 'min_mbps')}

def public_obligations(ledger):
    d = deepcopy(ledger['obligations']['backup.deadline'])
    d['id'] = 'backup.deadline'
    return dict(version=1, deadlines=[d], recovery=deepcopy(ledger['obligations']['recovery']))
