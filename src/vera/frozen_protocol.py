"""Frozen-protocol executor: corpus-driven episodes, annotation-based metric, analysis.

Implements contracts/frozen_protocol_20260924_20261008.json. Episodes reuse the
frozen ledger, admission rule and planner of the 2026-09-22 raw revision group.
The primary metric is computed from the independent annotation, never from what
an arm accepted. Infrastructure failures are ledgered and retried; scientific
failures are counted as unmet from the message tick and never retried.
"""
from copy import deepcopy
import hashlib
import re
import json
import random
import statistics
import time

from . import multiservice_control as m
from .multibusiness_revision import goals, public_obligations, commit, propose
from .raw_revision_diagnostic import messages, receive
from .open_rule import rule_request
from .obligation_static_rule import assess
from .obligation_admission import observe_deadlines
from .zero_shot_evidence import fresh, save
from . import obligation_ledger as L
from . import obligation_planner as P
from . import revision_v2 as V2
from . import recovery_shield as S

RULE_ARM = 'rule'
ORACLE_ARM = 'annotated_oracle'
MODEL_ARMS = {'qwen7b_full_rebuild': 'qwen7b', 'qwen1p5b_full_rebuild': 'qwen1p5b', 'qwen7b_two_stage': 'qwen7b', 'qwen1p5b_two_stage': 'qwen1p5b',
              'qwen7b_two_stage_lenient': 'qwen7b', 'qwen1p5b_two_stage_lenient': 'qwen1p5b'}
TWO_STAGE_ARMS = {'qwen7b_two_stage', 'qwen1p5b_two_stage', 'qwen7b_two_stage_lenient', 'qwen1p5b_two_stage_lenient'}
ESCALATION_ARMS = {}  # label -> dict(small=<two-stage arm label>, large=<two-stage arm label>, mode='detector'|'agreement'); registered by the runner
REFERENTIAL = re.compile(r'刚才|那个|那条|也一样|同样|照.*(标准|这个|那个)|回到|原来|再(收紧|紧|松|加|减|放)|减半|一倍|翻倍|和.*一样|比.*(再|多|少)|\b(same|that one|the same|like|as (the )?(video|query|backup)|again|back to|earlier|previous|another|more|less|halve|double|instead)\b', re.I)


def two_stage_compile(arm, old, text, conversation, compiler, meta, v2):
    """One two-stage compilation under ``arm``; returns (request, candidate, generation) or raises ValueError."""
    types = None if v2 else V2.V1_TYPES
    s1 = compiler.chat(V2.stage1_messages(old, text, conversation, meta['tick'], types), meta['item'], meta['tick'], MODEL_ARMS[arm], 'type')
    generation = dict(stage1=s1, stage2=None, compiler_ms=s1['compiler_ms'], compiler_timeout=s1['compiler_timeout'], raw=None, call=s1['call'], arm=arm)
    if s1['compiler_timeout']:
        raise ValueError('compiler time budget')
    obligation_type = V2.parse_stage1(s1['raw'], lenient=arm.endswith('_lenient') or arm in ESCALATION_SMALL_LENIENT, types=types)
    if obligation_type == 'none':
        raise ValueError('stage 1: no revisable obligation in this message')
    s2 = compiler.chat(V2.stage2_messages(old, text, conversation, meta['tick'], obligation_type), meta['item'], meta['tick'], MODEL_ARMS[arm], 'value')
    generation.update(stage2=s2, compiler_ms=s1['compiler_ms'] + s2['compiler_ms'], compiler_timeout=s2['compiler_timeout'], obligation_type=obligation_type)
    if s2['compiler_timeout']:
        raise ValueError('compiler time budget')
    request, candidate = V2.assemble(old, obligation_type, s2['raw'], None if v2 else (lambda o, r: propose(o, r, 'patch')))
    generation['raw'] = json.dumps(dict(request=request))
    return request, candidate, generation


ESCALATION_SMALL_LENIENT = set()  # adapter arm labels that use lenient stage-1 decoding
INFRA_EXCEPTIONS = (RuntimeError, TimeoutError, OSError, ImportError, MemoryError)
SCIENTIFIC_ABORTS = ('hard SLA or ACK budget', 'numerical proposal budget', 'cell time budget')


def world_seed(item_id):
    return int(hashlib.sha256(item_id.encode()).hexdigest()[:8], 16) % (2 ** 31)


def message_ticks(item, contract):
    if item['category'] == 'in_schema_sequence':
        return contract['request_ticks_sequence']
    if item['category'] == 'in_schema_triple':
        return contract['request_ticks_triple']
    if item['category'] == 'in_schema_pair':
        return contract['request_ticks_pair']
    return contract['request_ticks_single']


