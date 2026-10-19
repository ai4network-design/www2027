"""Reversible public telemetry layout; no intention interpretation or policy."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_conditional_protocol as original

HEADER=original.HEADER
parse=original.parse
grammar=original.grammar
compile_tables=original.compile_tables
calibration_gate=original.calibration_gate
select=original.select

def project(payload):
    p=deepcopy(payload);s=p['network'];h=p['confirmed_history']
    rows=s.pop('services');paths=s.pop('paths');caps=s.pop('caps_bps')
    rates=h.pop('rate_ticks');limited=h.pop('limited_ticks')
    if any(len(x)!=len(m.NAMES) for x in (rows,paths,caps,rates,limited)):
        raise ValueError('service vector length')
    services={}
    for i,name in enumerate(m.NAMES):
        row=rows[i]
        if row.pop('name')!=name:raise ValueError('service identity/order')
        cap=caps[i]
        if type(cap) is not int or cap<0:raise ValueError('integer configured bps')
        services[name]=dict(configured_control=dict(path=paths[i],cap_bps=cap,
            send_cap_mbps=None if cap==0 else cap/1000000),
            source=dict(uncapped_demand_mbps=row.pop('demand_mbps')),
            measured_feedback=row,
            confirmed_history=dict(rate_change_ticks=rates[i],continuously_limited_ticks=limited[i]))
    if 'services_by_name' in p:raise ValueError('reserved public field')
    p['services_by_name']=services
    return p

def restore(payload):
    p=deepcopy(payload);services=p.pop('services_by_name');s=p['network'];h=p['confirmed_history']
    if set(services)!=set(m.NAMES):raise ValueError('service identities')
    if any(k in s for k in ('services','paths','caps_bps')) or any(k in h for k in ('rate_ticks','limited_ticks')):
        raise ValueError('duplicate public source')
    rows=[];paths=[];caps=[];rates=[];limited=[]
    for name in m.NAMES:
        item=services[name]
        if set(item)!={'configured_control','source','measured_feedback','confirmed_history'}:
            raise ValueError('service role schema')
        control=item['configured_control'];source=item['source'];history=item['confirmed_history']
        if set(control)!={'path','cap_bps','send_cap_mbps'} or set(source)!={'uncapped_demand_mbps'} or set(history)!={'rate_change_ticks','continuously_limited_ticks'}:
            raise ValueError('role fields')
        cap=control['cap_bps'];alias=control['send_cap_mbps']
        if type(cap) is not int or cap<0 or (alias is not None and type(alias) not in (int,float)):
            raise ValueError('configured cap type')
        if alias!=(None if cap==0 else cap/1000000):raise ValueError('configured unit alias')
        row=item['measured_feedback']
        if 'name' in row or 'demand_mbps' in row:raise ValueError('duplicate feedback identity')
        row.update(name=name,demand_mbps=source['uncapped_demand_mbps']);rows.append(row)
        paths.append(control['path']);caps.append(cap);rates.append(history['rate_change_ticks']);limited.append(history['continuously_limited_ticks'])
    s.update(services=rows,paths=paths,caps_bps=caps);h.update(rate_ticks=rates,limited_ticks=limited)
    return p

def view(request,s,h,epoch):
    value=original.view(request,s,h,epoch)
    value['payload']=project(value['payload'])
    return value

def messages(v,frontend=False):
    if frontend:raise ValueError('raw-language direct action only')
    if v['phase']=='intent':return original.messages(v)
    if v['phase']!='action':raise ValueError('phase')
    old=restore(v['payload'])
    if old['network']!=v['network'] or old['confirmed_history']!=v['confirmed_history']:
        raise ValueError('model and safety public inputs differ')
    return [dict(role='system',content=(m.ROOT/HEADER).read_text().strip()),
            dict(role='user',content=m.canonical_json(v['payload']))]
