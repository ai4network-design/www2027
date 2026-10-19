"""Maintenance-window invariant (capacity-drop scenario; same structure as recovery_shield).

A belief is (path p, start s_b, end e_b, remaining capacity c_b in Mbps) or None. Robustness under the belief uses
the frozen predictor with public minimum capacities, except that path p is limited to c_b.

* ``robust_under(t, p, c)``            - the configuration is robust-safe with path p at c Mbps.
* ``reach(s, h, k, belief)``           - first action of a legal path of at most k actions (route moves and rate caps
                                          from the frozen candidate set, plus hold) that reaches a configuration robust
                                          under the belief; None when none exists within the node budget.
* ``invariant(s, h, t_next, belief)``  - for a state at interval t_next: inside [s_b, e_b) the configuration must be
                                          robust under the belief; before s_b a witness path must reach such a
                                          configuration by s_b; from e_b on there is no maintenance constraint.
"""
from functools import lru_cache
import json

from . import multiservice_control as m

MAX_NODES = 6000


def robust_under(t, p, c):
    """Frozen robust check (public minimum capacities, 2.5% overhead, SLA floors and delay) with path p limited to c."""
    caps = list(m.C['path_min_mbps']); caps[p - 1] = min(caps[p - 1], c)
    offered = [min(m.DEMAND[i], t['caps_bps'][i] / 1e6 or m.DEMAND[i]) for i in range(m.N)]
    loads = [sum(offered[i] * 1.025 for i in range(m.N) if t['paths'][i] == q) for q in m.PATHS]
    for i in range(m.N):
        q = t['paths'][i] - 1; cap = caps[q]; excess = max(0, loads[q] - cap); loss = excess / max(loads[q], 1e-9)
        delay = 2 + m.C['path_delay_ms'][q] + .00984 / cap * 1000 + 2 * .0984
        thr = max(0, offered[i] * (1 - loss) - .01)
        if thr < m.FLOOR[i] - 1e-9 or delay > 50 or loss > .01:
            return False
    return min(caps[q] - loads[q] for q in range(m.P)) >= -1e-9


def _minimal(s):
    return dict(tick=s['tick'], **m.config(s), links=[dict(capacity_mbps=c, queued_bytes=0) for c in m.C['path_min_mbps']])


def _moves(s):
    acts = [dict(hold=True)]
    for i, n in enumerate(m.NAMES):
        acts += [dict(service=n, path=q) for q in m.PATHS if s['paths'][i] != q]
        rates = {round(m.FLOOR[i] + .02, 3), None}
        for q, cap in enumerate(m.C['path_min_mbps'], 1):
            others = sum(min(m.DEMAND[j], s['caps_bps'][j] / 1e6 or m.DEMAND[j]) for j in range(m.N) if j != i and s['paths'][j] == q)
            for cc in (cap, 1.0, 1.5, 2.0, 3.0):
                r = int((cc / 1.025 - others) * 1000) / 1000
                if m.FLOOR[i] <= r < m.DEMAND[i]:
                    rates.add(r)
        acts += [dict(service=n, rate_mbps=r) for r in sorted(rates, key=lambda x: (x is None, x or 0))]
    return acts


@lru_cache(maxsize=65536)
def _reach(key, k, p, c):
    obj = json.loads(key); start, h0 = obj['state'], obj['history']
    if robust_under(m.config(start), p, c):
        return json.dumps(dict(hold=True))
    frontier = [(start, h0, None)]; seen = {m.canonical_json([m.config(start), h0])}; nodes = 0
    for depth in range(1, k + 1):
        nxt = []
        for s, h, first in frontier:
            for a in _moves(s):
                t, errors = m.operational(a, s, h)
                if errors:
                    continue
                ns, nh = m.successor(s, t, h); nodes += 1; f = first or a
                if robust_under(t, p, c):
                    return json.dumps(f)
                key2 = m.canonical_json([t, nh])
                if key2 in seen or nodes > MAX_NODES:
                    continue
                seen.add(key2); nxt.append((_minimal(ns), nh, f))
        frontier = nxt
        if not frontier:
            break
    return ''


def reach(s, h, k, belief):
    p, _, _, c = belief
    r = _reach(m.canonical_json(dict(state=_minimal(s), history=h)), max(0, k), p, c)
    return json.loads(r) if r else None


def invariant(s, h, t_next, belief):
    if belief is None:
        return True
    p, sb, eb, c = belief
    if t_next >= eb:
        return True
    if t_next >= sb:
        return robust_under(m.config(s), p, c)
    return reach(s, h, sb - t_next, belief) is not None