def item_requests(item, contract):
    texts = item['request'] if isinstance(item['request'], list) else [item['request']]
    refs = item['annotation']['reference_requests']
    ticks = message_ticks(item, contract)
    if len(texts) != len(ticks) or len(refs) != len(texts):
        raise ValueError('message count, tick count and reference count must agree')
    return [dict(tick=t, text=x, reference=r) for t, x, r in zip(ticks, texts, refs)]


def eligible_items(corpus, receipt):
    """Items named in the freeze receipt whose per-item SHA still matches."""
    def sha(obj):
        return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    by_id = {s['id']: s for s in corpus['slots']}
    out = []
    for item_id, digest in receipt['items'].items():
        slot = by_id[item_id]
        if sha(slot) != digest:
            raise ValueError(f'corpus item {item_id} changed after freeze')
        if slot['category'].startswith('in_schema'):
            out.append(slot)
    return out


class SmokeNative:
    """Predictor-driven stand-in for ns-3; development and pipeline checks only."""
    def __init__(self, build, out, seed, n):
        s = dict(tick=0, paths=[3, 2, 3], caps_bps=[0, 0, 0], physical_time=1,
                 links=[dict(id=i + 1, capacity_mbps=v, delay_ms=d, queued_bytes=0) for i, (v, d) in enumerate(zip(m.C['path_min_mbps'], m.C['path_delay_ms']))])
        s['services'] = m.predict(s, m.config(s))['services']
        for i, r in enumerate(s['services']):
            r.update(rx_bytes=0, offered_mbps=m.DEMAND[i], demand_mbps=m.DEMAND[i], tx=0, rx=0, dropped=0, pending=0, errors=0)
        self.state = s; self.tick = 0; self.n = n; self.seed = seed
    def apply(self, target):
        old = self.state; s, _ = m.successor(old, target, m.history())
        s['physical_time'] = s['tick'] + 1
        for i, (r, b) in enumerate(zip(s['services'], old['services'])):
            r.update(rx_bytes=b['rx_bytes'] + int(r['throughput_mbps'] * 1e6 / 8), offered_mbps=m.DEMAND[i], demand_mbps=m.DEMAND[i], tx=0, rx=0, dropped=0, pending=0, errors=0)
        self.state = s; self.tick += 1
        return dict(tick=self.tick - 1, edits=0, route_edits=0, rate_edits=0, **m.config(s))
    def close(self): return 0
    def abort(self): pass


def compile_request(arm, old, text, conversation, compiler, meta, v2=False):
    """Return (request, candidate, generation, error) for one message under one arm."""
    generation = None; error = None; request = None; candidate = None
    _rule = (lambda o, x: V2.rule_request(o, x, meta.get('tick'))) if v2 else rule_request
    _propose = (lambda o, r: L.propose(o, r)) if v2 else (lambda o, r: propose(o, r, 'patch'))
    try:
        if arm == RULE_ARM:
            begin = time.perf_counter()
            request = _rule(old, text)
            candidate = _propose(old, request)
            generation = dict(call=None, raw=json.dumps(dict(request=request)), compiler_ms=(time.perf_counter() - begin) * 1000, compiler_timeout=False)
        elif arm == ORACLE_ARM:
            ref = meta['reference']
            request = dict(base_version=old['version'], obligation_id=ref['obligation_id'], field=ref['field'], value=ref['value'])
            candidate = _propose(old, request)
            generation = dict(call=None, raw=json.dumps(dict(request=request)), compiler_ms=0.0, compiler_timeout=False, extra_information=True)
        elif arm in ESCALATION_ARMS:
            cfg = ESCALATION_ARMS[arm]; escalated = False; reason = None; total_ms = 0.0
            if REFERENTIAL.search(text):
                escalated, reason = True, 'detector'
            small = None
            if not escalated:
                try:
                    request, candidate, small = two_stage_compile(cfg['small'], old, text, conversation, compiler, meta, v2); total_ms += small['compiler_ms']
                    if not within_hard_bounds(candidate):
                        raise ValueError('value outside hard bounds')
                except ValueError as exc:
                    escalated, reason = True, f'small failed: {exc}'
                    if small: total_ms += small['compiler_ms']
            if not escalated and cfg['mode'] == 'agreement':
                shadow = compiler.chat(V2.stage1_messages(old, text, conversation, meta['tick'], None if v2 else V2.V1_TYPES), meta['item'], meta['tick'], MODEL_ARMS[cfg['large']], 'shadow_type'); total_ms += shadow['compiler_ms']
                try:
                    large_type = V2.parse_stage1(shadow['raw'], lenient=True, types=None if v2 else V2.V1_TYPES)
                except ValueError:
                    large_type = None
                if large_type != small['obligation_type']:
                    escalated, reason = True, f"disagreement: small={small['obligation_type']} large={large_type}"
            if escalated:
                request, candidate, generation = two_stage_compile(cfg['large'], old, text, conversation, compiler, meta, v2); total_ms += generation['compiler_ms']
            else:
                generation = small
            generation = dict(generation, compiler_ms=total_ms, escalated=escalated, escalation_reason=reason, small_arm=cfg['small'], large_arm=cfg['large'])
        elif arm in TWO_STAGE_ARMS:
            request, candidate, generation = two_stage_compile(arm, old, text, conversation, compiler, meta, v2)
        elif arm in MODEL_ARMS:
            generation = compiler.generate(old, text, conversation, 'full_rebuild', meta['item'], meta['tick'], MODEL_ARMS[arm], v2=v2)
            if generation['compiler_timeout']:
                raise ValueError('compiler time budget')
            request, candidate = V2.receive(old, generation['raw']) if v2 else receive(old, generation['raw'], 'full_rebuild')
        else:
            raise ValueError('unknown arm')
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
    return request, candidate, generation, error


