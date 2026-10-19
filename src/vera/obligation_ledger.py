"""Ledger v2: deadline, recovery window, service priority and the administrator's release-time estimate are revisable obligations.

The frozen v1 ledger (multibusiness_revision) only lets delay_ms / min_mbps vary
and its planner never reads the deadline, recovery or any priority. This module
keeps the same transaction discipline (one field per message, scope check, explicit
admission) but widens what an administrator can revise, so that a contract-aware
planner (obligation_planner) can act on it. v1 modules are untouched.
"""
from copy import deepcopy
import math

from .multiservice_control import NAMES as _NAMES
SERVICES = tuple(_NAMES)   # from the topology contract; a 'backup' service carries the deadline
ALLOWED = ({(f'{s}.qos', f) for s in SERVICES for f in ('delay_ms', 'min_mbps')}
           | {('backup.deadline', 'total_bytes'), ('backup.deadline', 'deadline_tick'), ('recovery', 'max_intervals'), ('priority', 'order'),
              ('release_estimate', 'earliest_tick'), ('scheduled', 'add')})
TRIGGER_EVENTS = ('ended',)
MAX_RECOVERY_INTERVALS = 4


def validate(ledger):
    if set(ledger) != {'version', 'epoch_tick', 'obligations'} or type(ledger['version']) is not int or ledger['version'] < 1 or ledger['epoch_tick'] != 0:
        raise ValueError('ledger identity')
    o = ledger['obligations']
    required = {f'{s}.qos' for s in SERVICES} | {'backup.deadline', 'recovery'}
    if not required <= set(o) or not set(o) <= required | {'priority', 'release_estimate', 'scheduled'}:
        raise ValueError('missing or extra obligation')
    for s in SERVICES:
        q = o[f'{s}.qos']
        if set(q) != {'service', 'delay_ms', 'min_mbps', 'release_event'} or q['service'] != s or q['release_event'] not in ('ended', 'never'):
            raise ValueError('qos binding or condition')
        for k in ('delay_ms', 'min_mbps'):
            if type(q[k]) not in (int, float) or not math.isfinite(q[k]) or q[k] <= 0:
                raise ValueError('numeric qos')
    d = o['backup.deadline']
    if set(d) != {'service', 'total_bytes', 'deadline_tick'} or d['service'] != 'backup' or type(d['total_bytes']) is not int or d['total_bytes'] <= 0 or type(d['deadline_tick']) is not int or d['deadline_tick'] <= 0:
        raise ValueError('deadline')
    r = o['recovery']
    if set(r) != {'target', 'condition_events', 'max_intervals'} or r['target'] != 'all_caps_removed' or r['condition_events'] != ['ended'] \
            or type(r['max_intervals']) is not int or not 1 <= r['max_intervals'] <= MAX_RECOVERY_INTERVALS:
        raise ValueError('recovery definition')
    if 'priority' in o:
        p = o['priority']
        if set(p) != {'order'} or sorted(p['order']) != sorted(SERVICES):
            raise ValueError('priority must be a permutation of the services')
    if 'release_estimate' in o:
        e = o['release_estimate']
        if set(e) != {'earliest_tick'} or type(e['earliest_tick']) is not int or e['earliest_tick'] < 0:
            raise ValueError('release_estimate.earliest_tick must be a non-negative integer tick')
    if 'scheduled' in o:
        sc = o['scheduled']
        if set(sc) != {'items'} or type(sc['items']) is not list:
            raise ValueError('scheduled.items list')
        for item in sc['items']:
            validate_scheduled_item(item)


def validate_scheduled_item(item):
    if set(item) != {'trigger', 'request', 'status'} or item['status'] not in ('pending', 'applied', 'deferred_at_trigger', 'rejected_at_trigger'):
        raise ValueError('scheduled item shape')
    tr = item['trigger']
    if tr.get('kind') == 'time':
        if set(tr) != {'kind', 'at_tick'} or type(tr['at_tick']) is not int or tr['at_tick'] < 0:
            raise ValueError('time trigger')
    elif tr.get('kind') == 'event':
        if set(tr) != {'kind', 'event'} or tr['event'] not in TRIGGER_EVENTS:
            raise ValueError('event trigger')
    else:
        raise ValueError('trigger kind')
    rq = item['request']
    if set(rq) != {'obligation_id', 'field', 'value'} or (rq['obligation_id'], rq['field']) not in ALLOWED or rq['obligation_id'] == 'scheduled':
        raise ValueError('scheduled request scope')


