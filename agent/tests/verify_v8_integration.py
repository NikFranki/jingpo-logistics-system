"""Opt-in real BE verification. Creates and removes its own isolated *_test database."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'agent'))
sys.path.insert(0, str(ROOT / 'agent/tests'))

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from api import create_app
from graph import build_graph
from identity import IdentityClient, IdentityError
from registry import build_tools
from test_harness import FakeModel, call


async def verify(base, fixture):
    checks = []
    auth = IdentityClient(base)
    actors = fixture['actors']
    async with httpx.AsyncClient(base_url=base, timeout=10) as client:
        tokens = {}
        for label, actor in actors.items():
            response = await client.post('/api/v1/auth/agent-token', headers={'Authorization': 'Bearer ' + actor['access_token']})
            assert response.status_code == 200, ('agent-token', response.status_code)
            tokens[label] = response.json()['access_token']
        a = await auth.authenticate(tokens['user'])
        other = await auth.authenticate(tokens['other'])
        assert a.owner != other.owner and a.own_only
        checks.append('real_introspection_and_personal_tokens')
        from be_client import get_order, BEClientError
        from state import TurnContext
        try:
            await get_order(base, fixture['orders'][1], TurnContext(identity=a, identity_client=auth))
            raise AssertionError('cross-user order was visible')
        except BEClientError as exc:
            assert exc.code == 'HTTP_404'
        checks.append('real_cross_user_order_hidden')
        model = FakeModel([call('search_orders', {}),
                           call('get_shipment_tracking', {'shipment_id': fixture['shipments'][0], 'include_tasks': True}, 'second'),
                           AIMessage(content='当前用户查询完成')])
        graph = build_graph(model, build_tools(base))
        with TestClient(create_app(graph, identity_client=auth), headers={'Authorization': 'Bearer ' + tokens['user']}) as api:
            sid = api.post('/agent/api/v1/sessions').json()['session_id']
            body = {'message_id': str(uuid4()), 'message': '查询我的订单和运单进度'}
            result = api.post(f'/agent/api/v1/sessions/{sid}/messages', json=body)
            frames = [frame for frame in result.text.split('\n\n') if frame.startswith('event: result')]
            assert frames, result.text
            answer = json.loads(frames[-1].split('data: ', 1)[1])
            assert answer['stop_reason'] is None
            assert answer['references'] and all(item['type'] != 'transport_task' for item in answer['references'])
            serialized = '\n'.join(str(message.content) for messages in model.inputs for message in messages if isinstance(message, ToolMessage))
            assert fixture['other_shipment_no'] not in serialized
            assert fixture['other_order_no'] not in serialized
            retry = api.post(f'/agent/api/v1/sessions/{sid}/messages', json=body)
            assert 'event: result' in retry.text and len(model.inputs) == 3
            api.headers['Authorization'] = 'Bearer ' + tokens['other']
            assert api.delete('/agent/api/v1/sessions/' + sid).status_code == 404
        checks.append('real_be_safe_shared_progress_sse_cache_and_owner')
        cancel = await client.post('/api/v1/orders/' + str(fixture['orders'][0]) + '/cancel',
                                   headers={'Authorization': 'Bearer ' + actors['user']['access_token'], 'Idempotency-Key': str(uuid4())},
                                   json={'reason': 'agent-v8-check'})
        assert cancel.status_code == 200, ('cancel', cancel.status_code)
        checked = await get_order(base, fixture['orders'][0], TurnContext(identity=a, identity_client=auth))
        assert checked['data']['status'] == 'CANCELLED' and checked['data']['cancel_reason'] == 'agent-v8-check'
        from be_client import get_shipment
        parcel = await get_shipment(base, fixture['shipments'][0], TurnContext(identity=a, identity_client=auth))
        assert parcel['data']['stage'] == 'CANCELLED'
        checks.append('real_cancelled_order_and_shipment_contract')
        admin_headers = {'Authorization': 'Bearer ' + actors['admin']['access_token']}
        class DemoteModel(FakeModel):
            async def ainvoke(self, messages):
                async with httpx.AsyncClient(base_url=base) as change:
                    response = await change.patch('/api/v1/admin/users/' + actors['developer']['user']['id'],
                                                  headers=admin_headers, json={'role': 'user'})
                    assert response.status_code == 200
                return AIMessage(content='RESTRICTED_OLD_ROLE_RESULT')
        graph = build_graph(DemoteModel([]), build_tools(base))
        with TestClient(create_app(graph, identity_client=auth), headers={'Authorization': 'Bearer ' + tokens['developer']}) as api:
            sid = api.post('/agent/api/v1/sessions').json()['session_id']
            response = api.post(f'/agent/api/v1/sessions/{sid}/messages', json={'message_id': str(uuid4()), 'message': 'query'})
            assert 'AUTH_CHANGED' in response.text and 'RESTRICTED_OLD_ROLE_RESULT' not in response.text
            assert sid not in graph.checkpointer.storage
        checks.append('real_demotion_during_query_discards_context')
        grant_response = await client.post('/api/v1/auth/cli/authorize')
        assert grant_response.status_code == 201
        grant = grant_response.json()
        approve = await client.post('/api/v1/auth/cli/approve', headers={'Authorization': 'Bearer ' + actors['user']['access_token']},
                                    json={'user_code': grant['user_code'], 'approve': True})
        assert approve.status_code == 200
        delivery = await client.post('/api/v1/auth/cli/token', json={'device_code': grant['device_code']})
        assert delivery.status_code == 200
        cli_token = delivery.json()['access_token']
        cli_identity = await auth.authenticate(cli_token)
        assert cli_identity.user_id == a.user_id and cli_identity.login_session_id != a.login_session_id
        repeat = await client.post('/api/v1/auth/cli/token', json={'device_code': grant['device_code']})
        assert repeat.status_code == 400 and repeat.json()['error'] == 'expired_token'
        logout = await client.post('/api/v1/auth/logout', headers={'Authorization': 'Bearer ' + cli_token})
        assert logout.status_code == 200
        try:
            await auth.authenticate(cli_token)
            raise AssertionError('revoked CLI login still active')
        except IdentityError:
            pass
        checks.append('real_cli_approval_one_time_delivery_and_logout')
    return checks


def main():
    database = 'jingpo_agent_v8_' + datetime.now().strftime('%Y%m%d%H%M%S') + '_' + uuid4().hex[:6] + '_test'
    env = dict(os.environ, DATABASE_URL='postgresql+psycopg:///' + database,
               AUTH_COOKIE_SECURE='false', AUTH_FRONTEND_URL='http://localhost:5173')
    python = str(ROOT / 'be/.venv/bin/python')
    created = False
    process = None
    try:
        subprocess.run(['createdb', database], check=True)
        created = True
        with tempfile.TemporaryDirectory(prefix='jingpo-agent-v8-') as temporary:
            with open(Path(temporary) / 'be.log', 'w') as log:
                subprocess.run([python, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT / 'be', env=env, stdout=log, stderr=log, check=True)
                seed = '''import sys,os,json
sys.path.insert(0,os.path.join(os.getcwd(),'tests'))
from test_v8_auth import V8AuthTests
V8AuthTests.setUpClass()
case=V8AuthTests(); case.setUp()
try:
 orders,shipments,task=case.shared_shipments()
 for role in ('admin','developer','ops'): case.actor(role)
 from db import SessionLocal
 from models import Order,Shipment
 with SessionLocal() as session:
  other_shipment_no=session.get(Shipment,shipments[1]).shipment_no
  other_order_no=session.get(Order,orders[1]).order_no
 print(json.dumps(dict(actors=case.actors,orders=orders,shipments=shipments,task=task,other_shipment_no=other_shipment_no,other_order_no=other_order_no)))
finally: case.doCleanups()
'''
                fixture = json.loads(subprocess.check_output([python, '-c', seed], cwd=ROOT / 'be', env=env, stderr=log, text=True))
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                process = subprocess.Popen([python, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)],
                                           cwd=ROOT / 'be', env=env, stdout=log, stderr=log)
                base = f'http://127.0.0.1:{port}'
                for _ in range(80):
                    try:
                        if httpx.get(base + '/health/ready', timeout=1).status_code == 200: break
                    except httpx.RequestError: pass
                    time.sleep(.1)
                else: raise RuntimeError('isolated BE failed readiness')
                checks = asyncio.run(verify(base, fixture))
                print(json.dumps({'checks': checks, 'passed': len(checks), 'real_model': False, 'browser_ui': False}))
    finally:
        if process:
            process.terminate()
            process.wait(timeout=10)
        if created:
            subprocess.run(['dropdb', database], check=True)


if __name__ == '__main__':
    main()