def base_ledger(item, contract, v2=False):
    """Contract base ledger, or a per-item initial_ledger override (pilot corpora only)."""
    ledger = deepcopy(item.get('initial_ledger') or contract['base_ledger'])
    return L.upgrade(ledger) if v2 else ledger


def within_hard_bounds(candidate):
    """Degraded admission still refuses values the safety layer could never honour or parse."""
    for name, q in candidate['obligations'].items():
        if name.endswith('.qos'):
            i = m.NAMES.index(q['service'])
            if not (0 < q['delay_ms'] <= 1000) or not (m.FLOOR[i] <= q['min_mbps'] <= m.DEMAND[i]):
                return False
    return True


def assess_escape(state, history, spec, mode, obligations, epoch, outcomes):
    """Static rule with its 5+caps recovery constant replaced by the real escape certificate of each candidate."""
    check = assess(state, history, spec, mode, obligations, epoch, deadline_outcomes=outcomes)
    if check['ok'] or any(r['code'] != 'no_static_candidate' for r in check['reasons']):
        return dict(check, admission='static_escape', corrected=False)
    for cand in check.get('candidates', []):
        if cand['issues'] == ['static_recovery']:
            ns, nh = m.successor(state, cand['target'], history)
            cert = m.escape(ns, nh)
            steps = len(cert['actions']) if cert['ok'] else None
            cand['escape_recovery_intervals'] = steps
            if steps is not None and steps <= obligations['recovery']['max_intervals']:
                return dict(check, ok=True, reasons=[], chosen=cand, admission='static_escape', corrected=True)
    return dict(check, admission='static_escape', corrected=False)


def release_tick(item, contract):
    return item.get('release_tick', contract['release_tick'])


FIXED_MARGIN_TICKS = 6  # static_margin: assume the release event is at least this many ticks away (just past the 5-tick route cooldown), never reading language


def assess_timed(state, history, spec, mode, obligations, epoch, outcomes, earliest_tick):
    """Static rule whose recovery check assumes the release event cannot arrive before ``earliest_tick``:
    hold the candidate configuration until then, and require the real escape certificate from that
    point to fit the recovery window. ``earliest_tick`` None falls back to the plain static rule."""
    check = assess(state, history, spec, mode, obligations, epoch, deadline_outcomes=outcomes)
    if earliest_tick is None or check['ok'] or any(r['code'] != 'no_static_candidate' for r in check['reasons']):
        return dict(check, admission='timed', earliest_tick=earliest_tick, corrected=False)
    for cand in check.get('candidates', []):
        if cand['issues'] == ['static_recovery']:
            ns, nh = m.successor(state, cand['target'], history)
            while ns['tick'] < earliest_tick:
                ns, nh = m.successor(ns, m.config(ns), nh)
            cert = m.escape(ns, nh)
            steps = len(cert['actions']) if cert['ok'] else None
            cand['timed_recovery_intervals'] = steps; cand['held_until_tick'] = ns['tick']
            if steps is not None and steps <= obligations['recovery']['max_intervals']:
                return dict(check, ok=True, reasons=[], chosen=cand, admission='timed', earliest_tick=earliest_tick, corrected=True)
    return dict(check, admission='timed', earliest_tick=earliest_tick, corrected=False)


def assess_forecast(state, history, spec, mode, obligations, epoch, outcomes, earliest_tick, window):
    """Admission for the release-event diagnostic: the static rule, whose conservative recovery constant is
    replaced by the recovery-window invariant of ``recovery_shield`` for the candidate's successor under the
    belief ``earliest_tick``. The same invariant then filters every executed action (see ``episode``)."""
    check = assess(state, history, spec, mode, obligations, epoch, deadline_outcomes=outcomes)
    if check['ok'] or any(r['code'] != 'no_static_candidate' for r in check['reasons']):
        return dict(check, admission='forecast', earliest_tick=earliest_tick, corrected=False)
    for cand in check.get('candidates', []):
        if cand['issues'] == ['static_recovery'] and S.admissible(state, history, cand['target'], earliest_tick, window):
            return dict(check, ok=True, reasons=[], chosen=cand, admission='forecast', earliest_tick=earliest_tick, corrected=True)
    return dict(check, admission='forecast', earliest_tick=earliest_tick, corrected=False)


