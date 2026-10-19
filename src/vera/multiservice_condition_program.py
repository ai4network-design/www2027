"""Model-written condition program; safe interpreter, not a natural-language parser."""
from decimal import Decimal
import re
from . import multiservice_control as m, multiservice_service_bound as original
from .multiservice_syntax import Builder, compile_tables as compile_original

HEADER = 'contracts/multiservice_condition_program_v1.txt'
TOKEN = re.compile(r'query|video|backup|>=|<|&&|\|\||!|\(|\)|(?:0|[1-9][0-9]*)(?:\.[0-9]+)?')

def messages(v, frontend=False):
    result = original.messages(v, frontend=frontend)
    if v['phase'] == 'action':
        result[0]['content'] = (m.ROOT / HEADER).read_text().strip()
    return result

def expression(text):
    """Whole-string recursive descent; no eval, recovery, raw-request matching or truth."""
    if type(text) is not str or not 1 <= len(text) <= 256:
        raise ValueError('bounded expression string')
    tokens = TOKEN.findall(text)
    if ''.join(tokens) != text or len(tokens) > 64:
        raise ValueError('invalid expression token')
    cursor = 0
    def peek(): return tokens[cursor] if cursor < len(tokens) else None
    def consume(value=None):
        nonlocal cursor
        token = peek()
        if token is None or (value is not None and token != value):
            raise ValueError('incomplete or invalid expression')
        cursor += 1
        return token
    def unary(depth):
        if depth > 8: raise ValueError('expression depth')
        if peek() == '!':
            consume('!'); return ('not', unary(depth + 1))
        if peek() == '(':
            consume('('); node = disjunction(depth + 1); consume(')'); return node
        service = consume()
        if service not in m.NAMES: raise ValueError('predicate service')
        relation = consume()
        if relation not in ('>=', '<'): raise ValueError('predicate comparison')
        raw = consume()
        if re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?', raw) is None:
            raise ValueError('finite decimal threshold')
        value = Decimal(raw)
        if not 0 <= value <= 1000000: raise ValueError('threshold range')
        return (relation, service, raw)
    def conjunction(depth):
        node = unary(depth)
        while peek() == '&&':
            consume('&&'); node = ('and', node, unary(depth))
        return node
    def disjunction(depth):
        node = conjunction(depth)
        while peek() == '||':
            consume('||'); node = ('or', node, conjunction(depth))
        return node
    node = disjunction(0)
    if cursor != len(tokens): raise ValueError('trailing expression tokens')
    return node

def parse(raw):
    value = m.decode(raw)
    if set(value) != {'service', 'cap', 'until'} or value['service'] not in m.NAMES:
        raise ValueError('exact complete program schema')
    rate = value['cap']
    if not m.finite(rate) or not 0 < rate <= m.DEMAND[m.NAMES.index(value['service'])]:
        raise ValueError('positive cap within source demand')
    d = Decimal(str(rate))
    if d * 1000 != (d * 1000).to_integral_value(): raise ValueError('millirate cap')
    expression(value['until'])
    return value

def evaluate(program, received_bytes):
    """Only model-owned numeric program + actual counters. No implicit action."""
    import json
    parse(json.dumps(program, separators=(',', ':'), allow_nan=False))
    return evaluate_expression(program['until'], received_bytes)


def evaluate_expression(text, received_bytes):
    """Shared expression evaluator independent of the historical cap-only wire."""
    if set(received_bytes) != set(m.NAMES) or any(type(x) is not int or x < 0 for x in received_bytes.values()):
        raise ValueError('three actual nonnegative integer byte counters')
    def walk(node):
        if node[0] == 'not': return not walk(node[1])
        if node[0] == 'and': return walk(node[1]) and walk(node[2])
        if node[0] == 'or': return walk(node[1]) or walk(node[2])
        actual = Decimal(received_bytes[node[1]])
        target = Decimal(node[2]) * 1000000
        return actual >= target if node[0] == '>=' else actual < target
    return walk(expression(text))

def grammar(phase):
    if phase == 'intent': return original.grammar(phase)
    if phase != 'action': raise ValueError('phase')
    b = Builder(); lit, seq, alt = b.literal, b.seq, b.either
    # A request-independent lexical expression loop, not a truth mask. The safe
    # parser validates precedence, operands and balance after complete JSON only.
    expr = alt(*(lit(s) for s in (*m.NAMES, '>=', '<', '&&', '||', '!', '(', ')')), b.number())
    b.eps[expr[1]].add(expr[0])
    packet = seq(lit('{"service":'), alt(*(lit('"'+n+'"') for n in m.NAMES)),
                 lit(',"cap":'), b.number(), lit(',"until":"'), expr, lit('"}'))
    return b.dfa(packet)

def compile_tables(tokenizer, output_vocab, denied_ids):
    return compile_original(tokenizer, output_vocab, denied_ids, grammar)
