"""Opt-in JSON diagnostics. Never log prompts, responses or raw exceptions."""
import hashlib
import json
import re
import sys
from time import monotonic


def safe_args(args):
    safe = {}
    for key, value in args.items():
        if key in {'page', 'page_size', 'shipment_id', 'order_id', 'task_id', 'include_tasks'} and type(value) in (int, bool):
            safe[key] = value
        elif key in {'route_code', 'status', 'stage'} and isinstance(value, str) and re.fullmatch(r'[A-Z_]{1,32}', value):
            safe[key] = value
        elif key in {'shipment_no', 'order_no', 'task_no'} and isinstance(value, str):
            safe[key] = 'sha256:' + hashlib.sha256(value.encode()).hexdigest()[:12]
    return safe


def trace(context, event, started=None, **fields):
    if not context.debug:
        return
    record = {'event': event, 'trace_id': context.trace_id}
    if started is not None:
        record['duration_ms'] = round((monotonic() - started) * 1000, 2)
    # Call sites supply fixed names/codes, never business text or exception strings.
    for key in ('operation', 'status', 'model_calls', 'tool_calls', 'http_calls', 'args'):
        if key in fields:
            record[key] = fields[key]
    request_id = fields.get('request_id')
    if isinstance(request_id, str) and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,128}', request_id):
        record['request_id'] = request_id
    try:
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr)
    except OSError:
        pass  # Diagnostics must not break a query.
