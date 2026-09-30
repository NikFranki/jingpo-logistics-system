import asyncio
from contextlib import redirect_stderr
from copy import deepcopy
from functools import partial
from io import StringIO
import json
from time import monotonic
import unittest
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

import be_client
from cli import restore_turn
from graph import build_graph, prepare_turn, trim_messages_by_size
from memory import compact_session, delete_session
from results import bound_result, encoded_size, MAX_RESULT_BYTES
from responses import validate_response
from state import TurnContext, TurnLimitError
from tracing import trace, safe_args
from tools import (create_shipment_tracking_tool, create_shipment_search_tool,
                   create_order_detail_tool, create_order_search_tool,
                   create_transport_task_detail_tool, create_transport_task_search_tool)

TIME = '2026-09-29T10:00:00+08:00'
TASK = dict(delay_monitoring_enabled=True, id='7', task_no='TASK-7', route_code='AB', status='IN_TRANSIT',
            expected_arrival_at=TIME, departed_at=TIME, arrived_at=None,
            delay_status='OVERDUE', delay_minutes=10, origin_station_id='1',
            destination_station_id='2', simulation_time=TIME, shipments=[])
EVENT = dict(id='1', event_type='DEPART', occurred_at=TIME, station_id='1', task_id='7')
SHIPMENT = dict(id='2', shipment_no='SHIP-2', stage='IN_TRANSIT',
                destination_station_id='1', last_scanned_station_id='1', tracking_events=[EVENT], sender_address='PRIVATE-ADDRESS',
                active_transport_task=dict(id='7', route_code='AB', origin_station_id='1', destination_station_id='2', status='IN_TRANSIT'))
ORDER = dict(id='3', order_no='ORDER-3', product_name='parcel', quantity=1,
             status='SHIPPED', shipment=dict(id='2', shipment_no='SHIP-2', stage='IN_TRANSIT'))


def page(items):
    return dict(items=items, page=1, page_size=10, total=len(items), simulation_time=TIME)


def call(name, args=None, ident='call1'):
    return AIMessage(content='', tool_calls=[dict(name=name, args=args or {}, id=ident, type='tool_call')])


class FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


@tool
async def noop() -> dict:
    """Return a harmless deterministic result."""
    return {'status': 'ok', 'data': {'value': 1}}


def config(thread='test'):
    return {'configurable': {'thread_id': thread}, 'recursion_limit': 30}


def tool_results(result):
    return [json.loads(m.content) for m in result['messages'] if isinstance(m, ToolMessage)]


class HarnessHelpers:
    async def invoke(self, model, tools=None, context=None, graph=None, cfg=None):
        graph = graph or build_graph(model, tools or [noop])
        result = await graph.ainvoke({'messages': [HumanMessage(content='query')]},
                                    config=cfg or config(), context=context or TurnContext())
        return result, graph

    def mocked(self, handler):
        return patch('be_client.httpx.AsyncClient', partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))


