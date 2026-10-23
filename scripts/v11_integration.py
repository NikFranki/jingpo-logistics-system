"""Isolated HTTP integration for the V11 BE -> Agent -> browser flow.

Run only against a disposable *_test database while the caller holds it open.
The fake model proposes one order edit; the script verifies preview, explicit
browser confirmation, BE audit and Agent's read-only result verification.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / 'agent'
BE = ROOT / 'be'
BE_PYTHON = Path(os.getenv('BE_PYTHON', BE / '.venv' / 'bin' / 'python'))
sys.path.insert(0, str(AGENT))

from api import create_app
from graph import build_graph
from identity import IdentityClient
from registry import build_tools


class ProposalModel:
    def __init__(self, tool_name, arguments, answer):
        self.calls = 0
        self.tool_name = tool_name
        self.arguments = arguments
        self.answer = answer

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content='', tool_calls=[{
                'name': self.tool_name, 'args': self.arguments,
                'id': 'v11-integration-proposal', 'type': 'tool_call',
            }])
        return AIMessage(content=self.answer)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def seed_actor(role='user'):
    username = 'v11e2e-' + uuid4().hex[:16]
    code = r'''
import json, sys
from auth.service import issue_session
from auth.models import User
from db import SessionLocal
username = sys.argv[1]
with SessionLocal.begin() as session:
    user = User(username=username, email=username + '@example.com', role=sys.argv[2],
                email_verified=True, password_hash='isolated-integration')
    session.add(user)
    session.flush()
    print(json.dumps(issue_session(session, user), separators=(',', ':')))
'''
    result = subprocess.run([str(BE_PYTHON), '-c', code, username, role], cwd=BE,
                            env=dict(os.environ), check=True, capture_output=True, text=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def start_be():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    base_url = f'http://127.0.0.1:{port}'
    environment = dict(os.environ)
    environment.update({'AUTH_FRONTEND_URL': 'http://localhost:5173',
                        'AUTH_COOKIE_SECURE': 'false', 'PYTHON_DOTENV_DISABLED': '1'})
    process = subprocess.Popen([str(BE_PYTHON), '-m', 'uvicorn', 'main:app',
                                '--host', '127.0.0.1', '--port', str(port), '--log-level', 'error'],
                               cwd=BE, env=environment, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Isolated BE server exited before becoming ready')
        try:
            with urlopen(base_url + '/health/ready', timeout=0.5) as response:
                if response.status == 200:
                    return process, base_url
        except (URLError, TimeoutError, OSError):
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError('Isolated BE server did not become ready')


def main():
    database = os.getenv('DATABASE_URL', '')
    require(database.rsplit('/', 1)[-1].endswith('_test'),
            'V11 integration refuses databases that do not end in *_test')
    actor = seed_actor()
    server, base_url = start_be()
    order_id = None
    try:
        browser_headers = {'Origin': 'http://localhost:5173',
                           'X-CSRF-Token': actor['csrf_token']}
        browser_cookies = {'jingpo_session': actor['access_token'],
                           'jingpo_csrf': actor['csrf_token']}
        created = httpx.post(base_url + '/api/v1/orders/create', headers={
            'Authorization': 'Bearer ' + actor['access_token'],
            'Idempotency-Key': str(uuid4()),
        }, json={'product_name': 'initial V11 integration order', 'quantity': 1,
                 'sender_name': 'sender', 'sender_address': 'synthetic sender address',
                 'recipient_name': 'recipient', 'recipient_address': 'synthetic recipient address'},
            timeout=5)
        require(created.status_code == 201, 'Could not create isolated integration order: ' + created.text)
        order = created.json()
        order_id = int(order['id'])
        os.environ['V11_ORDER_ID'] = str(order_id)

        token_response = httpx.post(base_url + '/api/v1/auth/agent-token',
                                    headers=browser_headers, cookies=browser_cookies, timeout=5)
        require(token_response.status_code == 200, 'Could not issue read-only Agent token')
        agent_token = token_response.json()['access_token']

        model = ProposalModel('propose_order_update', {
            'order_id': order_id,
            'changes': {'product_name': 'updated through V11 integration'},
        }, '已准备修改提案，请先核对预览。')
        graph = build_graph(model, build_tools(base_url, allow_actions=True))
        agent = create_app(graph, identity_client=IdentityClient(base_url), allow_actions=True)
        with TestClient(agent, headers={'Authorization': 'Bearer ' + agent_token}) as agent_http:
            created_session = agent_http.post('/agent/api/v1/sessions')
            require(created_session.status_code == 201, 'Agent session creation failed: ' + created_session.text)
            agent_session_id = created_session.json()['session_id']
            message_id = str(uuid4())
            streamed = agent_http.post(f'/agent/api/v1/sessions/{agent_session_id}/messages', json={
                'message_id': message_id, 'message': f'把订单 {order_id} 的货品名称改为 updated through V11 integration',
            })
            require(streamed.status_code == 200, 'Agent message failed: ' + streamed.text)
            candidate_event = next((frame for frame in streamed.text.split('\n\n')
                                    if frame.startswith('event: action_proposed\n')), None)
            require(candidate_event is not None, 'Agent did not emit action_proposed')
            candidate = json.loads(candidate_event.split('data: ', 1)[1])
            require(candidate['agent_session_id'] == agent_session_id and candidate['message_id'] == message_id,
                    'Candidate is not bound to its Agent session and message')

            preview_response = httpx.post(base_url + '/api/v1/agent-actions/preview',
                headers=browser_headers, cookies=browser_cookies,
                json={key: candidate[key] for key in ('candidate_id', 'agent_session_id', 'message_id',
                    'action_type', 'subject_id', 'payload')}, timeout=5)
            require(preview_response.status_code == 201, 'BE preview failed: ' + preview_response.text)
            preview = preview_response.json()
            require(preview['execution_policy'] == 'CONFIRM_REQUIRED', 'First release must require confirmation')
            require(preview['changes'][0]['before'] == 'initial V11 integration order',
                    'BE preview did not use the current order value')
            unchanged = httpx.get(base_url + f'/api/v1/orders/{order_id}',
                                  headers={'Authorization': 'Bearer ' + actor['access_token']}, timeout=5)
            require(unchanged.status_code == 200 and unchanged.json()['product_name'] == 'initial V11 integration order',
                    'Preview unexpectedly mutated the order')

            executed = httpx.post(base_url + f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                headers=browser_headers, cookies=browser_cookies, json={'mode': 'approved'}, timeout=5)
            require(executed.status_code == 200 and executed.json()['status'] == 'SUCCEEDED',
                    'Browser-confirmed BE action failed: ' + executed.text)
            updated = httpx.get(base_url + f'/api/v1/orders/{order_id}',
                                 headers={'Authorization': 'Bearer ' + actor['access_token']}, timeout=5)
            require(updated.status_code == 200 and updated.json()['product_name'] == 'updated through V11 integration',
                    'Confirmed action did not update the order')

            synced = agent_http.post(f'/agent/api/v1/sessions/{agent_session_id}/actions/{candidate["candidate_id"]}/sync',
                                     json={'proposal_id': preview['proposal_id']})
            require(synced.status_code == 200, 'Agent could not verify BE action: ' + synced.text)
            result = synced.json()
            require(result['status'] == 'SUCCEEDED' and result['subject']['number'] == order['order_no'],
                    'Agent did not return the verified BE result')
            require(result['result_summary']['be_request_id'] == executed.json()['result']['be_request_id'],
                    'Agent/BE request correlation was lost')

        query = r'''
import json, sys
from sqlalchemy import select
from db import SessionLocal
from auth.models import AgentActionProposal
from models import OperationLog
candidate_id = sys.argv[1]
with SessionLocal() as session:
    proposal = session.scalar(select(AgentActionProposal).where(AgentActionProposal.candidate_id == candidate_id))
    operation = session.get(OperationLog, proposal.operation_log_id) if proposal else None
    print(json.dumps({'status': proposal.status if proposal else None,
                      'candidate_id': str(operation.agent_candidate_id) if operation else None,
                      'message_id': str(operation.agent_message_id) if operation else None,
                      'request_id': proposal.result.get('be_request_id') if proposal else None}))
'''
        audit = subprocess.run([str(BE_PYTHON), '-c', query, candidate['candidate_id']], cwd=BE,
                               env=dict(os.environ), check=True, capture_output=True, text=True)
        correlation = json.loads(audit.stdout.strip().splitlines()[-1])
        require(correlation['status'] == 'SUCCEEDED' and
                correlation['candidate_id'] == candidate['candidate_id'] and
                correlation['message_id'] == message_id and
                correlation['request_id'] == executed.json()['result']['be_request_id'],
                'Proposal, operation log, message and BE request correlation did not match')

        ops = seed_actor('ops')
        ops_headers = {'Authorization': 'Bearer ' + ops['access_token'],
                       'Idempotency-Key': str(uuid4())}
        prefix = 'V11-' + uuid4().hex[:10].upper()
        stations = []
        for suffix in ('OLD', 'NEW'):
            response = httpx.post(base_url + '/api/v1/stations', headers={
                                  **ops_headers, 'Idempotency-Key': str(uuid4())},
                                  json={'code': prefix + '_' + suffix, 'name': prefix + ' ' + suffix,
                                        'allows_delivery': True}, timeout=5)
            require(response.status_code == 201, 'Could not seed destination station: ' + response.text)
            stations.append(response.json())
        ops_order = httpx.post(base_url + '/api/v1/orders/create', headers={
            **ops_headers, 'Idempotency-Key': str(uuid4())},
            json={'product_name': 'isolated destination order', 'quantity': 1,
                  'sender_name': 'sender', 'sender_address': 'synthetic sender address',
                  'recipient_name': 'recipient', 'recipient_address': 'synthetic recipient address'},
            timeout=5)
        require(ops_order.status_code == 201, 'Could not seed destination order: ' + ops_order.text)
        shipment_response = httpx.post(
            base_url + f"/api/v1/orders/{ops_order.json()['id']}/shipment",
            headers={**ops_headers, 'Idempotency-Key': str(uuid4())},
            json={'destination_station_id': int(stations[0]['id'])}, timeout=5)
        require(shipment_response.status_code == 201,
                'Could not seed destination shipment: ' + shipment_response.text)
        ops_browser_headers = {'Origin': 'http://localhost:5173', 'X-CSRF-Token': ops['csrf_token']}
        ops_browser_cookies = {'jingpo_session': ops['access_token'], 'jingpo_csrf': ops['csrf_token']}
        ops_token_response = httpx.post(base_url + '/api/v1/auth/agent-token',
                                        headers=ops_browser_headers, cookies=ops_browser_cookies, timeout=5)
        require(ops_token_response.status_code == 200, 'Could not issue ops read-only Agent token')
        ops_agent = create_app(build_graph(ProposalModel('propose_shipment_destination_update', {
            'shipment_id': int(shipment_response.json()['id']),
            'destination_station_name': stations[1]['name'], 'reason': 'isolated V11 integration',
        }, '已准备更正目的站提案，请先核对预览。'),
            build_tools(base_url, allow_actions=True)),
            identity_client=IdentityClient(base_url), allow_actions=True)
        with TestClient(ops_agent, headers={'Authorization': 'Bearer ' +
                                           ops_token_response.json()['access_token']}) as ops_http:
            session_response = ops_http.post('/agent/api/v1/sessions')
            require(session_response.status_code == 201, 'Ops Agent session creation failed')
            ops_session = session_response.json()['session_id']
            ops_message = str(uuid4())
            response = ops_http.post(f'/agent/api/v1/sessions/{ops_session}/messages', json={
                'message_id': ops_message,
                'message': f"将运单 {shipment_response.json()['shipment_no']} 的目的站更正为 {stations[1]['name']}，原因是 isolated V11 integration",
            })
            require(response.status_code == 200, 'Destination Agent message failed: ' + response.text)
            frame = next((item for item in response.text.split('\n\n')
                          if item.startswith('event: action_proposed\n')), None)
            require(frame is not None, 'Agent did not emit destination correction candidate')
            destination_candidate = json.loads(frame.split('data: ', 1)[1])
            require(destination_candidate['payload']['destination_station_id'] == str(stations[1]['id'])
                    or destination_candidate['payload']['destination_station_id'] == int(stations[1]['id']),
                    'Agent did not resolve the requested station name through the BE station directory')
            preview = httpx.post(base_url + '/api/v1/agent-actions/preview',
                headers=ops_browser_headers, cookies=ops_browser_cookies,
                json={key: destination_candidate[key] for key in (
                    'candidate_id', 'agent_session_id', 'message_id', 'action_type', 'subject_id', 'payload')},
                timeout=5)
            require(preview.status_code == 201, 'Destination BE preview failed: ' + preview.text)
            correction = preview.json()
            require(correction['changes'][0]['after'] ==
                    f"{stations[1]['name']}（{stations[1]['id']}）",
                    'Destination preview did not resolve the target station')
            executed = httpx.post(base_url + f"/api/v1/agent-actions/{correction['proposal_id']}/execute",
                headers=ops_browser_headers, cookies=ops_browser_cookies,
                json={'mode': 'approved'}, timeout=5)
            require(executed.status_code == 200 and executed.json()['status'] == 'SUCCEEDED',
                    'Confirmed destination correction failed: ' + executed.text)
            synced = ops_http.post(
                f'/agent/api/v1/sessions/{ops_session}/actions/{destination_candidate["candidate_id"]}/sync',
                json={'proposal_id': correction['proposal_id']})
            require(synced.status_code == 200 and synced.json()['status'] == 'SUCCEEDED',
                    'Agent did not verify destination correction: ' + synced.text)
        print('V11 isolated HTTP integration passed: Agent candidate -> BE preview -> explicit confirmation -> Agent verification -> audit correlation')
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == '__main__':
    main()