def upgrade(v1):
    """A v1 ledger is a valid v2 ledger without a priority entry."""
    ledger = deepcopy(v1)
    validate(ledger)
    return ledger


def request_check(old, request):
    validate(old)
    if set(request) != {'base_version', 'obligation_id', 'field', 'value'} or request['base_version'] != old['version'] or type(request['base_version']) is not int:
        raise ValueError('stale version or request schema')
    if (request['obligation_id'], request['field']) not in ALLOWED:
        raise ValueError('unsupported revision scope')


def propose(old, request, arm='patch'):
    request_check(old, request)
    key, field = request['obligation_id'], request['field']
    new = deepcopy(old)
    if key in ('priority', 'release_estimate') and key not in new['obligations']:
        new['obligations'][key] = {}
    if key == 'scheduled':
        item = dict(deepcopy(request['value']), status='pending') if 'status' not in request['value'] else deepcopy(request['value'])
        validate_scheduled_item(item)
        new['obligations'].setdefault('scheduled', {'items': []})['items'].append(item)
    else:
        new['obligations'][key][field] = deepcopy(request['value'])
    new['version'] += 1
    verify_scope(old, new, request)
    return new


def verify_scope(old, new, request):
    request_check(old, request)
    validate(new)
    if new['version'] != old['version'] + 1 or new['epoch_tick'] != old['epoch_tick']:
        raise ValueError('version or epoch changed')
    key, field = request['obligation_id'], request['field']
    expected = deepcopy(old['obligations'])
    if key == 'scheduled':
        item = dict(deepcopy(request['value']), status='pending') if 'status' not in request['value'] else deepcopy(request['value'])
        expected.setdefault('scheduled', {'items': []})['items'].append(item)
    else:
        expected.setdefault(key, {})[field] = request['value']
    if new['obligations'] != expected:
        raise ValueError('unrelated obligation changed or amendment omitted')


def commit(old, new, request, accepted):
    verify_scope(old, new, request)
    if type(accepted) is not bool:
        raise ValueError('explicit admission decision required')
    return deepcopy(new if accepted else old)


def goals(ledger, events):
    active = [q for name, q in ledger['obligations'].items() if name.endswith('.qos') and not events.get(q['release_event'], False)]
    return {field: {q['service']: q[field] for q in active} for field in ('delay_ms', 'min_mbps')}


def weights(ledger):
    """Priority order -> rank weights 4/2/1 so the top service outweighs the other two together; no priority -> all 1."""
    order = ledger['obligations'].get('priority', {}).get('order')
    if not order:
        return {s: 1 for s in SERVICES}
    return {s: 2 ** (len(order) - 1 - i) for i, s in enumerate(order)}


def public_obligations(ledger):
    d = deepcopy(ledger['obligations']['backup.deadline'])
    d['id'] = 'backup.deadline'
    return dict(version=1, deadlines=[d], recovery=deepcopy(ledger['obligations']['recovery']))


def due_scheduled(ledger, tick, events):
    """Indices of pending scheduled items whose trigger has fired."""
    out = []
    for i, item in enumerate(ledger['obligations'].get('scheduled', {}).get('items', [])):
        if item['status'] != 'pending':
            continue
        tr = item['trigger']
        if (tr['kind'] == 'time' and tick >= tr['at_tick']) or (tr['kind'] == 'event' and events.get(tr['event'], False)):
            out.append(i)
    return out


def apply_scheduled(ledger, index, accepted):
    """Apply (or mark) one due scheduled item; an event-triggered QoS revision after 'ended' persists (release_event 'never')."""
    new = deepcopy(ledger)
    item = new['obligations']['scheduled']['items'][index]
    if accepted:
        rq = item['request']
        if rq['obligation_id'].endswith('.qos') and item['trigger']['kind'] == 'event':
            new['obligations'][rq['obligation_id']]['release_event'] = 'never'
        new['obligations'][rq['obligation_id']][rq['field']] = deepcopy(rq['value'])
        item['status'] = 'applied'
    else:
        item['status'] = 'deferred_at_trigger'
    new['version'] += 1
    validate(new)
    return new
