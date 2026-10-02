"""V5-to-V6 preservation, cached-key replay and legacy task continuation."""
import os
import json
import unittest
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import text,select
from sqlalchemy.orm import Session
from models import Shipment,TransportTask,TaskShipment
from transport.schemas import TransportTaskCreateRequest,TransportTaskResponse
from transport.service import (build_create_task_request_hash,create_transport_task,
    depart_transport_task,arrive_transport_task)
from planning.schemas import ShipmentPathUpdateRequest,PathPlanCreateRequest
from planning.service import write_shipment_path,shipment_path_body,write_plan
from shipments.schemas import ShipmentDetailResponse
from errors import InvalidTaskShipmentError
import test_v2_migration as helpers

V5='d92f4b76e301'
V6='e61a7c93b204'

@unittest.skipUnless(os.getenv('RUN_V2_MIGRATION_TESTS')=='1','opt-in migration rehearsal')
class V6MigrationTests(unittest.TestCase):
    setUp=helpers.V2MigrationTests.setUp
    _drop_database=helpers.V2MigrationTests._drop_database
    run_migration=helpers.V2MigrationTests.run_migration
    seed_history=helpers.V2MigrationTests.seed_history
    snapshot=helpers.V2MigrationTests.snapshot

    def test_history_preserved_old_creation_replay_and_frozen_legacy_transit(self):
        self.seed_history(); self.run_migration(V5)
        with self.engine.begin() as conn:
            routes={r.code:r for r in conn.execute(text('SELECT * FROM transport_routes')).mappings()}
            clock=conn.execute(text('SELECT "current_time" FROM simulation_settings')).scalar_one()
            shipment_id=self.shipments['AT_A']
            task_id=conn.execute(text("INSERT INTO transport_tasks (route_id,expected_arrival_at) VALUES (:route,:expected) RETURNING id"),
                dict(route=routes['AB']['id'],expected=clock+timedelta(days=1))).scalar_one()
            conn.execute(text('INSERT INTO task_shipments (task_id,shipment_id) VALUES (:task,:shipment)'),dict(task=task_id,shipment=shipment_id))
            task=conn.execute(text('SELECT * FROM transport_tasks WHERE id=:id'),dict(id=task_id)).mappings().one()
            body={k:task[k] for k in ('task_no','status','delay_monitoring_enabled')}
            body.update(id=str(task_id),route_code='AB',origin_station_id=str(routes['AB']['origin_station_id']),
                destination_station_id=str(routes['AB']['destination_station_id']),cancelled_at=None,cancel_reason=None,shipments=[])
            body.update({k:task[k].isoformat() if task[k] else None for k in ('expected_arrival_at','departed_at','arrived_at','created_at')})
            request=TransportTaskCreateRequest(route_code='AB',expected_arrival_at=task['expected_arrival_at'],shipment_ids=[shipment_id])
            key=uuid4()
            conn.execute(text("INSERT INTO operation_logs (idempotency_key,request_hash,action,resource_type,resource_id,response_body,response_status,occurred_at) VALUES (:key,:hash,'CREATE_TRANSPORT_TASK','TRANSPORT_TASK',:task,CAST(:body AS jsonb),201,now())"),
                dict(key=key,hash=build_create_task_request_hash(request),task=task_id,body=json.dumps(body)))
        before=self.snapshot(); self.run_migration('head'); after=self.snapshot()
        for table in before:
            self.assertEqual(len(before[table]),len(after[table]))
            for old,new in zip(before[table],after[table]): self.assertEqual({k:new[k] for k in old},dict(old))
        self.assertTrue(all(s['path_version']==0 for s in after['shipments']))
        self.assertTrue(all(a['path_leg_id'] is None for a in after['task_shipments']))
        for log in after['operation_logs']:
            if log['resource_type']=='SHIPMENT': ShipmentDetailResponse.model_validate(log['response_body'])
        with Session(self.engine) as session:
            replay=create_transport_task(session,request,key)
        self.assertEqual(replay,body); TransportTaskResponse.model_validate(replay)
        self.assertEqual(self.snapshot(),after)
        # An unplanned legacy parcel cannot create another arbitrary task.
        with Session(self.engine) as session:
            unplanned=self.shipments['AT_B']
            with self.assertRaises(InvalidTaskShipmentError):
                create_transport_task(session,TransportTaskCreateRequest(route_code='BC',expected_arrival_at=clock+timedelta(days=1),shipment_ids=[unplanned]),uuid4())
        # Existing pending tasks continue without inventing a path retroactively.
        with Session(self.engine) as session: depart_transport_task(session,task_id,uuid4())
        with Session(self.engine) as session: arrive_transport_task(session,task_id,uuid4())
        # Existing in-transit routes can be linked as a frozen prefix; only the remainder is chosen.
        moving=self.shipments['IN_TRANSIT_AB']
        with Session(self.engine) as session:
            association=session.scalar(select(TaskShipment).join(TransportTask).where(
                TaskShipment.shipment_id==moving,TaskShipment.released_at.is_(None)))
            active_task_id=association.task_id
            task=session.get(TransportTask,active_task_id)
            original=(task.route_id,task.departed_at,task.expected_arrival_at)
        with Session(self.engine) as session:
            selected=write_shipment_path(session,moving,ShipmentPathUpdateRequest(expected_version=0,
                expected_anchor_station_id=routes['AB']['destination_station_id'],route_ids=[routes['BC']['id']],reason='补齐未来安排'),uuid4())
        self.assertEqual([l['state'] for l in selected['legs']],['IN_TRANSIT','PENDING'])
        with Session(self.engine) as session:
            task=session.get(TransportTask,active_task_id)
            self.assertEqual((task.route_id,task.departed_at,task.expected_arrival_at),original)
        with Session(self.engine) as session: arrive_transport_task(session,active_task_id,uuid4())
        with Session(self.engine) as session:
            current=shipment_path_body(session,session.get(Shipment,moving))
            self.assertEqual(current['next_route_code'],'BC')
        with Session(self.engine) as session:
            write_plan(session,PathPlanCreateRequest(code='BC_FUTURE',name='后续路线',route_ids=[routes['BC']['id']]),uuid4())
        # A legacy parcel already at B can adopt a whole remaining path before its next task.
        with Session(self.engine) as session:
            next_task=create_transport_task(session,TransportTaskCreateRequest(route_code='BC',
                expected_arrival_at=clock+timedelta(days=1),shipment_ids=[unplanned]),uuid4())
        self.assertEqual(next_task['route_code'],'BC')

    def test_empty_database_reaches_v6_without_fabricated_paths(self):
        self.run_migration(V6)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),V6)
            for table in ('path_plans','path_plan_legs','shipment_path_legs','shipment_path_versions'):
                self.assertEqual(conn.execute(text(f'SELECT count(*) FROM {table}')).scalar_one(),0)
