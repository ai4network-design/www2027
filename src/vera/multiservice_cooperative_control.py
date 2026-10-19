"""Phase-aware existing numerical primitives; no request/condition evaluator.

MPC uses the original two-step sum of violation/gap cost. The compact controller
uses a terminal-first constructive reachability objective and minimal edits.
Both search the same public numerical knots and certify every deployed step.
Neither is a newly trained neural model; legacy six-action MLP is not imported.
"""
from copy import deepcopy
import time
from . import multiservice_control as m
from .multiservice_cooperative import goal_maps

def decide(state,history,spec,mode,lower,*,started=None,clock=time.perf_counter):
    if mode not in ('protect','restore') or lower not in ('mpc','certificate'):
        raise ValueError('explicit model subgoal and lower controller required')
    start=clock() if started is None else started
    before=deepcopy((state,history,spec)); goal=goal_maps(spec)
    if mode=='restore':
        cert=m.escape(state,history)
        action=cert['actions'][0] if cert['ok'] and cert['actions'] else {'hold':True}
        detail=dict(mode=mode,lower=lower,restoration_certificate=cert,checked=0,branches=0)
    else:
        actions=m.candidates(state,goal);best=None;checked=0;branches=0;bounded=False
        # Full two-step physical search, not direct text-to-six-label prediction.
        for index,a in enumerate(actions):
            if (clock()-start)*1000>=650:bounded=True;break
            v=m.verify(a,state,history);checked+=1
            if not v['ok']:continue
            one=m.goal_score(goal,v['prediction']);edited=int(v['target']!=m.config(state))
            ns,nh=m.successor(state,v['target'],history)
            for j,b in enumerate(m.candidates(ns,goal)):
                if (clock()-start)*1000>=650:bounded=True;break
                w=m.verify(b,ns,nh);checked+=1
                if not w['ok']:continue
                two=m.goal_score(goal,w['prediction']);edits=edited+int(w['target']!=m.config(ns))
                rank=(one[0]+two[0],one[1]+two[1],edits,index,j) if lower=='mpc' else (*two,edits,*one,index,j)
                branches+=1
                if best is None or rank<best['rank']:best=dict(rank=rank,first=a,second=b,first_gap=one,second_gap=two)
            if bounded:break
        action=None if best is None else best['first']
        detail=dict(mode=mode,lower=lower,checked=checked,branches=branches,bounded=bounded,best=best)
    if before!=(state,history,spec):raise ValueError('lower mutated actual inputs')
    selected,selection=m.select(action,state,history)
    detail.update(raw_action=action,model_goal=deepcopy(spec),elapsed_from_tick_ms=(clock()-start)*1000,
                  private_condition_inputs=0,trained_neural_controller=False)
    return selected,selection,detail
