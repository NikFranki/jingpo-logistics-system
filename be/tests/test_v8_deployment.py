import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


class V8DeploymentTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('RUN_V8_MIGRATION_TESTS') == '1', 'opt-in V8 upgrade rehearsal')
    def test_existing_history_survives_upgrade_and_admin_recovery(self):
        source = make_url(os.environ['DATABASE_URL'])
        self.assertTrue((source.database or '').endswith('_test'), 'requires isolated test database configuration')
        name = 'jingpo_v8_upgrade_' + uuid4().hex[:12] + '_test'
        control = create_engine(source.set(database='postgres'), isolation_level='AUTOCOMMIT')
        with control.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        url = source.set(database=name).render_as_string(hide_password=False)
        env = {**os.environ, 'DATABASE_URL': url}
        engine = create_engine(url)
        try:
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'e2a7c4b19d63'], env=env, check=True, capture_output=True)
            with engine.begin() as connection:
                origin = connection.scalar(text("INSERT INTO stations(code,name,allows_first_arrival,allows_delivery) VALUES ('V8S','source',true,true) RETURNING id"))
                destination = connection.scalar(text("INSERT INTO stations(code,name,allows_first_arrival,allows_delivery) VALUES ('V8D','destination',true,true) RETURNING id"))
                route = connection.scalar(text("INSERT INTO transport_routes(code,origin_station_id,destination_station_id,travel_minutes) VALUES ('V8R',:origin,:destination,60) RETURNING id"), {'origin': origin, 'destination': destination})
                order = connection.scalar(text("INSERT INTO orders(product_name,quantity,sender_name,sender_address,recipient_name,recipient_address,status) VALUES ('history',1,'s','s','r','r','SHIPMENT_CREATED') RETURNING id"))
                shipment = connection.scalar(text("INSERT INTO shipments(order_id,sender_address,recipient_address,destination_station_id) VALUES (:order,'s','r',:destination) RETURNING id"), {'order': order, 'destination': destination})
                task = connection.scalar(text("INSERT INTO transport_tasks(route_id,status,expected_arrival_at) VALUES (:route,'PENDING_DEPARTURE',now()+interval '1 day') RETURNING id"), {'route': route})
                connection.execute(text("INSERT INTO task_shipments(task_id,shipment_id) VALUES (:task,:shipment)"), {'task': task, 'shipment': shipment})
                operation = connection.scalar(text("INSERT INTO operation_logs(idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,'CREATE_SHIPMENT','SHIPMENT',:shipment,'{}',201,now()) RETURNING id"), {'key': uuid4(), 'hash': '0' * 64, 'shipment': shipment})
                connection.execute(text("INSERT INTO tracking_events(shipment_id,event_type,operation_id,occurred_at) VALUES (:shipment,'SHIPMENT_CREATED',:operation,now())"), {'shipment': shipment, 'operation': operation})
                tables = ('orders', 'shipments', 'transport_tasks', 'task_shipments', 'tracking_events', 'operation_logs')
                before = {table: connection.scalar(text(f'SELECT jsonb_agg(to_jsonb(entry) ORDER BY id) FROM {table} entry')) for table in tables}
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env=env, check=True, capture_output=True)
            with engine.connect() as connection:
                after = {}
                for table in tables:
                    row = "to_jsonb(entry) - 'created_by_user_id' - 'cancelled_at' - 'cancel_reason'" if table == 'orders' else "to_jsonb(entry) - 'actor_user_id'" if table == 'operation_logs' else 'to_jsonb(entry)'
                    after[table] = connection.scalar(text(f'SELECT jsonb_agg({row} ORDER BY id) FROM {table} entry'))
                self.assertEqual(after, before)
                self.assertIsNone(connection.scalar(text('SELECT created_by_user_id FROM orders LIMIT 1')))
            for action in ('init', 'restore'):
                script = "from unittest.mock import patch; import sys; from auth.admin import main; sys.argv=['admin','" + action + "','--username','v8admin','--email','admin@example.com']; context=patch('auth.admin.getpass',return_value='migration-test-password-123'); context.start(); main()"
                subprocess.run([sys.executable, '-c', script], env=env, check=True, capture_output=True)
                if action == 'init':
                    with engine.begin() as connection:
                        connection.execute(text("INSERT INTO auth_sessions(token_hash,user_id,channel,csrf_hash,created_at,expires_at) SELECT :hash,id,'web',:hash,now(),now()+interval '1 day' FROM auth_users WHERE role='admin'"), {'hash': '1' * 64})
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM auth_users WHERE role='admin'")), 1)
                self.assertIsNotNone(connection.scalar(text('SELECT revoked_at FROM auth_sessions LIMIT 1')))
            subprocess.run([sys.executable, '-m', 'alembic', 'check'], env=env, check=True, capture_output=True)
        finally:
            engine.dispose()
            with control.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            control.dispose()

    @unittest.skipUnless(os.getenv('RUN_V8_SMTP_TESTS') == '1', 'opt-in local Mailpit test')
    def test_real_mailpit_registration_verification_and_reset(self):
        from fastapi.testclient import TestClient
        from main import app
        from auth.dependencies import COOKIE
        self.assertTrue((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test'))
        name = 'jingpo-v8-mail-' + uuid4().hex[:12]
        subprocess.run(['docker', 'run', '-d', '--rm', '--name', name,
            '-p', '127.0.0.1::1025', '-p', '127.0.0.1::8025', 'axllent/mailpit:v1.27'], check=True, capture_output=True)
        try:
            smtp_port = subprocess.check_output(['docker', 'port', name, '1025/tcp'], text=True).strip().rsplit(':', 1)[1]
            web_port = subprocess.check_output(['docker', 'port', name, '8025/tcp'], text=True).strip().rsplit(':', 1)[1]
            base = 'http://127.0.0.1:' + web_port
            for _ in range(100):
                try:
                    with urlopen(base + '/api/v1/messages', timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            environment = {'AUTH_COOKIE_SECURE': 'false', 'SMTP_HOST': '127.0.0.1', 'SMTP_PORT': smtp_port,
                'SMTP_STARTTLS': 'false', 'SMTP_SSL': 'false', 'SMTP_USERNAME': ''}
            with patch.dict(os.environ, environment), TestClient(app) as client:
                username = 'mail-' + uuid4().hex[:16]
                body = {'username': username, 'email': username + '@localhost.test', 'password': 'mail-test-password-123'}
                response = client.post('/api/v1/auth/register', json=body)
                self.assertEqual(response.status_code, 202, response.text)
                def received_token():
                    with urlopen(base + '/api/v1/messages', timeout=3) as response:
                        message_id = json.load(response)['messages'][0]['ID']
                    with urlopen(base + '/api/v1/message/' + message_id, timeout=3) as response:
                        content = json.load(response)['Text']
                    link = next(line for line in content.splitlines() if line.startswith('http'))
                    return parse_qs(urlparse(link).query)['token'][0]
                token = received_token()
                self.assertEqual(client.post('/api/v1/auth/verify-email', json={'token': token}).status_code, 200)
                self.assertEqual(client.post('/api/v1/auth/login', json={'username': username, 'password': body['password']}).status_code, 200)
                self.assertTrue(client.cookies.get(COOKIE))
                self.assertEqual(client.post('/api/v1/auth/forgot-password', json={'email': body['email']}).status_code, 202)
                reset = received_token()
                self.assertEqual(client.post('/api/v1/auth/reset-password', json={'token': reset, 'password': 'new-mail-password-123'}).status_code, 200)
                self.assertEqual(client.get('/api/v1/auth/me').status_code, 401)
                self.assertEqual(client.post('/api/v1/auth/login', json={'username': username, 'password': 'new-mail-password-123'}).status_code, 200)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
