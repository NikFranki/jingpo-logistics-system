"""Public V7 confirmation contract, request guards and retained cached replay."""
import os
import unittest
from uuid import uuid4
from sqlalchemy.engine import make_url
import test_v3_api as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V7ApiTests(unittest.TestCase):
    setUpClass = classmethod(helpers.V3ApiTests.setUpClass.__func__)
    stop_server = classmethod(helpers.V3ApiTests.stop_server.__func__)
    station = helpers.V3ApiTests.station

    def request(self, path, method='GET', body=None, key=None):
        return helpers.V3ApiTests.request(self, path, method, body, key, legacy=False)

    def test_schedule_review_confirm_history_depart_and_cancel_contract(self):
        prefix = 'V7HTTP_' + uuid4().hex[:8].upper()
        source = self.station(prefix+'_S', allows_first_arrival=True)
        middle = self.station(prefix+'_M', transfer_minutes=10)
        target = self.station(prefix+'_T', allows_delivery=True)
        status, line = self.request('/transport-lines', 'POST', dict(code=prefix+'_LINE', name='V7线路',
            station_ids=[int(source['id']), int(middle['id']), int(target['id'])],
            legs=[dict(travel_minutes=60), dict(travel_minutes=60)],
            transfer_overrides=[dict(station_id=int(middle['id']), minutes=15)]))
        self.assertEqual(status, 201, line)
        _, order = self.request('/orders/create', 'POST', dict(product_name='V7', quantity=1,
            sender_name='s', sender_address='s', recipient_name='r', recipient_address='r'))
        status, parcel = self.request('/orders/'+order['id']+'/shipment', 'POST',
            dict(destination_station_id=int(target['id'])))
        self.assertEqual(status, 201, parcel)
        base = '/shipments/'+parcel['id']
        status, preview = self.request(base+'/schedule/preview', 'POST', dict(origin_station_id=int(source['id']),
            line_id=int(line['id']), expected_line_version=1))
        self.assertEqual(status, 200, preview); self.assertEqual(preview['legs'][1]['transfer_reference_minutes'], 15)
        key = str(uuid4()); body = dict(preview_token=preview['preview_token'], reason='已审核')
        status, confirmed = self.request(base+'/schedule/confirm', 'POST', body, key)
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(self.request(base+'/schedule/confirm', 'POST', body, key)[1], confirmed)
        self.assertEqual(len(confirmed['legs']), 2)
        self.assertEqual(self.request(base+'/schedule-history')[1]['total'], 1)
        detail = self.request(base)[1]
        self.assertIsNone(detail['active_transport_task'])
        self.assertTrue(next(a for a in detail['allowed_actions'] if a['action']=='UPDATE_DESTINATION')['enabled'])
        task = '/transport-tasks/'+confirmed['legs'][0]['task_id']
        self.assertEqual(self.request(task+'/depart', 'POST', dict(expected_schedule_revision=1))[0], 409)
        self.assertEqual(self.request(task+'/cancel', 'POST', dict(reason='改计划', expected_schedule_revision=1))[0], 409)
        _, cancel = self.request(task+'/cancel-preview', 'POST', dict(reason='改计划'))
        self.assertEqual(len(cancel['impact']), 2)
        status, canceled = self.request(task+'/cancel', 'POST', dict(reason='改计划', expected_schedule_revision=1,
            cancel_token=cancel['cancel_token']))
        self.assertEqual(status, 200, canceled); self.assertEqual(len(canceled['cancel_impact']), 2)
        self.assertEqual(self.request(base+'/schedule')[1]['status'], 'NEEDS_RECONFIRMATION')
        for path in ('/shipments/0/schedule', base+'/schedule-history?page_size=101'):
            self.assertEqual(self.request(path)[0], 422)
        self.assertEqual(self.request(base+'/schedule/preview', 'POST', dict(line_id=True))[0], 422)
        self.assertEqual(self.request(base+'/schedule/confirm', 'POST', dict(preview_token='bad', reason='确认'))[0], 409)
        self.assertEqual(self.request(base+'/schedule/preview', 'POST', dict(origin_station_id=int(source['id']),
            first_departure_at='2026-10-01T17:30:01+08:00'))[0], 422)
        openapi = helpers.urlopen(self.base+'/openapi.json')
        import json
        with openapi: schema = json.load(openapi)
        self.assertIn('SchedulePreviewResponse', schema['components']['schemas'])
        self.assertNotIn('/api/v1/path-plans', schema['paths'])
        self.assertNotIn('/api/v1/shipments/{shipment_id}/path', schema['paths'])
        self.assertNotIn('/api/v1/shipments/{shipment_id}/path-options', schema['paths'])
        shipment_fields = schema['components']['schemas']['ShipmentDetailResponse']['properties']
        self.assertFalse({'path_version', 'transport_path'} & shipment_fields.keys())
        self.assertEqual(self.request(base+'/schedule/preview', 'POST', dict(route_ids=[1]))[0], 422)

    def test_destination_correction_releases_unstarted_schedule_and_requires_review(self):
        prefix = 'V7DEST_' + uuid4().hex[:8].upper()
        source = self.station(prefix+'_S', allows_first_arrival=True)
        target = self.station(prefix+'_T', allows_delivery=True)
        replacement = self.station(prefix+'_R', allows_delivery=True)
        status, line = self.request('/transport-lines', 'POST', dict(code=prefix+'_LINE', name='V7线路',
            station_ids=[int(source['id']), int(target['id'])], legs=[dict(travel_minutes=60)]))
        self.assertEqual(status, 201, line)
        _, order = self.request('/orders/create', 'POST', dict(product_name='V7', quantity=1,
            sender_name='s', sender_address='s', recipient_name='r', recipient_address='r'))
        status, parcel = self.request('/orders/'+order['id']+'/shipment', 'POST',
            dict(destination_station_id=int(target['id'])))
        self.assertEqual(status, 201, parcel)
        base = '/shipments/'+parcel['id']
        status, preview = self.request(base+'/schedule/preview', 'POST', dict(origin_station_id=int(source['id']),
            line_id=int(line['id']), expected_line_version=1))
        self.assertEqual(status, 200, preview)
        status, schedule = self.request(base+'/schedule/confirm', 'POST',
            dict(preview_token=preview['preview_token'], reason='审核通过'), str(uuid4()))
        self.assertEqual(status, 200, schedule)
        task_ids = [leg['task_id'] for leg in schedule['legs']]

        status, corrected = self.request(base+'/destination', 'PATCH', dict(
            expected_destination_station_id=int(target['id']),
            destination_station_id=int(replacement['id']), reason='目的站更正'), str(uuid4()))

        self.assertEqual(status, 200, corrected)
        self.assertEqual(corrected['destination_station_id'], replacement['id'])
        self.assertEqual(corrected['schedule']['status'], 'NEEDS_RECONFIRMATION')
        for task_id in task_ids:
            status, task = self.request('/transport-tasks/'+task_id)
            self.assertEqual(status, 200, task)
            self.assertEqual(task['status'], 'CANCELLED')
