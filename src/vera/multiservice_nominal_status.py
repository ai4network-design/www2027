"""Public current-configuration forecast, separate from actual telemetry."""
from copy import deepcopy
from . import multiservice_control as m
from . import multiservice_quota as q
from . import multiservice_owned_status as owned

KEY='hold_forecast'

def forecast(v):
    actual=owned.status(v)
    spec=q.parse(m.canonical_json(v['payload']['model_declared_quota']))
    prediction=m.predict(v['network'],m.config(v['network']))
    row=next(x for x in prediction['services'] if x['name']==spec['service'])
    if not all(m.finite(row[k]) for k in ('delay_ms','throughput_mbps')):
        raise ValueError('finite public nominal prediction')
    goal=dict(delay_ms={spec['service']:spec['delay_ms']},min_mbps={spec['service']:spec['min_mbps']})
    return dict(delay_ms=round(row['delay_ms'],6),throughput_mbps=round(row['throughput_mbps'],6),
        qos_satisfied=(m.goal_score(goal,prediction)[0]==0) if actual['qos_required'] else None)

def enrich(v):
    result=owned.enrich(v)
    if v['phase']=='action':
        expected=forecast(v)
        if KEY in v['payload'] and v['payload'][KEY]!=expected:
            raise ValueError('stale or forged nominal forecast')
        result['payload'][KEY]=expected
    return result
