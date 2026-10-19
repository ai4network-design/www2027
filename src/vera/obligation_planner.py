"""Obligation-aware planner: deadline, recovery window and priority enter the objective.

Same candidate enumeration and the same safety layer (verify / select / escape) as
the frozen planner in multiservice_control; only the ranking of certified
candidates changes:

* priority   - per-service QoS violations and gaps are weighted by the ledger's
               priority order (4/2/1), so protecting the top service outweighs
               the other two together;
* deadline   - the backup deadline is turned into a required rate
               (remaining bytes / remaining ticks); a candidate whose predicted
               backup throughput falls below it is a violation, and the exact
               required rate is added as a cap candidate so backup can be
               throttled only as far as the deadline permits;
* recovery   - caps that could not all be released inside recovery.max_intervals
               (one field edit per tick) are a violation, so a short recovery
               window limits how many services may be capped at once.

CPU only, deterministic, no language. The frozen planner is left unchanged and
remains the strong baseline.
"""
import math

from . import multiservice_control as m
from .obligation_ledger import goals, weights, SERVICES

BYTES_PER_MBIT = 1e6 / 8


def required_backup_mbps(ledger, s, epoch):
    d = ledger['obligations']['backup.deadline']
    remaining_ticks = d['deadline_tick'] - s['tick']
    if remaining_ticks <= 0:
        return 0.0
    received = s['services'][SERVICES.index('backup')]['rx_bytes'] - epoch[SERVICES.index('backup')]
    remaining = max(0, d['total_bytes'] - received)
    return remaining / BYTES_PER_MBIT / remaining_ticks


def score(ledger, goal, w, obs, target, required_mbps, events):
    """(weighted violations, weighted gap). Lower is better."""
    rows = {r['name']: r for r in obs['services']}
    violations = 0.0
    gap = 0.0
    for n, v in goal['delay_ms'].items():
        g = max(0.0, rows[n]['delay_ms'] / v - 1)
        violations += w[n] * (g > 1e-9)
        gap += w[n] * g
    for n, v in goal['min_mbps'].items():
        g = max(0.0, 1 - rows[n]['throughput_mbps'] / v)
        violations += w[n] * (g > 1e-9)
        gap += w[n] * g
    top = max(w.values())
    if required_mbps > 0:
        g = max(0.0, 1 - rows['backup']['throughput_mbps'] / required_mbps)
        violations += top * (g > 1e-9)
        gap += top * g
    caps = sum(1 for c in target['caps_bps'] if c)
    limit = ledger['obligations']['recovery']['max_intervals']
    if not events['ended'] and caps > limit:
        violations += top * (caps - limit)
    return violations, gap


def candidates(s, goal, required_mbps):
    base = m.candidates(s, goal)
    i = SERVICES.index('backup')
    if required_mbps > 0 and m.FLOOR[i] <= required_mbps <= m.DEMAND[i]:
        r = math.ceil(required_mbps * 1000) / 1000
        a = m.canonical(dict(service='backup', rate_mbps=r), s)
        if m.canonical_json(a) not in {m.canonical_json(x) for x in base}:
            base.append(a)
    return base


def plan(s, h, ledger, events, epoch, horizon=2, admissible=None):
    """``admissible(target) -> bool`` optionally removes first-step candidates (recovery-window shield); None keeps the frozen behaviour."""
    goal = m.parse_goal(m.canonical_json(goals(ledger, events)))
    w = weights(ledger)
    req = required_backup_mbps(ledger, s, epoch)
    options = []
    checked = 0
    for index, a in enumerate(candidates(s, goal, req)):
        v = m.verify(a, s, h)
        checked += 1
        if not v['ok'] or (admissible is not None and not admissible(v['target'])):
            continue
        first = score(ledger, goal, w, v['prediction'], v['target'], req, events)
        edits = int(v['target'] != m.config(s))
        best = (*first, edits) if horizon == 1 else None
        if horizon == 2:
            ns, nh = m.successor(s, v['target'], h)
            # bytes delivered during the first step reduce what the deadline still needs
            ns_epoch = epoch
            ns['services'] = [dict(r, rx_bytes=s['services'][i]['rx_bytes'] + int(r['throughput_mbps'] * BYTES_PER_MBIT)) for i, r in enumerate(ns['services'])]
            req2 = required_backup_mbps(ledger, ns, ns_epoch)
            for second in candidates(ns, goal, req2):
                u = m.verify(second, ns, nh)
                checked += 1
                if u['ok']:
                    q = score(ledger, goal, w, u['prediction'], u['target'], req2, events)
                    rank = (first[0] + q[0], first[1] + q[1], edits + int(u['target'] != m.config(ns)))
                    if best is None or rank < best:
                        best = rank
        if best is None:
            continue
        options.append((best, index, a, first))
    chosen = min(options, key=lambda r: (r[0], r[1])) if options else None
    return (chosen[2] if chosen else None), dict(goal=goal, weights=w, required_backup_mbps=req, checked_candidates=checked, horizon=horizon,
                                                  chosen_rank=chosen[0] if chosen else None, immediate_score=chosen[3] if chosen else None)
