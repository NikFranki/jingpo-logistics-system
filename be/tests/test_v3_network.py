"""V3 network configuration and shipment invariants on an isolated *_test DB."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from pydantic import ValidationError
from db import SessionLocal
from errors import NetworkError, InvalidShipmentStateError, InvalidTaskShipmentError, IdempotencyKeyReusedError
from models import Station, Shipment, SimulationSettings, TransportTask, OperationLog, TrackingEvent
from network.schemas import StationCreateRequest, StationUpdateRequest, RouteCreateRequest, RouteUpdateRequest
from network.service import write_network, list_stations, list_routes
from orders.schemas import OrderCreateRequest
from orders.service import create_order
from shipments.schemas import ShipmentEventRequest
from shipments.service import create_shipment, process_shipment_event, build_shipment_response_body, get_shipment
from transport.schemas import TransportTaskCreateRequest
from transport.service import create_transport_task, depart_transport_task, arrive_transport_task, list_candidate_shipments, get_transport_task, calculate_task_delay

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V3NetworkTests(unittest.TestCase):
    def call(self, fn, *args):
        with SessionLocal() as session:
            return fn(session, *args)

    def setUp(self):
        self.prefix = 'T' + uuid4().hex[:8].upper()
        self.source = self.station('GZ', first=True)
        self.middle = self.station('WH', delivery=True)
        self.target = self.station('HZ', delivery=True)
        self.first = self.route(self.source, self.middle, 'GZ_WH', monitor=True)
        self.second = self.route(self.middle, self.target, 'WH_HZ')
        with SessionLocal() as session:
            self.clock = session.get(SimulationSettings, 1).current_time

    def station(self, code, first=False, delivery=False):
        return self.call(write_network, 'STATION', StationCreateRequest(
            code=self.prefix+'_'+code, name=code+'站', allows_first_arrival=first, allows_delivery=delivery), uuid4())

    def route(self, source, target, code, monitor=False):
        return self.call(write_network, 'ROUTE', RouteCreateRequest(code=self.prefix+'_'+code,
            origin_station_id=int(source['id']), destination_station_id=int(target['id']),
            delay_monitoring_enabled=monitor), uuid4())

    def parcel(self, target=None):
        order = self.call(create_order, OrderCreateRequest(product_name='V3 sample', quantity=1,
            sender_name='发件人', sender_address='下单发件地址', recipient_name='收件人', recipient_address='下单收件地址'), uuid4())
        shipment, _ = self.call(create_shipment, int(order['id']), uuid4(), int((target or self.target)['id']))
        return int(shipment['id']), int(order['id'])

    def event(self, shipment_id, event, station=None):
        return self.call(process_shipment_event, shipment_id, ShipmentEventRequest(
            event_type=event, **({'station_id': station['id']} if station else {})), uuid4())

    def task(self, route, shipment):
        return self.call(create_transport_task, TransportTaskCreateRequest(route_code=route['code'],
            expected_arrival_at=self.clock+timedelta(hours=1), shipment_ids=[shipment]), uuid4())

    def test_new_network_full_flow_and_destination_guard(self):
        shipment, order = self.parcel()
        self.event(shipment, 'PICKUP')
        with self.assertRaises(InvalidShipmentStateError):
            self.event(shipment, 'ARRIVE', self.middle)
        self.event(shipment, 'ARRIVE', self.source)
        with self.assertRaises(InvalidTaskShipmentError):
            self.task(self.second, shipment)
        for route in (self.first, self.second):
            task = self.task(route, shipment)
            self.call(depart_transport_task, int(task['id']), uuid4())
            self.call(arrive_transport_task, int(task['id']), uuid4())
            if route == self.first:
                with self.assertRaises(InvalidShipmentStateError):
                    self.event(shipment, 'START_DELIVERY')
        self.event(shipment, 'START_DELIVERY')
        signed = self.event(shipment, 'SIGN')
        self.assertEqual(signed['stage'], 'SIGNED')
        self.assertEqual(signed['destination_station_id'], self.target['id'])
        self.assertEqual(len(signed['tracking_events']), 9)
        self.assertEqual(signed['last_scanned_station_id'], self.target['id'])

    def test_configuration_validation_and_idempotency(self):
        request = StationCreateRequest(code=self.prefix+'_EXTRA', name='  备用站  ')
        key = uuid4()
        created = self.call(write_network, 'STATION', request, key)
        self.assertEqual(created['name'], '备用站')
        self.assertEqual(self.call(write_network, 'STATION', request, key), created)
        with self.assertRaises(IdempotencyKeyReusedError):
            self.call(write_network, 'STATION', StationCreateRequest(code=self.prefix+'_OTHER', name='其他'), key)
        with self.assertRaises(NetworkError):
            self.call(write_network, 'STATION', request, uuid4())
        with self.assertRaises(NetworkError):
            self.route(self.source, self.middle, 'DUPLICATE')
        with self.assertRaises(NetworkError):
            self.route(self.source, self.source, 'LOOP')
        for cls, fields in [(StationUpdateRequest, {'code':'NEW'}), (RouteUpdateRequest, {'origin_station_id':1}),
                            (StationUpdateRequest, {}), (StationUpdateRequest, {'enabled':None})]:
            with self.assertRaises(ValidationError):
                cls(**fields)
        self.call(write_network, 'STATION', StationUpdateRequest(name='新名称'), uuid4(), int(created['id']))
        self.call(write_network, 'STATION', StationUpdateRequest(enabled=False), uuid4(), int(created['id']))
        with SessionLocal() as session:
            self.assertIn(int(created['id']), [s.id for s in list_stations(session)])
            self.assertNotIn(int(created['id']), [s.id for s in list_stations(session, True)])
            logs = list(session.scalars(select(OperationLog).where(OperationLog.resource_type=='STATION', OperationLog.resource_id==int(created['id'])).order_by(OperationLog.id)))
            self.assertEqual(len(logs), 3)
            self.assertEqual(logs[1].before_data['name'], '备用站')
            self.assertEqual(logs[1].after_data['name'], '新名称')

    def test_disabled_route_existing_task_and_delay_snapshot(self):
        shipment, _ = self.parcel()
        self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', self.source)
        task = self.task(self.first, shipment)
        self.call(write_network, 'ROUTE', RouteUpdateRequest(enabled=False, delay_monitoring_enabled=False), uuid4(), int(self.first['id']))
        with self.assertRaises(NetworkError):
            self.call(list_candidate_shipments, self.first['code'], 1, 100)
        other, _ = self.parcel()
        self.event(other, 'PICKUP'); self.event(other, 'ARRIVE', self.source)
        with self.assertRaises(NetworkError):
            self.task(self.first, other)
        self.call(depart_transport_task, int(task['id']), uuid4())
        with SessionLocal() as session:
            current = session.get(TransportTask, int(task['id']))
            self.assertTrue(current.delay_monitoring_enabled)
            self.assertEqual(calculate_task_delay(current, self.clock+timedelta(hours=2)), ('OVERDUE',60))
        self.call(arrive_transport_task, int(task['id']), uuid4())
        self.call(write_network, 'ROUTE', RouteUpdateRequest(enabled=True), uuid4(), int(self.first['id']))
        newer = self.task(self.first, other)
        self.assertFalse(newer['delay_monitoring_enabled'])
        with SessionLocal() as session:
            self.assertEqual(calculate_task_delay(session.get(TransportTask, int(newer['id'])), self.clock+timedelta(hours=2)), ('NOT_APPLICABLE',None))

    def test_station_protection_and_first_station_is_destination(self):
        local = self.station('LOCAL', first=True, delivery=True)
        shipment, _ = self.parcel(local)
        for changes in ({'enabled':False}, {'allows_delivery':False}):
            with self.assertRaises(NetworkError):
                self.call(write_network,'STATION',StationUpdateRequest(**changes),uuid4(),int(local['id']))
        self.event(shipment,'PICKUP'); self.event(shipment,'ARRIVE',local)
        outgoing = self.route(local,self.target,'LOCAL_OUT')
        with SessionLocal() as session:
            candidates,_ = list_candidate_shipments(session,outgoing['code'],1,100)
            self.assertNotIn(shipment,[s.id for s in candidates])
        with self.assertRaises(InvalidTaskShipmentError):
            self.task(outgoing,shipment)
        self.event(shipment,'START_DELIVERY'); self.event(shipment,'SIGN')
        self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=False),uuid4(),int(outgoing['id']))
        self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(local['id']))
        with self.assertRaises(NetworkError):
            self.parcel(local)
        # Existing destination cannot be silently changed using a new key.
        with SessionLocal() as session:
            order_id=session.get(Shipment,shipment).order_id
        with self.assertRaises(NetworkError):
            self.call(create_shipment,order_id,uuid4(),int(self.target['id']))

    def test_station_inventory_tasks_routes_and_capability_guards(self):
        source_id=int(self.source['id'])
        # Enabled routes alone prevent disabling an otherwise empty station.
        with self.assertRaises(NetworkError) as blocked:
            self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),source_id)
        self.assertEqual(blocked.exception.code,'NETWORK_RESOURCE_IN_USE')
        self.call(write_network,'STATION',StationUpdateRequest(allows_first_arrival=False),uuid4(),source_id)
        shipment,_=self.parcel()
        self.event(shipment,'PICKUP')
        with self.assertRaises(InvalidShipmentStateError):
            self.event(shipment,'ARRIVE',self.source)
        self.call(write_network,'STATION',StationUpdateRequest(allows_first_arrival=True),uuid4(),source_id)
        self.event(shipment,'ARRIVE',self.source)
        task=self.task(self.first,shipment)
        self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=False),uuid4(),int(self.first['id']))
        self.call(depart_transport_task,int(task['id']),uuid4())
        # No enabled route or in-station inventory at source; unfinished task still protects it.
        with self.assertRaises(NetworkError):
            self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),source_id)
        self.call(arrive_transport_task,int(task['id']),uuid4())
        self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),source_id)
        with self.assertRaises(NetworkError):
            self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=True),uuid4(),int(self.first['id']))
        self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=False),uuid4(),int(self.second['id']))
        # All incident routes disabled and prior task arrived; in-station inventory still protects middle.
        with self.assertRaises(NetworkError):
            self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(self.middle['id']))
        with SessionLocal() as session:
            current=session.get(Station,int(self.middle['id']))
            self.assertTrue(current.enabled)

    def test_concurrent_disabling_and_new_destination(self):
        target=self.station('RACE',delivery=True)
        def admit(_):
            try:
                if _ == 0:
                    return ('shipment',self.parcel(target)[0])
                return ('disable',self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(target['id'])))
            except NetworkError:
                return ('rejected',None)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(admit,range(2)))
        self.assertEqual(sum(kind!='rejected' for kind,_ in outcomes),1)
        with SessionLocal() as session:
            enabled=session.get(Station,int(target['id'])).enabled
            count=session.scalar(select(func.count()).select_from(Shipment).where(Shipment.destination_station_id==int(target['id'])))
            self.assertEqual(bool(count), enabled)
