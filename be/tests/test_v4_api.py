"""HTTP qualification, cancellation, history and validation contracts."""
import os
import unittest
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy.engine import make_url
import test_v3_api as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V4ApiTests(unittest.TestCase):
    setUpClass = classmethod(helpers.V3ApiTests.setUpClass.__func__)
    stop_server = classmethod(helpers.V3ApiTests.stop_server.__func__)
    request = helpers.V3ApiTests.request
    station = helpers.V3ApiTests.station

    def test_cancel_history_qualification_errors_and_retry(self):
        prefix='V4HTTP_'+uuid4().hex[:8].upper()
        source=self.station(prefix+'_S',allows_first_arrival=True)
        target=self.station(prefix+'_D',allows_delivery=True)
        status, route=self.request('/routes','POST',dict(code=prefix+'_R',origin_station_id=int(source['id']),destination_station_id=int(target['id']),delay_monitoring_enabled=True))
        self.assertEqual(status,201,route)
        status,plan=self.request('/path-plans','POST',dict(code=prefix+'_PLAN',name='HTTP path',route_ids=[int(route['id'])]))
        self.assertEqual(status,201,plan)
        status,order=self.request('/orders/create','POST',dict(product_name='V4',quantity=1,sender_name='s',sender_address='s',recipient_name='r',recipient_address='r'))
        self.assertEqual(status,201,order)
        status,parcel=self.request('/orders/'+order['id']+'/shipment','POST',{'destination_station_id':int(target['id'])})
        self.assertEqual(status,201,parcel)
        base='/shipments/'+parcel['id']
        history=base+'/transport-tasks'
        self.assertEqual(self.request(history)[1],dict(items=[],total=0,page=1,page_size=20))
        for params in ('?page=0','?page_size=101','?page_size=0'):
            self.assertEqual(self.request(history+params)[0],422)
        self.assertEqual(self.request('/shipments/999999999/transport-tasks')[0],404)
        for event in (dict(event_type='PICKUP'),dict(event_type='ARRIVE',station_id=source['id'])):
            self.assertEqual(self.request(base+'/events','POST',event)[0],200)
        detail=self.request(base)[1]
        self.assertTrue(next(a for a in detail['allowed_actions'] if a['action']=='CREATE_TRANSPORT_TASK')['enabled'])
        clock=datetime.fromisoformat(self.request('/server-time')[1]['server_time'])
        request=dict(route_code=route['code'],expected_arrival_at=(clock+timedelta(hours=1)).isoformat(),shipment_ids=[int(parcel['id'])])
        status, task=self.request('/transport-tasks/create','POST',request)
        self.assertEqual(status,201,task)
        self.assertIsNone(task['cancelled_at']); self.assertIsNone(task['cancel_reason'])
        taskbase='/transport-tasks/'+task['id']; cancel=taskbase+'/cancel'
        self.assertTrue(next(a for a in self.request(taskbase)[1]['allowed_actions'] if a['action']=='CANCEL')['enabled'])
        for body in ({},{'reason':' '},{'reason':3},{'reason':None},{'reason':'x'*501},{'reason':'ok','extra':True}):
            status,error=self.request(cancel,'POST',body)
            self.assertEqual((status,error['error']['code']),(422,'VALIDATION_ERROR'))
        self.assertEqual(self.request(cancel,'POST',{'reason':'ok'},'invalid-key')[0],422)
        self.assertEqual(self.request('/transport-tasks/999999999/cancel','POST',{'reason':'ok'})[0],404)
        key=str(uuid4())
        status,cancelled=self.request(cancel,'POST',{'reason':'  调整路线  '},key)
        self.assertEqual(status,200,cancelled)
        self.assertEqual((cancelled['status'],cancelled['cancel_reason']),('CANCELLED','调整路线'))
        self.assertTrue(all(not a['enabled'] for a in cancelled['allowed_actions']))
        self.assertEqual((cancelled['delay_status'],cancelled['delay_minutes']),('NOT_APPLICABLE',None))
        self.assertEqual(self.request(cancel,'POST',{'reason':'调整路线'},key),(200,cancelled))
        status, repeated = self.request(cancel,'POST',{'reason':'调整路线'})
        self.assertEqual(status, 200)
        self.assertEqual({k:v for k,v in repeated.items() if k != 'server_time'}, {k:v for k,v in cancelled.items() if k != 'server_time'})
        for suffix,body in (('/depart',None),('/arrive',None),('/cancel',{'reason':'另一原因'})):
            status,error=self.request(taskbase+suffix,'POST',body)
            self.assertEqual((status,error['error']['code']),(409,'HTTP_409'))
        self.assertEqual(self.request(cancel,'POST',{'reason':'其他'},key)[0],409)
        after=self.request(base)[1]
        self.assertEqual(after['tracking_events'],detail['tracking_events'])
        self.assertIsNone(after['active_transport_task'])
        self.assertTrue(next(a for a in after['allowed_actions'] if a['action']=='CREATE_TRANSPORT_TASK')['enabled'])
        listing=self.request('/transport-tasks?status=CANCELLED&route_code='+route['code'])[1]
        self.assertEqual(listing['total'],1)
        self.assertEqual(listing['items'][0]['cancel_reason'],'调整路线')
        self.assertEqual(self.request('/transport-tasks?status=BAD')[0],422)
        status,new=self.request('/transport-tasks/create','POST',request)
        self.assertEqual(status,201,new)
        self.assertEqual(self.request(cancel,'POST',{'reason':'调整路线'},key),(200,cancelled))
        self.assertEqual(self.request(base)[1]['active_transport_task']['id'],new['id'])
        self.assertEqual(self.request(history+'?page_size=1')[1]['items'][0]['id'],new['id'])
        self.assertEqual(self.request(history+'?page=2&page_size=1')[1]['items'][0]['id'],task['id'])
        self.assertEqual(self.request(history+'?page=3&page_size=1')[1]['items'],[])

