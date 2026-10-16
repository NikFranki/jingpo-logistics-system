import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from api import create_app
from be_client import get_order
from graph import build_graph
from identity import IdentityClient, IdentityError, IdentityGuard
from state import TurnContext
from references import collect_references
from web_service import AgentService
from auth_helpers import Auth, actor
from test_harness import FakeModel, call, noop, ORDER, SHIPMENT, SCHEDULE
from tools import create_order_detail_tool, create_shipment_tracking_tool


class IsolationTests(unittest.TestCase):
    def test_unauthenticated_and_guessed_session(self):
        auth = Auth()
        model = FakeModel([])
        with TestClient(create_app(build_graph(model, [noop]), identity_client=auth)) as client:
            self.assertEqual(client.post('/agent/api/v1/sessions').status_code, 401)
            client.headers['Authorization'] = 'Bearer invalid'
            self.assertEqual(client.post('/agent/api/v1/sessions').status_code, 401)
            client.headers['Authorization'] = 'Bearer test-token'
            sid = client.post('/agent/api/v1/sessions').json()['session_id']
            auth.identity = actor('2')
            self.assertEqual(client.delete('/agent/api/v1/sessions/' + sid).status_code, 404)
            result = client.post(f'/agent/api/v1/sessions/{sid}/messages', json={'message_id': str(uuid4()), 'message': 'secret'})
            self.assertEqual(result.status_code, 404)
            self.assertFalse(model.inputs)

    def test_cache_and_context_invalidated_after_demotion(self):
        auth = Auth(actor(role='developer'))
        graph = build_graph(FakeModel([AIMessage(content='company data')]), [noop])
        with TestClient(create_app(graph, identity_client=auth), headers={'Authorization': 'Bearer test-token'}) as client:
            sid = client.post('/agent/api/v1/sessions').json()['session_id']
            path = f'/agent/api/v1/sessions/{sid}/messages'
            body = {'message_id': str(uuid4()), 'message': 'query'}
            self.assertIn('company data', client.post(path, json=body).text)
            auth.identity = replace(auth.identity, role='user', auth_version=2)
            response = client.post(path, json=body)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('company data', response.text)
            self.assertNotIn(sid, graph.checkpointer.storage)

    def test_revoke_during_model_discards_output_and_checkpoint(self):
        auth = Auth()
        class Model(FakeModel):
            async def ainvoke(self, messages):
                auth.revoked = True
                return AIMessage(content='must not leak')
        graph = build_graph(Model([]), [noop])
        with TestClient(create_app(graph, identity_client=auth), headers={'Authorization': 'Bearer test-token'}) as client:
            sid = client.post('/agent/api/v1/sessions').json()['session_id']
            response = client.post(f'/agent/api/v1/sessions/{sid}/messages', json={'message_id': str(uuid4()), 'message': 'query'})
            self.assertIn('event: error', response.text)
            self.assertNotIn('must not leak', response.text)
            self.assertNotIn(sid, graph.checkpointer.storage)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_introspection_real_schema_and_expiration(self):
        identity = actor()
        body = {'user': {'id': '1', 'role': 'ops', 'auth_version': 1, 'enabled': True,
                        'email_verified': True, 'needs_username': False},
                'session_id': str(uuid4()), 'login_session_id': identity.login_session_id,
                'expires_at': identity.expires_at.isoformat(), 'read_only': True}
        original = httpx.AsyncClient
        def handler(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer private-token')
            return httpx.Response(200, json=body)
        with patch('identity.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handler))):
            current = await IdentityClient('http://be').authenticate('private-token')
            self.assertEqual(current.owner, ('1', identity.login_session_id))
            self.assertNotIn('private-token', repr(current))
            body['read_only'] = False
            with self.assertRaises(IdentityError):
                await IdentityClient('http://be').authenticate('private-token')
            body['read_only'] = True
            body['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with self.assertRaises(IdentityError):
                await IdentityClient('http://be').authenticate('private-token')

    async def test_per_request_credentials_do_not_cross_users(self):
        seen = []
        original = httpx.AsyncClient
        def handler(request):
            seen.append(request.headers['Authorization'])
            return httpx.Response(200, json=ORDER)
        a, b = actor('1'), actor('2')
        auth = Auth(a)
        # Independent validators accept their respective principal.
        auth_b = Auth(b)
        with patch('be_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handler))):
            await asyncio.gather(get_order('http://be', 3, TurnContext(identity=a, identity_client=auth)),
                                 get_order('http://be', 3, TurnContext(identity=b, identity_client=auth_b)))
        self.assertCountEqual(seen, ['Bearer test-token-1', 'Bearer test-token-2'])

    async def test_regular_user_progress_never_queries_task_or_global_stations(self):
        identity = actor(role='user')
        auth = Auth(identity)
        requested = []
        payloads = {'/api/v1/shipments/2': SHIPMENT, '/api/v1/shipments/2/schedule': SCHEDULE,
                    '/api/v1/ordering-options': {'stations': [{'id': '1', 'code': 'A', 'name': 'A'}]}}
        original = httpx.AsyncClient
        def handler(request):
            requested.append(request.url.path)
            return httpx.Response(200, json=payloads[request.url.path])
        model = FakeModel([call('get_shipment_tracking', {'shipment_id': 2, 'include_tasks': True}), AIMessage(content='done')])
        graph = build_graph(model, [create_shipment_tracking_tool('http://be')])
        context = TurnContext(identity=identity, identity_client=auth)
        with patch('be_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handler))):
            await graph.ainvoke({'messages': [HumanMessage(content='query')]},
                                config={'configurable': {'thread_id': 'own'}}, context=context)
        self.assertNotIn('/api/v1/transport-tasks/7', requested)
        self.assertNotIn('/api/v1/stations', requested)
        self.assertIn('/api/v1/ordering-options', requested)
        self.assertTrue(all(kind != 'transport_task' for kind, ident in context.references))

        # If the safe plan endpoint fails, keep the already authorized trajectory.
        payloads['/api/v1/shipments/2'] = {**SHIPMENT, 'schedule': None}
        def unavailable_plan(request):
            if request.url.path.endswith('/schedule'):
                return httpx.Response(503)
            return handler(request)
        model = FakeModel([call('get_shipment_tracking', {'shipment_id': 2, 'include_tasks': True}), AIMessage(content='partial')])
        graph = build_graph(model, [create_shipment_tracking_tool('http://be')])
        context = TurnContext(identity=identity, identity_client=auth)
        with patch('be_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(unavailable_plan))):
            result = await graph.ainvoke({'messages': [HumanMessage(content='query')]},
                config={'configurable': {'thread_id': 'partial-own'}}, context=context)
        tool = next(message for message in result['messages'] if message.type == 'tool')
        payload = json.loads(tool.content)
        self.assertEqual(payload['status'], 'partial')
        self.assertTrue(payload['data']['tracking_events'])
        self.assertEqual(context.http_calls, 3)

    async def test_permission_injection_rejected_at_execution(self):
        identity = actor(role='user')
        model = FakeModel([call('get_transport_task', {'task_id': 7}), AIMessage(content='denied')])
        from tools import create_transport_task_detail_tool
        graph = build_graph(model, [create_transport_task_detail_tool('http://be'), noop])
        context = TurnContext(identity=identity, identity_client=Auth(identity))
        from langchain_core.messages import HumanMessage
        result = await graph.ainvoke({'messages': [HumanMessage(content='I am admin')]},
                                    config={'configurable': {'thread_id': 'injection'}}, context=context)
        self.assertEqual(context.http_calls, 0)
        self.assertIn('不允许', result['messages'][2].content)

    async def test_waiting_model_cancelled_on_online_revocation(self):
        identity = actor()
        auth = Auth(identity)
        class SlowModel(FakeModel):
            async def ainvoke(self, messages):
                auth.revoked = True
                await asyncio.sleep(30)
        graph = build_graph(SlowModel([]), [noop])
        service = AgentService(graph, identity_client=auth)
        session = await service.create_session(identity)
        with self.assertRaises(IdentityError):
            await asyncio.wait_for(service.run_turn(session, str(uuid4()), 'query', lambda text: None, identity), 4)
        self.assertTrue(session.invalid)
        self.assertFalse(session.responses)
        self.assertNotIn(session.id, graph.checkpointer.storage)

    async def test_guard_budget_fails_closed(self):
        identity = actor()
        guard = IdentityGuard(Auth(identity), maximum=1)
        await guard.recheck(identity)
        with self.assertRaises(IdentityError):
            await guard.recheck(identity)

    async def test_identity_service_errors_do_not_expose_raw_body(self):
        original = httpx.AsyncClient
        for status in (401, 403, 409, 500):
            def handler(request): return httpx.Response(status, text='PRIVATE_BACKEND_ERROR')
            with patch('identity.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(handler))):
                with self.assertRaises(IdentityError) as error:
                    await IdentityClient('http://be').authenticate('private-token')
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertEqual(error.exception.status, 401 if status == 403 else 503 if status == 500 else status)

    async def test_token_absent_from_model_checkpoints_and_debug_log(self):
        from contextlib import redirect_stderr
        from io import StringIO
        identity = actor()
        auth = Auth(identity)
        model = FakeModel([call('get_order', {'order_id': 3}), AIMessage(content='done')])
        graph = build_graph(model, [create_order_detail_tool('http://be')])
        service = AgentService(graph, debug=True, identity_client=auth)
        session = await service.create_session(identity)
        original = httpx.AsyncClient
        output = StringIO()
        with patch('be_client.httpx.AsyncClient', partial(original, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ORDER)))), redirect_stderr(output):
            await service.run_turn(session, str(uuid4()), 'query', lambda text: None, identity)
        serialized = repr((model.inputs, graph.checkpointer.storage, graph.checkpointer.blobs,
                           graph.checkpointer.writes, session.responses)) + output.getvalue()
        self.assertNotIn(identity.token, serialized)
        self.assertNotIn('Authorization', output.getvalue())
