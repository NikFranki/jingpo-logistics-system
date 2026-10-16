"""V2-to-V3 replay in ephemeral databases, including old creation keys."""
import hashlib
import json
import os
import unittest
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from shipments.schemas import ShipmentDetailResponse
from shipments.service import create_shipment
from transport.schemas import TransportTaskResponse
from transport.service import depart_transport_task, build_depart_task_request_hash
import test_v2_migration as v2_rehearsal

V2 = 'f714e269c2db'
V3 = 'a83c9e14d602'

@unittest.skipUnless(os.getenv('RUN_V2_MIGRATION_TESTS') == '1', 'opt-in migration rehearsal')
class V3MigrationTests(unittest.TestCase):
    setUp = v2_rehearsal.V2MigrationTests.setUp
    _drop_database = v2_rehearsal.V2MigrationTests._drop_database
    run_migration = v2_rehearsal.V2MigrationTests.run_migration
    seed_history = v2_rehearsal.V2MigrationTests.seed_history
    snapshot = v2_rehearsal.V2MigrationTests.snapshot

    def test_v2_history_and_create_key_replay(self):
        self.seed_history()
        self.run_migration(V2)
        shipment_id = self.shipments['PENDING_PICKUP']
        key = uuid4()
        with self.engine.begin() as conn:
            body = conn.execute(text("SELECT response_body FROM operation_logs WHERE resource_id=:id ORDER BY id LIMIT 1"), {'id':shipment_id}).scalar_one()
            conn.execute(text("INSERT INTO operation_logs (idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,'CREATE_SHIPMENT','SHIPMENT',:id,CAST(:body AS jsonb),201,now())"),
                {'key':key, 'hash':hashlib.sha256(f"CREATE_SHIPMENT:{body['order_id']}".encode()).hexdigest(), 'id':shipment_id, 'body':json.dumps(body)})
        task_key = uuid4()
        with self.engine.begin() as conn:
            task = conn.execute(text("SELECT t.*,r.code AS route_code,r.origin_station_id,r.destination_station_id FROM transport_tasks t JOIN transport_routes r ON r.id=t.route_id WHERE t.status='IN_TRANSIT' AND r.code='AB' LIMIT 1")).mappings().one()
            task_id = task['id']
            task_body = {field: task[field] for field in ('task_no','route_code','status')}
            task_body.update({field: str(task[field]) for field in ('id','origin_station_id','destination_station_id')})
            task_body.update({field: task[field].isoformat() if task[field] else None for field in ('expected_arrival_at','departed_at','arrived_at','created_at')})
            task_body['shipments'] = []
            conn.execute(text("INSERT INTO operation_logs (idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,'DEPART_TRANSPORT_TASK','TRANSPORT_TASK',:id,CAST(:body AS jsonb),200,now())"),
                {'key':task_key,'hash':build_depart_task_request_hash(task_id),'id':task_id,'body':json.dumps(task_body)})
        before = self.snapshot()
        # Fixed-code V2 could still have incorrectly connected routes: reject atomically.
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE transport_routes SET destination_station_id=(SELECT id FROM stations WHERE code='C') WHERE code='AB'"))
        failure=self.run_migration('head',succeeds=False)
        self.assertIn('Invalid V2 route endpoints',failure.stderr)
        with self.engine.begin() as conn:
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),V2)
            conn.execute(text("UPDATE transport_routes SET destination_station_id=(SELECT id FROM stations WHERE code='B') WHERE code='AB'"))
        self.assertEqual(self.snapshot(),before)
        self.run_migration('head')
        after=self.snapshot()
        self.assertEqual(after['tracking_events'],before['tracking_events'])
        self.assertEqual([{k:r[k] for k in old} for old,r in zip(before['task_shipments'],after['task_shipments'])],before['task_shipments'])
        with self.engine.connect() as conn:
            c=conn.execute(text("SELECT id FROM stations WHERE code='C'")).scalar_one()
            capabilities=conn.execute(text('SELECT code,allows_first_arrival,allows_delivery FROM stations ORDER BY code')).all()
            self.assertEqual(capabilities,[('A',True,False),('B',False,False),('C',False,True)])
            monitored={r.id:r.delay_monitoring_enabled for r in conn.execute(text('SELECT id,delay_monitoring_enabled FROM transport_routes'))}
        for old,new in zip(before['shipments'],after['shipments']):
            self.assertEqual({k:new[k] for k in old},dict(old))
            self.assertEqual(new['destination_station_id'],c)
        for old,new in zip(before['transport_tasks'],after['transport_tasks']):
            self.assertEqual({k:new[k] for k in old},dict(old))
            self.assertEqual(new['delay_monitoring_enabled'],monitored[new['route_id']])
        for old,new in zip(before['operation_logs'],after['operation_logs']):
            self.assertEqual(new['idempotency_key'],old['idempotency_key'])
            self.assertEqual(new['occurred_at'],old['occurred_at'])
            if new['resource_type'] == 'SHIPMENT':
                response=ShipmentDetailResponse.model_validate(new['response_body'])
                self.assertEqual(response.destination_station_id,str(c))
            else:
                response=TransportTaskResponse.model_validate(new['response_body'])
                self.assertTrue(response.delay_monitoring_enabled)
        with Session(self.engine) as session:
            replay,status=create_shipment(session,int(body['order_id']),key,c)
        self.assertEqual(status,201)
        self.assertEqual(replay['destination_station_id'],str(c))
        with Session(self.engine) as session:
            task_replay=depart_transport_task(session,task_id,task_key)
        self.assertTrue(task_replay['delay_monitoring_enabled'])
        self.assertEqual(task_replay['id'],str(task_id))
        self.assertEqual(self.snapshot(),after)

    def test_missing_c_refuses_without_partial_schema(self):
        self.run_migration(V2)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO orders (product_name,quantity,sender_name,sender_address,recipient_name,recipient_address) VALUES ('sample',1,'s','s','r','r')"))
            conn.execute(text("INSERT INTO shipments (order_id,sender_address,recipient_address) SELECT id,'s','r' FROM orders"))
        before=self.snapshot()
        failure=self.run_migration('head',succeeds=False)
        self.assertIn('Station C is required',failure.stderr)
        self.assertEqual(self.snapshot(),before)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),V2)
