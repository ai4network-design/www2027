"""Per-interval recovery-window invariant (fix of review 108 section 3.3).

The old timed admission checked one static candidate configuration once, held it to the forecast lower bound
and looked for an escape; the actions actually executed afterwards were chosen by the planner and were not
bound to that witness. Here the same test is applied to *every executed action*.

Definitions (all under the public worst-case link model that ``multiservice_control.escape`` already uses, and
the exact operational bookkeeping of ``multiservice_control.operational``):

* ``distance(s, h)`` - the minimum number of single-field actions after which no service is capped, found by
  breadth-first search (the frozen ``escape`` is depth-first and returns the first path, not the shortest).
* ``reach(s, h, k, window)`` - the first action of a legal path of k hold / release / home-move actions that
  ends in a state with ``distance <= window`` (a witness), or None.
* ``invariant(s, h, e, window)`` for a state at state tick ``t`` in which the release event has not happened:
  if ``t >= e`` the event may arrive now, so ``distance <= window``; otherwise a witness path to state tick ``e``
  must exist.

Claim (proved in docs/reproduce/release_diagnostic_20260924.md): if the invariant holds at the start, every
executed action keeps it (the shield enforces this with witness-path / shortest-escape fallbacks), the
controller follows a shortest escape once the event is observed, and ``verify`` accepts those fallback actions,
then an event at any interval ``tau >= e`` is followed by all caps removed within ``window`` intervals. The
guarantee is conditional on ``tau >= e`` and on the fallbacks being accepted by ``verify``; both are logged.
"""
from functools import lru_cache
import json

from . import multiservice_control as m

MAX_NODES = 20000


def _minimal(s):
    return dict(tick=s['tick'], **m.config(s), links=[dict(capacity_mbps=c, queued_bytes=0) for c in m.C['path_min_mbps']])


def _moves(s):
    """Releases first, then hold, then home-route moves: among equally short escapes the search prefers the one
    that spends no route edit (route edits are budgeted 2 per 60 intervals)."""
    acts = [dict(service=m.NAMES[i], rate_mbps=None) for i in range(m.N) if s['caps_bps'][i]]
    acts.append(dict(hold=True))
    return acts + [dict(service=m.NAMES[i], path=i + 1) for i in range(m.N) if s['paths'][i] != i + 1]


@lru_cache(maxsize=65536)
def _bfs(key, limit):
    obj = json.loads(key); start, h0 = obj['state'], obj['history']
    if not any(start['caps_bps']):
        return 0, None
    frontier = [(start, h0, None)]; seen = {m.canonical_json([start['tick'], m.config(start), h0])}; nodes = 0
    for depth in range(1, limit + 1):
        nxt = []
        for s, h, first in frontier:
            for a in _moves(s):
                t, errors = m.operational(a, s, h)
                if errors:
                    continue
                ns, nh = m.successor(s, t, h); nodes += 1
                f = first or a
                if not any(ns['caps_bps']):
                    return depth, f
                k = m.canonical_json([ns['tick'], m.config(ns), nh])
                if k in seen or nodes > MAX_NODES:
                    continue
                seen.add(k); nxt.append((_minimal(ns), nh, f))
        frontier = nxt
        if not frontier:
            break
    return None, None


def distance(s, h, limit=12):
    """(steps, first action) of a shortest escape to an uncapped configuration, or (None, None) beyond ``limit``."""
    steps, first = _bfs(m.canonical_json(dict(state=_minimal(s), history=h)), limit)
    return steps, (json.loads(json.dumps(first)) if first else None)


def wait_action(s, h):
    """Hold when legal, else the first action of a shortest escape (kept for reference; the invariant uses ``reach``)."""
    t, errors = m.operational(dict(hold=True), s, h)
    if not errors:
        return dict(hold=True)
    return distance(s, h)[1] or dict(hold=True)


def _wait_moves(s):
    """Hold first (keep the current configuration as long as possible), then releases, then home-route moves."""
    acts = [dict(hold=True)] + [dict(service=m.NAMES[i], rate_mbps=None) for i in range(m.N) if s['caps_bps'][i]]
    return acts + [dict(service=m.NAMES[i], path=i + 1) for i in range(m.N) if s['paths'][i] != i + 1]


@lru_cache(maxsize=262144)
def _reach(key, steps, window):
    """First action of a legal path of exactly ``steps`` actions (hold / release / home moves) that ends in a state
    whose shortest escape fits ``window``; '' when none exists (a hold-only path cannot always survive the
    8-interval cap dwell limit, so the path may have to leave the useful configuration early)."""
    obj = json.loads(key); s, h = obj['state'], obj['history']
    if steps == 0:
        d, _ = _bfs(m.canonical_json(dict(state=s, history=h)), window)
        return 'ok' if d is not None and d <= window else ''
    for a in _wait_moves(s):
        t, errors = m.operational(a, s, h)
        if errors:
            continue
        ns, nh = m.successor(s, t, h)
        if _reach(m.canonical_json(dict(state=_minimal(ns), history=nh)), steps - 1, window):
            return m.canonical_json(a)
    return ''


def reach(s, h, steps, window):
    """First action of a witness path (dict) or None."""
    r = _reach(m.canonical_json(dict(state=_minimal(s), history=h)), steps, window)
    if not r:
        return None
    return dict(hold=True) if r == 'ok' else json.loads(r)


def invariant(s, h, e, window):
    """Recovery-window invariant for state ``s`` (release not yet happened); ``e`` None means 'may happen now'.
    From state tick e on: shortest escape within the window. Before e: some legal hold/release/home-move path
    reaches state tick e in such a state."""
    e = s['tick'] if e is None else max(e, s['tick'])
    if s['tick'] >= e:
        d, _ = distance(s, h, window)
        return d is not None and d <= window
    return reach(s, h, e - s['tick'], window) is not None


def admissible(s, h, target, e, window):
    """Would the successor of executing ``target`` from ``s`` satisfy the invariant?"""
    ns, nh = m.successor(s, target, h)
    return invariant(ns, nh, e, window)


def fallback(s, h, e, window):
    """The action that preserves the invariant by construction: the first step of the witness path (pre-e) or of
    a shortest escape (from e on). Returns None when the invariant does not hold at ``s`` itself."""
    if e is None or s['tick'] >= e:   # the invariant at s is distance <= window: a shortest-escape step lowers it
        d, first = distance(s, h)
        return dict(hold=True) if d == 0 else first
    return reach(s, h, e - s['tick'], window)   # the remainder of the witness path witnesses the successor


def recovery_action(s, h):
    """After the release event: first action of a shortest escape (hold when nothing is capped)."""
    d, first = distance(s, h)
    return dict(hold=True) if not d else first
