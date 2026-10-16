import os
import smtplib
import unittest
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from auth.dependencies import COOKIE, CSRF_COOKIE
from auth.models import DeviceGrant, EmailToken, GoogleIdentity, LoginSession, User
from auth.security import digest, password_hash, password_matches
from auth.service import issue_session
from business_time import server_now
from db import SessionLocal
from main import app
from models import OperationLog, Order, Shipment, Station, TaskShipment, TransportTask


@unittest.skipUnless((make_url(os.getenv('DATABASE_URL', 'postgresql:///')).database or '').endswith('_test'), 'requires isolated *_test database')
class V8AuthTests(unittest.TestCase):
    password = 'V8-test-password-123'

    @classmethod
    def setUpClass(cls):
        cls.encoded = password_hash(cls.password)

    def setUp(self):
        self.env = patch.dict(os.environ, {'AUTH_COOKIE_SECURE': 'false', 'AUTH_FRONTEND_URL': 'http://localhost:5173',
            'SMTP_HOST': 'mailpit', 'SMTP_PORT': '1025', 'SMTP_STARTTLS': 'false',
            'GOOGLE_CLIENT_ID': 'test-client', 'GOOGLE_CLIENT_SECRET': 'test-secret',
            'GOOGLE_REDIRECT_URI': 'http://localhost:8000/api/v1/auth/google/callback'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.smtp = patch('auth.service.smtplib.SMTP').start()
        self.addCleanup(patch.stopall)
        self.client = TestClient(app, client=('v8-' + uuid4().hex, 50000))
        self.addCleanup(self.client.close)
        self.actors = {}

    def actor(self, label='user', verified=True):
        if label not in self.actors:
            role = label if label in ('admin', 'ops', 'developer') else 'user'
            with SessionLocal.begin() as session:
                user = session.scalar(select(User).where(User.role == 'admin')) if role == 'admin' else None
                if user is None:
                    name = 'v8-' + uuid4().hex[:20]
                    user = User(username=name, email=name + '@example.com', password_hash=self.encoded,
                                role=role, email_verified=verified)
                    session.add(user)
                    session.flush()
                result = issue_session(session, user)
                self.actors[label] = result
        return self.actors[label]

    def call(self, path, method='GET', body=None, actor='user', key=None):
        headers = {'Authorization': 'Bearer ' + self.actor(actor)['access_token']}
        if method != 'GET':
            headers['Idempotency-Key'] = key or str(uuid4())
        return self.client.request(method, '/api/v1' + path, json=body, headers=headers)

    def order(self, actor='user', key=None):
        body = dict(product_name='v8', quantity=1, sender_name='sender', sender_address='from',
                    recipient_name='recipient', recipient_address='to')
        response = self.call('/orders/create', 'POST', body, actor, key)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def email_token(self):
        message = self.smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        url = next(line for line in message.get_content().splitlines() if line.startswith('http'))
        return parse_qs(urlparse(url).query)['token'][0]

    def browser_headers(self):
        return {'Origin': 'http://localhost:5173', 'X-CSRF-Token': self.client.cookies.get(CSRF_COOKIE)}

    def test_registration_verification_one_time_and_role_injection(self):
        name = 'v8reg-' + uuid4().hex[:15]
        body = {'username': name, 'email': name + '@example.com', 'password': self.password}
        self.assertEqual(self.client.post('/api/v1/auth/register', json={**body, 'role': 'admin'}).status_code, 422)
        response = self.client.post('/api/v1/auth/register', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        raw = self.email_token()
        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.username == name))
            self.assertEqual((user.role, user.email_verified), ('user', False))
            self.assertNotEqual(user.password_hash, self.password)
            self.assertTrue(password_matches(self.password, user.password_hash))
            self.assertIsNotNone(session.get(EmailToken, digest(raw)))
            self.assertIsNone(session.get(EmailToken, raw))
        response = self.client.post('/api/v1/auth/login', json={'username': name, 'password': self.password})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('access_token', response.json())
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/v1/orders').status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/verify-email', json={'token': raw}).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/verify-email', json={'token': raw}).status_code, 400)
        self.assertEqual(self.client.get('/api/v1/orders').json()['total'], 0)

    def test_login_csrf_device_logout_and_absolute_expiry(self):
        actor = self.actor()
        user = actor['user']
        login = self.client.post('/api/v1/auth/login', json={'username': user['username'], 'password': self.password})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/logout', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/logout', json={}, headers=self.browser_headers()).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/orders').status_code, 401)
        self.assertEqual(self.call('/orders').status_code, 200)
        with SessionLocal.begin() as session:
            entry = session.get(LoginSession, digest(actor['access_token']))
            self.assertAlmostEqual((entry.expires_at - entry.created_at).total_seconds(), 86400, places=2)
            entry.expires_at = server_now() - timedelta(seconds=1)
        self.assertEqual(self.call('/orders').status_code, 401)

    def test_browser_account_switch_revokes_previous_device_and_children(self):
        first, second = self.actor(), self.actor('other')
        self.assertEqual(self.client.post('/api/v1/auth/login', json={'username': first['user']['username'], 'password': self.password}).status_code, 200)
        child = self.client.post('/api/v1/auth/agent-token', headers=self.browser_headers()).json()['access_token']
        self.assertEqual(self.client.post('/api/v1/auth/login', json={'username': second['user']['username'], 'password': self.password}).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me').json()['user']['id'], second['user']['id'])
        self.assertEqual(self.client.get('/api/v1/orders', headers={'Authorization': 'Bearer ' + child}).status_code, 401)
        self.assertEqual(self.call('/orders').status_code, 200)

    def test_ownership_lists_search_details_and_idempotency(self):
        key = str(uuid4())
        first, other = self.order(key=key), self.order('other', key)
        self.assertNotEqual(first['id'], other['id'])
        own = self.call('/orders').json()
        self.assertEqual((own['total'], [item['id'] for item in own['items']]), (1, [first['id']]))
        self.assertEqual(self.call('/orders?order_no=' + other['order_no']).json()['total'], 0)
        self.assertEqual(self.call('/orders/' + other['id']).status_code, 404)
        self.assertEqual(self.call('/orders/' + other['id'], 'PATCH', {'quantity': 2}).status_code, 404)
        self.assertEqual(self.order(key=key)['id'], first['id'])
        self.assertEqual(self.call('/orders/' + first['id'], 'PATCH', {'quantity': 2}, 'ops').status_code, 200)
        with SessionLocal() as session:
            order = session.get(Order, int(first['id']))
            self.assertEqual(str(order.created_by_user_id), self.actor()['user']['id'])
            logs = session.scalars(select(OperationLog).where(OperationLog.resource_type == 'ORDER', OperationLog.resource_id == order.id)).all()
            self.assertEqual({entry.actor_user_id for entry in logs}, {int(self.actor()['user']['id']), int(self.actor('ops')['user']['id'])})
        self.assertEqual(self.call('/orders/' + first['id'], 'PATCH', {'created_by_user_id': 1}).status_code, 422)

    def test_all_business_routes_authenticate_and_developer_cannot_write(self):
        for path in ('/orders', '/shipments', '/stations', '/transport-lines', '/provinces', '/server-time'):
            response = self.client.get('/api/v1' + path)
            self.assertEqual(response.status_code, 401, (path, response.text))
        self.assertEqual(self.call('/stations', actor='developer').status_code, 200)
        self.assertEqual(self.call('/stations', actor='user').status_code, 403)
        self.assertEqual(self.call('/ordering-options', actor='user').status_code, 200)
        self.assertEqual(self.call('/stations', 'POST', {'code': 'DENIED', 'name': 'denied'}, 'developer').status_code, 403)
        self.assertEqual(self.call('/orders/create', 'POST', {}, 'developer').status_code, 403)
        self.assertEqual(self.call('/shipments/destination-changes').status_code, 403)
        self.assertEqual(self.call('/transport-tasks').status_code, 403)

    def test_every_registered_business_endpoint_requires_login(self):
        for path, methods in app.openapi()['paths'].items():
            if not path.startswith('/api/v1/') or path.startswith(('/api/v1/auth/', '/api/v1/admin/')):
                continue
            import re
            concrete = re.sub(r'\{[^}]+\}', '999999999', path)
            for method in methods:
                if method.upper() not in ('GET', 'POST', 'PATCH', 'DELETE', 'PUT'):
                    continue
                response = self.client.request(method.upper(), concrete, json={})
                self.assertEqual(response.status_code, 401, (method, path, response.text))

    def test_unowned_history_company_scope_only(self):
        with SessionLocal.begin() as session:
            order = Order(product_name='history', quantity=1, sender_name='s', sender_address='s', recipient_name='r', recipient_address='r')
            session.add(order)
            session.flush()
            order_id = str(order.id)
        self.assertEqual(self.call('/orders/' + order_id).status_code, 404)
        for role in ('admin', 'ops', 'developer'):
            self.assertEqual(self.call('/orders/' + order_id, actor=role).status_code, 200)

    def shared_shipments(self):
        from shipments.service import create_shipment
        from lines.schemas import LineCreateRequest
        from lines.service import write_line
        from scheduling.schemas import ScheduleConfirmRequest, SchedulePreviewRequest
        from scheduling.service import confirm_schedule, preview_schedule
        order_ids = [int(self.order(label)['id']) for label in ('user', 'other')]
        code = 'V8_' + uuid4().hex[:12].upper()
        with SessionLocal.begin() as session:
            origin = Station(code=code + '_S', name='source', allows_first_arrival=True, allows_delivery=True, transfer_minutes=0)
            destination = Station(code=code + '_D', name='destination', allows_first_arrival=True, allows_delivery=True, transfer_minutes=0)
            session.add_all([origin, destination])
            session.flush()
            origin_id, destination_id = origin.id, destination.id
        with SessionLocal() as session:
            line = write_line(session, LineCreateRequest(code=code, name='line', station_ids=[origin_id, destination_id], legs=[{'travel_minutes': 60}]), uuid4())
        when = server_now().replace(second=0, microsecond=0) + timedelta(hours=2)
        shipments, task_ids = [], []
        for order_id in order_ids:
            with SessionLocal() as session:
                body, _ = create_shipment(session, order_id, uuid4(), destination_id)
            shipment_id = int(body['id'])
            with SessionLocal() as session:
                preview = preview_schedule(session, shipment_id, SchedulePreviewRequest(line_id=int(line['id']), origin_station_id=origin_id, first_departure_at=when))
            with SessionLocal() as session:
                reviewed = confirm_schedule(session, shipment_id, ScheduleConfirmRequest(preview_token=preview['preview_token'], reason='fixture'), uuid4())
            shipments.append(shipment_id)
            task_ids.append(int(reviewed['legs'][0]['task_id']))
        self.assertEqual(task_ids[0], task_ids[1])
        return order_ids, shipments, task_ids[0]

    def test_shared_members_are_hidden_and_task_detail_is_denied(self):
        _, shipments, task = self.shared_shipments()
        own = self.call('/shipments/' + str(shipments[0]))
        self.assertEqual(own.status_code, 200, own.text)
        self.assertTrue(all(not action['enabled'] for action in own.json()['allowed_actions']))
        self.assertEqual(own.json()['schedule']['legs'][0]['waiting_members'], [])
        self.assertEqual(self.call('/shipments/' + str(shipments[1])).status_code, 404)
        self.assertEqual(self.call('/shipments/' + str(shipments[1]) + '/schedule-history').status_code, 404)
        self.assertEqual(self.call('/transport-tasks/' + str(task)).status_code, 403)
        self.assertEqual(self.call('/shipments/' + str(shipments[0]) + '/schedule').json()['legs'][0]['waiting_members'], [])
        company = self.call('/shipments/' + str(shipments[0]), actor='ops').json()
        self.assertEqual(len(company['schedule']['legs'][0]['waiting_members']), 2)

    def test_cancel_own_order_releases_only_own_shared_membership(self):
        orders, shipments, task_id = self.shared_shipments()
        key = str(uuid4())
        response = self.call('/orders/' + str(orders[0]) + '/cancel', 'POST', {'reason': 'cancel own'}, key=key)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'CANCELLED')
        self.assertEqual(self.call('/orders/' + str(orders[0]) + '/cancel', 'POST', {'reason': 'cancel own'}, key=key).json(), response.json())
        self.assertEqual(self.call('/orders/' + str(orders[1]) + '/cancel', 'POST', {'reason': 'wrong owner'}).status_code, 404)
        with SessionLocal() as session:
            self.assertEqual(session.get(Shipment, shipments[0]).stage, 'CANCELLED')
            self.assertNotEqual(session.get(TransportTask, task_id).status, 'CANCELLED')
            entries = session.scalars(select(TaskShipment).where(TaskShipment.task_id == task_id).order_by(TaskShipment.id)).all()
            self.assertEqual([entry.association_state for entry in entries], ['RELEASED', 'PLANNED'])
        from shipments.schemas import ShipmentEventRequest
        from shipments.service import process_shipment_event
        with SessionLocal() as session:
            process_shipment_event(session, shipments[1], ShipmentEventRequest(event_type='PICKUP'), uuid4())
        self.assertEqual(self.call('/orders/' + str(orders[1]) + '/cancel', 'POST', {'reason': 'after pickup'}, 'other').status_code, 409)

    def test_roles_disable_reenable_and_admin_protection(self):
        actor = self.actor('ops')
        user_id = actor['user']['id']
        self.assertEqual(self.call('/admin/users', actor='ops').status_code, 403)
        response = self.call('/admin/users/' + user_id, 'PATCH', {'role': 'developer'}, 'admin')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.call('/orders/create', 'POST', {}, 'ops').status_code, 403)
        self.assertEqual(self.call('/auth/recheck', 'POST', {'auth_version': actor['user']['auth_version']}, 'ops').status_code, 409)
        self.assertEqual(self.call('/admin/users/' + user_id, 'PATCH', {'enabled': False}, 'admin').status_code, 200)
        self.assertEqual(self.call('/orders', actor='ops').status_code, 401)
        self.assertEqual(self.call('/admin/users/' + user_id, 'PATCH', {'enabled': True}, 'admin').status_code, 200)
        self.assertEqual(self.call('/orders', actor='ops').status_code, 401)
        admin_id = self.actor('admin')['user']['id']
        self.assertEqual(self.call('/admin/users/' + admin_id, 'PATCH', {'enabled': False}, 'admin').status_code, 409)
        self.assertEqual(self.call('/admin/users/' + admin_id, 'PATCH', {'role': 'ops'}, 'admin').status_code, 409)
        with self.assertRaises(IntegrityError), SessionLocal.begin() as session:
            session.add(User(username='second-' + uuid4().hex[:12], email=uuid4().hex + '@example.com', role='admin'))
        audit = self.call('/admin/users/' + user_id + '/audit', actor='admin')
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()['total'], 3)
        self.assertNotIn('password', audit.text)

    def test_reset_password_revokes_all_sessions_and_one_time_token(self):
        actor = self.actor()
        response = self.client.post('/api/v1/auth/forgot-password', json={'email': actor['user']['email']})
        self.assertEqual(response.status_code, 202)
        raw = self.email_token()
        body = {'token': raw, 'password': 'changed-password-1234'}
        self.assertEqual(self.client.post('/api/v1/auth/reset-password', json=body).status_code, 200)
        self.assertEqual(self.call('/orders').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/reset-password', json=body).status_code, 400)
        login = self.client.post('/api/v1/auth/login', json={'username': actor['user']['username'], 'password': body['password']})
        self.assertEqual(login.status_code, 200)

    def test_mail_failure_invalidates_token_and_absent_account_response_is_same(self):
        actor = self.actor()
        known = self.client.post('/api/v1/auth/forgot-password', json={'email': actor['user']['email']})
        unknown = self.client.post('/api/v1/auth/forgot-password', json={'email': uuid4().hex + '@example.com'})
        self.assertEqual((known.status_code, known.json()), (unknown.status_code, unknown.json()))
        self.smtp.return_value.__enter__.return_value.send_message.side_effect = smtplib.SMTPException('offline')
        response = self.client.post('/api/v1/auth/forgot-password', json={'email': actor['user']['email']})
        self.assertEqual(response.status_code, 503)
        with SessionLocal() as session:
            entries = session.scalars(select(EmailToken).where(EmailToken.user_id == int(actor['user']['id']))).all()
            self.assertTrue(all(entry.consumed_at for entry in entries))

    def test_agent_child_session_is_read_only_and_parent_logout_revokes_it(self):
        actor = self.actor('ops')
        response = self.call('/auth/agent-token', 'POST', {}, 'ops')
        self.assertEqual(response.status_code, 200, response.text)
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        self.assertEqual(response.json()['login_session_id'], actor['login_session_id'])
        identity = self.client.get('/api/v1/auth/introspect', headers=headers).json()
        self.assertEqual(identity['user']['permissions'], ['business:read'])
        self.assertEqual(identity['login_session_id'], actor['login_session_id'])
        self.assertEqual(self.client.get('/api/v1/orders', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/orders/create', json={}, headers=headers).status_code, 403)
        self.assertEqual(self.call('/auth/logout', 'POST', {}, 'ops').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/orders', headers=headers).status_code, 401)

    def test_cli_pending_polling_approval_read_only_and_replay(self):
        device = self.client.post('/api/v1/auth/cli/authorize').json()
        body = {'device_code': device['device_code']}
        poll = self.client.post('/api/v1/auth/cli/token', json=body)
        self.assertEqual(poll.json(), {'error': 'authorization_pending'})
        self.assertEqual(self.call('/auth/cli/approve', 'POST', {'user_code': device['user_code'], 'approve': True}, 'ops').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/cli/token', json=body).json(), {'error': 'slow_down'})
        with SessionLocal.begin() as session:
            session.get(DeviceGrant, digest(device['device_code'])).last_polled_at = server_now() - timedelta(seconds=6)
        result = self.client.post('/api/v1/auth/cli/token', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        headers = {'Authorization': 'Bearer ' + result.json()['access_token']}
        self.assertEqual(self.client.get('/api/v1/orders', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/v1/orders/create', headers=headers, json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/cli/token', json=body).json(), {'error': 'expired_token'})

    def test_google_new_user_requires_profile_and_state_is_one_time(self):
        start = self.client.get('/api/v1/auth/google/start')
        state = parse_qs(urlparse(start.json()['authorization_url']).query)['state'][0]
        claims = {'sub': uuid4().hex, 'email': 'v8' + uuid4().hex + '@gmail.com', 'email_verified': True}
        with patch('auth.external.exchange_google', return_value=claims):
            result = self.client.get('/api/v1/auth/google/callback', params={'state': state, 'code': 'code'}, follow_redirects=False)
        self.assertEqual(result.status_code, 303, result.text)
        self.assertNotIn('token', result.headers['location'])
        user = self.client.get('/api/v1/auth/me').json()['user']
        self.assertEqual((user['role'], user['needs_username'], user['email_verified']), ('user', True, True))
        self.assertEqual(self.client.get('/api/v1/orders').status_code, 403)
        completed = self.client.post('/api/v1/auth/complete-profile', json={'username': 'v8google-' + uuid4().hex[:12]}, headers=self.browser_headers())
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(self.client.get('/api/v1/orders').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/google/callback', params={'state': state, 'code': 'code'}).status_code, 400)

    def test_google_existing_email_never_merges_or_promotes(self):
        actor = self.actor('ops')
        state = parse_qs(urlparse(self.client.get('/api/v1/auth/google/start').json()['authorization_url']).query)['state'][0]
        claims = {'sub': uuid4().hex, 'email': actor['user']['email'], 'email_verified': True, 'hd': 'example.com'}
        with patch('auth.external.exchange_google', return_value=claims):
            response = self.client.get('/api/v1/auth/google/callback', params={'state': state, 'code': 'code'})
        self.assertEqual(response.status_code, 409)
        with SessionLocal() as session:
            self.assertIsNone(session.get(GoogleIdentity, claims['sub']))
            self.assertEqual(session.get(User, int(actor['user']['id'])).role, 'ops')

    def test_google_explicit_binding_preserves_role_and_ownership(self):
        actor = self.actor('ops')
        self.client.cookies.set(COOKIE, actor['access_token'])
        self.client.cookies.set(CSRF_COOKIE, actor['csrf_token'])
        start = self.client.post('/api/v1/auth/google/bind/start', headers=self.browser_headers())
        self.assertEqual(start.status_code, 200, start.text)
        state = parse_qs(urlparse(start.json()['authorization_url']).query)['state'][0]
        subject = uuid4().hex
        with patch('auth.external.exchange_google', return_value={'sub': subject, 'email': actor['user']['email'], 'email_verified': True}):
            response = self.client.get('/api/v1/auth/google/callback', params={'state': state, 'code': 'code'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        with SessionLocal() as session:
            self.assertEqual(session.get(GoogleIdentity, subject).user_id, int(actor['user']['id']))
        self.assertEqual(self.client.get('/api/v1/auth/me').json()['user']['role'], 'ops')

    def test_google_nonce_and_verification_failures_are_rejected(self):
        from auth.external import exchange_google
        from errors import NetworkError
        with patch('auth.external.urlopen') as request, patch('google.oauth2.id_token.verify_oauth2_token') as verifier:
            request.return_value.__enter__.return_value.read.return_value = b'{"id_token":"test-id-token"}'
            verifier.return_value = {'sub': 'subject', 'nonce': 'wrong'}
            with self.assertRaises(NetworkError) as error:
                exchange_google('code', 'verifier', 'expected')
            self.assertEqual(error.exception.code, 'GOOGLE_AUTH_FAILED')
            verifier.side_effect = ValueError('invalid audience')
            with self.assertRaises(NetworkError):
                exchange_google('code', 'verifier', 'expected')

    def test_cli_denial_expiry_cancellation_and_role_change(self):
        actor = self.actor('ops')
        for mode in ('deny', 'expire', 'cancel', 'change_role'):
            grant = self.client.post('/api/v1/auth/cli/authorize').json()
            body = {'device_code': grant['device_code']}
            if mode in ('deny', 'change_role'):
                self.assertEqual(self.call('/auth/cli/approve', 'POST', {'user_code': grant['user_code'], 'approve': mode != 'deny'}, 'ops').status_code, 200)
            if mode == 'expire':
                with SessionLocal.begin() as session:
                    session.get(DeviceGrant, digest(grant['device_code'])).expires_at = server_now() - timedelta(seconds=1)
            if mode == 'cancel':
                self.client.post('/api/v1/auth/cli/cancel', json=body)
            if mode == 'change_role':
                self.call('/admin/users/' + actor['user']['id'], 'PATCH', {'role': 'developer'}, 'admin')
            response = self.client.post('/api/v1/auth/cli/token', json=body)
            self.assertEqual(response.json()['error'], 'expired_token' if mode == 'expire' else 'access_denied')

    def test_authorization_changed_before_business_write_is_rejected(self):
        from auth.service import Principal
        from errors import NetworkError
        from orders.schemas import OrderCreateRequest
        from orders.service import create_order
        actor = self.actor('ops')
        self.call('/admin/users/' + actor['user']['id'], 'PATCH', {'role': 'developer'}, 'admin')
        with SessionLocal() as session:
            session.info['principal'] = Principal(int(actor['user']['id']), 'ops', actor['user']['auth_version'], digest(actor['access_token']), False)
            with self.assertRaises(NetworkError) as error:
                create_order(session, OrderCreateRequest(product_name='stale', quantity=1, sender_name='s', sender_address='s', recipient_name='r', recipient_address='r'), uuid4())
            self.assertEqual(error.exception.code, 'AUTH_CHANGED')

    def test_cancel_and_pickup_race_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from errors import InvalidShipmentStateError, NetworkError
        from orders.schemas import OrderCancelRequest
        from orders.service import cancel_order
        from shipments.schemas import ShipmentEventRequest
        from shipments.service import process_shipment_event
        orders, shipments, _ = self.shared_shipments()
        def cancel():
            with SessionLocal() as session:
                try:
                    cancel_order(session, orders[0], OrderCancelRequest(reason='race'), uuid4())
                    return True
                except NetworkError:
                    return False
        def pickup():
            with SessionLocal() as session:
                try:
                    process_shipment_event(session, shipments[0], ShipmentEventRequest(event_type='PICKUP'), uuid4())
                    return True
                except InvalidShipmentStateError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [executor.submit(cancel), executor.submit(pickup)]
            self.assertEqual(sum(result.result() for result in results), 1)

    def test_rate_limits_persist_across_requests(self):
        from auth.security import throttle
        from errors import NetworkError
        identifier = uuid4().hex
        throttle('test', identifier, limit=1)
        with self.assertRaises(NetworkError) as error:
            throttle('test', identifier, limit=1)
        self.assertEqual(error.exception.status, 429)

    def test_permission_change_during_query_discards_response(self):
        import orders.router as order_router
        original = order_router.list_orders
        actor = self.actor()
        def change_role(*args, **kwargs):
            result = original(*args, **kwargs)
            with SessionLocal.begin() as session:
                user = session.get(User, int(actor['user']['id']))
                user.role, user.auth_version = 'developer', user.auth_version + 1
            return result
        with patch('orders.router.list_orders', side_effect=change_role):
            response = self.call('/orders')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'AUTH_CHANGED')


if __name__ == '__main__':
    unittest.main()