def episode(root, contract, item, arm, native_factory, compiler, source, v2=False, planner='frozen', admission='static', belief=None, shield=True):
    """``belief`` (release_forecast.*Belief) switches on the release-event diagnostic: admission 'forecast' and the
    per-interval recovery-window invariant on every executed action; it requires ledger v2 and the obligation planner.
    ``shield=False`` with admission 'timed_belief' is the controller-layer comparison of the calibration diagnostic:
    the same belief feeds the old one-shot timed admission and nothing constrains the executed actions."""
    if belief is not None and shield and not (v2 and planner == 'obligation' and admission == 'forecast'):
        raise ValueError('a release belief requires ledger v2, the obligation planner and admission forecast')
    if belief is not None and not shield and not (v2 and planner == 'obligation' and admission == 'timed_belief'):
        raise ValueError('the unshielded belief comparison requires ledger v2, the obligation planner and admission timed_belief')
    said = []; shield_log = []
    root = fresh(root); env = None; rows = []
    ledger = base_ledger(item, contract, v2); h = m.history(); outcomes = {}; conversation = []
    _goals = L.goals if v2 else goals; _public = L.public_obligations if v2 else public_obligations; _commit = L.commit if v2 else commit
    requests = item_requests(item, contract)
    report = dict(ok=False, item=item['id'], arm=arm, seed=world_seed(item['id']), failure_class=None, v2=v2, planner=planner, admission=admission)
    try:
        env = native_factory(root / 'native', report['seed'], contract['ticks'])
        epoch = [f['rx_bytes'] for f in env.state['services']]; report['epoch'] = epoch; cell_start = time.monotonic()
        for tick in range(contract['ticks']):
            before = deepcopy(env.state); hb = deepcopy(h); events = {'ended': tick >= release_tick(item, contract), 'never': False}; trans = None; status = 'not_requested'; fired = []
            if v2:  # apply scheduled revisions whose trigger fired, through the same admission
                for idx in L.due_scheduled(ledger, tick, events):
                    sc = ledger['obligations']['scheduled']['items'][idx]; rq = sc['request']
                    trial = L.apply_scheduled(ledger, idx, True)
                    if rq['obligation_id'].endswith('.qos') or rq['obligation_id'] == 'backup.deadline':
                        svc = trial['obligations'][rq['obligation_id']]['service'] if rq['obligation_id'] != 'backup.deadline' else 'backup'
                        qq = trial['obligations'][svc + '.qos']; spec = dict(service=svc, delay_ms=qq['delay_ms'], min_mbps=qq['min_mbps'])
                        est = trial['obligations'].get('release_estimate', {}).get('earliest_tick')
                        if admission == 'timed_belief':
                            chk = assess_timed(before, h, spec, 'restore' if events['ended'] else 'protect', _public(trial), epoch, outcomes, belief.earliest(tick, trial, said))
                        elif admission == 'forecast':
                            chk = assess_forecast(before, h, spec, 'restore' if events['ended'] else 'protect', _public(trial), epoch, outcomes, belief.earliest(tick, trial, said), trial['obligations']['recovery']['max_intervals'])
                        else:
                            chk = assess_timed(before, h, spec, 'restore' if events['ended'] else 'protect', _public(trial), epoch, outcomes, est if admission == 'static_estimate' else None)
                        ok = chk['ok'] and within_hard_bounds(trial)
                    else:
                        chk = dict(ok=True, no_qos_admission=True); ok = True
                    ledger = trial if ok else L.apply_scheduled(ledger, idx, False)
                    fired.append(dict(index=idx, request=rq, trigger=sc['trigger'], applied=ok, check=chk))
            msg = next((x for x in requests if x['tick'] == tick), None)
            if msg:
                old = deepcopy(ledger); said.append(dict(tick=tick, text=msg['text']))
                request, candidate, generation, error = compile_request(arm, old, msg['text'], conversation, compiler, dict(item=item['id'], tick=tick, reference=msg['reference']), v2=v2)
                if belief is not None:   # a text forecaster calls its model here, outside the numerical proposal budget (like compilation)
                    belief.earliest(tick, candidate if candidate is not None else old, said)
                start = time.perf_counter(); status = 'rejected'; check = None
                if error is None and v2 and not within_hard_bounds(candidate):
                    error = 'value outside hard bounds'; status = 'rejected'
                if error is None:
                    key = request['obligation_id']
                    if key.endswith('.qos') or key == 'backup.deadline':
                        service = candidate['obligations'][key]['service']; q = candidate['obligations'][service + '.qos']
                        spec = dict(service=service, delay_ms=q['delay_ms'], min_mbps=q['min_mbps'])
                        mode = 'restore' if events['ended'] else 'protect'
                        if admission == 'static_escape':
                            check = assess_escape(before, h, spec, mode, _public(candidate), epoch, outcomes)
                        elif admission == 'static_estimate':
                            est = candidate['obligations'].get('release_estimate', {}).get('earliest_tick')
                            check = assess_timed(before, h, spec, mode, _public(candidate), epoch, outcomes, est)
                        elif admission == 'static_margin':
                            check = assess_timed(before, h, spec, mode, _public(candidate), epoch, outcomes, tick + FIXED_MARGIN_TICKS)
                        elif admission == 'oracle_time':
                            check = assess_timed(before, h, spec, mode, _public(candidate), epoch, outcomes, release_tick(item, contract))
                        elif admission == 'forecast':
                            check = assess_forecast(before, h, spec, mode, _public(candidate), epoch, outcomes, belief.earliest(tick, candidate, said), candidate['obligations']['recovery']['max_intervals'])
                        elif admission == 'timed_belief':
                            check = assess_timed(before, h, spec, mode, _public(candidate), epoch, outcomes, belief.earliest(tick, candidate, said))
                        else:
                            check = assess(before, h, spec, mode, _public(candidate), epoch, deadline_outcomes=outcomes)
                    else:  # recovery window and priority add no QoS constraint; accepted after the scope check
                        check = dict(ok=True, reasons=[], elapsed_ms=0.0, no_qos_admission=True)
                    if admission == 'degrade':
                        # Accept every in-bounds obligation the administrator states; the planner degrades by priority.
                        accept = within_hard_bounds(candidate)
                        ledger = _commit(old, candidate, request, accept)
                        status = 'applied' if accept and check['ok'] else 'applied_degraded' if accept else 'rejected'
                        check = dict(check, static_verdict=check['ok'], admission='degrade')
                    else:
                        ledger = _commit(old, candidate, request, check['ok']); status = 'applied' if check['ok'] else 'deferred'
                trans = dict(old=old, request=request, candidate=candidate, check=check, error=error, status=status, compiler=generation,
                             administrator_message=msg['text'], prior_messages=conversation[:])
                conversation.append(msg['text'])
            else:
                start = time.perf_counter()
            goal = _goals(ledger, events)
            if belief is not None and shield:
                action, detail, v, sel = shielded_step(before, h, ledger, events, epoch, belief, tick, said, shield_log)
            else:
                if events['ended'] and (any(before['caps_bps']) or not v2 or not (goal['delay_ms'] or goal['min_mbps'])):
                    rec = m.escape(before, h); action = rec['actions'][0] if rec['ok'] and rec['actions'] else {'hold': True}; detail = dict(recovery=rec)
                elif planner == 'obligation':
                    action, detail = P.plan(before, h, L.upgrade(ledger), events, epoch, horizon=2)
                else:
                    action, detail = m.plan(before, h, goal, horizon=2)
                v, sel = m.select(action, before, h)
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed >= contract['budgets']['proposal_ms']:
                raise ValueError('numerical proposal budget')
            ack = env.apply(v['target']); after = deepcopy(env.state); h = m.advance(h, before, v['target']); driver = (time.perf_counter() - start) * 1000
            obs = _public(ledger); outcomes = observe_deadlines(obs, after, epoch, outcomes)
            row = dict(tick=tick, before=before, after=after, history_before=hb, history_after=deepcopy(h), ledger=deepcopy(ledger), goal=goal, scheduled_fired=fired,
                       public_events=events, transaction=trans, status=status, obligations=obs, deadline_outcomes=deepcopy(outcomes),
                       selected=v, selection=sel, detail=detail, ack=ack, proposal_ms=elapsed, driver_ms=driver, actual_hard_sla=m.sla(after))
            save(root / f'tick_{tick:03d}.json', row); rows.append(row)
            if not m.sla(after) or driver >= contract['budgets']['ack_ms']:
                raise ValueError('hard SLA or ACK budget')
            if time.monotonic() - cell_start > contract['budgets']['cell_seconds']:
                raise ValueError('cell time budget')
        assert env.close() == 0; env = None; report['ok'] = True
    except INFRA_EXCEPTIONS as exc:
        report.update(error=repr(exc), failure_class='infrastructure')
    except Exception as exc:  # scientific aborts and anything else count against the arm
        report.update(error=repr(exc), failure_class='scientific')
    finally:
        if env is not None:
            env.abort()
        report.update(native_intervals=len(rows), missing_intervals=contract['ticks'] - len(rows))
        if belief is not None and not shield:
            report['belief'] = dict(belief=belief.name, release_tick=release_tick(item, contract), last_e_hat=getattr(belief, 'last_e_hat', None), shield=False)
        if belief is not None and shield:
            report['shield'] = dict(belief=belief.name, release_tick=release_tick(item, contract), fallbacks=sum(x['fallback'] is not None for x in shield_log),
                                    fallback_rejected=sum(x['fallback_rejected'] for x in shield_log), invariant_broken_before_event=sum(x['invariant_before'] is False and not x['ended'] for x in shield_log),
                                    belief_at_event=next((x['earliest'] for x in reversed(shield_log) if not x['ended']), None) if any(x['ended'] for x in shield_log) else None,
                                    admissible_before_event=next((x.get('admissible_selected') for x in reversed(shield_log) if not x['ended']), None) if any(x['ended'] for x in shield_log) else None,
                                    distance_at_event=next((x['distance'] for x in shield_log if x['ended']), None), log=shield_log,
                                    forecaster=getattr(belief, 'log', None))
        report['metric'] = metric(rows, item, contract, v2) if report['failure_class'] != 'infrastructure' else None
        save(root / 'report.json', report)
    return report, rows


