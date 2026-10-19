"""Model-owned QoS quota and monotone release condition; no natural-language rules.

Single protected service, exact decimal-MB thresholds and bounded boolean trees.
Monotonicity makes an UNTIL release permanent on nondecreasing received counters.
Nonmonotone programs are rejected, not silently reinterpreted or repaired.
"""
from copy import deepcopy
import json
from . import multiservice_control as m, multiservice_quota as q, multiservice_quota_program as numeric
from .multiservice_condition_program import expression
from .multiservice_syntax import Builder, compile_tables as original_compile

C=json.loads((m.ROOT/'contracts/multiservice_conditional_quota_20260914.json').read_text())

HEADER = ('Read the original administrator request. Return exactly '
    '[service,quota_decimal_MB,max_delay_ms,min_throughput_Mbps,release_expression]. '
    'The expression uses query/video/backup received decimal MB since this request, '
    '>=, <, &&, ||, ! and parentheses. Express the complete release condition including '
    'every required service threshold. Preserve the original quota, not remaining traffic. '
    'Use a monotone release condition: once true it stays true as received counts increase. '
    'The numerical controller enforces QoS until your expression is true and safely removes '
    'temporary ceilings afterwards. Do not choose paths, caps, current modes, or explanations. '
    'JSON array only, no whitespace; no more than three decimal digits in the numerical goal fields.')


def validate_release(text):
    tree=expression(text)
    def increasing(node, positive=True):
        if node[0]=='not':return increasing(node[1],not positive)
        if node[0] in ('and','or'):return increasing(node[1],positive) and increasing(node[2],positive)
        return (node[0]=='>=')==positive
    if not increasing(tree):raise ValueError('nonmonotone release needs a different explicit temporal contract')
    return tree


def parse(raw, stage='goal'):
    if stage!='goal':raise ValueError('one complete contract per request version')
    value=m.decode('{"value":'+raw+'}')
    if set(value)!={'value'} or type(value['value']) is not list or len(value['value'])!=5:
        raise ValueError('five complete contract fields')
    row=value['value'];spec=numeric.parse(m.canonical_json(row[:4]));validate_release(row[4])
    return dict(spec=spec,release_expression=row[4])


def encode(value):
    if type(value) is not dict or set(value)!={'spec','release_expression'}:raise ValueError('complete conditional quota')
    validate_release(value['release_expression'])
    row=json.loads(numeric.encode(value['spec']))+[value['release_expression']]
    return m.canonical_json(row)


def establish(request,value,epoch,tick,call):
    value=parse(encode(value))
    return q.establish(request,value['spec'],epoch,tick,call,release_expression=value['release_expression'])


def accept(value,g):
    if value is None or not g['proposal_admitted'] or not g['complete_json_stop']:return None
    if g['stage']!='goal' or g['phase']!='intent' or not g['deployment_allowed'] or g['diagnostic_only']:
        raise ValueError('fresh paid conditional goal required')
    decoded=parse(g['raw_text'])
    if decoded!=value or g['parsed']!=value:raise ValueError('model-owned complete wire identity')
    return decoded


def view(request,state,history,epoch):
    # Compilation of an original demand, not re-estimation from current progress.
    if epoch['request_sha256']!=m.digest(request):raise ValueError('original request epoch identity')
    return dict(stage='goal',administrator_request=request,request_sha256=epoch['request_sha256'],
                origin_tick=epoch['origin_tick'],observation_tick=state['tick'])


def messages(v):
    if v['stage']!='goal':raise ValueError('contract stage')
    return [dict(role='system',content=HEADER),dict(role='user',content=m.canonical_json(v))]


def grammar(phase):
    if phase not in ('intent','action'):raise ValueError('grammar phase')
    b=Builder();lit,seq,alt=b.literal,b.seq,b.either
    expr=alt(*(lit(x) for x in (*m.NAMES,'>=','<','&&','||','!','(',')')),b.number())
    b.eps[expr[1]].add(expr[0])
    # Syntax only, independent of the request and desired answer. The full parser
    # checks balanced expressions, bounds and monotonicity after generation.
    return b.dfa(seq(lit('['),alt(*(lit('"'+n+'"') for n in m.NAMES)),lit(','),b.number(),
        lit(','),b.number(),lit(','),b.number(),lit(',"'),expr,lit('"]')))


def compile_tables(tokenizer,output_vocab,denied_ids):
    return original_compile(tokenizer,output_vocab,denied_ids,grammar)
