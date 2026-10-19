"""One episode of the maintenance capacity-drop scenario (design: docs/reproduce/maintenance_scenario_design_20260930.md).

The base ledger of the frozen contract is the only obligation set (no revisions, no release inside the episode).
The maintenance notice arrives at interval 2; from then on the arm's belief (path, start, end, remaining Mbps) or None
(reactive) steers the maintenance shield. Hard-SLA violations are scored intervals, not aborts; when the safety layer
certifies no action the configuration is kept and the interval is recorded as uncertified.
"""
from copy import deepcopy
import time

from . import multiservice_control as m, obligation_ledger as L, obligation_planner as P, maintenance_shield as MS
from .frozen_protocol import obligation_outcomes
from .zero_shot_evidence import fresh, save

NOTICE_TICK = 2


def run(root, contract, world, belief, native_factory, shield=True, planning=False, heuristic_lead=None):
    """world: dict(id, seed, maintenance=(p, s, e, mbps)); belief: (p, s_b, e_b, c_b) or None.
    ``planning`` adds forecast-aware goal keeping: before the believed start, an action that would lower the best
    obligation score reachable under the belief is replaced by the first step towards that best configuration."""
    root = fresh(root); env = None; rows = []; log = []
    ledger = L.upgrade(deepcopy(contract['base_ledger'])); h = m.history(); events = {'ended': False, 'never': False}
    schedule = belief_schedule(belief)
    report = dict(ok=False, world=world['id'], belief=(list(belief) if isinstance(belief, tuple) else belief) if belief else None, shield=shield, planning=planning, error=None)
    try:
        p, s, e, mbps = world['maintenance']
        env = native_factory(root / 'native', world['seed'], contract['ticks'], [(p, s, e, mbps)] if e > s else [])
        epoch = [f['rx_bytes'] for f in env.state['services']]
        for tick in range(contract['ticks']):
            before = deepcopy(env.state); hb = deepcopy(h); b = belief_at(schedule, tick) if (shield and tick >= NOTICE_TICK) else None
            start = time.perf_counter(); entry = dict(tick=tick, belief=list(b) if b else None, fallback=None, uncertified=False)
            ok = (lambda tg: MS.admissible(before, h, tg, b)) if b else None
            action, detail = P.plan(before, h, ledger, events, epoch, horizon=2, admissible=ok)
            try:
                v, sel = m.select(action, before, h)
                if b and not MS.admissible(before, h, v['target'], b):
                    fb = MS.fallback(before, h, b); vf = m.verify(fb, before, h) if fb else dict(ok=False)
                    entry['fallback'] = fb or 'none'
                    if vf['ok']:
                        v, sel = vf, dict(source='maintenance_fallback')
                    else:
                        entry['fallback_rejected'] = True
                if b and heuristic_lead and not planning:   # fixed-lead control: prepare k intervals before the believed start without searching
                    pb, sb, eb, cb = b
                    if tick + 1 < sb <= tick + 1 + heuristic_lead and not MS.robust_under(v['target'], pb, cb):
                        fb = MS.fallback(before, h, (pb, tick + 1, eb, cb)); vf = m.verify(fb, before, h) if fb else dict(ok=False)
                        if vf['ok']:
                            v, sel = vf, dict(source='fixed_lead'); entry['fixed_lead'] = fb
                if b and planning:
                    keeps, now, _ = MS.goal_keeping(before, h, v['target'], ledger, b)
                    entry['goal_keeps'] = keeps
                    if not keeps and now is not None:
                        vg = m.verify(now[1], before, h)
                        if vg['ok'] and MS.admissible(before, h, vg['target'], b):
                            v, sel = vg, dict(source='maintenance_goal'); entry['goal_step'] = now[1]; entry['goal_score'] = list(now[0])
                target = v['target']
            except ValueError:
                target = m.config(before); entry['uncertified'] = True; sel = dict(source='uncertified_hold')
            entry['invariant_after'] = None if not b else MS.admissible(before, h, target, b)
            ms = (time.perf_counter() - start) * 1000
            env.apply(target); after = deepcopy(env.state); h = m.advance(h, before, target)
            row = dict(tick=tick, before=before, after=after, history_before=hb, public_events=events, selected=dict(target=target), selection=sel, proposal_ms=ms,
                       actual_hard_sla=m.sla(after), shield=entry)
            save(root / f'tick_{tick:03d}.json', row); rows.append(row); log.append(entry)
        assert env.close() == 0; env = None; report['ok'] = True
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        if env is not None:
            env.abort()
    report['metric'] = metric(rows, contract, ledger, world)
    report['uncertified_intervals'] = sum(x['uncertified'] for x in log)
    report['fallbacks'] = sum(x['fallback'] is not None for x in log)
    report['goal_steps'] = sum('goal_step' in x for x in log); report['fixed_lead_steps'] = sum('fixed_lead' in x for x in log)
    save(root / 'report.json', report)
    return report, rows


def belief_schedule(belief):
    """A belief is a tuple (p, s_b, e_b, c_b), None, or a schedule {tick: belief-or-None} giving the belief that holds from
    that tick on (a revision thread: the announcement's window first, the update's window from the tick it arrives)."""
    if belief is None or isinstance(belief, tuple):
        return {0: belief}
    return {int(k): (tuple(v) if v else None) for k, v in belief.items()}


def belief_at(schedule, tick):
    keys = [k for k in schedule if k <= tick]
    return schedule[max(keys)] if keys else None


def metric(rows, contract, ledger, world):
    n = contract['ticks']
    if not rows:
        return dict(satisfaction=0.0, met=0, intervals=n, hard_sla_violation_intervals=0, window_met=0, window_intervals=0, missing=n)
    epoch = [f['rx_bytes'] for f in rows[0]['before']['services']]
    met = 0; hard = 0; wmet = 0; wn = 0; p, s, e, _ = world['maintenance']
    for r in rows:
        ok_sla = m.sla(r['after']); hard += not ok_sla
        good = ok_sla and all(obligation_outcomes(r, ledger, epoch, contract).values())
        met += good
        if s <= r['tick'] < e:
            wn += 1; wmet += good
    return dict(satisfaction=met / n, met=met, intervals=n, hard_sla_violation_intervals=hard, window_met=wmet, window_intervals=wn, missing=n - len(rows))