def shielded_step(before, h, ledger, events, epoch, belief, tick, said, log):
    """One interval of the release-event diagnostic controller (see recovery_shield)."""
    window = ledger['obligations']['recovery']['max_intervals']
    e = belief.earliest(tick, ledger, said)
    entry = dict(tick=tick, ended=events['ended'], earliest=e, window=window, fallback=None, fallback_rejected=False,
                 invariant_before=None if events['ended'] else S.invariant(before, h, e, window))
    if events['ended']:
        entry['distance'] = S.distance(before, h)[0]
        if any(before['caps_bps']):
            action = S.recovery_action(before, h); detail = dict(recovery='shortest_escape', distance=entry['distance'])
        else:   # recovered: keep every cap off
            action, detail = P.plan(before, h, L.upgrade(ledger), events, epoch, horizon=2, admissible=lambda tg: not any(tg['caps_bps']))
            action = action or {'hold': True}
        v, sel = m.select(action, before, h)
        if any(v['target']['caps_bps']) and not any(before['caps_bps']):
            entry['fallback_rejected'] = True
    else:
        ok = lambda tg: S.admissible(before, h, tg, e, window)
        action, detail = P.plan(before, h, L.upgrade(ledger), events, epoch, horizon=2, admissible=ok)
        v, sel = m.select(action, before, h)
        if not ok(v['target']):
            fb = S.fallback(before, h, e, window); entry['fallback'] = fb or 'none'
            vf = m.verify(fb, before, h) if fb else dict(ok=False, errors=['invariant does not hold at the current state'])
            if vf['ok']:
                v, sel = vf, dict(source='shield_fallback', proposal=sel.get('proposal'))
            else:
                entry['fallback_rejected'] = True; entry['fallback_errors'] = vf.get('errors')
        entry['admissible_selected'] = ok(v['target'])
    entry['selected'] = v['target']; log.append(entry)
    return action, dict(detail or {}, shield=entry), v, sel


