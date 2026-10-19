"""Operational-clock prose only; no new observations or action selection."""
from .multiservice_typed_array_protocol import parse, encode, view, grammar, compile_tables, calibration_gate, HEADER
from . import multiservice_typed_array_protocol as original
from . import multiservice_control as m

NOTE = 'contracts/multiservice_operation_clocks_v1.txt'

def messages(v, frontend=False):
    result = original.messages(v, frontend=frontend)
    if v['phase'] == 'action':
        result[0]['content'] += '\n' + (m.ROOT / NOTE).read_text().strip()
    return result
