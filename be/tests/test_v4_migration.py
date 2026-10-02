"""Upgrade V3 history and replay actual old task operation keys."""
import json
import os
import subprocess
import sys
import unittest
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from transport.schemas import TransportTaskCreateRequest, TransportTaskResponse, TransportTaskDetailResponse
from transport.service import (create_transport_task, depart_transport_task, arrive_transport_task,
    build_create_task_request_hash, build_depart_task_request_hash, build_arrive_task_request_hash)
from shipments.schemas import ShipmentDetailResponse
import test_v2_migration as helpers

V3 = 'a83c9e14d602'
V4 = 'd92f4b76e301'

@unittest.skipUnless(os.getenv('RUN_V2_MIGRATION_TESTS') == '1', 'opt-in migration rehearsal')
class V4MigrationTests(unittest.TestCase):
    setUp = helpers.V2MigrationTests.setUp
    _drop_database = helpers.V2MigrationTests._drop_database
    run_migration = helpers.V2MigrationTests.run_migration
    seed_history = helpers.V2MigrationTests.seed_history
    snapshot = helpers.V2MigrationTests.snapshot

    def test_v3_history_caches_and_actual_old_key_replay(self):
        self.seed_history(); self.run_migration(V3)
        fixtures = []
        with self.engine.begin() as conn:
            tasks = conn.execute(text('SELECT t.*,r.code route_code,r.origin_station_id,r.destination_station_id FROM transport_tasks t JOIN transport_routes r ON t.route_id=r.id ORDER BY t.id')).mappings().all()
            for action, fn in [('CREATE_TRANSPORT_TASK', create_transport_task),
                               ('DEPART_TRANSPORT_TASK', depart_transport_task),
                               ('ARRIVE_TRANSPORT_TASK', arrive_transport_task)]:
                task = next(t for t in tasks if t['status'] == ('ARRIVED' if action=='ARRIVE_TRANSPORT_TASK' else 'IN_TRANSIT'))
                body = {k: task[k] for k in ('task_no','route_code','status','delay_monitoring_enabled')}
                body.update({k: str(task[k]) for k in ('id','origin_station_id','destination_station_id')})
                body.update({k: task[k].isoformat() if task[k] else None for k in ('expected_arrival_at','departed_at','arrived_at','created_at')})
                body['shipments'] = []
                if action=='DEPART_TRANSPORT_TASK':
                    body.update(simulation_time=body['departed_at'], delay_status='NONE', delay_minutes=0,
                        allowed_actions=[{'action':'DEPART','enabled':False},{'action':'ARRIVE','enabled':True}])
                ids = list(conn.execute(text('SELECT shipment_id FROM task_shipments WHERE task_id=:id'), {'id':task['id']}).scalars())
                request = TransportTaskCreateRequest(route_code=task['route_code'], expected_arrival_at=task['expected_arrival_at'], shipment_ids=ids)
                digest = (build_create_task_request_hash(request) if action=='CREATE_TRANSPORT_TASK' else
                    build_depart_task_request_hash(task['id']) if action=='DEPART_TRANSPORT_TASK' else build_arrive_task_request_hash(task['id']))
                key = uuid4()
                conn.execute(text("INSERT INTO operation_logs (idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,:action,'TRANSPORT_TASK',:id,CAST(:body AS jsonb),:status,now())"),
                    dict(key=key, hash=digest, action=action, id=task['id'], body=json.dumps(body), status=201 if action=='CREATE_TRANSPORT_TASK' else 200))
                fixtures.append((action, fn, request if action=='CREATE_TRANSPORT_TASK' else task['id'], key))
        before = self.snapshot()
        self.run_migration('head')
        after = self.snapshot()
        for table in ('shipments', 'task_shipments', 'tracking_events'):
            self.assertEqual([{k:r[k] for k in old} for old,r in zip(before[table],after[table])], before[table])
        for old, new in zip(before['transport_tasks'], after['transport_tasks']):
            self.assertEqual({k:new[k] for k in old}, dict(old))
            self.assertIsNone(new['cancelled_at']); self.assertIsNone(new['cancel_reason'])
        for old, new in zip(before['operation_logs'], after['operation_logs']):
            for k in old:
                if k not in ('before_data','after_data','response_body'):
                    self.assertEqual(old[k], new[k])
            body = new['response_body']
            if 'task_no' in body:
                TransportTaskResponse.model_validate(body)
                self.assertIsNone(body['cancelled_at'])
                if 'allowed_actions' in body:
                    from business_time import normalize_cached_response
                    TransportTaskDetailResponse.model_validate(normalize_cached_response(body))
                    self.assertFalse(next(a for a in body['allowed_actions'] if a['action']=='CANCEL')['enabled'])
            else:
                ShipmentDetailResponse.model_validate(body)
                added = next(a for a in body['allowed_actions'] if a['action']=='CREATE_TRANSPORT_TASK')
                self.assertEqual((added['enabled'], added['reason_code']), (False, 'REFRESH_REQUIRED'))
        for action, fn, arg, key in fixtures:
            expected = next(log['response_body'] for log in after['operation_logs'] if log['idempotency_key']==key)
            with Session(self.engine) as session:
                actual = fn(session, arg, key)
                if 'simulation_time' in expected:
                    expected = {k:v for k,v in expected.items() if k != 'simulation_time'}
                    self.assertIn('server_time', actual)
                    actual = {k:v for k,v in actual.items() if k != 'server_time'}
                self.assertEqual(actual, expected)
        self.assertEqual(self.snapshot(), after)
        env = {**os.environ, 'DATABASE_URL':self.url.render_as_string(hide_password=False)}
        failed = subprocess.run([sys.executable,'-m','alembic','downgrade',V3], env=env, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('restore the pre-upgrade backup', failed.stderr)
        self.assertEqual(self.snapshot(), after)

    def test_empty_database_and_pending_cached_action(self):
        self.run_migration(V3)
        # Test recursive nested snapshots without depending on current network state.
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO operation_logs (idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,'LEGACY_SNAPSHOT','TRANSPORT_TASK',1,CAST(:body AS jsonb),200,now())"),
                dict(key=uuid4(), hash='1'*64, body=json.dumps({'nested':{'task_no':'TRIP-OLD','route_code':'OLD','status':'PENDING_DEPARTURE','allowed_actions':[]}})))
        self.run_migration('head')
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), helpers.HEAD)
            body = conn.execute(text('SELECT response_body FROM operation_logs')).scalar_one()['nested']
            self.assertTrue(body['allowed_actions'][0]['enabled'])
            self.assertIsNone(body['cancel_reason'])
            self.assertEqual(conn.execute(text('SELECT count(*) FROM transport_tasks')).scalar_one(), 0)