def admissible(s, h, target, belief):
    ns, nh = m.successor(s, target, h)
    return invariant(ns, nh, s['tick'] + 1, belief)


def fallback(s, h, belief):
    """Action that keeps the invariant: the first step of a witness path (or hold when already safe/unconstrained)."""
    if belief is None:
        return dict(hold=True)
    p, sb, eb, c = belief
    if s['tick'] + 1 >= eb:
        return dict(hold=True)
    return reach(s, h, max(1, sb - s['tick']), belief)


# ----------------------------------------------------------------------------- forecast-aware planning (goal-keeping)

def maintenance_prediction(t, p, c):
    """Predicted service rows for configuration t on public minimum capacities with path p limited to c."""
    caps = list(m.C['path_min_mbps']); caps[p - 1] = min(caps[p - 1], c)
    s = dict(tick=0, **t, links=[dict(capacity_mbps=x, queued_bytes=0) for x in caps])
    return m.predict(s, t)


def score_under(ledger, t, belief):
    """(violations, gap) of the base obligations if configuration t holds during the believed window (lower is better)."""
    from . import obligation_planner as P
    from .obligation_ledger import goals, weights
    p, _, _, c = belief
    events = {'ended': False, 'never': False}
    goal = m.parse_goal(m.canonical_json(goals(ledger, events)))
    return P.score(ledger, goal, weights(ledger), maintenance_prediction(t, p, c), t, 0.0, events)


def _key(s, h):
    """Configuration plus the operational-history features that constrain future actions (dedupe for the search)."""
    tick = s['tick']; act = lambda xs: sum(x > tick - 60 for x in xs)
    return m.canonical_json([m.config(s), act(h['route_ticks']), min(5, tick - h['route_ticks'][-1]) if h['route_ticks'] else 9,
                             act(h['edit_ticks']), [min(2, tick - r[-1]) if r else 9 for r in h['rate_ticks']], [min(8, x) for x in h['limited_ticks']]])


SEARCH_MAX_NODES = 20000      # node budget of best_reachable (sensitivity runs override it)
SEARCH_DEPTH_CAP = None       # when set, goal keeping searches at most this many actions ahead (greedy control: 1)


def best_reachable(s, h, k, ledger, belief, max_nodes=None):
    """Best (score, first action, steps) over configurations robust under the belief that are reachable within k legal
    actions (hold, route moves, the cap set of _moves); None when none is reachable within the node budget."""
    p, _, _, c = belief
    max_nodes = SEARCH_MAX_NODES if max_nodes is None else max_nodes
    best = None
    def consider(t, first, depth):
        nonlocal best
        if robust_under(t, p, c):
            sc = score_under(ledger, t, belief)
            cand = (sc, depth)
            if best is None or cand < (best[0], best[2]):
                best = (sc, first or dict(hold=True), depth)
    consider(m.config(s), None, 0)
    frontier = [(_minimal(s), h, None)]; seen = {_key(s, h)}; nodes = 0
    for depth in range(1, max(0, k) + 1):
        nxt = []
        for st, hh, first in frontier:
            for a in _moves(st):
                t, errors = m.operational(a, st, hh)
                if errors:
                    continue
                ns, nh = m.successor(st, t, hh); nodes += 1; f = first or a
                consider(t, f, depth)
                kk = _key(ns, nh)
                if kk in seen or nodes > max_nodes:
                    continue
                seen.add(kk); nxt.append((_minimal(ns), nh, f))
        frontier = nxt
        if not frontier or nodes > max_nodes:
            break
    return best


def goal_keeping(s, h, target, ledger, belief):
    """Does executing ``target`` keep the best score reachable by the believed start? Returns (keeps, best_now, best_after)."""
    p, sb, eb, c = belief
    t = s['tick']
    if t + 1 >= eb:
        return True, None, None
    if t + 1 >= sb:   # inside the believed window: the executed configuration must be robust under the belief
        return robust_under(target, p, c), None, None
    cap = lambda k: k if SEARCH_DEPTH_CAP is None else min(k, SEARCH_DEPTH_CAP)
    now = best_reachable(s, h, cap(sb - t), ledger, belief)
    ns, nh = m.successor(s, target, h)
    after = best_reachable(ns, nh, cap(sb - t - 1), ledger, belief)
    if now is None:
        return True, None, after
    return after is not None and after[0] <= now[0], now, after
