"""V5 destination corrections: fulfillment guards, audit, races and rollback."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from db import SessionLocal, engine
from errors import (InvalidShipmentDestinationError, IdempotencyKeyReusedError,
    InvalidTaskShipmentError, NetworkError, ShipmentNotFoundError)
from models import OperationLog, Shipment, Order, TaskShipment, TransportTask, Station, TransportRoute
from shipments.schemas import ShipmentDestinationUpdateRequest, ShipmentDetailResponse
from shipments.service import (update_shipment_destination, list_destination_changes,
    create_shipment, build_shipment_response_body, get_shipment)
from transport.service import depart_transport_task, arrive_transport_task, cancel_transport_task
from transport.schemas import TransportTaskCancelRequest
from network.service import write_network
from network.schemas import StationUpdateRequest
from orders.service import create_order
from orders.schemas import OrderCreateRequest
import test_v3_network as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V5DestinationTests(unittest.TestCase):
    call = helpers.V3NetworkTests.call
    setUp = helpers.V3NetworkTests.setUp
    station = helpers.V3NetworkTests.station
    route = helpers.V3NetworkTests.route
    parcel = helpers.V3NetworkTests.parcel
    event = helpers.V3NetworkTests.event
    task = helpers.V3NetworkTests.task

    def detail(self, shipment):
        with SessionLocal() as session:
            parcel, events = get_shipment(session, shipment)
            return build_shipment_response_body(session, parcel, events)

    def change(self, shipment, old, new, reason='纠正目的站', key=None):
        return self.call(update_shipment_destination, shipment, ShipmentDestinationUpdateRequest(
            expected_destination_station_id=int(old['id']), destination_station_id=int(new['id']), reason=reason), key or uuid4())

    def ready(self):
        shipment, order = self.parcel()
        self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', self.source)
        return shipment, order

    def test_allowed_stages_history_and_local_delivery_without_address_changes(self):
        self.call(write_network, 'STATION', StationUpdateRequest(allows_delivery=True), uuid4(), int(self.source['id']))
        shipment, order = self.parcel()
        destinations = [(self.target,self.middle), (self.middle,self.target), (self.target,self.source)]
        for index, (old,new) in enumerate(destinations):
            before = self.detail(shipment)
            result = self.change(shipment,old,new,reason=f'  更正 {index}  ')
            self.assertEqual(result['destination_station_id'],new['id'])
            for field in before:
                if field not in ('destination_station_id','updated_at','allowed_actions'):
                    self.assertEqual(before[field],result[field])
            if index == 0: self.event(shipment,'PICKUP')
            if index == 1: self.event(shipment,'ARRIVE',self.source)
        detail = self.detail(shipment)
        actions = {a['action']:a['enabled'] for a in detail['allowed_actions']}
        self.assertTrue(actions['START_DELIVERY']); self.assertFalse(actions['CREATE_TRANSPORT_TASK'])
        history,total = self.call(list_destination_changes,shipment,1,2)
        self.assertEqual(total,3)
        self.assertEqual([r['reason'] for r in history],['更正 2','更正 1'])
        self.assertEqual(history[0]['previous_destination_station_id'],self.target['id'])
        self.assertEqual(history[0]['destination_station_id'],self.source['id'])
        self.assertEqual(history[0]['occurred_at'],self.clock)
        self.assertEqual(self.call(list_destination_changes,shipment,2,2)[0][0]['reason'],'更正 0')
        self.event(shipment,'START_DELIVERY'); self.event(shipment,'SIGN')
        with SessionLocal() as session:
            saved = session.get(Order,order)
            self.assertEqual((saved.status,saved.sender_address,saved.recipient_address),('COMPLETED','下单发件地址','下单收件地址'))
        self.assertEqual(len(self.detail(shipment)['tracking_events']),5)

    def test_normalized_idempotency_stale_precondition_and_target_guards(self):
        shipment,_ = self.parcel(); key = uuid4()
        first = self.change(shipment,self.target,self.middle,'  选错了  ',key)
        self.assertEqual(self.change(shipment,self.target,self.middle,'选错了',key),first)
        for old,new,reason in [(self.target,self.middle,'别的原因'),(self.middle,self.target,'选错了')]:
            with self.assertRaises(IdempotencyKeyReusedError): self.change(shipment,old,new,reason,key)
        for old,new in [(self.target,self.source),(self.middle,self.middle)]:
            with self.assertRaises(InvalidShipmentDestinationError): self.change(shipment,old,new)
        with self.assertRaises(NetworkError): self.change(shipment,self.middle,self.source)  # no delivery capability
        missing={'id':'999999999'}
        with self.assertRaises(NetworkError): self.change(shipment,self.middle,missing)
        disabled=self.station('OFF',delivery=True)
        self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(disabled['id']))
        with self.assertRaises(NetworkError): self.change(shipment,self.middle,disabled)
        self.change(shipment,self.middle,self.target)
        self.assertEqual(self.change(shipment,self.target,self.middle,'选错了',key),first)
        self.assertEqual(self.detail(shipment)['destination_station_id'],self.target['id'])
        self.assertEqual(self.call(list_destination_changes,shipment,1,20)[1],2)
        other,_=self.parcel()
        self.assertEqual(self.call(list_destination_changes,other,1,20),([],0))
        with self.assertRaises(ShipmentNotFoundError): self.call(list_destination_changes,999999999,1,20)
        with self.assertRaises(ShipmentNotFoundError): self.change(999999999,self.target,self.middle)

    def test_in_transit_destination_change_freezes_current_leg_and_releases_future_plan(self):
        from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest
        from scheduling.service import preview_schedule, confirm_schedule

        shipment, _ = self.parcel()
        new_destination = self.station('REROUTE', delivery=True)
        self.route(self.middle, new_destination, 'WH_REROUTE')
        with SessionLocal() as session, session.begin():
            session.get(TransportRoute, int(self.first['id'])).travel_minutes = 60
            session.get(TransportRoute, int(self.second['id'])).travel_minutes = 60
            session.get(Station, int(self.middle['id'])).transfer_minutes = 10

        self.event(shipment, 'PICKUP')
        self.event(shipment, 'ARRIVE', self.source)
        preview = self.call(preview_schedule, shipment, SchedulePreviewRequest(
            line_id=int(self.lines['FULL']['id']), expected_line_version=1))
        schedule = self.call(confirm_schedule, shipment, ScheduleConfirmRequest(
            preview_token=preview['preview_token'], reason='初始排程'), uuid4())
        current_task_id = int(schedule['legs'][0]['task_id'])
        future_task_id = int(schedule['legs'][1]['task_id'])
        self.call(depart_transport_task, current_task_id, uuid4(), schedule['legs'][0]['schedule_revision'])

        before = self.detail(shipment)
        action = next(item for item in before['allowed_actions'] if item['action'] == 'UPDATE_DESTINATION')
        self.assertTrue(action['enabled'])
        updated = self.change(shipment, self.target, new_destination)

        self.assertEqual(updated['destination_station_id'], new_destination['id'])
        self.assertEqual(updated['active_transport_task']['id'], str(current_task_id))
        self.assertEqual(updated['active_transport_task']['status'], 'IN_TRANSIT')
        self.assertEqual(updated['schedule']['status'], 'NEEDS_RECONFIRMATION')
        with SessionLocal() as session:
            association = session.scalar(select(TaskShipment).where(
                TaskShipment.shipment_id == shipment,
                TaskShipment.task_id == future_task_id))
            task = session.get(TransportTask, future_task_id)
            self.assertEqual(association.association_state, 'RELEASED')
            self.assertEqual(task.status, 'CANCELLED')

    def test_in_transit_change_to_current_leg_endpoint_completes_on_arrival(self):
        from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest
        from scheduling.service import preview_schedule, confirm_schedule

        shipment, _ = self.parcel()
        with SessionLocal() as session, session.begin():
            session.get(TransportRoute, int(self.first['id'])).travel_minutes = 60
            session.get(TransportRoute, int(self.second['id'])).travel_minutes = 60
            session.get(Station, int(self.middle['id'])).transfer_minutes = 10
        self.event(shipment, 'PICKUP')
        self.event(shipment, 'ARRIVE', self.source)
        preview = self.call(preview_schedule, shipment, SchedulePreviewRequest(
            line_id=int(self.lines['FULL']['id']), expected_line_version=1))
        schedule = self.call(confirm_schedule, shipment, ScheduleConfirmRequest(
            preview_token=preview['preview_token'], reason='初始排程'), uuid4())
        task_id = int(schedule['legs'][0]['task_id'])
        self.call(depart_transport_task, task_id, uuid4(), schedule['legs'][0]['schedule_revision'])

        self.change(shipment, self.target, self.middle)
        self.call(arrive_transport_task, task_id, uuid4())

        detail = self.detail(shipment)
        self.assertEqual(detail['schedule']['status'], 'COMPLETED')
        self.assertTrue(next(item for item in detail['allowed_actions'] if item['action'] == 'START_DELIVERY')['enabled'])

    def test_waiting_task_does_not_block_destination_change(self):
        from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest
        from scheduling.service import preview_schedule, confirm_schedule

        shipment, _ = self.parcel()
        new_destination = self.station('WAITING_REROUTE', delivery=True)
        self.route(self.source, new_destination, 'GZ_WAITING_REROUTE')
        with SessionLocal() as session, session.begin():
            session.get(TransportRoute, int(self.first['id'])).travel_minutes = 60
            session.get(TransportRoute, int(self.second['id'])).travel_minutes = 60
            session.get(Station, int(self.middle['id'])).transfer_minutes = 10
        self.event(shipment, 'PICKUP')
        self.event(shipment, 'ARRIVE', self.source)
        preview = self.call(preview_schedule, shipment, SchedulePreviewRequest(
            line_id=int(self.lines['FULL']['id']), expected_line_version=1))
        schedule = self.call(confirm_schedule, shipment, ScheduleConfirmRequest(
            preview_token=preview['preview_token'], reason='初始排程'), uuid4())

        before = self.detail(shipment)
        self.assertEqual(before['stage'], 'AT_STATION')
        self.assertTrue(next(item for item in before['allowed_actions'] if item['action'] == 'UPDATE_DESTINATION')['enabled'])
        updated = self.change(shipment, self.target, new_destination)

        self.assertEqual(updated['destination_station_id'], new_destination['id'])
        self.assertIsNone(updated['active_transport_task'])
        self.assertEqual(updated['schedule']['status'], 'NEEDS_RECONFIRMATION')
        with SessionLocal() as session:
            tasks = [session.get(TransportTask, int(leg['task_id'])) for leg in schedule['legs']]
            self.assertTrue(all(task.status == 'CANCELLED' for task in tasks))

    def test_concurrent_changes_and_task_creation(self):
        self.call(write_network,'STATION',StationUpdateRequest(allows_delivery=True),uuid4(),int(self.source['id']))
        shipment,_ = self.parcel()
        def change(new):
            try: return self.change(shipment,self.target,new)
            except InvalidShipmentDestinationError: return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(change,[self.middle,self.source]))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.call(list_destination_changes,shipment,1,20)[1],1)

    def test_network_disable_and_delivery_capability_races_and_protection(self):
        for field in ('enabled','allows_delivery'):
            shipment,_=self.parcel(); new=self.station('RACE_'+field.upper(),delivery=True)
            def compete(kind):
                try:
                    if kind=='change': return self.change(shipment,self.target,new)
                    return self.call(write_network,'STATION',StationUpdateRequest(**{field:False}),uuid4(),int(new['id']))
                except NetworkError: return None
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes=list(executor.map(compete,['change','disable']))
            self.assertEqual(sum(o is not None for o in outcomes),1)
            with SessionLocal() as session:
                from models import Station
                state=session.get(Station,int(new['id']))
                self.assertEqual(getattr(state,field),self.detail(shipment)['destination_station_id']==new['id'])
        # Old target protection follows the new destination rather than old logs.
        old=self.station('OLD',delivery=True); new=self.station('NEW',delivery=True)
        shipment,_=self.parcel(old); self.change(shipment,old,new)
        self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(old['id']))
        with self.assertRaises(NetworkError):
            self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(new['id']))

    def test_log_failure_rolls_back_destination_and_legacy_cache_replays(self):
        shipment,_=self.ready(); before=self.detail(shipment); key=uuid4()
        function='v5_fault_'+uuid4().hex[:8]
        with engine.begin() as conn:
            conn.exec_driver_sql(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.action='UPDATE_SHIPMENT_DESTINATION' AND NEW.resource_id={shipment} THEN
                RAISE EXCEPTION 'V5 injected log failure'; END IF; RETURN NEW; END $$''')
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE INSERT ON operation_logs FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            with self.assertRaises(DBAPIError): self.change(shipment,self.target,self.middle,key=key)
            self.assertEqual(self.detail(shipment),before)
            self.assertEqual(self.call(list_destination_changes,shipment,1,20),([],0))
            with SessionLocal() as session:
                self.assertIsNone(session.scalar(select(OperationLog).where(OperationLog.idempotency_key==key)))
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON operation_logs')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        self.change(shipment,self.target,self.middle,key=key)
        order=self.call(create_order,OrderCreateRequest(product_name='legacy',quantity=1,sender_name='s',sender_address='s',recipient_name='r',recipient_address='r'),uuid4())
        creation_key=uuid4()
        body,_=self.call(create_shipment,int(order['id']),creation_key,int(self.target['id']))
        # Model a V4 persisted response, whose allowed_actions lacks UPDATE_DESTINATION.
        legacy={**body,'allowed_actions':[a for a in body['allowed_actions'] if a['action']!='UPDATE_DESTINATION']}
        with SessionLocal.begin() as session:
            log=session.scalar(select(OperationLog).where(OperationLog.idempotency_key==creation_key))
            log.response_body=legacy
        self.change(int(body['id']),self.target,self.middle)
        replay,status=self.call(create_shipment,int(order['id']),creation_key,int(self.target['id']))
        self.assertEqual((replay,status),(legacy,201))
        ShipmentDetailResponse.model_validate(replay)
        self.assertEqual(self.detail(int(body['id']))['destination_station_id'],self.middle['id'])