def annotated_ledger_at(tick, item, contract, v2=False):
    """Contract the administrator actually asked for at ``tick``, per independent annotation."""
    ledger = base_ledger(item, contract, v2)
    msgs = item_requests(item, contract)
    for tk in range(tick + 1):
        for msg in msgs:
            if msg['tick'] == tk:
                ref = msg['reference']
                req = dict(base_version=ledger['version'], obligation_id=ref['obligation_id'], field=ref['field'], value=ref['value'])
                ledger = L.propose(ledger, req) if v2 else propose(ledger, req, 'patch')
        if v2:
            ev = {'ended': tk >= release_tick(item, contract), 'never': False}
            for idx in L.due_scheduled(ledger, tk, ev):
                ledger = L.apply_scheduled(ledger, idx, True)
    return ledger


DEADLINE_TIMINGS = ('state', 'legacy')


def deadline_due(row, deadline_tick, timing='state'):
    """Interval whose outcome carries the deadline check.

    ``state`` (default, 2026-09-24 fix of review 108 section 3.1): the deadline "by tick D" is judged on the
    observation at state tick D, i.e. ``after`` of interval D-1 -- the same instant at which the runtime latches
    deadline evidence (observe_deadlines), and the horizon the planner and static rule use.
    ``legacy``: the pre-fix rule that read ``after`` of interval D (state tick D+1), one interval late."""
    if timing == 'legacy':
        return row['tick'] == deadline_tick
    return row['after']['tick'] == deadline_tick


