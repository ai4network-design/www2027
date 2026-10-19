"""Language interfaces for ledger v2 (deadline, recovery window, priority).

* ``rule_request`` : transparent same-information rule; deadline / recovery /
  priority patterns first, then the v1 delay/throughput rule. Written before any
  independent corpus exists. Abstains (ValueError) otherwise.
* ``messages``     : prompt for the frozen instruct models; full-ledger output.
* ``receive``      : strict decode of the model output against the v2 scope check.
"""
import json
import re
from copy import deepcopy

from . import obligation_ledger as L
from .open_rule import rule_request as rule_v1, ALIASES, DELAY_WORDS, RATE_WORDS

COMMON = '''You revise an accepted network intent contract from the administrator's current message. Return JSON only. Preserve every previously accepted obligation unless this message explicitly revises it. Interpret the message using the conversation and the prior accepted state. Do not use network feasibility to change what the administrator requested; a separate engine decides feasibility. Exactly one field changes per message. Obligations and their revisable fields: "<service>.qos" with numeric "delay_ms" (milliseconds) or "min_mbps" (Mbps) for services query, video, backup; "backup.deadline" with integer "total_bytes" (bytes; 1 MB = 1000000 bytes) or integer "deadline_tick" (the scenario clock counts minutes, tick N means "by minute N"); "recovery" with integer "max_intervals" (how many minutes after the conference ends all temporary caps must be removed; "immediately" means 1); "priority" with "order", a list of the three service names from most to least important (services not mentioned keep their relative default order query, video, backup after the mentioned ones); "release_estimate" with integer "earliest_tick", the earliest minute at which the administrator says the conference can end: a relative statement ("about 8 more minutes") is added to the current minute given as current_tick, and an approximate wording ("about", "roughly", "大约", "左右") subtracts one minute of safety margin; an exact statement subtracts nothing. The request object has exactly base_version, obligation_id, field, value; base_version is the current accepted version. Return exactly {"request": REQUEST, "candidate": COMPLETE_UPDATED_LEDGER}; the candidate must contain version incremented by one, unchanged epoch_tick, and every obligation and field of the accepted ledger with only the requested field changed (add a "priority" obligation only when the request sets it). No reasoning, Markdown or examples.'''


def messages(ledger, current, conversation, current_tick=None):
    return [dict(role='system', content=COMMON),
            dict(role='user', content=json.dumps(dict(accepted_ledger=ledger, prior_administrator_messages=conversation, current_administrator_message=current, current_tick=current_tick,
                                                      public_businesses=list(L.SERVICES), public_event=dict(ended='conference has ended')), ensure_ascii=False, sort_keys=True))]


