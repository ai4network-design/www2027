"""Transparent same-information rule for open administrator requests.

Written on 2026-09-23 BEFORE any open corpus item existed, so it cannot be
tuned to the held-out text. It is the strong comparator of the frozen protocol
(contracts/frozen_protocol_20260924_20261008.json). Covers the whole frozen
revision schema: one changed numeric field per message, delay_ms or min_mbps,
Chinese or English, unit conversion, "from old to new" phrasing. Abstains
(raises ValueError) on anything else. No case IDs, no answer tables.
"""
import re

ALIASES = {
    'query': ['query', 'queries', 'search', 'searches', '查询', '检索'],
    'video': ['video', 'conference', 'meeting', 'call', '视频', '会议', '画面'],
    'backup': ['backup', 'backups', 'copy', 'replication', '备份', '拷贝', '复制'],
}
DELAY_WORDS = r'(?:时延|延迟|响应时间|响应|latency|delay|response\s*time|lag)'
RATE_WORDS = r'(?:带宽|吞吐|速率|bandwidth|throughput|rate|speed)'
TIME_UNIT = r'(ms|毫秒|msec|milliseconds?|s|秒|秒钟|sec|seconds?)'
RATE_UNIT = r'(Mbps|mbps|Mb/s|MB/s|M|兆|Mbit/s|Mbit|kbps|Kbps|Gbps)'
NUMBER = r'(?<![\d.])(\d+(?:\.\d+)?)\s*'
SPLIT = r'[。；;！!\n]|(?:[.?]\s)'


def _mentions(text, words):
    return [x for x in re.finditer(words, text, re.I)]


def _service(text, at):
    """Nearest public business name (or alias) before ``at`` in the same message, else nearest after."""
    best = None
    for name, aliases in ALIASES.items():
        for a in aliases:
            for x in re.finditer(re.escape(a), text, re.I):
                d = at - x.start() if x.start() < at else (x.start() - at) + 10_000
                if best is None or d < best[0]:
                    best = (d, name)
    if best is None:
        raise ValueError('no public business named')
    return best[1]


def _quantity(clause, unit_pattern):
    q = list(re.finditer(NUMBER + unit_pattern + r'(?![A-Za-z])', clause, re.I))
    if not q:
        raise ValueError('missing quantity with unit')
    from_to = re.search(r'从|from', clause, re.I) and re.search(r'改为|改成|变成|调整为|调到|设为|to\b', clause, re.I)
    if len(q) > 2 or (len(q) == 2 and not from_to):
        raise ValueError('ambiguous quantities')
    return q[-1]


def rule_request(ledger, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('empty message')
    delay = _mentions(text, DELAY_WORDS)
    rate = _mentions(text, RATE_WORDS)
    if bool(delay) == bool(rate):
        raise ValueError('exactly one of delay or throughput must be mentioned')
    if re.search(r'截止|deadline|优先|priority|prioriti|如果|若|if\b|unless|撤回|取消|cancel|withdraw|恢复|restore|resume|不用管|ignore', text, re.I):
        raise ValueError('out of frozen revision scope')
    field = 'delay_ms' if delay else 'min_mbps'
    unit_pattern = TIME_UNIT if delay else RATE_UNIT
    # First mention whose own clause carries a quantity; a bare "response is slow" sentence is skipped.
    chosen = None
    for mention in (delay or rate):
        start = 0
        for x in re.finditer(SPLIT, text):
            if x.start() < mention.start():
                start = x.end()
            else:
                break
        end_match = re.search(SPLIT, text[mention.start():])
        clause = text[start:mention.start() + (end_match.start() if end_match else len(text) - mention.start())]
        if re.search(NUMBER + unit_pattern + r'(?![A-Za-z])', clause, re.I):
            chosen = (mention, clause)
            break
    if chosen is None:
        raise ValueError('missing quantity with unit')
    mention, clause = chosen
    service = _service(text, mention.start())
    q = _quantity(clause, unit_pattern)
    if field == 'delay_ms':
        value = float(q.group(1)) * (1000 if q.group(2).lower() in ('s', '秒', '秒钟', 'sec', 'second', 'seconds') else 1)
    else:
        u = q.group(2)
        scale = 0.001 if u.lower() == 'kbps' else 1000 if u.lower() == 'gbps' else 8 if u == 'MB/s' else 1
        value = float(q.group(1)) * scale
    if value <= 0:
        raise ValueError('non-positive value')
    return dict(base_version=ledger['version'], obligation_id=f'{service}.qos', field=field, value=value)
