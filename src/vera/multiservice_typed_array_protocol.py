"""Typed-schema-only ablation; all transport and control semantics unchanged."""
from .multiservice_array_protocol import parse, encode, view, grammar, compile_tables, calibration_gate
from . import multiservice_array_protocol as original
from . import multiservice_control as m

HEADER = 'contracts/multiservice_typed_array_header_v1.txt'

def messages(v, frontend=False):
    result = original.messages(v, frontend=frontend)
    if v['phase'] == 'action':
        _, body = result[0]['content'].split('\n', 1)
        result[0]['content'] = (m.ROOT / HEADER).read_text().strip() + '\n' + body
    return result
