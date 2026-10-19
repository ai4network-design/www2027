"""Bounded public-model admission screen around unchanged retention and MPC.

The rollout is a witness under the existing approximate predictor with minimum
public capacities, not a proof for every future observation or model error.
No private event schedule, case ID, desired decision, or oracle is an input.
"""
from copy import deepcopy
import math
import time
from . import multiservice_control as m
from . import multiservice_cooperative_control as lower
from .obligation_retention import accept


def validate(obligations):
    if obligations.get('version') != 1:
        raise ValueError('unsupported obligation version')
    seen = set()
    for item in obligations['deadlines']:
        if item['id'] in seen or item['service'] not in m.NAMES:
            raise ValueError('duplicate obligation or invalid service')
        seen.add(item['id'])
        if type(item['deadline_tick']) is not int or item['deadline_tick'] < 0:
            raise ValueError('invalid deadline')
        if type(item['total_bytes']) not in (int, float) or not math.isfinite(item['total_bytes']) or item['total_bytes'] < 0:
            raise ValueError('invalid transfer amount')
    r = obligations['recovery']
    if type(r['max_intervals']) is not int or not 0 <= r['max_intervals'] <= 12:
        raise ValueError('invalid recovery budget')
    if r['target'] != 'all_caps_removed' or not r['condition_events']:
        raise ValueError('explicit recovery target and old condition required')



def observe_deadlines(obligations, state, epoch_bytes, outcomes):
    """Latch deadline evidence once; later delivery cannot erase an earlier miss."""
    result = deepcopy(outcomes)
    for item in obligations['deadlines']:
        if item['id'] not in result and state['tick'] >= item['deadline_tick']:
            received = state['services'][m.NAMES.index(item['service'])]['rx_bytes'] - epoch_bytes[m.NAMES.index(item['service'])]
            on_time_observation = state['tick'] == item['deadline_tick']
            result[item['id']] = dict(observed_tick=state['tick'],
                met=received >= item['total_bytes'] if on_time_observation else None,
                shortfall_bytes=max(0, item['total_bytes'] - received),
                evidence='deadline_observation' if on_time_observation else 'missing_deadline_observation')
    return result


def assess(state, history, spec, mode, obligations, epoch_bytes, *,
           horizon=8, max_ms=500, clock=time.perf_counter, deadline_outcomes=None):
    """Credit real received bytes, then screen one candidate-MPC continuation.

At each predicted successor, the actual restore controller's escape must fit
within the supplied time budget if the old release event happens there. Event
arrival time is intentionally not supplied. Deadline absence is not inferred.
"""
    started = clock()
    original = deepcopy((state, history, spec, obligations, epoch_bytes))
    validate(obligations)
    if mode not in ('protect', 'restore') or type(horizon) is not int or not 1 <= horizon <= 12:
        raise ValueError('invalid assessment mode or horizon')
    s, h = deepcopy(state), deepcopy(history)
    credit = [max(0, f['rx_bytes'] - epoch_bytes[i]) for i, f in enumerate(state['services'])]
    pending = {item['id']: deepcopy(item) for item in obligations['deadlines']}
    reasons, trace, deadlines, recovery = [], [], [], []

    def check_deadlines(tick):
        for key, item in list(pending.items()):
            i = m.NAMES.index(item['service'])
            if item['deadline_tick'] <= tick:
                shortfall = max(0, item['total_bytes'] - credit[i])
                deadlines.append(dict(id=key, tick=tick, deadline_tick=item['deadline_tick'],
                                      estimated_received_bytes=credit[i], shortfall_bytes=shortfall))
                if shortfall > 1e-6:
                    reasons.append(dict(code='deadline_not_certified', obligation=key,
                                        shortfall_bytes=shortfall))
                del pending[key]

    def check_recovery():
        cert = m.escape(s, h)
        steps = len(cert['actions']) if cert['ok'] else None
        recovery.append(dict(tick=s['tick'], steps=steps, certificate=cert))
        if steps is None or steps > obligations['recovery']['max_intervals']:
            reasons.append(dict(code='recovery_time_not_certified', tick=s['tick'], steps=steps,
                                budget=obligations['recovery']['max_intervals']))

    for key, evidence in (deadline_outcomes or {}).items():
        if key in pending:
            if evidence['met'] is not True:
                reasons.append(dict(code='existing_deadline_not_met', obligation=key, evidence=deepcopy(evidence)))
            del pending[key]
    check_deadlines(s['tick'])
    check_recovery()
    for _ in range(horizon):
        if (clock() - started) * 1000 >= max_ms:
            reasons.append(dict(code='assessment_budget_exhausted'))
            break
        # First action uses the real current observation. Later links use public
        # minima; queue observations are retained, not future native observations.
        selected, selection, detail = lower.decide(s, h, spec, mode, 'mpc')
        if detail.get('bounded', False):
            reasons.append(dict(code='controller_preview_truncated'))
            break
        predicted = m.predict(s, selected['target'], robust=True)
        rates = [f['throughput_mbps'] for f in predicted['services']]
        credit = [v + rate * 1e6 / 8 for v, rate in zip(credit, rates)]
        trace.append(dict(tick=s['tick'], before=deepcopy(s), history=deepcopy(h),
                          action=selected['action'], target=selected['target'],
                          selection_source=selection['source'], estimated_payload_mbps=rates))
        s, h = m.successor(s, selected['target'], h)
        s['links'] = [dict(link, capacity_mbps=cap) for link, cap in zip(s['links'], m.C['path_min_mbps'])]
        check_deadlines(s['tick'])
        check_recovery()
    for key, item in pending.items():
        if credit[m.NAMES.index(item['service'])] + 1e-6 < item['total_bytes']:
            reasons.append(dict(code='deadline_beyond_certified_horizon', obligation=key))
    elapsed = (clock() - started) * 1000
    if elapsed >= max_ms and not any(r['code'] == 'assessment_budget_exhausted' for r in reasons):
        reasons.append(dict(code='assessment_budget_exhausted'))
    assert original == (state, history, spec, obligations, epoch_bytes), 'admission mutated input'
    return dict(ok=not reasons, reasons=reasons, elapsed_ms=elapsed,
                model_scope='bounded approximate predictor, minimum capacities, no future events',
                horizon=horizon, deadlines=deadlines, recovery=recovery, trace=trace)


def admit(previous, proposal, state, history, spec, mode, obligations, epoch_bytes, **limits):
    """Atomic proposal admission: keep the old ledger if certification fails."""
    candidate = accept(previous, proposal, 'retain')
    if proposal is None or (previous is not None and proposal['business'] != previous['business']):
        return deepcopy(previous), dict(status='deferred', proposal=deepcopy(proposal),
                                        reasons=[dict(code='unsupported_or_missing_proposal')], check=None)
    check = assess(state, history, spec, mode, obligations, epoch_bytes, **limits)
    return (candidate if check['ok'] else deepcopy(previous)), dict(
        status='partial_applied' if check['ok'] else 'deferred', proposal=deepcopy(proposal),
        reasons=deepcopy(check['reasons']), check=check,
        complete_intent_accepted=False)
