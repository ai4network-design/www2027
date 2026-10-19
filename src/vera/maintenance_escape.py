"""Post-hoc exploratory fallback for the maintenance shield (not part of contracts/maintenance_diagnostic_20260930.json).

In the registered shield, once the believed window has opened (or the configuration can no longer be made robust by
the believed start) the fallback searches a single step; when no single step reaches a robust configuration the
controller holds. The window-agnostic arm M_worst (window [2, 12)) therefore never prepared and equalled the reactive
controller. This fallback takes the first step of the shortest legal path to a robust configuration within the rest of
the believed window instead (same node budget), like the escape of the recovery-window shield.
"""
from . import maintenance_shield as MS


def fallback_escape(s, h, belief):
    if belief is None:
        return dict(hold=True)
    p, sb, eb, c = belief
    t = s['tick']
    if t + 1 >= eb:
        return dict(hold=True)
    r = MS.reach(s, h, sb - t, belief) if t + 1 < sb else None
    return r or MS.reach(s, h, max(1, eb - t - 1), belief)


def install():
    MS.fallback = fallback_escape
