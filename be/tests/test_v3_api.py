"""Exercise the HTTP contract using a subprocess bound only to the test DB."""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from sqlalchemy.engine import make_url

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False, 'requires *_test database')
class V3ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        cls.server=subprocess.Popen([sys.executable,'-m','uvicorn','main:app','--host','127.0.0.1','--port',str(port)], stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_server)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                with urlopen(cls.base+'/health/ready',timeout=1) as response:
                    if response.status==200:
                        return
            except (URLError,TimeoutError):
                if cls.server.poll() is not None:
                    raise RuntimeError('test backend stopped during startup')
                time.sleep(.1)
        raise RuntimeError('test backend did not become ready')

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill(); cls.server.wait()

    def request(self,path,method='GET',body=None,key=None):
        headers={'Content-Type':'application/json'}
        if method!='GET':
            headers['Idempotency-Key']=key or str(uuid4())
        request=Request(self.base+'/api/v1'+path,method=method,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            response=urlopen(request,timeout=5)
        except HTTPError as error:
            response=error
        with response:
            payload=json.load(response)
            if response.status>=400:
                self.assertEqual(response.headers['X-Request-ID'],payload['request_id'])
            return response.status,payload

    def station(self,code,**flags):
        status,body=self.request('/stations','POST',{'code':code,'name':code,**flags})
        self.assertEqual(status,201,body)
        return body

    def test_network_errors_filters_and_immutable_fields(self):
        code='API_'+uuid4().hex[:8].upper()
        key=str(uuid4())
        request={'code':code,'name':' API 名称 ','allows_delivery':True}
        status,created=self.request('/stations','POST',request,key)
        self.assertEqual(status,201,created)
        self.assertEqual(created['name'],'API 名称')
        self.assertEqual(self.request('/stations','POST',request,key),(201,created))
        status,error=self.request('/stations','POST',request)
        self.assertEqual((status,error['error']['code']),(409,'NETWORK_CODE_CONFLICT'))
        status,error=self.request('/stations/'+created['id'],'PATCH',{'code':'CHANGE'})
        self.assertEqual((status,error['error']['code']),(422,'VALIDATION_ERROR'))
        self.assertEqual(self.request('/stations/999999999','PATCH',{'name':'missing'})[0],404)
        patch_key=str(uuid4())
        status,disabled=self.request('/stations/'+created['id'],'PATCH',{'enabled':False},patch_key)
        self.assertEqual(status,200,disabled)
        self.assertFalse(disabled['enabled'])
        self.assertEqual(self.request('/stations/'+created['id'],'PATCH',{'enabled':False},patch_key),(200,disabled))
        self.assertIn(created['id'],[s['id'] for s in self.request('/stations')[1]])
        self.assertNotIn(created['id'],[s['id'] for s in self.request('/stations?enabled=true')[1]])
        target=self.station(code+'_D',allows_delivery=True)
        status,error=self.request('/routes','POST',{'code':code+'_R','origin_station_id':int(created['id']),'destination_station_id':int(target['id'])})
        self.assertEqual((status,error['error']['code']),(409,'NETWORK_RESOURCE_DISABLED'))
        status,error=self.request('/routes','POST',{'code':code+'_L','origin_station_id':int(target['id']),'destination_station_id':int(target['id'])})
        self.assertEqual((status,error['error']['code']),(409,'INVALID_NETWORK_CONFIGURATION'))
        self.assertEqual(self.request('/transport-tasks/candidates?route_code=DOES_NOT_EXIST')[0],404)

    def test_non_abc_http_flow_and_contract(self):
        prefix='HTTP_'+uuid4().hex[:8].upper()
        source=self.station(prefix+'_S',allows_first_arrival=True)
        target=self.station(prefix+'_D',allows_delivery=True)
        code=prefix+'_SD'
        status,route=self.request('/routes','POST',{'code':code,'origin_station_id':int(source['id']),'destination_station_id':int(target['id']),'delay_monitoring_enabled':True})
        self.assertEqual(status,201,route)
        self.assertEqual(self.request('/routes/'+route['id'],'PATCH',{'destination_station_id':int(source['id'])})[0],422)
        status,order=self.request('/orders/create','POST',{'product_name':'HTTP 样本','quantity':1,'sender_name':'s','sender_address':'原发件地址','recipient_name':'r','recipient_address':'原收件地址'})
        self.assertEqual(status,201,order)
        self.assertEqual(self.request('/orders/'+order['id']+'/shipment','POST',{})[0],422)
        status,shipment=self.request('/orders/'+order['id']+'/shipment','POST',{'destination_station_id':int(target['id'])})
        self.assertEqual(status,201,shipment)
        self.assertEqual(shipment['destination_station_id'],target['id'])
        events='/shipments/'+shipment['id']+'/events'
        self.assertEqual(self.request(events,'POST',{'event_type':'PICKUP'})[0],200)
        status,arrived=self.request(events,'POST',{'event_type':'ARRIVE','station_id':source['id']})
        self.assertEqual(status,200,arrived)
        candidates=self.request('/transport-tasks/candidates?route_code='+code)[1]
        self.assertIn(shipment['id'],[s['id'] for s in candidates['items']])
        clock=datetime.fromisoformat(self.request('/simulation/clock')[1]['current_time'])
        status,task=self.request('/transport-tasks/create','POST',{'route_code':code,'expected_arrival_at':(clock+timedelta(hours=1)).isoformat(),'shipment_ids':[int(shipment['id'])]})
        self.assertEqual(status,201,task)
        self.assertTrue(task['delay_monitoring_enabled'])
        self.assertEqual(self.request('/routes/'+route['id'],'PATCH',{'enabled':False,'delay_monitoring_enabled':False})[0],200)
        self.assertEqual(self.request('/transport-tasks/'+task['id']+'/depart','POST')[0],200)
        self.assertEqual(self.request('/simulation/clock/advance','POST',{'minutes':120})[0],200)
        status,detail=self.request('/transport-tasks/'+task['id'])
        self.assertEqual(status,200,detail)
        self.assertEqual((detail['delay_status'],detail['delay_minutes']),('OVERDUE',60))
        self.assertEqual(self.request('/transport-tasks/'+task['id']+'/arrive','POST')[0],200)
        detail=self.request('/transport-tasks/'+task['id'])[1]
        self.assertEqual((detail['delay_status'],detail['delay_minutes']),('LATE_ARRIVAL',60))
        self.assertEqual(self.request(events,'POST',{'event_type':'START_DELIVERY'})[0],200)
        status,signed=self.request(events,'POST',{'event_type':'SIGN'})
        self.assertEqual(status,200,signed)
        self.assertEqual(signed['stage'],'SIGNED')
        self.assertEqual(self.request('/orders/'+order['id'])[1]['status'],'COMPLETED')
        filtered=self.request('/transport-tasks?route_code='+code)[1]
        self.assertEqual(filtered['total'],1)
        self.assertEqual(filtered['items'][0]['route_code'],code)
        self.assertIn(route['id'],[r['id'] for r in self.request('/routes')[1]])
        self.assertNotIn(route['id'],[r['id'] for r in self.request('/routes?enabled=true')[1]])
