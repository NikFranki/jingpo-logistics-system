"""Reviewed schedules: preview, atomic chains, activation, sharing and cancellation."""
import os
import unittest
from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from db import SessionLocal, engine
from models import Shipment, Station, TransportRoute, TransportTask, TaskShipment, ShipmentScheduleVersion, OperationLog
from errors import NetworkError
from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest
from scheduling.service import preview_schedule, confirm_schedule, schedule_body, preview_cancel
from transport.schemas import TransportTaskCancelRequest
from transport.service import depart_transport_task, arrive_transport_task, cancel_transport_task, get_transport_task
from network.schemas import RouteUpdateRequest
from network.service import write_network
import test_v3_network as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V7ScheduleTests(unittest.TestCase):
    call = helpers.V3NetworkTests.call
    station = helpers.V3NetworkTests.station
    route = helpers.V3NetworkTests.route
    parcel = helpers.V3NetworkTests.parcel
    event = helpers.V3NetworkTests.event

    def setUp(self):
        helpers.V3NetworkTests.setUp(self)
        with SessionLocal() as session, session.begin():
            for route in (self.first, self.second):
                session.get(TransportRoute, int(route['id'])).travel_minutes = 60
            session.get(Station, int(self.middle['id'])).transfer_minutes = 10

    def shipment(self, ready=True):
        shipment, _ = self.parcel()
        with SessionLocal() as session, session.begin():
            session.get(Shipment, shipment).scheduling_mode = 'REVIEWED'
        if ready:
            self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', self.source)
        return shipment

    def preview(self, shipment, **changes):
        args = {'route_ids': [int(self.first['id']), int(self.second['id'])]}
        args.update(changes)
        return self.call(preview_schedule, shipment, SchedulePreviewRequest(**args))

    def confirm(self, shipment, preview=None, key=None, warnings=()):
        preview = preview or self.preview(shipment)
        return self.call(confirm_schedule, shipment, ScheduleConfirmRequest(
            preview_token=preview['preview_token'], reason='审核运输计划', acknowledged_warning_codes=list(warnings)), key or uuid4())

    def state(self, shipment):
        return self.call(lambda session: schedule_body(session, session.get(Shipment, shipment)))

    def counts(self):
        return self.call(lambda session: tuple(session.scalar(select(func.count()).select_from(model))
            for model in (TransportTask, TaskShipment, ShipmentScheduleVersion, OperationLog)))

    def test_time_passage_preserves_preview_but_rejects_past_departures(self):
        shipment = self.shipment()
        preview = self.preview(shipment, first_departure_at=self.clock+timedelta(hours=1))
        self.server_clock.return_value += timedelta(seconds=1)
        self.assertEqual(self.confirm(shipment, preview)['status'], 'CONFIRMED')
        other = self.shipment()
        preview = self.preview(other, first_departure_at=(self.server_clock.return_value+timedelta(minutes=1)).replace(second=0))
        before = self.counts()
        self.server_clock.return_value += timedelta(minutes=2)
        with self.assertRaises(NetworkError) as result:
            self.confirm(other, preview)
        self.assertEqual(result.exception.code, 'PREVIEW_EXPIRED')
        self.assertEqual(self.counts(), before)

    def test_plan_task_can_depart_and_arrive_before_scheduled_times(self):
        shipment = self.shipment()
        preview = self.preview(shipment, first_departure_at=self.clock+timedelta(hours=2))
        reviewed = self.confirm(shipment, preview)
        task_id = int(reviewed['legs'][0]['task_id'])
        detail = self.call(get_transport_task, task_id)
        self.assertTrue(next(action for action in detail['allowed_actions'] if action['action'] == 'DEPART')['enabled'])
        departed = self.call(depart_transport_task, task_id, uuid4(), detail['schedule_revision'])
        self.assertEqual(departed['status'], 'IN_TRANSIT')
        arrived = self.call(arrive_transport_task, task_id, uuid4())
        self.assertEqual(arrived['status'], 'ARRIVED')
        self.assertLess(arrived['arrived_at'], reviewed['legs'][0]['planned_arrival_at'])

    def test_preview_is_read_only_confirm_all_legs_and_arrival_activates_same_id(self):
        shipment = self.shipment(); before = self.counts()
        preview = self.preview(shipment); self.preview(shipment)
        self.assertEqual(self.counts(), before)
        key = uuid4(); result = self.confirm(shipment, preview, key)
        self.assertEqual(self.confirm(shipment, preview, key), result)
        a, b = [int(row['task_id']) for row in result['legs']]
        self.assertEqual([row['task_status'] for row in result['legs']], ['PENDING_DEPARTURE', 'WAITING_PREDECESSOR'])
        with self.assertRaises(NetworkError): self.call(depart_transport_task, b, uuid4(), 1)
        self.call(depart_transport_task, a, uuid4(), 1)
        self.server_clock.return_value += timedelta(minutes=60)
        count = self.counts()[0]; self.call(arrive_transport_task, a, uuid4())
        rows = self.state(shipment)['legs']
        self.assertEqual((self.counts()[0], int(rows[1]['task_id']), rows[1]['association_state']), (count, b, 'ACTIVE'))
        with self.assertRaises(NetworkError): self.call(depart_transport_task, b, uuid4(), 1)
        self.server_clock.return_value += timedelta(minutes=10); self.call(depart_transport_task, b, uuid4(), 1)
        self.call(arrive_transport_task, b, uuid4())
        self.assertEqual(self.state(shipment)['status'], 'COMPLETED')

    def test_plan_task_detail_includes_schedule_metadata(self):
        shipment = self.shipment()
        reviewed = self.confirm(shipment)
        task_id = int(reviewed['legs'][0]['task_id'])
        detail = self.call(get_transport_task, task_id)
        self.assertEqual(detail['scheduled_trip_id'], None)
        self.assertEqual(detail['planned_departure_at'], reviewed['legs'][0]['planned_departure_at'])

    def test_prearrival_waits_then_activates_first_existing_task(self):
        shipment = self.shipment(False)
        preview = self.preview(shipment, origin_station_id=int(self.source['id']), planned_origin_arrival_at=self.clock+timedelta(minutes=30))
        self.assertEqual(preview['legs'][0]['planned_departure_at'], (self.clock+timedelta(minutes=30)).isoformat())
        result = self.confirm(shipment, preview); task_id = int(result['legs'][0]['task_id'])
        self.assertEqual(result['legs'][0]['task_status'], 'WAITING_CARGO')
        count = self.counts()[0]
        self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', self.source)
        result = self.state(shipment)
        self.assertEqual((self.counts()[0], int(result['legs'][0]['task_id']), result['legs'][0]['association_state']), (count, task_id, 'ACTIVE'))

    def test_shorter_reference_requires_ack_and_stale_configuration_rejects(self):
        shipment = self.shipment()
        legs = [{'route_id': int(self.first['id']), 'planned_departure_at': self.clock,
                 'planned_arrival_at': self.clock+timedelta(minutes=30)},
                {'route_id': int(self.second['id']), 'planned_departure_at': self.clock+timedelta(minutes=35),
                 'planned_arrival_at': self.clock+timedelta(minutes=65)}]
        preview = self.preview(shipment, legs=legs); self.assertEqual(len(preview['warnings']), 3)
        with self.assertRaises(NetworkError): self.confirm(shipment, preview)
        self.confirm(shipment, preview, warnings=[w['code'] for w in preview['warnings']])
        second = self.shipment(); preview = self.preview(second)
        self.call(write_network, 'ROUTE', RouteUpdateRequest(travel_minutes=90), uuid4(), int(self.first['id']))
        before = self.counts()
        with self.assertRaises(NetworkError): self.confirm(second, preview)
        self.assertEqual(self.counts(), before)

    def test_sharing_membership_revision_waits_and_cancel_invalidates_downstream(self):
        first = self.shipment(); preview = self.preview(first)
        second = self.shipment(False)
        # Keep identical reviewed times; only one member has actually entered the origin.
        first_result = self.confirm(first, preview)
        second_preview = self.preview(second, origin_station_id=int(self.source['id']))
        second_result = self.confirm(second, second_preview)
        self.assertEqual([r['task_id'] for r in first_result['legs']], [r['task_id'] for r in second_result['legs']])
        a, b = [int(row['task_id']) for row in first_result['legs']]
        with self.assertRaises(NetworkError): self.call(depart_transport_task, a, uuid4(), 1)
        with self.assertRaises(NetworkError): self.call(depart_transport_task, a, uuid4(), 2)
        self.event(second, 'PICKUP'); self.event(second, 'ARRIVE', self.source)
        preview = self.call(preview_cancel, a, '取消整趟')
        self.assertEqual(len(preview['impact']), 4)
        self.call(cancel_transport_task, a, TransportTaskCancelRequest(reason='取消整趟',
            expected_schedule_revision=2, cancel_token=preview['cancel_token']), uuid4())
        for shipment in (first, second):
            state = self.state(shipment)
            self.assertEqual(state['status'], 'NEEDS_RECONFIRMATION')
            self.assertTrue(all(row['association_state'] == 'RELEASED' for row in state['legs']))
        self.assertEqual(self.call(lambda s: s.get(TransportTask, b).status), 'CANCELLED')

    def test_two_confirmations_have_one_winner_and_version_failure_rolls_back(self):
        shipment = self.shipment(); preview = self.preview(shipment)
        def compete(_):
            try: return self.confirm(shipment, preview)
            except NetworkError: return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(compete, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        other = self.shipment(); preview = self.preview(other); before = self.counts()
        function = 'v7_fault_' + uuid4().hex[:8]
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.shipment_id={other} THEN RAISE EXCEPTION 'V7 fault'; END IF; RETURN NEW; END $$")
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE INSERT ON shipment_schedule_versions FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            with self.assertRaises(DBAPIError): self.confirm(other, preview)
            self.assertEqual(self.counts(), before)
            self.assertEqual(self.state(other)['version'], 0)
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON shipment_schedule_versions')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        self.confirm(other, preview)

    def test_cancel_preserves_unrelated_member_of_shared_downstream(self):
        shipment = self.shipment(); reviewed = self.confirm(shipment)
        a, b = [int(row['task_id']) for row in reviewed['legs']]
        with SessionLocal() as session, session.begin():
            session.get(Station, int(self.middle['id'])).allows_first_arrival = True
        other = self.shipment(False)
        row = reviewed['legs'][1]
        preview = self.preview(other, route_ids=[int(self.second['id'])], origin_station_id=int(self.middle['id']),
            legs=[dict(route_id=int(self.second['id']), planned_departure_at=row['planned_departure_at'],
                planned_arrival_at=row['planned_arrival_at'])])
        unrelated = self.confirm(other, preview)
        self.assertEqual(int(unrelated['legs'][0]['task_id']), b)
        cancel = self.call(preview_cancel, a, '取消上游')
        self.call(cancel_transport_task, a, TransportTaskCancelRequest(reason='取消上游',
            expected_schedule_revision=1, cancel_token=cancel['cancel_token']), uuid4())
        remaining = self.state(other)
        self.assertEqual(remaining['status'], 'CONFIRMED')
        self.assertEqual(remaining['legs'][0]['association_state'], 'PLANNED')
        self.assertEqual(remaining['legs'][0]['task_status'], 'WAITING_CARGO')
        self.assertEqual(remaining['legs'][0]['schedule_revision'], 3)

    def test_forecast_delay_preserves_baseline_and_reconfirmation_freezes_prefix(self):
        shipment = self.shipment(); original = self.confirm(shipment)
        a = int(original['legs'][0]['task_id']); b = int(original['legs'][1]['task_id'])
        self.call(depart_transport_task, a, uuid4(), 1)
        self.server_clock.return_value += timedelta(minutes=90)
        late = self.state(shipment)
        self.assertTrue(late['legs'][0]['forecast_stale'])
        self.assertEqual(late['legs'][1]['planned_arrival_at'], original['legs'][1]['planned_arrival_at'])
        preview = self.preview(shipment, route_ids=[int(self.second['id'])])
        self.assertEqual(int(preview['frozen_task_id']), a)
        revised = self.confirm(shipment, preview)
        self.assertEqual(revised['version'], 2)
        self.assertEqual(int(revised['legs'][0]['task_id']), a)
        self.assertNotEqual(int(revised['legs'][1]['task_id']), b)
        self.assertEqual(self.call(lambda s: s.get(TransportTask, b).status), 'CANCELLED')
        self.call(arrive_transport_task, a, uuid4())
        preview = self.preview(shipment, route_ids=[int(self.second['id'])])
        self.assertEqual(preview['legs'][0]['approved_transfer_minutes'], 10)
        revised = self.confirm(shipment, preview)
        self.assertEqual(revised['version'], 3)
        self.assertEqual(int(revised['legs'][0]['task_id']), a)

    def test_arrival_activation_failure_rolls_back_fact_and_can_retry(self):
        shipment = self.shipment(); result = self.confirm(shipment)
        a, b = [int(row['task_id']) for row in result['legs']]
        self.call(depart_transport_task, a, uuid4(), 1)
        function = 'v7_activation_' + uuid4().hex[:8]
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.task_id={b} AND NEW.association_state='ACTIVE' THEN RAISE EXCEPTION 'activation fault'; END IF; RETURN NEW; END $$")
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE UPDATE ON task_shipments FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            before = self.counts()
            with self.assertRaises(DBAPIError): self.call(arrive_transport_task, a, uuid4())
            self.assertEqual(self.counts(), before)
            state = self.state(shipment)
            self.assertEqual([r['task_status'] for r in state['legs']], ['IN_TRANSIT', 'WAITING_PREDECESSOR'])
            self.assertEqual([r['association_state'] for r in state['legs']], ['ACTIVE', 'PLANNED'])
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON task_shipments')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        key = uuid4(); self.call(arrive_transport_task, a, key)
        count = self.counts(); self.call(arrive_transport_task, a, key)
        self.assertEqual(self.counts(), count)
        self.assertEqual(self.state(shipment)['legs'][1]['association_state'], 'ACTIVE')

    def test_missing_references_can_be_manually_completed_and_wrong_origin_is_blocked(self):
        with SessionLocal() as session, session.begin():
            for route in (self.first, self.second):
                session.get(TransportRoute, int(route['id'])).travel_minutes = None
            session.get(Station, int(self.middle['id'])).transfer_minutes = None
        shipment = self.shipment(False)
        origin = int(self.source['id'])
        incomplete = self.preview(shipment, origin_station_id=origin)
        self.assertFalse(incomplete['can_confirm'])
        with self.assertRaises(NetworkError): self.confirm(shipment, incomplete)
        rows = [dict(route_id=int(route['id']), planned_departure_at=self.clock+timedelta(minutes=i*70),
            planned_arrival_at=self.clock+timedelta(minutes=i*70+60)) for i, route in enumerate((self.first, self.second))]
        preview = self.preview(shipment, origin_station_id=origin, legs=rows)
        self.confirm(shipment, preview)
        wrong = self.station('WRONG', first=True)
        self.event(shipment, 'PICKUP'); self.event(shipment, 'ARRIVE', wrong)
        result = self.state(shipment)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(all(row['association_state'] == 'PLANNED' for row in result['legs']))
        self.assertEqual(self.call(lambda s: s.get(Shipment, shipment).last_scanned_station_id), int(wrong['id']))

    def test_shared_limit_100_creates_another_trip(self):
        task_ids = []
        for _ in range(101):
            shipment = self.shipment(False)
            reviewed = self.confirm(shipment, self.preview(shipment, origin_station_id=int(self.source['id'])))
            task_ids.append(reviewed['legs'][0]['task_id'])
        self.assertEqual(len(set(task_ids[:100])), 1)
        self.assertNotEqual(task_ids[100], task_ids[0])
        self.assertEqual(self.call(lambda s: s.scalar(select(func.count()).select_from(TaskShipment).where(
            TaskShipment.task_id == int(task_ids[0]), TaskShipment.association_state != 'RELEASED'))), 100)

    def test_expired_preview_and_changed_cancel_membership_have_no_writes(self):
        from unittest.mock import patch
        shipment = self.shipment(); preview = self.preview(shipment)
        before = self.counts()
        import time
        with patch('scheduling.service.time.time', return_value=time.time()+601):
            with self.assertRaises(NetworkError) as expired: self.confirm(shipment, preview)
            self.assertEqual(expired.exception.code, 'PREVIEW_EXPIRED')
        self.assertEqual(self.counts(), before)
        first = self.confirm(shipment, preview); task = int(first['legs'][0]['task_id'])
        cancel = self.call(preview_cancel, task, '取消')
        second = self.shipment(); self.confirm(second)
        before = self.counts()
        with self.assertRaises(NetworkError):
            self.call(cancel_transport_task, task, TransportTaskCancelRequest(reason='取消',
                expected_schedule_revision=1, cancel_token=cancel['cancel_token']), uuid4())
        self.assertEqual(self.counts(), before)

    def test_reference_bounds_and_invalid_plan_overrides_are_rejected(self):
        from pydantic import ValidationError
        from network.schemas import StationCreateRequest, RouteCreateRequest
        from planning.schemas import PathPlanCreateRequest
        from planning.service import write_plan
        for minutes in (-1, True, '10', 525601):
            with self.assertRaises(ValidationError): StationCreateRequest(code='BOUNDS', name='bounds', transfer_minutes=minutes)
        for minutes in (0, -1, True, '10', 525601):
            with self.assertRaises(ValidationError): RouteCreateRequest(code='BOUNDS', origin_station_id=1, destination_station_id=2, travel_minutes=minutes)
        for overrides in ([dict(station_id=int(self.source['id']), minutes=5)],
                          [dict(station_id=int(self.middle['id']), minutes=5)]*2):
            request = PathPlanCreateRequest(code=self.prefix+'_INVALID', name='bad overrides',
                route_ids=[int(self.first['id']), int(self.second['id'])], transfer_overrides=overrides)
            with self.assertRaises(NetworkError): self.call(write_plan, request, uuid4())

    def test_depart_cancel_race_keeps_a_single_consistent_outcome(self):
        shipment = self.shipment(); reviewed = self.confirm(shipment)
        task = int(reviewed['legs'][0]['task_id'])
        preview = self.call(preview_cancel, task, '发车前取消')
        request = TransportTaskCancelRequest(reason='发车前取消', expected_schedule_revision=1, cancel_token=preview['cancel_token'])
        def compete(kind):
            try:
                if kind == 'depart': return self.call(depart_transport_task, task, uuid4(), 1)
                return self.call(cancel_transport_task, task, request, uuid4())
            except NetworkError: return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(compete, ('depart', 'cancel')))
        self.assertEqual(sum(result is not None for result in results), 1)
        state = self.state(shipment)
        if state['legs'][0]['task_status'] == 'CANCELLED':
            self.assertEqual(state['status'], 'NEEDS_RECONFIRMATION')
            self.assertTrue(all(row['association_state'] == 'RELEASED' for row in state['legs']))
        else:
            self.assertEqual(state['legs'][0]['task_status'], 'IN_TRANSIT')
            self.assertEqual(state['legs'][0]['association_state'], 'ACTIVE')
            self.assertEqual(state['legs'][1]['association_state'], 'PLANNED')

    def test_switching_route_preview_preserves_confirmed_plan_until_confirmation(self):
        shipment = self.shipment(); original = self.confirm(shipment)
        via = self.station('VIA'); one = self.route(self.source, via, 'S_V'); two = self.route(via, self.target, 'V_T')
        with SessionLocal() as session, session.begin():
            session.get(Station, int(via['id'])).transfer_minutes = 5
            for route in (one, two): session.get(TransportRoute, int(route['id'])).travel_minutes = 45
        before = self.counts(); original = self.state(shipment)
        preview = self.preview(shipment, route_ids=[int(one['id']), int(two['id'])])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.state(shipment), original)
        self.assertEqual([row['route_id'] for row in preview['legs']], [one['id'], two['id']])
        revised = self.confirm(shipment, preview)
        self.assertEqual(revised['version'], 2)
        self.assertEqual([row['route_id'] for row in revised['legs']], [one['id'], two['id']])
        for row in original['legs']:
            self.assertEqual(self.call(lambda s: s.get(TransportTask, int(row['task_id'])).status), 'CANCELLED')

    def test_invalid_time_order_is_rejected_without_writes(self):
        shipment = self.shipment(); before = self.counts()
        base = [dict(route_id=int(self.first['id']), planned_departure_at=self.clock,
                     planned_arrival_at=self.clock+timedelta(minutes=60)),
                dict(route_id=int(self.second['id']), planned_departure_at=self.clock+timedelta(minutes=70),
                     planned_arrival_at=self.clock+timedelta(minutes=130))]
        for index, field, value in [(0, 'planned_arrival_at', self.clock),
                                     (0, 'planned_departure_at', self.clock-timedelta(minutes=1)),
                                     (1, 'planned_departure_at', self.clock+timedelta(minutes=59))]:
            legs = [dict(row) for row in base]; legs[index][field] = value
            with self.assertRaises(NetworkError): self.preview(shipment, legs=legs)
        self.assertEqual(self.counts(), before)

    def test_cancel_downstream_release_failure_rolls_back_entire_chain(self):
        shipment = self.shipment(); reviewed = self.confirm(shipment)
        a, b = [int(row['task_id']) for row in reviewed['legs']]
        preview = self.call(preview_cancel, a, '取消审核')
        request = TransportTaskCancelRequest(reason='取消审核', expected_schedule_revision=1, cancel_token=preview['cancel_token'])
        function = 'v7_cancel_' + uuid4().hex[:8]
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.task_id={b} AND NEW.association_state='RELEASED' THEN RAISE EXCEPTION 'cancel fault'; END IF; RETURN NEW; END $$")
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE UPDATE ON task_shipments FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            before = self.counts()
            with self.assertRaises(DBAPIError): self.call(cancel_transport_task, a, request, uuid4())
            self.assertEqual(self.counts(), before)
            self.assertEqual(self.state(shipment), reviewed)
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON task_shipments')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        self.call(cancel_transport_task, a, request, uuid4())
        self.assertEqual(self.state(shipment)['status'], 'NEEDS_RECONFIRMATION')

    def test_arrival_cancel_race_preserves_fact_and_never_recreates_task(self):
        shipment = self.shipment(); reviewed = self.confirm(shipment)
        a, b = [int(row['task_id']) for row in reviewed['legs']]
        self.call(depart_transport_task, a, uuid4(), 1)
        preview = self.call(preview_cancel, b, '取消未来段')
        request = TransportTaskCancelRequest(reason='取消未来段', expected_schedule_revision=1, cancel_token=preview['cancel_token'])
        count = self.counts()[0]
        def compete(kind):
            try:
                if kind == 'arrive': return self.call(arrive_transport_task, a, uuid4())
                return self.call(cancel_transport_task, b, request, uuid4())
            except NetworkError: return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            arrived, canceled = list(executor.map(compete, ('arrive', 'cancel')))
        self.assertIsNotNone(arrived)
        self.assertEqual(self.counts()[0], count)
        state = self.state(shipment)
        self.assertEqual(state['legs'][0]['task_status'], 'ARRIVED')
        self.assertEqual(self.call(lambda s: s.get(Shipment, shipment).last_scanned_station_id), int(self.middle['id']))
        if canceled:
            self.assertEqual(state['status'], 'NEEDS_RECONFIRMATION')
            self.assertEqual(state['legs'][1]['association_state'], 'RELEASED')
        else:
            self.assertEqual(state['legs'][1]['association_state'], 'ACTIVE')
