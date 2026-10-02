"""V5 HTTP destination editing and read-only correction history."""
import os
import unittest
from uuid import uuid4
from datetime import datetime
from sqlalchemy.engine import make_url
import test_v3_api as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V5ApiTests(unittest.TestCase):
    setUpClass = classmethod(helpers.V3ApiTests.setUpClass.__func__)
    stop_server = classmethod(helpers.V3ApiTests.stop_server.__func__)
    request = helpers.V3ApiTests.request
    station = helpers.V3ApiTests.station

    def test_correction_history_validation_idempotency_and_delivery(self):
        prefix='V5HTTP_'+uuid4().hex[:8].upper()
        current=self.station(prefix+'_CURRENT',allows_first_arrival=True,allows_delivery=True)
        old=self.station(prefix+'_OLD',allows_delivery=True)
        status,order=self.request('/orders/create','POST',dict(product_name='V5',quantity=1,sender_name='s',sender_address='s',recipient_name='r',recipient_address='r'))
        self.assertEqual(status,201,order)
        status,parcel=self.request('/orders/'+order['id']+'/shipment','POST',{'destination_station_id':int(old['id'])})
        self.assertEqual(status,201,parcel)
        base='/shipments/'+parcel['id']; path=base+'/destination'; history=base+'/destination-changes'
        self.assertEqual(self.request(history)[1],dict(items=[],total=0,page=1,page_size=20))
        self.assertTrue(next(a for a in parcel['allowed_actions'] if a['action']=='UPDATE_DESTINATION')['enabled'])
        body=dict(expected_destination_station_id=int(old['id']),destination_station_id=int(current['id']),reason='  更正  ')
        invalids=[{}, {**body,'extra':True}]
        for field in ('expected_destination_station_id','destination_station_id'):
            invalids.extend({**body,field:value} for value in ('1',1.5,True,0,-1,None))
        invalids.extend({**body,'reason':value} for value in (' ',3,None,'x'*501))
        for invalid in invalids:
            status,error=self.request(path,'PATCH',invalid)
            self.assertEqual((status,error['error']['code']),(422,'VALIDATION_ERROR'))
        self.assertEqual(self.request(path,'PATCH',body,'invalid-key')[0],422)
        self.assertEqual(self.request('/shipments/999999999/destination','PATCH',body)[0],404)
        self.assertEqual(self.request('/shipments/999999999/destination-changes')[0],404)
        self.assertEqual(self.request(path,'PATCH',{**body,'destination_station_id':999999999})[0],404)
        non_delivery=self.station(prefix+'_SORT')
        status,error=self.request(path,'PATCH',{**body,'destination_station_id':int(non_delivery['id'])})
        self.assertEqual((status,error['error']['code']),(409,'INVALID_NETWORK_CONFIGURATION'))
        for params in ('?page=0','?page_size=101','?page_size=0'):
            self.assertEqual(self.request(history+params)[0],422)
        key=str(uuid4())
        status,corrected=self.request(path,'PATCH',body,key)
        self.assertEqual(status,200,corrected)
        self.assertEqual(corrected['destination_station_id'],current['id'])
        normalized={**body,'reason':'更正'}
        self.assertEqual(self.request(path,'PATCH',normalized,key),(200,corrected))
        record=self.request(history)[1]
        self.assertEqual(record['total'],1)
        item=record['items'][0]
        self.assertEqual((item['previous_destination_station_id'],item['destination_station_id'],item['reason']),(old['id'],current['id'],'更正'))
        self.assertIsNotNone(datetime.fromisoformat(item['occurred_at']).utcoffset())
        self.assertIsInstance(item['id'],str)
        self.assertNotIn('idempotency_key',item)
        status,error=self.request(path,'PATCH',normalized)
        self.assertEqual((status,error['error']['code']),(409,'HTTP_409'))  # stale old station
        self.assertEqual(self.request(path,'PATCH',{**normalized,'reason':'different'},key)[0],409)
        same={**normalized,'expected_destination_station_id':int(current['id'])}
        self.assertEqual(self.request(path,'PATCH',same)[0],409)
        reverse=dict(expected_destination_station_id=int(current['id']),destination_station_id=int(old['id']),reason='x'*500)
        self.assertEqual(self.request(path,'PATCH',reverse)[0],200)
        first_page=self.request(history+'?page_size=1')[1]
        second_page=self.request(history+'?page=2&page_size=1')[1]
        self.assertEqual(first_page['total'],2)
        self.assertEqual(first_page['items'][0]['reason'],'x'*500)
        self.assertEqual(second_page['items'][0],item)
        self.assertEqual(self.request(history+'?page=3&page_size=1')[1]['items'],[])
        self.assertEqual(self.request(path,'PATCH',normalized,key),(200,corrected))
        self.assertEqual(self.request(base)[1]['destination_station_id'],old['id'])
        for event in (dict(event_type='PICKUP'),dict(event_type='ARRIVE',station_id=current['id'])):
            self.assertEqual(self.request(base+'/events','POST',event)[0],200)
        status,at_destination=self.request(path,'PATCH',normalized)
        self.assertEqual(status,200,at_destination)
        self.assertTrue(next(a for a in at_destination['allowed_actions'] if a['action']=='START_DELIVERY')['enabled'])
        self.assertEqual(self.request(base+'/events','POST',{'event_type':'START_DELIVERY'})[0],200)
        status,error=self.request(path,'PATCH',reverse)
        self.assertEqual((status,error['error']['code']),(409,'HTTP_409'))
        self.assertEqual(self.request(base+'/events','POST',{'event_type':'SIGN'})[0],200)
        self.assertEqual(self.request(path,'PATCH',reverse)[0],409)
        detail=self.request(base)[1]
        self.assertEqual(len(detail['tracking_events']),5)
        self.assertEqual(detail['recipient_address'],'r')
        self.assertFalse(next(a for a in detail['allowed_actions'] if a['action']=='UPDATE_DESTINATION')['enabled'])
        self.assertEqual(self.request(history)[1]['total'],3)
        self.assertEqual(self.request('/orders/'+order['id'])[1]['recipient_address'],'r')

