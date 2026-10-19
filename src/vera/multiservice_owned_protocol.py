"""Clock protocol plus shared own-goal actual-state predicates, no actions."""
from .multiservice_clock_protocol import parse, encode, grammar, compile_tables, calibration_gate, HEADER, NOTE
from . import multiservice_clock_protocol as original
from . import multiservice_control as m
from .multiservice_owned_status import enrich

STATUS_NOTE = 'contracts/multiservice_owned_status_v1.txt'

def view(request,s,h,epoch,ledger=None,previous=None):
    return enrich(original.view(request,s,h,epoch,ledger,previous))

def messages(v,frontend=False):
    result = original.messages(enrich(v),frontend=frontend)
    if v['phase'] == 'action':
        result[0]['content'] += '\n' + (m.ROOT/STATUS_NOTE).read_text().strip()
    return result
