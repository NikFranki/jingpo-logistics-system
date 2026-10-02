"""V4 transactional cancellation and operation qualification on *_test only."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from db import SessionLocal, engine
from errors import (InvalidTransportTaskStateError, InvalidTaskShipmentError,
    IdempotencyKeyReusedError, ShipmentNotFoundError, NetworkError)
from models import Shipment, TransportTask, TaskShipment, TrackingEvent, OperationLog, Order
from transport.schemas import TransportTaskCancelRequest, TransportTaskCreateRequest
from transport.service import (cancel_transport_task, get_transport_task, create_transport_task,
    depart_transport_task, arrive_transport_task, calculate_task_delay)
from shipments.service import get_shipment, build_shipment_response_body, list_shipment_transport_tasks
from network.service import write_network
from network.schemas import RouteUpdateRequest, StationUpdateRequest
import test_v3_network as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V4CancellationTests(unittest.TestCase):
    call = helpers.V3NetworkTests.call
    setUp = helpers.V3NetworkTests.setUp
    station = helpers.V3NetworkTests.station
    route = helpers.V3NetworkTests.route
    parcel = helpers.V3NetworkTests.parcel
    event = helpers.V3NetworkTests.event
    task = helpers.V3NetworkTests.task

    def ready(self):
        shipment, order = self.parcel()
        self.event(shipment, 'PICKUP')
        self.event(shipment, 'ARRIVE', self.source)
        return shipment, order

    def detail(self, shipment):
        with SessionLocal() as session:
            parcel, events = get_shipment(session, shipment)
            return build_shipment_response_body(session, parcel, events)

    def cancel(self, task, reason='线路错误', key=None):
        return self.call(cancel_transport_task, int(task['id']),
            TransportTaskCancelRequest(reason=reason), key or uuid4())

    def enabled(self, shipment):
        return {a['action']: a['enabled'] for a in self.detail(shipment)['allowed_actions']}

    def test_batch_release_rebuild_history_and_complete(self):
        pairs = [self.ready(), self.ready()]
        ids = [p[0] for p in pairs]
        before = [self.detail(i) for i in ids]
        task = self.call(create_transport_task, TransportTaskCreateRequest(route_code=self.first['code'],
            expected_arrival_at=self.clock+timedelta(hours=1), shipment_ids=ids), uuid4())
        self.assertFalse(self.enabled(ids[0])['CREATE_TRANSPORT_TASK'])
        cancelled = self.cancel(task, '  线路错误  ')
        self.assertEqual(cancelled['cancel_reason'], '线路错误')
        self.assertEqual(cancelled['status'], 'CANCELLED')
        self.assertEqual((cancelled['delay_status'], cancelled['delay_minutes']), ('NOT_APPLICABLE', None))
        self.assertTrue(all(not a['enabled'] for a in cancelled['allowed_actions']))
        for shipment, snapshot in zip(ids, before):
            self.assertEqual(self.detail(shipment), snapshot)
            history, total = self.call(list_shipment_transport_tasks, shipment, 1, 20)
            self.assertEqual(total, 1)
            self.assertEqual(history[0]['status'], 'CANCELLED')
            self.assertEqual(history[0]['released_at'].isoformat(), cancelled['cancelled_at'])
        new = self.call(create_transport_task, TransportTaskCreateRequest(route_code=self.first['code'],
            expected_arrival_at=self.clock+timedelta(hours=1), shipment_ids=ids), uuid4())
        history, total = self.call(list_shipment_transport_tasks, ids[0], 1, 1)
        self.assertEqual((total, history[0]['id'], history[0]['released_at']), (2, new['id'], None))
        second, _ = self.call(list_shipment_transport_tasks, ids[0], 2, 1)
        self.assertEqual(second[0]['id'], task['id'])
        for fn in (depart_transport_task, arrive_transport_task):
            self.call(fn, int(new['id']), uuid4())
        for shipment, order in pairs:
            final_task = self.task(self.second, shipment)
            self.call(depart_transport_task, int(final_task['id']), uuid4())
            self.call(arrive_transport_task, int(final_task['id']), uuid4())
            self.event(shipment, 'START_DELIVERY'); self.event(shipment, 'SIGN')
            self.assertEqual(len(self.detail(shipment)['tracking_events']), 9)
            history, total = self.call(list_shipment_transport_tasks, shipment, 1, 20)
            self.assertEqual(total, 3)
            self.assertEqual([item['status'] for item in history], ['ARRIVED', 'ARRIVED', 'CANCELLED'])
            self.assertTrue(all(item['released_at'] is not None for item in history))
            with SessionLocal() as session:
                original = session.get(Order, order)
                self.assertEqual((original.status, original.recipient_address), ('COMPLETED', '下单收件地址'))
        with SessionLocal() as session:
            log = session.scalar(select(OperationLog).where(OperationLog.action=='CANCEL_TRANSPORT_TASK', OperationLog.resource_id==int(task['id'])))
            self.assertEqual(len(log.after_data['released_association_ids']), 2)
            self.assertEqual(log.before_data['status'], 'PENDING_DEPARTURE')
            self.assertEqual(session.scalar(select(func.count()).select_from(TrackingEvent).where(TrackingEvent.task_id==int(task['id']))), 0)

    def test_retries_do_not_release_new_task_or_overwrite_reason(self):
        shipment, _ = self.ready()
        task = self.task(self.first, shipment)
        key = uuid4()
        first = self.cancel(task, '  重排  ', key)
        self.assertEqual(self.cancel(task, '重排', key), first)
        new = self.task(self.first, shipment)
        self.call(depart_transport_task, int(new['id']), uuid4())
        self.assertEqual(self.cancel(task, '重排', key), first)  # original snapshot
        retry = self.cancel(task, '重排')
        self.assertEqual(retry['cancelled_at'], first['cancelled_at'])
        self.assertEqual(retry['shipments'][0]['stage'], 'IN_TRANSIT')
        for retry_key in (key, uuid4()):
            with self.assertRaises((IdempotencyKeyReusedError, InvalidTransportTaskStateError)):
                self.cancel(task, '不同原因', retry_key)
        self.assertEqual(self.detail(shipment)['active_transport_task']['id'], new['id'])
        with SessionLocal() as session:
            active = session.scalar(select(TaskShipment).where(TaskShipment.shipment_id==shipment, TaskShipment.released_at.is_(None)))
            self.assertEqual(active.task_id, int(new['id']))

    def test_state_guards_and_cancel_depart_race(self):
        shipment, _ = self.ready()
        task = self.task(self.first, shipment)
        def act(kind):
            try:
                if kind == 'cancel':
                    return kind, self.cancel(task)
                return kind, self.call(depart_transport_task, int(task['id']), uuid4())
            except InvalidTransportTaskStateError:
                return 'rejected', None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(act, ['cancel', 'depart']))
        self.assertEqual(sum(kind!='rejected' for kind, _ in results), 1)
        status = self.call(get_transport_task, int(task['id']))['status']
        if status == 'IN_TRANSIT':
            with self.assertRaises(InvalidTransportTaskStateError):
                self.cancel(task)
            self.call(arrive_transport_task, int(task['id']), uuid4())
            with self.assertRaises(InvalidTransportTaskStateError):
                self.cancel(task)
        else:
            for fn in (depart_transport_task, arrive_transport_task):
                with self.assertRaises(InvalidTransportTaskStateError):
                    self.call(fn, int(task['id']), uuid4())
        # Always cover both terminal guards irrespective of race winner.
        shipment, _ = self.ready(); cancelled = self.task(self.first, shipment); self.cancel(cancelled)
        for fn in (depart_transport_task, arrive_transport_task):
            with self.assertRaises(InvalidTransportTaskStateError): self.call(fn, int(cancelled['id']), uuid4())
        moving = self.task(self.first, shipment)
        self.call(depart_transport_task, int(moving['id']), uuid4())
        with self.assertRaises(InvalidTransportTaskStateError): self.cancel(moving)
        self.call(arrive_transport_task, int(moving['id']), uuid4())
        with self.assertRaises(InvalidTransportTaskStateError): self.cancel(moving)

    def test_fault_on_second_release_rolls_back_everything(self):
        ids = [self.ready()[0], self.ready()[0]]
        task = self.call(create_transport_task, TransportTaskCreateRequest(route_code=self.first['code'],
            expected_arrival_at=self.clock+timedelta(hours=1), shipment_ids=ids), uuid4())
        key = uuid4()
        function = 'v4_fault_'+uuid4().hex[:8]
        with engine.begin() as conn:
            conn.exec_driver_sql(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.shipment_id = {max(ids)} AND NEW.released_at IS NOT NULL THEN
                RAISE EXCEPTION 'V4 injected second release failure'; END IF; RETURN NEW; END $$''')
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE UPDATE ON task_shipments FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            with self.assertRaises(DBAPIError): self.cancel(task, key=key)
            with SessionLocal() as session:
                current = session.get(TransportTask, int(task['id']))
                self.assertEqual((current.status, current.cancelled_at, current.cancel_reason), ('PENDING_DEPARTURE', None, None))
                self.assertEqual(session.scalar(select(func.count()).select_from(TaskShipment).where(TaskShipment.task_id==current.id, TaskShipment.released_at.is_(None))), 2)
                self.assertIsNone(session.scalar(select(OperationLog).where(OperationLog.idempotency_key==key)))
            self.assertTrue(all(self.detail(i)['stage']=='AT_STATION' for i in ids))
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON task_shipments')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        self.cancel(task, key=key)

    def test_qualification_disabled_routes_and_cancelled_station_protection(self):
        shipment, _ = self.ready()
        self.assertTrue(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        task = self.task(self.first, shipment)
        self.call(write_network, 'ROUTE', RouteUpdateRequest(enabled=False), uuid4(), int(self.first['id']))
        self.cancel(task)  # allowed even on disabled route
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        with self.assertRaises(NetworkError):
            self.call(write_network, 'STATION', StationUpdateRequest(enabled=False), uuid4(), int(self.source['id']))
        self.call(write_network, 'ROUTE', RouteUpdateRequest(enabled=True), uuid4(), int(self.first['id']))
        new = self.task(self.first, shipment)
        self.call(depart_transport_task, int(new['id']), uuid4())
        self.call(arrive_transport_task, int(new['id']), uuid4())
        self.call(write_network, 'ROUTE', RouteUpdateRequest(enabled=False), uuid4(), int(self.first['id']))
        # Cancelled history does not protect source after all cargo has moved away.
        self.call(write_network, 'STATION', StationUpdateRequest(enabled=False), uuid4(), int(self.source['id']))
        onward = self.task(self.second, shipment)
        self.call(depart_transport_task, int(onward['id']), uuid4())
        self.call(arrive_transport_task, int(onward['id']), uuid4())
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        self.assertTrue(self.enabled(shipment)['START_DELIVERY'])

    def test_corrupt_association_refused_empty_history_and_db_constraints(self):
        shipment, _ = self.parcel()
        self.assertEqual(self.call(list_shipment_transport_tasks, shipment, 1, 20), ([], 0))
        with self.assertRaises(ShipmentNotFoundError): self.call(list_shipment_transport_tasks, 999999999, 1, 20)
        self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', self.source)
        task = self.task(self.first, shipment)
        with engine.begin() as conn:
            conn.execute(text("UPDATE task_shipments SET released_at=now(), association_state='RELEASED' WHERE task_id=:id"), {'id': int(task['id'])})
        with self.assertRaises(InvalidTaskShipmentError): self.cancel(task)
        for sql in ("status='CANCELLED'", "cancel_reason='oops'", "status='CANCELLED',cancelled_at=now(),cancel_reason=' '"):
            with self.assertRaises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(f'UPDATE transport_tasks SET {sql} WHERE id=:id'), {'id': int(task['id'])})

    def test_creation_eligibility_follows_shipment_lifecycle(self):
        local = self.station('LOCAL', first=True, delivery=True)
        self.route(local, self.target, 'LOCAL_TARGET')
        shipment, _ = self.parcel(local)
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        self.event(shipment, 'PICKUP')
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        self.event(shipment, 'ARRIVE', local)
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        self.event(shipment, 'START_DELIVERY')
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        self.event(shipment, 'SIGN')
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        shipment, _ = self.ready()
        self.assertTrue(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
        task = self.task(self.first, shipment)
        self.call(depart_transport_task, int(task['id']), uuid4())
        self.assertFalse(self.enabled(shipment)['CREATE_TRANSPORT_TASK'])
