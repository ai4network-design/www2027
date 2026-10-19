"""Fixed diagnostic adapters; no oracle intent passed into the receiver.

Foundation and old parsers are untouched. Supplied prior state is accepted system
state. Request scope is inferred from raw language by the tested method itself.
"""
import json,re
from copy import deepcopy
from .multibusiness_revision import propose,verify_scope

COMMON='''You revise an accepted network intent contract from the administrator's current message. Return JSON only. Preserve every previously accepted obligation unless this message explicitly revises it, including other services, deadlines, release events, recovery and original epoch. Interpret the current message using the provided conversation and prior accepted state. Do not use network feasibility to change what the administrator requested; a separate engine decides feasibility. This task supports one changed numeric field per message. Services query, video, backup are the public business names. QoS delay_ms uses milliseconds and min_mbps uses Mbps. The request object has exactly base_version, obligation_id, field, value. For a QoS update obligation_id is service.qos. Use the current accepted version as base_version. Do not return reasoning, Markdown or examples.'''
FULL=' Return exactly {"request": REQUEST, "candidate": COMPLETE_UPDATED_LEDGER}. The candidate must include version incremented by one, the unchanged epoch_tick, and every entry and field of obligations from the accepted ledger, with only the requested field changed.'
PATCH=' Return exactly {"request": REQUEST}. The engine copies the previous ledger and changes only that requested field.'

def messages(ledger,current,conversation,arm):
 if arm not in ('full_rebuild','patch'):raise ValueError('model arm')
 return [dict(role='system',content=COMMON+(FULL if arm=='full_rebuild' else PATCH)),dict(role='user',content=json.dumps(dict(accepted_ledger=ledger,prior_administrator_messages=conversation,current_administrator_message=current,public_businesses=['query','video','backup'],public_event=dict(ended='conference has ended')),ensure_ascii=False,sort_keys=True))]

def decode(raw):
 def pairs(items):
  out={}
  for k,v in items:
   if k in out:raise ValueError('duplicate JSON key')
   out[k]=v
  return out
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite')))

def receive(old,raw,arm):
 obj=decode(raw)
 if type(obj) is not dict or set(obj)!=({'request','candidate'} if arm=='full_rebuild' else {'request'}):raise ValueError('output schema')
 request=obj['request']
 if arm=='full_rebuild':candidate=obj['candidate'];verify_scope(old,candidate,request)
 elif arm in ('patch','rule'):candidate=propose(old,request,'patch')
 else:raise ValueError('unknown arm')
 return request,deepcopy(candidate)

def rule_request(ledger,text):
 """Restricted transparent rule: one latency clause, nearest preceding named
 business, final quantity in that clause (supports from-old-to-new). Abstain on
 unsupported or ambiguous text. No case ID, gold label or stored answer table.
 """
 matches=list(re.finditer(r'(?:平均)?时延上限',text))
 if len(matches)!=1:raise ValueError('one explicit latency field required')
 field=matches[0];names=list(re.finditer(r'\b(query|video|backup)\b',text[:field.start()]))
 if not names:raise ValueError('no preceding public business')
 service=names[-1].group(1)
 clause=re.split(r'[。；;，,]',text[field.end():],maxsplit=1)[0]
 quantities=list(re.finditer(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)(?![A-Za-z])',clause))
 if not quantities:raise ValueError('missing time quantity')
 if len(quantities)>2 or len(quantities)==2 and not ('从' in clause and ('改为' in clause or '改成' in clause)):raise ValueError('ambiguous quantities')
 q=quantities[-1];v=float(q.group(1))*(1000 if q.group(2) in ('s','秒') else 1)
 return dict(base_version=ledger['version'],obligation_id=service+'.qos',field='delay_ms',value=v)
