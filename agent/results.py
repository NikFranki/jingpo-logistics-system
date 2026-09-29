"""Bound tool messages without slicing serialized JSON or inventing facts."""
from copy import deepcopy
import json

MAX_RESULT_BYTES = 32 * 1024
MAX_EVENTS = 50
TRUNCATION_WARNING = '结果已裁剪；仅展示部分记录，请结合总数查询。'


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def bound_result(value):
    result = deepcopy(value)
    meta = result.setdefault('meta', {})
    meta.setdefault('truncated', False)
    collections = meta.setdefault('collections', {})
    data = result.get('data')
    lists = []
    if isinstance(data, dict):
        for key, items in data.items():
            if isinstance(items, list):
                lists.append((key, items))
                collections[key] = {'total': len(items), 'returned': len(items)}
        events = data.get('tracking_events')
        if isinstance(events, list) and len(events) > MAX_EVENTS:
            # BE sorts newest first; task discovery uses the full history before trimming.
            del events[MAX_EVENTS:]
            meta['truncated'] = True

    while True:
        for key, items in lists:
            collections[key]['returned'] = len(items)
        if meta['truncated']:
            warnings = result.setdefault('warnings', [])
            if TRUNCATION_WARNING not in warnings:
                warnings.append(TRUNCATION_WARNING)
        if encoded_size(result) <= MAX_RESULT_BYTES:
            return result
        available = [(key, items) for key, items in lists if items]
        if not available:
            # A giant scalar cannot safely be represented as a shortened business fact.
            return {
                'status': 'error', 'data': None,
                'error': {'code': 'RESULT_TOO_LARGE', 'message': '结果过大，请缩小查询范围。'},
                'warnings': ['原始结果未展示，不能据此判断没有数据。'],
                'meta': {'truncated': True},
            }
        _, items = max(available, key=lambda pair: encoded_size(pair[1]))
        items.pop()
        meta['truncated'] = True