def obligation_outcomes(row, ledger, epoch, contract, item=None, deadline_timing='state'):
    """{obligation name: met?} for the obligations valid at this interval; priority is a preference, not an obligation."""
    tick = row['tick']; after = row['after']; events = row['public_events']
    rows = {r['name']: r for r in after['services']}
    out = {}
    for name, q in ledger['obligations'].items():
        if name.endswith('.qos') and not events.get(q['release_event'], False):
            r = rows[q['service']]
            out[name] = not (r['delay_ms'] > q['delay_ms'] + 1e-9 or r['throughput_mbps'] < q['min_mbps'] - 1e-9)
    d = ledger['obligations']['backup.deadline']
    if deadline_due(row, d['deadline_tick'], deadline_timing):
        out['backup.deadline'] = rows['backup']['rx_bytes'] - epoch[m.NAMES.index('backup')] >= d['total_bytes']
    rec = ledger['obligations']['recovery']
    if events['ended'] and tick >= (release_tick(item, contract) if item else contract['release_tick']) + rec['max_intervals'] - 1:
        out['recovery'] = not any(after['caps_bps'])
    return out


def interval_met(row, item, contract, epoch, v2=False, deadline_timing='state'):
    """Every obligation valid at this interval is met by realised QoS."""
    if not m.sla(row['after']):
        return False
    return all(obligation_outcomes(row, annotated_ledger_at(row['tick'], item, contract, v2), epoch, contract, item, deadline_timing).values())


def weighted_interval(row, item, contract, epoch, v2=False, deadline_timing='state'):
    """Priority-weighted share of obligation weight met this interval (secondary; deadline/recovery carry the top weight)."""
    if not m.sla(row['after']):
        return 0.0
    ledger = annotated_ledger_at(row['tick'], item, contract, v2)
    w = L.weights(ledger) if v2 else {s: 1 for s in L.SERVICES}; top = max(w.values())
    outcomes = obligation_outcomes(row, ledger, epoch, contract, item, deadline_timing)
    total = 0.0; got = 0.0
    for name, ok in outcomes.items():
        weight = w[ledger['obligations'][name]['service']] if name.endswith('.qos') else top
        total += weight; got += weight * ok
    return got / total if total else 1.0


def secondaries(rows, item, contract, epoch, v2=False, deadline_timing='state'):
    """Per-episode secondary outcomes used by the acceptance gates: hard-SLA violation intervals, deadline
    (met / shortfall bytes / not observed because the episode ended earlier) and recovery violation intervals,
    all judged on the independent annotation, never on what the arm accepted."""
    hard = sum(not m.sla(r['after']) for r in rows)
    deadline = dict(met=None, shortfall_bytes=None, observed=False); recovery = 0
    for r in rows:
        ledger = annotated_ledger_at(r['tick'], item, contract, v2); out = obligation_outcomes(r, ledger, epoch, contract, item, deadline_timing)
        if 'backup.deadline' in out:
            d = ledger['obligations']['backup.deadline']; got = r['after']['services'][m.NAMES.index('backup')]['rx_bytes'] - epoch[m.NAMES.index('backup')]
            deadline = dict(met=out['backup.deadline'], shortfall_bytes=max(0, d['total_bytes'] - got), observed=True)
        recovery += out.get('recovery') is False
    if not deadline['observed'] and len(rows) < contract['ticks']:
        deadline = dict(met=False, shortfall_bytes=None, observed=False)  # episode ended before the deadline observation: counted as missed
    return dict(hard_sla_violation_intervals=hard, deadline_met=deadline['met'], deadline_shortfall_bytes=deadline['shortfall_bytes'], deadline_observed=deadline['observed'],
                recovery_violation_intervals=recovery)


def metric(rows, item, contract, v2=False, deadline_timing='state'):
    if not rows:
        return dict(met=0, intervals=contract['ticks'], satisfaction=0.0, weighted=0.0, missing=contract['ticks'], deadline_timing=deadline_timing,
                    hard_sla_violation_intervals=0, deadline_met=False, deadline_shortfall_bytes=None, deadline_observed=False, recovery_violation_intervals=0)
    epoch = [f['rx_bytes'] for f in rows[0]['before']['services']]
    met = sum(interval_met(r, item, contract, epoch, v2, deadline_timing) for r in rows)
    weighted = sum(weighted_interval(r, item, contract, epoch, v2, deadline_timing) for r in rows) / contract['ticks']
    return dict(met=met, intervals=contract['ticks'], satisfaction=met / contract['ticks'], weighted=weighted, missing=contract['ticks'] - len(rows), deadline_timing=deadline_timing,
                **secondaries(rows, item, contract, epoch, v2, deadline_timing))


def bootstrap_ci(diffs, resamples=10000, seed=0, alpha=0.05):
    if not diffs:
        return None
    rng = random.Random(seed); n = len(diffs); means = []
    for _ in range(resamples):
        means.append(statistics.fmean(rng.choice(diffs) for _ in range(n)))
    means.sort()
    return dict(mean=statistics.fmean(diffs), lower=means[int(alpha / 2 * resamples)], upper=means[int((1 - alpha / 2) * resamples) - 1], n=n)