def decode(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    text = raw.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        text = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite')))


def receive(old, raw):
    obj = decode(raw)
    if type(obj) is not dict or set(obj) != {'request', 'candidate'}:
        raise ValueError('output schema')
    request, candidate = obj['request'], obj['candidate']
    L.verify_scope(old, candidate, request)
    return request, deepcopy(candidate)


# --- two-stage compilation: type first, then value ---------------------------
TYPES = {
    'query.qos.delay_ms': 'the administrator sets an average latency ceiling for the query service (milliseconds)',
    'query.qos.min_mbps': 'the administrator sets a bandwidth floor for the query service (Mbps)',
    'video.qos.delay_ms': 'latency ceiling for the video service (milliseconds)',
    'video.qos.min_mbps': 'bandwidth floor for the video service (Mbps)',
    'backup.qos.delay_ms': 'latency ceiling for the backup service (milliseconds)',
    'backup.qos.min_mbps': 'bandwidth floor for the backup service (Mbps)',
    'backup.deadline.deadline_tick': 'the minute by which the backup transfer must be COMPLETE (a deadline on the backup job itself)',
    'backup.deadline.total_bytes': 'the size of the backup transfer in bytes',
    'recovery.max_intervals': 'how many minutes after the conference ends all temporary caps must be removed',
    'priority.order': 'which services matter most when they compete',
    'release_estimate.earliest_tick': 'when the administrator expects the CONFERENCE ITSELF to end (not a backup deadline): the earliest minute it can end',
    'scheduled.add': 'a revision that should take effect LATER: after the conference ends ("散会后", "once the meeting is over") or from a given minute ("从第 6 分钟起", "starting at minute 6"); the message names a trigger and one field change',
    'none': 'the message does not revise any of the above',
}
STAGE1 = ('You classify one administrator message about a three-service network (query, video, backup). Choose exactly one obligation type from this list and return JSON only, as {"obligation": TYPE}:\n'
          + '\n'.join(f'- {k}: {v}' for k, v in TYPES.items())
          + '\nDo not output a value. Do not explain. Use the conversation only to resolve references.')
STAGE2 = {
    'delay_ms': 'Return JSON only, {"value": NUMBER}: the latency ceiling in milliseconds stated in the message (convert seconds to milliseconds; 0.02 s = 20).',
    'min_mbps': 'Return JSON only, {"value": NUMBER}: the bandwidth floor in Mbps stated in the message (kbps/1000, MB/s*8, Gbps*1000).',
    'deadline_tick': 'Return JSON only, {"value": INTEGER}: the minute by which the backup must be complete ("第 6 分钟前" -> 6, "by minute 7" -> 7).',
    'total_bytes': 'Return JSON only, {"value": INTEGER}: the backup size in bytes (1 MB = 1000000).',
    'max_intervals': 'Return JSON only, {"value": INTEGER}: minutes allowed after the conference ends to remove all caps; "immediately" -> 1.',
    'order': 'Return JSON only, {"value": [SERVICE, SERVICE, SERVICE]}: the three service names query, video, backup from most to least important; services not mentioned keep their default relative order query, video, backup after the mentioned ones.',
    'earliest_tick': 'Return JSON only, {"value": INTEGER}: the earliest minute the conference can end. A relative statement ("about 8 more minutes") is current_tick plus the minutes; an absolute statement ("minute 10", "第 10 分钟") is that minute. If the wording is approximate (about, roughly, 大约, 左右) subtract 1; if exact subtract nothing. current_tick is given.',
    'add': 'Return JSON only, {"value": {"trigger": {"kind": "event", "event": "ended"} or {"kind": "time", "at_tick": INTEGER}, "request": {"obligation_id": ID, "field": FIELD, "value": VALUE}}}. obligation_id is one of query.qos, video.qos, backup.qos (fields delay_ms in milliseconds, min_mbps in Mbps), backup.deadline (deadline_tick, total_bytes), recovery (max_intervals), priority (order). "after the conference / 散会后 / 会议一结束" is the event trigger; "from minute N / 从第 N 分钟起" is the time trigger with at_tick N.',
}


V1_TYPES = {k: v for k, v in TYPES.items() if k.endswith('.delay_ms') or k.endswith('.min_mbps') or k == 'none'}


def stage1_prompt(types):
    return ('You classify one administrator message about a three-service network (query, video, backup). Choose exactly one obligation type from this list and return JSON only, as {"obligation": TYPE}:\n'
            + '\n'.join(f'- {k}: {v}' for k, v in types.items())
            + '\nIf the message says WHEN the change should start (after the conference / 散会后 / from minute N / 从第 N 分钟起), the type is scheduled.add, not the inner field. Do not output a value. Do not explain. Use the conversation only to resolve references.')


def stage1_messages(ledger, current, conversation, current_tick=None, types=None):
    return [dict(role='system', content=stage1_prompt(types or TYPES)),
            dict(role='user', content=json.dumps(dict(prior_administrator_messages=conversation, current_administrator_message=current, current_tick=current_tick,
                                                      accepted_obligations=sorted(ledger['obligations'])), ensure_ascii=False, sort_keys=True))]


def stage2_messages(ledger, current, conversation, current_tick, obligation_type):
    field = obligation_type.split('.')[-1]
    return [dict(role='system', content=f'The message revises {obligation_type}: {TYPES[obligation_type]}. ' + STAGE2[field]),
            dict(role='user', content=json.dumps(dict(prior_administrator_messages=conversation, current_administrator_message=current, current_tick=current_tick,
                                                      current_value=_current_value(ledger, obligation_type)), ensure_ascii=False, sort_keys=True))]


def _current_value(ledger, obligation_type):
    parts = obligation_type.split('.')
    key, field = '.'.join(parts[:-1]), parts[-1]
    if key == 'scheduled':
        return {k: {f: v for f, v in o.items() if f in ('delay_ms', 'min_mbps', 'deadline_tick', 'total_bytes', 'max_intervals')} for k, o in ledger['obligations'].items() if k.endswith('.qos') or k in ('backup.deadline', 'recovery')}
    return ledger['obligations'].get(key, {}).get(field)


def parse_stage1(raw, lenient=False, types=None):
    """Strict: valid JSON {"obligation": "<type>"}. Lenient: additionally accept the bare closed-list identifier
    without quotes, e.g. {"obligation": backup.qos.delay_ms}; the identifier must still match the list exactly."""
    try:
        obj = decode(raw)
    except ValueError:
        if not lenient:
            raise ValueError('stage 1: invalid JSON')
        m_ = re.fullmatch(r'\s*\{\s*"obligation"\s*:\s*([A-Za-z_.0-9]+)\s*\}\s*', raw.strip().strip('`').replace('json', '', 1) if raw.strip().startswith('```') else raw)
        if not m_ or m_.group(1) not in (types or TYPES):
            raise ValueError('stage 1: obligation type not in the closed list')
        return m_.group(1)
    if type(obj) is not dict or set(obj) != {'obligation'} or obj['obligation'] not in (types or TYPES):
        raise ValueError('stage 1: obligation type not in the closed list')
    return obj['obligation']


def assemble(ledger, obligation_type, raw_value, propose_fn=None):
    """``propose_fn(ledger, request)`` defaults to the v2 ledger; the v1 ledger passes its own patch function."""
    if obligation_type == 'none':
        raise ValueError('stage 1: no revisable obligation in this message')
    obj = decode(raw_value)
    if type(obj) is not dict or set(obj) != {'value'}:
        raise ValueError('stage 2: value schema')
    parts = obligation_type.split('.')
    key, field = '.'.join(parts[:-1]), parts[-1]
    value = obj['value']
    if field in ('deadline_tick', 'total_bytes', 'max_intervals', 'earliest_tick'):
        if not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError('stage 2: integer required')
        value = int(value)
    if field == 'add':
        if not isinstance(value, dict) or set(value) - {'trigger', 'request'} or not {'trigger', 'request'} <= set(value):
            raise ValueError('stage 2: scheduled value needs trigger and request')
        tr = value['trigger']
        if isinstance(tr, dict) and tr.get('kind') == 'time' and isinstance(tr.get('at_tick'), (int, float)):
            tr['at_tick'] = int(tr['at_tick'])
        rq = value['request']
        if isinstance(rq, dict) and rq.get('field') in ('deadline_tick', 'total_bytes', 'max_intervals') and isinstance(rq.get('value'), (int, float)):
            rq['value'] = int(rq['value'])
    request = dict(base_version=ledger['version'], obligation_id=key, field=field, value=value)
    return request, (propose_fn or L.propose)(ledger, request)


# --- transparent rule -------------------------------------------------------
DEADLINE_WORDS = r'(?:截止|之前完成|前完成|前做完|前跑完|deadline|finish(?:ed)? by|done by|complete(?:d)? by|by minute|before minute)'
BYTES_WORDS = r'(?:数据量|大小|总量|size|volume|amount)'
RECOVERY_WORDS = r'(?:恢复|撤掉|撤销|解除|取消限速|restore|lift|remove (?:the )?(?:caps|limits|throttl)|resume)'
PRIORITY_WORDS = r'(?:优先|首先保|先保|其次|最后|priorit|first|then|last|most important|least important)'
IMMEDIATE = r'(?:立刻|马上|立即|即刻|一结束就|immediately|right away|at once|as soon as)'
ESTIMATE_WORDS = r'(?:还要开|还要|还有|还得|才(?:会)?(?:结束|散会)|结束|散会|wraps? up|end(?:s|ing)?|finish(?:es)?|run until|last|more minutes|go on)'
APPROX = r'(?:大约|大概|左右|差不多|约|about|roughly|around|approximately|or so)'


def _services_in_order(text):
    found = []
    for name, aliases in ALIASES.items():
        for a in aliases:
            for x in re.finditer(re.escape(a), text, re.I):
                found.append((x.start(), name))
    order = []
    for _, name in sorted(found):
        if name not in order:
            order.append(name)
    return order


def estimate_request(ledger, text, current_tick):
    """Release-time estimate: relative ("还有 8 分钟", "8 more minutes") or absolute ("第 10 分钟", "until minute 10")."""
    if current_tick is None:
        raise ValueError('current tick required for a relative estimate')
    absolute = re.search(r'(?:第\s*(\d+)\s*分钟|(?:until|at|by)\s*minute\s*(\d+))', text, re.I)
    relative = re.search(r'(?:(\d+)\s*(?:分钟|分|minutes?|min))', text, re.I)
    if absolute:
        tick = int(next(g for g in absolute.groups() if g))
    elif relative:
        tick = current_tick + int(relative.group(1))
    else:
        raise ValueError('release estimate without a minute value')
    if re.search(APPROX, text, re.I):
        tick -= 1
    return dict(base_version=ledger['version'], obligation_id='release_estimate', field='earliest_tick', value=max(0, tick))


DEFER_EVENT = r'^(?:\s*(?:会议|散会|meeting|conference)?\s*(?:一结束|结束后|结束以后|散会后|散会之后|之后|过后|once (?:the )?(?:meeting|conference) (?:is over|ends|has ended)|after (?:the )?(?:meeting|conference)(?: ends| is over)?|when (?:the )?(?:meeting|conference) (?:ends|is over))\s*[,，:：]?\s*)'
DEFER_TIME = r'^(?:\s*(?:从第\s*(\d+)\s*分钟(?:起|开始)|第\s*(\d+)\s*分钟(?:起|以后|之后)|(?:starting|from|beginning)\s+(?:at|from)?\s*minute\s*(\d+)(?:\s+on(?:wards)?)?)\s*[,，:：]?\s*)'


def deferred_request(ledger, text, current_tick):
    ev = re.match(DEFER_EVENT, text, re.I); tm = re.match(DEFER_TIME, text, re.I)
    if not ev and not tm:
        return None
    rest = text[(ev or tm).end():]
    try:
        inner = rule_request(ledger, rest, current_tick)
    except ValueError:
        return None  # let the plain rule decide (or abstain) on the whole message
    if inner['obligation_id'] in ('scheduled', 'recovery', 'release_estimate'):
        return None  # "after the meeting lift the caps" IS the recovery obligation, not a deferred revision
    trigger = dict(kind='event', event='ended') if ev else dict(kind='time', at_tick=int(next(g for g in tm.groups() if g)))
    return dict(base_version=ledger['version'], obligation_id='scheduled', field='add', value=dict(trigger=trigger, request=dict(obligation_id=inner['obligation_id'], field=inner['field'], value=inner['value'])))


def rule_request(ledger, text, current_tick=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('empty message')
    v = ledger['version']
    deferred = deferred_request(ledger, text, current_tick)
    if deferred is not None:
        return deferred
    if re.search(r'会议|meeting|conference|散会', text, re.I) and re.search(ESTIMATE_WORDS, text, re.I) and not re.search(DELAY_WORDS + '|' + RATE_WORDS + '|' + RECOVERY_WORDS + '|' + PRIORITY_WORDS + '|截止|deadline', text, re.I):
        return estimate_request(ledger, text, current_tick)
    has_deadline = re.search(DEADLINE_WORDS, text, re.I) or (re.search(BYTES_WORDS, text, re.I) and re.search(r'\d\s*(?:MB|兆字节|megabytes?)', text, re.I))
    has_recovery = re.search(RECOVERY_WORDS, text, re.I)
    has_priority = re.search(PRIORITY_WORDS, text, re.I)
    if sum(map(bool, (has_deadline, has_recovery, has_priority))) > 1:
        raise ValueError('more than one obligation kind mentioned')
    if has_deadline:
        minute = re.search(r'(?:第\s*(\d+)\s*分钟|minute\s*(\d+)|(\d+)\s*分钟(?:之)?(?:前|内)|within\s*(\d+)\s*minutes?)', text, re.I)
        mb = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(?:MB|兆字节|megabytes?)(?![A-Za-z])', text, re.I)
        if minute and mb:
            raise ValueError('deadline tick and size in one message')
        if minute:
            n = int(next(g for g in minute.groups() if g))
            return dict(base_version=v, obligation_id='backup.deadline', field='deadline_tick', value=n)
        if mb:
            return dict(base_version=v, obligation_id='backup.deadline', field='total_bytes', value=int(round(float(mb.group(1)) * 1_000_000)))
        raise ValueError('deadline without minute or size')
    if has_recovery:
        if re.search(IMMEDIATE, text, re.I):
            return dict(base_version=v, obligation_id='recovery', field='max_intervals', value=1)
        n = re.search(r'(\d+)\s*(?:分钟|minutes?)\s*(?:内|之内|以内|within)?', text, re.I) or re.search(r'within\s*(\d+)', text, re.I)
        if n:
            return dict(base_version=v, obligation_id='recovery', field='max_intervals', value=int(n.group(1)))
        raise ValueError('recovery without window')
    if has_priority:
        order = _services_in_order(text)
        if not order:
            raise ValueError('priority without a named service')
        rest = [s for s in L.SERVICES if s not in order]
        return dict(base_version=v, obligation_id='priority', field='order', value=order + rest)
    return rule_v1(ledger, text)
