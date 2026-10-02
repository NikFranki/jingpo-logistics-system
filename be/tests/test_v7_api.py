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
        routes = []
        for suffix, a, b in [('SM', source, middle), ('MT', middle, target)]:
            status, route = self.request('/routes', 'POST', dict(code=prefix+'_'+suffix,
                origin_station_id=int(a['id']), destination_station_id=int(b['id']), travel_minutes=60))
            self.assertEqual(status, 201, route); routes.append(route)
        status, plan = self.request('/path-plans', 'POST', dict(code=prefix+'_PLAN', name='V7计划',
            route_ids=[int(r['id']) for r in routes], transfer_overrides=[dict(station_id=int(middle['id']), minutes=15)]))
        self.assertEqual(status, 201, plan)
        _, order = self.request('/orders/create', 'POST', dict(product_name='V7', quantity=1,
            sender_name='s', sender_address='s', recipient_name='r', recipient_address='r'))
        status, parcel = self.request('/orders/'+order['id']+'/shipment', 'POST',
            dict(destination_station_id=int(target['id'])))
        self.assertEqual(status, 201, parcel); self.assertEqual(parcel['scheduling_mode'], 'REVIEWED')
        base = '/shipments/'+parcel['id']
        status, preview = self.request(base+'/schedule/preview', 'POST', dict(origin_station_id=int(source['id']),
            plan_id=int(plan['id']), expected_plan_version=1))
        self.assertEqual(status, 200, preview); self.assertEqual(preview['legs'][1]['transfer_reference_minutes'], 15)
        key = str(uuid4()); body = dict(preview_token=preview['preview_token'], reason='已审核')
        status, confirmed = self.request(base+'/schedule/confirm', 'POST', body, key)
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(self.request(base+'/schedule/confirm', 'POST', body, key)[1], confirmed)
        self.assertEqual(len(confirmed['legs']), 2)
        self.assertEqual(self.request(base+'/schedule-history')[1]['total'], 1)
        detail = self.request(base)[1]
        self.assertIsNone(detail['active_transport_task'])
        self.assertFalse(next(a for a in detail['allowed_actions'] if a['action']=='UPDATE_DESTINATION')['enabled'])
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
        self.assertEqual(self.request(base+'/schedule/preview', 'POST', dict(plan_id=True))[0], 422)
        self.assertEqual(self.request(base+'/schedule/confirm', 'POST', dict(preview_token='bad', reason='确认'))[0], 409)
        self.assertEqual(self.request(base+'/schedule/preview', 'POST', dict(origin_station_id=int(source['id']),
            first_departure_at='2026-10-01T17:30:01+08:00'))[0], 422)
        openapi = helpers.urlopen(self.base+'/openapi.json')
        import json
        with openapi: schema = json.load(openapi)
        self.assertIn('SchedulePreviewResponse', schema['components']['schemas'])