def _gate_totals(metrics):
    """Sums of the gate quantities over paired items; None when any metric predates the secondaries."""
    if any('recovery_violation_intervals' not in x for x in metrics):
        return None
    return dict(hard_sla_violation_intervals=sum(x['hard_sla_violation_intervals'] for x in metrics),
                deadline_misses=sum(x['deadline_met'] is not True for x in metrics),
                deadline_shortfall_bytes=sum(x['deadline_shortfall_bytes'] or 0 for x in metrics),
                recovery_violation_intervals=sum(x['recovery_violation_intervals'] for x in metrics))


AMENDMENT_D = ('advance also requires recovery violation intervals of the decisive arm not above the rule arm over the '
               'same paired items; the average satisfaction may not compensate a deadline or recovery regression')


def amendment_d_adopted(contract):
    return any(a.get('id') == 'D' and str(a.get('status', '')).startswith('ADOPTED') for a in contract.get('amendments', []))


def analyse(reports, contract):
    """Paired differences arm minus rule over items with a non-infrastructure outcome in both arms.

    The decisive arm is read from ``contract['analysis']['decisive_arm']`` (fix of review 108 section 3.2; it was
    hard-coded to qwen7b_full_rebuild). The contract's advance clause requires no increase in hard-SLA violation
    intervals and a deadline shortfall that is not worse; both gates are now executed, not only mentioned.
    Amendment D (adopted 2026-09-25) adds a recovery gate; it is enforced when the contract lists D as ADOPTED,
    so archived contract copies without D keep their original rule. ``decision_without_D`` is always reported."""
    by = {}
    for rep in reports:
        if rep['failure_class'] != 'infrastructure':
            by.setdefault(rep['arm'], {})[rep['item']] = rep['metric']
    rule = by.get(RULE_ARM, {})
    out = dict(items_per_arm={a: len(v) for a, v in by.items()}, paired={})
    for arm, vals in by.items():
        if arm == RULE_ARM:
            continue
        ids = [i for i in vals if i in rule]
        diffs = [vals[i]['satisfaction'] - rule[i]['satisfaction'] for i in ids]
        out['paired'][arm] = dict(bootstrap=bootstrap_ci(diffs, contract['analysis']['resamples'], contract['analysis']['seed']), diffs=diffs,
                                  gates=dict(arm=_gate_totals([vals[i] for i in ids]), rule=_gate_totals([rule[i] for i in ids])))
    decisive = contract['analysis'].get('decisive_arm', 'qwen7b_full_rebuild'); out['decisive_arm'] = decisive
    entry = out['paired'].get(decisive, {}); main = entry.get('bootstrap')
    n_min = contract['sample_size']['minimum_paired_items']
    g = entry.get('gates') or {}; ga, gr = g.get('arm'), g.get('rule')
    if ga is None or gr is None:
        gates = dict(evaluable=False)
    else:
        gates = dict(evaluable=True, hard_sla_not_increased=ga['hard_sla_violation_intervals'] <= gr['hard_sla_violation_intervals'],
                     deadline_not_worse=ga['deadline_misses'] <= gr['deadline_misses'] and ga['deadline_shortfall_bytes'] <= gr['deadline_shortfall_bytes'],
                     recovery_not_worse_proposed_D=ga['recovery_violation_intervals'] <= gr['recovery_violation_intervals'])
    out['gates'] = gates
    if main is None or main['n'] < n_min:
        out['decision'] = f'not_decidable: fewer than {n_min} paired items'
    elif main['lower'] > 0:
        if not gates.get('evaluable'):
            out['decision'] = f'advance on the mean only: {decisive} minus rule lower bound > 0, but the secondary gates are not evaluable from these reports'
        elif gates['hard_sla_not_increased'] and gates['deadline_not_worse']:
            out['decision'] = f'advance: {decisive} minus rule lower bound > 0; hard-SLA and deadline gates hold'
        else:
            out['decision'] = f'blocked: {decisive} minus rule lower bound > 0 but a contract gate fails (hard SLA or deadline)'
    elif main['upper'] < 0:
        out['decision'] = f'negative: rule beats {decisive}; language claim on this task closed'
    else:
        out['decision'] = 'tie: interval covers 0; write up and stop, no pivot before window end'
    base = out['decision']
    if base.startswith('advance:'):
        with_d = base if gates['recovery_not_worse_proposed_D'] else f'blocked (amendment D): recovery violation intervals of {decisive} exceed the rule arm'
    elif base.startswith('advance on the mean only'):
        with_d = base + '; the amendment D recovery gate is not evaluable either'
    else:
        with_d = base
    adopted = amendment_d_adopted(contract)
    out.update(decision=with_d if adopted else base, decision_without_D=base, decision_under_proposed_D=with_d,   # last key kept for older summaries
               amendment_D=dict(adopted=adopted, rule=AMENDMENT_D))
    return out
