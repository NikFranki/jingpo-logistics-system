"""Real HTTP actions use server time without any mutable clock table."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from db import SessionLocal, engine
from models import OperationLog, TrackingEvent
import test_v3_api as helpers


@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test')
                     if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class ServerTimeApiTests(unittest.TestCase):
    setUpClass = classmethod(helpers.V3ApiTests.setUpClass.__func__)
    stop_server = classmethod(helpers.V3ApiTests.stop_server.__func__)
    request = helpers.V3ApiTests.request
    station = helpers.V3ApiTests.station

    def test_real_actions_track_server_time_and_retries_preserve_facts(self):
        with engine.connect() as conn:
            self.assertIsNone(conn.scalar(text("SELECT to_regclass('simulation_settings')")))
        self.assertEqual(self.request('/simulation/clock')[0], 404)
        self.assertEqual(self.request('/simulation/clock/advance', 'POST', {'minutes': 120})[0], 404)
        prefix = 'TIME_' + uuid4().hex[:8].upper()
        source = self.station(prefix+'_S', allows_first_arrival=True)
        target = self.station(prefix+'_D', allows_delivery=True)
        _, route = self.request('/routes', 'POST', dict(code=prefix+'_R',
            origin_station_id=int(source['id']), destination_station_id=int(target['id'])))
        self.assertEqual(self.request('/path-plans', 'POST', dict(code=prefix+'_P', name='time path',
            route_ids=[int(route['id'])]))[0], 201)
        _, order = self.request('/orders/create', 'POST', dict(product_name='time', quantity=1,
            sender_name='s', sender_address='s', recipient_name='r', recipient_address='r'))
        _, shipment = self.request('/orders/'+order['id']+'/shipment', 'POST',
            {'destination_station_id': int(target['id'])})
        base = '/shipments/'+shipment['id']
        self.assertEqual(self.request(base+'/events', 'POST', dict(event_type='PICKUP',
            occurred_at='2000-01-01T00:00:00Z'))[0], 422)

        def event(body):
            before = datetime.now(timezone.utc)
            key = str(uuid4())
            status, result = self.request(base+'/events', 'POST', body, key)
            after = datetime.now(timezone.utc)
            self.assertEqual(status, 200, result)
            latest = result['tracking_events'][0]
            instant = datetime.fromisoformat(latest['occurred_at'])
            self.assertLessEqual(before, instant)
            self.assertLessEqual(instant, after)
            self.assertEqual(self.request(base+'/events', 'POST', body, key), (200, result))

        event({'event_type': 'PICKUP'})
        event({'event_type': 'ARRIVE', 'station_id': source['id']})
        _, task = self.request('/transport-tasks/create', 'POST', dict(route_code=route['code'],
            expected_arrival_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            shipment_ids=[int(shipment['id'])]))
        for action, field in (('depart', 'departed_at'), ('arrive', 'arrived_at')):
            before = datetime.now(timezone.utc)
            status, result = self.request('/transport-tasks/'+task['id']+'/'+action, 'POST')
            after = datetime.now(timezone.utc)
            self.assertEqual(status, 200, result)
            instant = datetime.fromisoformat(result[field])
            self.assertLessEqual(before, instant)
            self.assertLessEqual(instant, after)
            self.assertIn('server_time', result)
            self.assertNotIn('simulation_time', result)
            current = self.request(base)[1]
            self.assertEqual(datetime.fromisoformat(current['tracking_events'][0]['occurred_at']), instant)
        event({'event_type': 'START_DELIVERY'})
        event({'event_type': 'SIGN'})
        with SessionLocal() as session:
            events = list(session.scalars(select(TrackingEvent).where(
                TrackingEvent.shipment_id == int(shipment['id']))))
            self.assertEqual(len(events), 7)
            for item in events:
                self.assertEqual(item.occurred_at, session.get(OperationLog, item.operation_id).occurred_at)
        observed = self.request('/server-time')[1]['server_time']
        self.assertEqual(datetime.fromisoformat(observed).utcoffset(), timedelta(0))
