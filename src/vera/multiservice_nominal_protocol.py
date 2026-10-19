"""Owned protocol plus a labeled nominal HOLD forecast, not a controller."""
from .multiservice_owned_protocol import parse,encode,grammar,compile_tables,calibration_gate,HEADER,NOTE,STATUS_NOTE
from . import multiservice_owned_protocol as original
from . import multiservice_control as m
from .multiservice_nominal_status import enrich

FORECAST_NOTE='contracts/multiservice_nominal_status_v1.txt'

def view(request,s,h,epoch,ledger=None,previous=None):
    return enrich(original.view(request,s,h,epoch,ledger,previous))

def messages(v,frontend=False):
    result=original.messages(enrich(v),frontend=frontend)
    if v['phase']=='action':
        result[0]['content']+='\n'+(m.ROOT/FORECAST_NOTE).read_text().strip()
    return result
