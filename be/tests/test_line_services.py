"""Line-service edits materialize new-version trips without rewriting old snapshots."""
import os
import unittest
from datetime import time
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url

from db import SessionLocal, engine
from lines.schemas import (LineCreateRequest, LineLegInput, LineServiceCreateRequest,
                           LineServiceStopInput, LineServiceUpdateRequest)
from lines.service import write_line
from lines.service_schedules import write_service
from models import ScheduledTrip
import test_v3_network as helpers


@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False,
                     'requires *_test database')
class LineServiceTests(unittest.TestCase):
    call = helpers.V3NetworkTests.call
    station = helpers.V3NetworkTests.station
    route = helpers.V3NetworkTests.route

    def setUp(self):
        columns = {column['name'] for column in inspect(engine).get_columns('path_plan_legs')}
        if 'travel_override_minutes' not in columns:
            self.skipTest('test database is missing unified transport-line columns')
        helpers.V3NetworkTests.setUp(self)
        self.clock = self.clock.astimezone(ZoneInfo('Asia/Shanghai'))
        self.trip_clock = patch('lines.service_schedules.server_now', return_value=self.clock)
        self.trip_clock.start()
        self.addCleanup(self.trip_clock.stop)

    def test_service_edit_generates_new_week_and_preserves_old_trip_snapshot(self):
        line = self.call(write_line, LineCreateRequest(
            code=self.prefix+'_SVC', name='班次测试线路',
            station_ids=[int(self.source['id']), int(self.middle['id'])],
            legs=[LineLegInput(travel_minutes=60)]), uuid4())
        service = self.call(write_service, int(line['id']), LineServiceCreateRequest(
            code='DAILY', name='每日班次', valid_from=self.clock.date(), weekdays=[1, 2, 3, 4, 5, 6, 7],
            stops=[LineServiceStopInput(station_id=int(self.source['id']), arrival_day_offset=None,
                arrival_time=None, departure_day_offset=0, departure_time=time(8)),
                LineServiceStopInput(station_id=int(self.middle['id']), arrival_day_offset=0,
                    arrival_time=time(9), departure_day_offset=None, departure_time=None)]), uuid4())
        with SessionLocal() as session:
            old_trips = list(session.scalars(select(ScheduledTrip).where(
                ScheduledTrip.service_id == int(service['id']),
                ScheduledTrip.service_version == 1).order_by(ScheduledTrip.service_date)))
            self.assertEqual(len(old_trips), 7)
            old_trip_id = old_trips[0].id
            old_snapshot = old_trips[0].stops_snapshot

        updated = self.call(write_service, int(line['id']), LineServiceUpdateRequest(
            expected_version=1, stops=[
                LineServiceStopInput(station_id=int(self.source['id']), arrival_day_offset=None,
                    arrival_time=None, departure_day_offset=0, departure_time=time(10)),
                LineServiceStopInput(station_id=int(self.middle['id']), arrival_day_offset=0,
                    arrival_time=time(11), departure_day_offset=None, departure_time=None)]),
            uuid4(), int(service['id']))
        self.assertEqual(updated['version'], 2)
        with SessionLocal() as session:
            trips = list(session.scalars(select(ScheduledTrip).where(
                ScheduledTrip.service_id == int(service['id'])).order_by(
                    ScheduledTrip.service_version, ScheduledTrip.service_date)))
            old_trip = session.get(ScheduledTrip, old_trip_id)
            new_trips = [trip for trip in trips if trip.service_version == 2]
            self.assertEqual(len(trips), 14)
            self.assertEqual(len(new_trips), 7)
            self.assertEqual(old_trip.stops_snapshot, old_snapshot)
            self.assertIn('T08:00:00', old_snapshot[0]['departure_at'])
            self.assertIn('T10:00:00', new_trips[0].stops_snapshot[0]['departure_at'])
