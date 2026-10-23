import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url

from auth.dependencies import COOKIE, CSRF_COOKIE
from auth.models import AgentActionProposal, LoginSession, User
from auth.security import digest
from auth.service import issue_session
from db import SessionLocal
from main import app
from models import Order, OperationLog
from errors import NetworkError
import business_time


@unittest.skipUnless((make_url(os.getenv('DATABASE_URL', 'postgresql:///')).database or '').endswith('_test'),
                     'requires isolated *_test database')
class V11AgentActionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, client=('v11-' + uuid4().hex, 50001))
        self.addCleanup(self.client.close)
        self.actors = {}

    def actor(self, label='user'):
        if label not in self.actors:
            role = label if label in ('admin', 'ops', 'developer') else 'user'
            with SessionLocal.begin() as session:
                user = session.scalar(select(User).where(User.role == 'admin')) if role == 'admin' else None
                if user is None:
                    name = 'v11-' + uuid4().hex[:20]
                    user = User(username=name, email=name + '@example.com', role=role,
                                email_verified=True, password_hash='test')
                    session.add(user)
                    session.flush()
                self.actors[label] = issue_session(session, user)
        return self.actors[label]

    def headers(self, actor='user', csrf=True):
        value = self.actor(actor)
        self.client.cookies.set(COOKIE, value['access_token'])
        self.client.cookies.set(CSRF_COOKIE, value['csrf_token'])
        headers = {'Origin': 'http://localhost:5173'}
        if csrf:
            headers['X-CSRF-Token'] = value['csrf_token']
        return headers

    def order(self, actor='user'):
        value = self.actor(actor)
        response = self.client.post('/api/v1/orders/create', json={
            'product_name': 'v11 test', 'quantity': 1, 'sender_name': 'sender',
            'sender_address': 'from', 'recipient_name': 'recipient', 'recipient_address': 'to',
        }, headers={'Authorization': 'Bearer ' + value['access_token'],
                   'Idempotency-Key': str(uuid4())})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def candidate(self, order, **overrides):
        body = {
            'candidate_id': str(uuid4()), 'agent_session_id': str(uuid4()),
            'message_id': str(uuid4()), 'action_type': 'order.update',
            'subject_id': order['id'], 'payload': {'product_name': 'changed by assistant'},
        }
        body.update(overrides)
        return body

    def test_preview_confirm_is_single_use_and_audited(self):
        order = self.order()
        body = self.candidate(order)
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview', json=body, headers=headers)
        self.assertEqual(preview.status_code, 201, preview.text)
        data = preview.json()
        self.assertEqual(data['execution_policy'], 'CONFIRM_REQUIRED')
        self.assertEqual(data['changes'][0]['before'], 'v11 test')
        self.assertEqual(data['changes'][0]['after'], 'changed by assistant')
        with SessionLocal() as session:
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'v11 test')

        no_csrf = self.client.post(f"/api/v1/agent-actions/{data['proposal_id']}/execute",
                                   json={'mode': 'approved'}, headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(no_csrf.status_code, 403)
        done = self.client.post(f"/api/v1/agent-actions/{data['proposal_id']}/execute",
                                json={'mode': 'approved'}, headers=headers)
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()['status'], 'SUCCEEDED')
        replay = self.client.post(f"/api/v1/agent-actions/{data['proposal_id']}/execute",
                                  json={'mode': 'approved'}, headers=headers)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()['result'], done.json()['result'])
        with SessionLocal() as session:
            current = session.get(Order, int(order['id']))
            self.assertEqual(current.product_name, 'changed by assistant')
            proposal = session.get(AgentActionProposal, data['proposal_id'])
            self.assertEqual(proposal.status, 'SUCCEEDED')
            log = session.get(OperationLog, proposal.operation_log_id)
            self.assertEqual(log.agent_action_proposal_id, proposal.id)
            self.assertEqual(log.agent_candidate_id, proposal.candidate_id)
            self.assertEqual(log.agent_message_id, proposal.message_id)

    def test_action_gateway_requires_browser_cookie_origin_and_csrf(self):
        order = self.order()
        body = self.candidate(order)
        path = '/api/v1/agent-actions/preview'
        anonymous = TestClient(app, client=('v11-anon-' + uuid4().hex, 50003))
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.post(path, json=body).status_code, 401)
        self.assertEqual(anonymous.post(path, json=body,
                                        headers={'Authorization': 'Bearer read-only-agent-token'}).status_code, 403)

        headers = self.headers()
        no_csrf = self.client.post(path, json=body,
                                   headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(no_csrf.status_code, 403)
        wrong_origin = {**headers, 'Origin': 'https://attacker.example'}
        self.assertEqual(self.client.post(path, json=body, headers=wrong_origin).status_code, 403)

    def test_developer_cannot_preview_order_updates(self):
        order = self.order()
        response = self.client.post('/api/v1/agent-actions/preview',
                                    json=self.candidate(order), headers=self.headers('developer'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'FORBIDDEN')

    def test_candidate_replay_returns_same_preview_but_cannot_change_bound_content(self):
        order = self.order()
        headers = self.headers()
        body = self.candidate(order)
        first = self.client.post('/api/v1/agent-actions/preview', json=body, headers=headers)
        self.assertEqual(first.status_code, 201, first.text)
        replay = self.client.post('/api/v1/agent-actions/preview', json=body, headers=headers)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()['proposal_id'], first.json()['proposal_id'])

        changed_payload = {**body, 'payload': {'product_name': 'different content'}}
        conflict = self.client.post('/api/v1/agent-actions/preview', json=changed_payload, headers=headers)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()['error']['code'], 'PROPOSAL_CONSUMED')

        changed_message = {**body, 'message_id': str(uuid4())}
        message_conflict = self.client.post('/api/v1/agent-actions/preview', json=changed_message,
                                            headers=headers)
        self.assertEqual(message_conflict.status_code, 409, message_conflict.text)
        self.assertEqual(message_conflict.json()['error']['code'], 'PROPOSAL_CONSUMED')

    def test_concurrent_confirmation_returns_one_audited_result(self):
        order = self.order()
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview',
                                   json=self.candidate(order), headers=headers).json()
        actor = self.actor()
        barrier = Barrier(2)

        def confirm():
            client = TestClient(app, client=('v11-race-' + uuid4().hex, 50002))
            client.cookies.set(COOKIE, actor['access_token'])
            client.cookies.set(CSRF_COOKIE, actor['csrf_token'])
            barrier.wait(timeout=5)
            try:
                return client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                                   json={'mode': 'approved'}, headers={
                                       'Origin': 'http://localhost:5173',
                                       'X-CSRF-Token': actor['csrf_token']})
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(responses[0].json()['status'], 'SUCCEEDED')
        self.assertEqual(responses[0].json()['result'], responses[1].json()['result'])
        with SessionLocal() as session:
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            self.assertEqual(proposal.status, 'SUCCEEDED')
            self.assertIsNotNone(proposal.operation_log_id)
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'changed by assistant')
            self.assertEqual(session.scalar(select(OperationLog.id).where(
                OperationLog.agent_action_proposal_id == proposal.id)), proposal.operation_log_id)

    def test_revoked_web_session_cannot_confirm_preview(self):
        order = self.order()
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview',
                                   json=self.candidate(order), headers=headers).json()
        with SessionLocal.begin() as session:
            # LoginSession is keyed by the token digest; locate the active web session.
            login = session.scalar(select(LoginSession).where(
                LoginSession.user_id == int(self.actor()['user']['id']),
                LoginSession.channel == 'web', LoginSession.revoked_at.is_(None)))
            self.assertIsNotNone(login)
            login.revoked_at = business_time.server_now()
        result = self.client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                                  json={'mode': 'approved'}, headers=headers)
        self.assertEqual(result.status_code, 401, result.text)
        with SessionLocal() as session:
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            self.assertEqual(proposal.status, 'PREVIEWED')
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'v11 test')

    def test_business_write_failure_rolls_back_order_and_audit(self):
        order = self.order()
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview',
                                   json=self.candidate(order), headers=headers).json()
        from agent_actions import service as action_service
        original_update_order = action_service.update_order

        def fail_after_business_write(*args, **kwargs):
            original_update_order(*args, **kwargs)
            raise NetworkError('INTERNAL_ERROR', 'simulated downstream failure', 500)

        with patch('agent_actions.service.update_order', side_effect=fail_after_business_write):
            result = self.client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                                      json={'mode': 'approved'}, headers=headers)
        self.assertEqual(result.status_code, 500, result.text)
        with SessionLocal() as session:
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            self.assertEqual(proposal.status, 'FAILED')
            self.assertIsNone(proposal.operation_log_id)
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'v11 test')
            self.assertIsNone(session.scalar(select(OperationLog.id).where(
                OperationLog.idempotency_key == action_service.uuid5(
                    action_service.NAMESPACE_URL, f'jingpo:agent-action:{proposal.id}'))))

    def test_read_only_agent_can_read_owner_status_but_cannot_write(self):
        order = self.order()
        headers = self.headers()
        body = self.candidate(order)
        proposal = self.client.post('/api/v1/agent-actions/preview', json=body, headers=headers).json()
        token = self.client.post('/api/v1/auth/agent-token', headers=headers).json()['access_token']
        status = self.client.get(f"/api/v1/agent-actions/{proposal['proposal_id']}",
                                 headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()['status'], 'PREVIEWED')
        forbidden = self.client.post(f"/api/v1/agent-actions/{proposal['proposal_id']}/execute",
                                     json={'mode': 'approved'}, headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(forbidden.status_code, 403)
        other = self.actor('other')
        hidden = self.client.get(f"/api/v1/agent-actions/{proposal['proposal_id']}",
                                 headers={'Authorization': 'Bearer ' + other['access_token']})
        self.assertEqual(hidden.status_code, 404)

    def test_regular_user_cannot_preview_another_users_order(self):
        order = self.order()
        headers = self.headers('other')
        denied = self.client.post('/api/v1/agent-actions/preview',
                                  json=self.candidate(order), headers=headers)
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(denied.json()['error']['code'], 'NOT_FOUND')

    def test_ops_can_preview_and_confirm_destination_correction(self):
        ops = self.actor('ops')
        authorization = {'Authorization': 'Bearer ' + ops['access_token']}
        prefix = 'V11_' + uuid4().hex[:10].upper()
        old_station = self.client.post('/api/v1/stations', json={
            'code': prefix + '_OLD', 'name': prefix + ' old', 'allows_delivery': True,
        }, headers={**authorization, 'Idempotency-Key': str(uuid4())})
        new_station = self.client.post('/api/v1/stations', json={
            'code': prefix + '_NEW', 'name': prefix + ' new', 'allows_delivery': True,
        }, headers={**authorization, 'Idempotency-Key': str(uuid4())})
        self.assertEqual(old_station.status_code, 201, old_station.text)
        self.assertEqual(new_station.status_code, 201, new_station.text)
        order = self.client.post('/api/v1/orders/create', json={
            'product_name': 'v11 destination', 'quantity': 1, 'sender_name': 'sender',
            'sender_address': 'from', 'recipient_name': 'recipient', 'recipient_address': 'to',
        }, headers={**authorization, 'Idempotency-Key': str(uuid4())})
        self.assertEqual(order.status_code, 201, order.text)
        shipment = self.client.post(f"/api/v1/orders/{order.json()['id']}/shipment",
                                    json={'destination_station_id': int(old_station.json()['id'])},
                                    headers={**authorization, 'Idempotency-Key': str(uuid4())})
        self.assertEqual(shipment.status_code, 201, shipment.text)
        headers = self.headers('ops')
        candidate = self.candidate(shipment.json(), action_type='shipment.destination.update',
                                   payload={'expected_destination_station_id': int(old_station.json()['id']),
                                            'destination_station_id': int(new_station.json()['id']),
                                            'reason': 'V11 destination test'})
        preview = self.client.post('/api/v1/agent-actions/preview', json=candidate, headers=headers)
        self.assertEqual(preview.status_code, 201, preview.text)
        self.assertEqual(preview.json()['changes'][0]['after'],
                         f"{new_station.json()['name']}（{new_station.json()['id']}）")
        executed = self.client.post(
            f"/api/v1/agent-actions/{preview.json()['proposal_id']}/execute",
            json={'mode': 'approved'}, headers=headers)
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()['status'], 'SUCCEEDED')
        detail = self.client.get(f"/api/v1/shipments/{shipment.json()['id']}", headers=authorization)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()['destination_station_id'], new_station.json()['id'])

    def test_stale_order_version_cannot_be_confirmed(self):
        order = self.order()
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview', json=self.candidate(order), headers=headers).json()
        current = self.client.patch(f"/api/v1/orders/{order['id']}",
                                    json={'product_name': 'changed in another tab'},
                                    headers={'Authorization': 'Bearer ' + self.actor()['access_token'],
                                             'Idempotency-Key': str(uuid4())})
        self.assertEqual(current.status_code, 200, current.text)
        result = self.client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                                  json={'mode': 'approved'}, headers=headers)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()['error']['code'], 'PROPOSAL_STALE')
        with SessionLocal() as session:
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'changed in another tab')
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            self.assertEqual(proposal.status, 'FAILED')

    def test_automatic_policy_stays_closed_and_reject_does_not_write(self):
        order = self.order()
        headers = self.headers()
        policy = self.client.get('/api/v1/auth/agent-write-policy')
        self.assertEqual(policy.status_code, 200, policy.text)
        self.assertEqual(policy.headers.get('cache-control'), 'no-store')
        self.assertEqual(policy.json()['eligible_action_types'], [])
        self.assertEqual(policy.json()['enabled_action_types'], [])
        rejected_enable = self.client.put('/api/v1/auth/agent-write-policy',
                                          json={'enabled_action_types': ['order.update']}, headers=headers)
        self.assertEqual(rejected_enable.status_code, 422)
        updated = self.client.put('/api/v1/auth/agent-write-policy',
                                  json={'enabled_action_types': []}, headers=headers)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.headers.get('cache-control'), 'no-store')
        body = self.candidate(order)
        preview = self.client.post('/api/v1/agent-actions/preview', json=body, headers=headers).json()
        self.assertEqual(preview['execution_policy'], 'CONFIRM_REQUIRED')
        automatic = self.client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/execute",
                                      json={'mode': 'automatic'}, headers=headers)
        self.assertEqual(automatic.status_code, 403, automatic.text)
        self.assertEqual(automatic.json()['error']['code'], 'FORBIDDEN')
        status = self.client.get(f"/api/v1/agent-actions/{preview['proposal_id']}", headers=headers)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()['status'], 'PREVIEWED')
        rejected = self.client.post(f"/api/v1/agent-actions/{preview['proposal_id']}/reject",
                                    json={'reason_code': 'USER_CANCELLED'}, headers=headers)
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()['status'], 'REJECTED')
        with SessionLocal() as session:
            self.assertEqual(session.get(Order, int(order['id'])).product_name, 'v11 test')

    def test_expired_proposal_is_terminal_and_personal_payload_is_scrubbed(self):
        order = self.order()
        headers = self.headers()
        preview = self.client.post('/api/v1/agent-actions/preview',
                                   json=self.candidate(order), headers=headers).json()
        with SessionLocal.begin() as session:
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            proposal.expires_at = business_time.server_now() - timedelta(seconds=1)
        token = self.client.post('/api/v1/auth/agent-token', headers=headers).json()['access_token']
        response = self.client.get(f"/api/v1/agent-actions/{preview['proposal_id']}",
                                   headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'EXPIRED')
        self.assertEqual(response.json()['changes'][0]['before'], '[已清理]')
        with SessionLocal() as session:
            proposal = session.get(AgentActionProposal, preview['proposal_id'])
            self.assertEqual(proposal.status, 'EXPIRED')
            self.assertEqual(proposal.payload, {})
