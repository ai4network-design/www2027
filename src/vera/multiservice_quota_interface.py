"""Public dynamic telemetry and shared retention, never an intent interpreter."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_quota as q
from . import multiservice_progress_signals as progress
from . import multiservice_phase as phase
from . import multiservice_named as named
from . import multiservice_ledger as static

def view(request,s,h,epoch,ledger=None,previous=None):
    obs=progress.observe(request,s,epoch)
    if ledger is not None and ledger['epoch']!=epoch:raise ValueError('one immutable request epoch')
    dynamic=q.current(request,s,ledger)
    # Reuse only existing public telemetry arithmetic and causal feedback checks.
    old=None if dynamic is None else dict(request_sha256=m.digest(request),goal=dynamic['goal'],
        origin_tick=dynamic['origin_tick'],origin_call=dynamic['origin_call'])
    full=phase.view(request,s,h,'L',previous,old)
    payload=named.action_input(full)
    payload['request_progress']=deepcopy(obs)
    payload['dynamic_intent']=deepcopy(dynamic)
    payload['phase']='intent' if dynamic is None else 'action'
    return payload

def select(request,a,s,h,ledger):
    dynamic=q.current(request,s,ledger)
    if dynamic is None:
        selected,selection=m.select(None,s,h)
        return selected,selection,dict(stage=None,checked=False,blocked=False,reason='no_owned_intent')
    if dynamic['stage']=='urgent':
        selected,selection,detail=static.select(a,s,h,{'goal':dynamic['goal']})
        return selected,selection,dict(stage='urgent',**detail)
    selected,selection=m.select(a,s,h)
    detail=dict(stage='restore',checked=False,blocked=False)
    # Retain achieved restoration only. Never choose a recovery target or bypass
    # the original hard-SLA/certificate layer when restoration is still unmet.
    if selection['source']=='proposal' and selected['target']!=m.config(s) and m.sla(s):
        before=not any(s['caps_bps']);after=not any(selected['target']['caps_bps'])
        detail.update(checked=True,before_restored=before,proposed_restored=after)
        if before and not after:
            hold=m.verify({'hold':True},s,h);detail['hold_admitted']=hold['ok']
            if hold['ok']:
                selected=hold;selection=dict(source='intent_hold',proposal=selection['proposal']);detail['blocked']=True
    return selected,selection,detail
