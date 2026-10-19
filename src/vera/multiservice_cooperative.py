"""Separate model-owned numerical goal, raw condition and fresh feedback decision.

No natural-language reference parser and no task schedule in this controller.
The high-level two-valued subgoal does not restrict service/path/rate operations.
"""
from copy import deepcopy
import json
from . import multiservice_control as m, multiservice_quota_program as goal
from .multiservice_syntax import Builder, compile_tables as compile_original

C=json.loads((m.ROOT/'contracts/multiservice_cooperative_20260914.json').read_text())

def parse(raw, stage):
    if stage=='goal': return goal.parse(raw)
    value=json.loads(raw)
    if type(value) is not list or len(value)!=1 or value[0] not in ('protect','restore'):
        raise ValueError('one complete subgoal, not a numerical action')
    return value[0]

def grammar(phase):
    if phase=='intent':return goal.grammar('action')
    if phase!='action':raise ValueError('grammar stage')
    b=Builder()
    return b.dfa(b.either(b.literal('["protect"]'),b.literal('["restore"]')))

def compile_tables(tokenizer, output_vocab, denied_ids):
    return compile_original(tokenizer,output_vocab,denied_ids,grammar)

def view(request,state,history,epoch,spec=None,previous=None):
    if len(epoch)!=3:raise ValueError('three actual service counters')
    received={name:(state['services'][i]['rx_bytes']-epoch[i])/1e6 for i,name in enumerate(m.NAMES)}
    if any(x<0 for x in received.values()):raise ValueError('counter regression')
    return dict(stage='goal' if spec is None else 'strategy',administrator_request=request,
        request_version=0,observation_tick=state['tick'],epoch_rx_bytes=list(epoch),
        received_decimal_MB=received,model_owned_goal=deepcopy(spec),
        network=deepcopy(state),confirmed_history=deepcopy(history),previous_feedback=deepcopy(previous),
        public=m.view(request,state,history)['public'])

def messages(v):
    if v['stage']=='goal':
        header=(m.ROOT/goal.HEADER).read_text().strip()
        header+='\nInterpret the numerical protection objective, even when its release also depends on other services. Do not decide paths or rate ceilings.'
    elif v['stage']=='strategy':
        header=('You are the online semantic supervisor of a network controller. Read the administrator_request '
          'and actual received_decimal_MB since this request, your own numerical goal, and previous real execution feedback. '
          'Choose the CURRENT subgoal. Output exactly ["protect"] when the request still requires QoS protection, '
          'or ["restore"] when its release condition is satisfied. For combined conditions obey AND/OR and exceptions in '
          'the request; completion of one service does not imply completion of another. The lower controller computes '
          'safe numeric rate and route changes for your chosen subgoal. Protect may require preparation and migration; '
          'restore safely removes temporary ceilings. Do not output an action, explanation, tick-based schedule, or goal update.')
    else:raise ValueError('stage')
    return [dict(role='system',content=header),dict(role='user',content=m.canonical_json(v))]

def goal_maps(spec):
    # Numerical fields are model-owned, never filled from the experiment contract.
    spec=goal.parse(goal.encode(spec))
    return dict(delay_ms={spec['service']:spec['delay_ms']},min_mbps={spec['service']:spec['min_mbps']})