class HarnessTests(HarnessHelpers, unittest.IsolatedAsyncioTestCase):
    async def test_five_tools_then_followup_and_compact(self):
        model = FakeModel([call('noop', ident=str(i)) for i in range(5)] + [AIMessage(content='next')])
        result, graph = await self.invoke(model)
        self.assertEqual((result['model_calls'], result['tool_calls']), (5, 5))
        self.assertEqual(result['stop_reason'], 'MODEL_CALL_LIMIT')
        self.assertEqual(len(model.inputs), 5)
        await compact_session(graph, config())
        self.assertEqual(len(list(graph.checkpointer.list(config()))), 1)
        result, _ = await self.invoke(model, graph=graph)
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(result['messages'][-1].content, 'next')
        for messages in model.inputs:
            ids = [c['id'] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
            replies = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
            self.assertEqual(ids, replies)

    async def test_fifth_final_answer(self):
        model = FakeModel([call('noop', ident=str(i)) for i in range(4)] + [AIMessage(content='done')])
        result, _ = await self.invoke(model)
        self.assertEqual(result['model_calls'], 5)
        self.assertEqual(result['tool_calls'], 4)
        self.assertIsNone(result['stop_reason'])

    async def test_many_turns_bounded_checkpoint_and_new(self):
        model = FakeModel([AIMessage(content='ok')] * 35)
        graph = build_graph(model, [noop])
        for i in range(35):
            await self.invoke(model, graph=graph)
            await compact_session(graph, config())
            self.assertEqual(len(list(graph.checkpointer.list(config()))), 1)
            self.assertLessEqual(len(graph.checkpointer.blobs), 10)
            self.assertLessEqual(len(graph.checkpointer.writes), 2)
        snapshot = await graph.aget_state(config())
        self.assertLessEqual(sum(isinstance(m, HumanMessage) for m in snapshot.values['messages']), 11)
        await delete_session(graph, 'test')
        self.assertFalse(graph.checkpointer.storage)
        self.assertFalse((await graph.aget_state(config())).values)
        self.assertFalse(graph.checkpointer.blobs)
        self.assertFalse(graph.checkpointer.writes)

    async def test_model_failures_and_invalid_protocol(self):
        for code, reason in [(401, 'MODEL_AUTH_ERROR'), (403, 'MODEL_AUTH_ERROR'), (429, 'MODEL_RATE_LIMIT'), (503, 'MODEL_ERROR')]:
            exc = RuntimeError('SECRET')
            exc.status_code = code
            result, _ = await self.invoke(FakeModel([exc]))
            self.assertEqual(result['stop_reason'], reason)
            self.assertNotIn('SECRET', result['messages'][-1].content)
        for output, expected in [(TimeoutError(), 'REQUEST_TIMEOUT'), ('bad', 'INVALID_RESPONSE'),
                                 (AIMessage(content=''), 'INVALID_RESPONSE'),
                                 (AIMessage(content='', invalid_tool_calls=[dict(name='noop', args='{', id='x', error='bad', type='invalid_tool_call')]), 'INVALID_RESPONSE')]:
            result, _ = await self.invoke(FakeModel([output]))
            self.assertEqual(result['stop_reason'], expected)
        duplicate = AIMessage(content='', tool_calls=[dict(name='noop', args={}, id='same')] * 2)
        result, _ = await self.invoke(FakeModel([duplicate]))
        self.assertEqual(result['tool_calls'], 0)

    async def test_tool_limit_unknown_invalid_and_write_denial(self):
        for request in [call('depart_task', {'task_id': 7}), call('get_shipment_tracking', {'shipment_id': -1}),
                        call('get_shipment_tracking', {'shipment_id': 2, 'url': 'https://evil.invalid'})]:
            ctx = TurnContext()
            result, _ = await self.invoke(FakeModel([request, AIMessage(content='done')]), [create_shipment_tracking_tool('http://be')], ctx)
            self.assertEqual(ctx.http_calls, 0)
            self.assertEqual(tool_results(result)[0]['status'], 'error')
        many = AIMessage(content='', tool_calls=[dict(name='noop', args={}, id=str(i)) for i in range(10)])
        result, _ = await self.invoke(FakeModel([many]))
        self.assertEqual(result['tool_calls'], 8)
        self.assertEqual(len(tool_results(result)), 10)
        self.assertEqual(result['stop_reason'], 'TOOL_CALL_LIMIT')

    async def test_http_errors_invalid_json_and_schema(self):
        responses = [(httpx.Response(404), 'HTTP_404'), (httpx.Response(503), 'HTTP_503'),
                     (httpx.Response(302, headers={'location': 'https://evil.invalid'}), 'HTTP_302'),
                     (httpx.Response(200, text='not-json'), 'INVALID_RESPONSE'),
                     (httpx.Response(200, json={}), 'INVALID_RESPONSE'),
                     (httpx.Response(200, json={**TASK, 'delay_minutes': '10'}), 'INVALID_RESPONSE')]
        for response, code in responses:
            with self.mocked(lambda req: response):
                with self.assertRaises(be_client.BEClientError) as raised:
                    await be_client.get_transport_task('http://be', 7, TurnContext())
                self.assertEqual(raised.exception.code, code)
        for exc, code in [(httpx.ReadTimeout('SECRET'), 'TIMEOUT'), (httpx.ConnectError('SECRET'), 'NETWORK_ERROR')]:
            def fail(req):
                raise exc
            with self.mocked(fail):
                with self.assertRaises(be_client.BEClientError) as raised:
                    await be_client.get_transport_task('http://be', 7, TurnContext())
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn('SECRET', str(raised.exception))

    async def test_combined_partial_dedup_full_history_before_trim(self):
        shipment = deepcopy(SHIPMENT)
        shipment['tracking_events'] = [{**EVENT, 'id': str(i + 1), 'task_id': '7'} for i in range(55)]
        shipment['tracking_events'][-1]['task_id'] = '8'
        seen = []
        def handler(req):
            self.assertEqual(req.method, 'GET')
            self.assertEqual(req.url.host, 'be')
            seen.append(req.url.path)
            payload = {'/api/v1/shipments/2': shipment,
                       '/api/v1/stations': [dict(id='1', code='A', name='A station')],
                       '/api/v1/transport-tasks/7': TASK,
                       '/api/v1/transport-tasks/8': {'malformed': True}}[req.url.path]
            return httpx.Response(200, json=payload, headers={'X-Request-ID': 'req-1'})
        model = FakeModel([call('get_shipment_tracking', {'shipment_id': 2, 'include_tasks': True}), AIMessage(content='done')])
        ctx = TurnContext()
        with self.mocked(handler):
            result, _ = await self.invoke(model, [create_shipment_tracking_tool('http://be')], ctx)
        data = tool_results(result)[0]
        self.assertEqual(ctx.http_calls, 4)
        self.assertEqual(seen.count('/api/v1/transport-tasks/7'), 1)
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['data']['task_errors'][0]['code'], 'INVALID_RESPONSE')
        self.assertEqual(len(data['data']['tracking_events']), 50)
        self.assertEqual(data['data']['tracking_events'][0]['id'], '1')
        self.assertEqual(data['meta']['collections']['tracking_events'], {'total': 55, 'returned': 50})
        self.assertNotIn('PRIVATE-ADDRESS', json.dumps(data))
        self.assertEqual(data['data']['tasks'][0]['delay_status'], 'OVERDUE')

    async def test_configured_route_and_destination_are_distinct(self):
        shipment = {**SHIPMENT, 'destination_station_id': '3',
                    'active_transport_task': {**SHIPMENT['active_transport_task'], 'route_code': 'GZ_WH'}}
        task = {**TASK, 'route_code': 'GZ_WH'}
        paths = []
        def handler(req):
            paths.append(req.url.path)
            if req.url.path == '/api/v1/stations':
                payload = [dict(id='1', code='GZ', name='广州'), dict(id='2', code='WH', name='武汉'), dict(id='3', code='HZ', name='杭州')]
            elif req.url.path == '/api/v1/shipments/2':
                payload = shipment
            else:
                payload = task
            return httpx.Response(200, json=payload)
        with self.mocked(handler):
            result, _ = await self.invoke(FakeModel([call('get_shipment_tracking', {'shipment_id': 2, 'include_tasks': True}), AIMessage(content='done')]), [create_shipment_tracking_tool('http://be')])
        payload = tool_results(result)[0]
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['data']['destination_station']['code'], 'HZ')
        self.assertEqual(payload['data']['last_scanned_station']['code'], 'GZ')
        self.assertEqual(payload['data']['active_transport_task']['destination_station']['code'], 'WH')
        self.assertEqual(payload['data']['tasks'][0]['route_code'], 'GZ_WH')

    async def test_search_accepts_configured_route_code(self):
        def handler(req):
            self.assertEqual(req.url.params['route_code'], 'GZ_WH')
            return httpx.Response(200, json=page([{**TASK, 'route_code': 'GZ_WH'}]))
        with self.mocked(handler):
            result, _ = await self.invoke(FakeModel([call('search_transport_tasks', {'route_code': 'GZ_WH'}), AIMessage(content='done')]), [create_transport_task_search_tool('http://be')])
        self.assertEqual(tool_results(result)[0]['data']['items'][0]['route_code'], 'GZ_WH')

    async def test_pending_task_is_visible_before_first_depart_event(self):
        shipment = {**SHIPMENT,
                    'stage': 'AT_STATION',
                    'tracking_events': [dict(id='1', event_type='ARRIVE', occurred_at=TIME,
                                             station_id='1', task_id=None)],
                    'active_transport_task': {**SHIPMENT['active_transport_task'],
                                              'status': 'PENDING_DEPARTURE'}}
        task = {**TASK, 'status': 'PENDING_DEPARTURE', 'departed_at': None,
                'delay_status': 'NONE', 'delay_minutes': 0}
        paths = []
        def handler(req):
            paths.append(req.url.path)
            payload = {'/api/v1/shipments/2': shipment,
                       '/api/v1/stations': [dict(id='1', code='A', name='A station'),
                                            dict(id='2', code='B', name='B station')],
                       '/api/v1/transport-tasks/7': task}[req.url.path]
            return httpx.Response(200, json=payload)
        model = FakeModel([call('get_shipment_tracking', {'shipment_id': 2,
                                                         'include_tasks': True}),
                           AIMessage(content='done')])
        with self.mocked(handler):
            result, _ = await self.invoke(model, [create_shipment_tracking_tool('http://be')])
        data = tool_results(result)[0]['data']
        self.assertEqual(paths.count('/api/v1/transport-tasks/7'), 1)
        self.assertEqual(data['active_transport_task']['status'], 'PENDING_DEPARTURE')
        self.assertEqual(data['active_transport_task']['destination_station']['code'], 'B')
        self.assertEqual(data['tasks'][0]['status'], 'PENDING_DEPARTURE')

    async def test_all_six_tools_and_order_chain(self):
        registry = [f('http://be') for f in (create_order_search_tool, create_order_detail_tool, create_shipment_search_tool,
                                            create_shipment_tracking_tool, create_transport_task_search_tool, create_transport_task_detail_tool)]
        payloads = {'/api/v1/orders': page([ORDER]), '/api/v1/orders/3': ORDER,
                    '/api/v1/shipments': page([SHIPMENT]), '/api/v1/shipments/2': SHIPMENT,
                    '/api/v1/stations': [dict(id='1', code='A', name='A'), dict(id='2', code='B', name='B')],
                    '/api/v1/transport-tasks': page([TASK]), '/api/v1/transport-tasks/7': TASK}
        def handler(req):
            self.assertEqual(req.method, 'GET')
            return httpx.Response(200, json=payloads[req.url.path])
        with self.mocked(handler):
            for name, args in [('search_orders', {}), ('get_order', {'order_no': 'ORDER-3'}),
                               ('search_shipments', {}), ('get_shipment_tracking', {'shipment_no': 'SHIP-2'}),
                               ('search_transport_tasks', {'route_code': 'AB', 'status': 'IN_TRANSIT'}),
                               ('get_transport_task', {'task_no': 'TASK-7'})]:
                result, _ = await self.invoke(FakeModel([call(name, args), AIMessage(content='done')]), registry)
                self.assertEqual(tool_results(result)[0]['status'], 'ok', name)
            model = FakeModel([call('get_order', {'order_no': 'ORDER-3'}),
                               call('get_shipment_tracking', {'shipment_id': 2, 'include_tasks': True}, 'c2'), AIMessage(content='done')])
            result, _ = await self.invoke(model, registry)
            self.assertEqual(result['model_calls'], 3)
            self.assertEqual(tool_results(result)[1]['data']['tasks'][0]['id'], '7')

    async def test_http_budget_and_timeout_recovery(self):
        ctx = TurnContext(http_calls=24)
        result, graph = await self.invoke(FakeModel([call('get_shipment_tracking', {'shipment_id': 2})]),
                                           [create_shipment_tracking_tool('http://be')], ctx)
        self.assertEqual(result['stop_reason'], 'HTTP_CALL_LIMIT')
        self.assertEqual(ctx.http_calls, 24)
        previous = deepcopy((await graph.aget_state(config())).values)
        await restore_turn(graph, config(), previous, 'retry', 'TURN_TIMEOUT', 'timeout')
        await compact_session(graph, config())
        self.assertFalse((await graph.aget_state(config())).next)
        expired = TurnContext(deadline=monotonic() - 1)
        result, _ = await self.invoke(FakeModel([]), context=expired)
        self.assertEqual(result['stop_reason'], 'TURN_TIMEOUT')

    async def test_message_limit_and_state_writeback(self):
        model = FakeModel([])
        graph = build_graph(model, [noop])
        result = await graph.ainvoke({'messages': [HumanMessage(content='x' * 70000)]}, config=config(), context=TurnContext())
        self.assertEqual(result['stop_reason'], 'CONTEXT_LIMIT')
        self.assertEqual(model.inputs, [])
        model = FakeModel([AIMessage(content='ok')])
        graph = build_graph(model, [noop])
        messages = [HumanMessage(content='x' * 70000), AIMessage(content='old'), HumanMessage(content='new')]
        result = await graph.ainvoke({'messages': messages}, config=config(), context=TurnContext())
        self.assertNotIn('x' * 70000, [m.content for m in result['messages']])

    def test_truncation_unicode_and_giant_scalar(self):
        value = dict(status='ok', data={'items': [{'name': '中' * 1000} for _ in range(30)]}, meta={'pagination': {'total': 100}})
        out = bound_result(value)
        self.assertLessEqual(encoded_size(out), MAX_RESULT_BYTES)
        self.assertTrue(out['meta']['truncated'])
        self.assertEqual(out['meta']['collections']['items']['total'], 30)
        self.assertEqual(out['meta']['pagination']['total'], 100)
        self.assertEqual(len(value['data']['items']), 30)
        out = bound_result({'status': 'ok', 'data': {'name': '中' * 40000}})
        self.assertEqual(out['error']['code'], 'RESULT_TOO_LARGE')
        self.assertLessEqual(encoded_size(out), MAX_RESULT_BYTES)

    def test_debug_redaction(self):
        stream = StringIO()
        with redirect_stderr(stream):
            trace(TurnContext(), 'tool', args={'SECRET': 'secret'})
        self.assertEqual(stream.getvalue(), '')
        with redirect_stderr(stream):
            trace(TurnContext(debug=True), 'tool', operation='get_order', args=safe_args({'order_no': 'SECRET', 'api_key': 'SECRET', 'address': 'PRIVATE', 'order_id': 3}), request_id='req-123')
        value = stream.getvalue()
        self.assertNotIn('SECRET', value)
        self.assertNotIn('PRIVATE', value)
        self.assertEqual(json.loads(value)['request_id'], 'req-123')
        self.assertIn('trace_id', json.loads(value))

    def test_delay_variants_and_response_types(self):
        for route, status, delay, minutes in [('AB', 'IN_TRANSIT', 'OVERDUE', 10), ('AB', 'ARRIVED', 'LATE_ARRIVAL', 8), ('BC', 'IN_TRANSIT', 'NOT_APPLICABLE', None), ('AB', 'IN_TRANSIT', 'NONE', 0)]:
            data = validate_response({**TASK, 'route_code': route, 'status': status, 'delay_status': delay, 'delay_minutes': minutes}, 'task')
            self.assertEqual(data['delay_status'], delay)
        for field, value in [('id', 7), ('delay_minutes', True), ('shipments', None), ('simulation_time', 'no-date')]:
            with self.assertRaises(ValueError):
                validate_response({**TASK, field: value}, 'task')


if __name__ == '__main__':
    unittest.main()
