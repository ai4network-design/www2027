"""Conservative single-business transaction handling, independent of the parser/MPC.

U remains unresolved. With no old release predicate the known QoS is protected
provisionally; this is not acceptance of an unconditional or complete intent.
"""
from copy import deepcopy


def accept(previous, proposal, policy):
    if policy not in ('whole', 'retain'):
        raise ValueError('unknown policy')
    old = deepcopy(previous)
    if proposal is None:
        return old
    if old is not None and old['business'] != proposal['business']:
        return old  # single-business bridge cannot replace unrelated obligations
    if proposal['mode'] != 'U':
        return deepcopy(proposal)
    if policy == 'whole':
        return old
    result = deepcopy(proposal)
    result['unresolved_release'] = True
    result['complete_intent_accepted'] = False
    result['release_origin'] = 'unresolved'
    if old is not None and old['mode'] in ('E', 'N'):
        result['mode'] = old['mode']
        result['events'] = deepcopy(old['events'])
        result['release_origin'] = 'prior'
    return result


def phase(ledger, events):
    if ledger is None:
        return None
    if ledger['mode'] in ('N', 'U'):
        return 'protect'
    if ledger['mode'] != 'E' or not ledger['events']:
        raise ValueError('invalid release condition')
    return 'restore' if all(events[e] for e in ledger['events']) else 'protect'
