"""Static remaining-rate/recovery rule. No MPC rollout and no case-specific IDs."""
from copy import deepcopy
import math,time
from . import multiservice_control as m
from .obligation_admission import validate

def assess(state,history,spec,mode,obligations,epoch_bytes,*,horizon=8,max_ms=500,clock=time.perf_counter,deadline_outcomes=None):
 start=clock();validate(obligations);before=deepcopy((state,history,spec,obligations,epoch_bytes))
 reasons=[];candidates=[];best=None;i=m.NAMES.index(spec['service'])
 for key,evidence in (deadline_outcomes or {}).items():
  if evidence['met'] is not True:reasons.append(dict(code='existing_deadline_not_met',obligation=key))
 # Static configs change only the goal service. Single-field deployment remains
 # the common MPC's responsibility; this rule is not an alternative controller.
 configs=[]
 if mode=='restore':configs=[m.config(state)]
 else:
  for path in m.PATHS:
   others=sum(min(m.DEMAND[j],state['caps_bps'][j]/1e6 or m.DEMAND[j]) for j in range(m.N) if j!=i and state['paths'][j]==path)
   rate=math.floor(min(m.DEMAND[i],m.C['path_min_mbps'][path-1]/1.025-others)*1000)/1000
   if rate<m.FLOOR[i]:continue
   target=m.config(state);target['paths'][i]=path;target['caps_bps'][i]=0 if rate==m.DEMAND[i] else round(rate*1e6)
   configs.append(target)
 for target in configs:
  prediction=m.predict({},target,robust=True);issues=[]
  if not m.robust_ok(target):issues.append('hard_capacity')
  f=prediction['services'][i]
  if mode=='protect' and (f['delay_ms']>spec['delay_ms'] or f['throughput_mbps']<spec['min_mbps']):issues.append('static_qos')
  uncapped=dict(paths=target['paths'][:],caps_bps=[0]*m.N)
  if not any(target['caps_bps']):recovery=0
  elif m.robust_ok(uncapped):recovery=2+sum(bool(x) for x in target['caps_bps'])-1
  else:
   escape_exists=False
   for p in m.PATHS:
    restored=dict(paths=target['paths'][:],caps_bps=[0]*m.N);restored['paths'][i]=p
    escape_exists=escape_exists or m.robust_ok(restored)
   recovery=5+sum(bool(x) for x in target['caps_bps']) if escape_exists else None
  if recovery is None or recovery>obligations['recovery']['max_intervals']:issues.append('static_recovery')
  checks=[]
  for item in obligations['deadlines']:
   if item['id'] in (deadline_outcomes or {}):continue
   k=m.NAMES.index(item['service']);remaining=max(0,item['total_bytes']-(state['services'][k]['rx_bytes']-epoch_bytes[k]));seconds=item['deadline_tick']-state['tick']
   required=remaining*8/seconds/1e6 if seconds>0 else (0 if remaining==0 else None)
   available=prediction['services'][k]['throughput_mbps']
   passed=required is not None and required<=available+1e-9
   checks.append(dict(id=item['id'],remaining_bytes=remaining,seconds=seconds,required_mbps=required,available_mbps=available,ok=passed))
   if not passed:issues.append('static_deadline')
  entry=dict(target=target,estimated_recovery_intervals=recovery,deadlines=checks,issues=issues)
  candidates.append(entry)
  if not issues and best is None:best=entry
 if best is None:reasons.append(dict(code='no_static_candidate'))
 elapsed=(clock()-start)*1000
 if elapsed>=max_ms:reasons.append(dict(code='assessment_budget_exhausted'))
 assert before==(state,history,spec,obligations,epoch_bytes)
 return dict(ok=not reasons,reasons=reasons,elapsed_ms=elapsed,candidates=candidates,chosen=best,model_scope='static single-service rate envelope and recovery bound, no rollout')
