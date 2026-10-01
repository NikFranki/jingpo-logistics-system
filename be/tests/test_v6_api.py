"""Public HTTP flow for whole paths, automatic next-leg tasks and future edits."""
import os
import unittest
from datetime import datetime,timedelta
from uuid import uuid4
from sqlalchemy.engine import make_url
import test_v3_api as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False,'requires *_test database')
class V6ApiTests(unittest.TestCase):
    setUpClass=classmethod(helpers.V3ApiTests.setUpClass.__func__)
    stop_server=classmethod(helpers.V3ApiTests.stop_server.__func__)
    request=helpers.V3ApiTests.request
    station=helpers.V3ApiTests.station

    def prepare(self):
        prefix='V6HTTP_'+uuid4().hex[:8].upper()
        source=self.station(prefix+'_S',allows_first_arrival=True)
        middle=self.station(prefix+'_M')
        target=self.station(prefix+'_T',allows_delivery=True)
        routes=[]
        for suffix,a,b in [('SM',source,middle),('MT',middle,target)]:
            status,route=self.request('/routes','POST',dict(code=prefix+'_'+suffix,origin_station_id=int(a['id']),destination_station_id=int(b['id'])))
            self.assertEqual(status,201,route); routes.append(route)
        status,plan=self.request('/path-plans','POST',dict(code=prefix+'_PLAN',name='完整路径',route_ids=[int(r['id']) for r in routes]))
        self.assertEqual(status,201,plan)
        status,order=self.request('/orders/create','POST',dict(product_name='V6',quantity=1,sender_name='s',sender_address='s',recipient_name='r',recipient_address='r'))
        self.assertEqual(status,201,order)
        status,parcel=self.request('/orders/'+order['id']+'/shipment','POST',dict(destination_station_id=int(target['id'])))
        self.assertEqual(status,201,parcel)
        return prefix,source,middle,target,routes,plan,parcel

    def test_automatic_full_path_next_leg_task_and_future_path_contract(self):
        prefix,source,middle,target,routes,plan,parcel=self.prepare()
        base='/shipments/'+parcel['id']; path=base+'/path'
        self.assertEqual(self.request(path)[1]['status'],'WAITING_FIRST_ARRIVAL')
        for event in (dict(event_type='PICKUP'),dict(event_type='ARRIVE',station_id=source['id'])):
            self.assertEqual(self.request(base+'/events','POST',event)[0],200)
        initial=self.request(path)[1]
        self.assertEqual((initial['version'],initial['next_route_code']),(1,routes[0]['code']))
        detail=self.request(base)[1]
        self.assertEqual(detail['transport_path'],initial)
        self.assertEqual(self.request(base+'/path-options')[1]['plans'][0]['id'],plan['id'])
        clock=datetime.fromisoformat(self.request('/simulation/clock')[1]['current_time'])
        body=dict(shipment_ids=[int(parcel['id'])],expected_arrival_at=(clock+timedelta(hours=1)).isoformat(),expected_path_versions={parcel['id']:1})
        status,task=self.request('/transport-tasks/create','POST',body)
        self.assertEqual(status,201,task)
        self.assertEqual(task['route_code'],routes[0]['code'])
        replacement=dict(expected_version=1,expected_anchor_station_id=int(source['id']),reason='未来路径',route_ids=[int(r['id']) for r in routes])
        status,error=self.request(path,'PUT',replacement)
        self.assertEqual((status,error['error']['code']),(409,'PATH_TASK_OCCUPIED'))
        taskbase='/transport-tasks/'+task['id']
        self.assertEqual(self.request(taskbase+'/depart','POST')[0],200)
        via=self.station(prefix+'_V')
        future=[]
        for suffix,a,b in [('MV',middle,via),('VT',via,target)]:
            status,route=self.request('/routes','POST',dict(code=prefix+'_'+suffix,origin_station_id=int(a['id']),destination_station_id=int(b['id'])))
            self.assertEqual(status,201,route); future.append(route)
        request=dict(expected_version=1,expected_anchor_station_id=int(middle['id']),reason='  调整未来段  ',route_ids=[int(r['id']) for r in future])
        key=str(uuid4()); status,updated=self.request(path,'PUT',request,key)
        self.assertEqual(status,200,updated)
        self.assertEqual(updated['legs'][0]['id'],initial['legs'][0]['id'])
        self.assertEqual(updated['legs'][0]['state'],'IN_TRANSIT')
        self.assertEqual(self.request(path,'PUT',{**request,'reason':'调整未来段'},key),(200,updated))
        self.assertEqual(self.request(path,'PUT',{**request,'reason':'其他'},key)[0],409)
        status,error=self.request(path,'PUT',request)
        self.assertEqual((status,error['error']['code']),(409,'PATH_VERSION_CONFLICT'))
        for params in ('?page=0','?page_size=101'):
            self.assertEqual(self.request(base+'/path-history'+params)[0],422)
        self.assertEqual(self.request(base+'/path-history?page_size=1')[1]['total'],2)
        self.assertEqual(self.request(base+'/path-history?page=2&page_size=1')[1]['items'][0]['version'],1)
        self.assertEqual(self.request(taskbase+'/arrive','POST')[0],200)
        self.assertEqual(self.request(path)[1]['next_route_code'],future[0]['code'])
        for expected_route in future:
            body['expected_path_versions']={parcel['id']:2}
            status,next_task=self.request('/transport-tasks/create','POST',body)
            self.assertEqual(status,201,next_task)
            self.assertEqual(next_task['route_code'],expected_route['code'])
            for event in ('depart','arrive'):
                self.assertEqual(self.request('/transport-tasks/'+next_task['id']+'/'+event,'POST')[0],200)
        self.assertEqual(self.request(path)[1]['status'],'COMPLETED')
        for event in ('START_DELIVERY','SIGN'):
            self.assertEqual(self.request(base+'/events','POST',dict(event_type=event))[0],200)
        self.assertEqual(self.request('/orders/'+parcel['order_id'])[1]['status'],'COMPLETED')

    def test_plan_request_validation_versions_unplanned_binding_and_errors(self):
        prefix,source,middle,target,routes,plan,parcel=self.prepare()
        base='/shipments/'+parcel['id']; path=base+'/path'
        valid=dict(code=prefix+'_OTHER',name='另一个',route_ids=[int(r['id']) for r in routes])
        for invalid in ({**valid,'extra':1},{**valid,'route_ids':[]},{**valid,'route_ids':[True]}, {**valid,'route_ids':['1']}):
            self.assertEqual(self.request('/path-plans','POST',invalid)[0],422)
        self.assertEqual(self.request('/path-plans','POST',valid)[0],201)
        self.assertEqual(self.request('/path-plans','POST',valid)[0],409)
        self.assertEqual(self.request('/path-plans/'+plan['id'],'PATCH',dict(expected_version=1,code='change'))[0],422)
        self.assertEqual(self.request('/path-plans/'+plan['id'],'PATCH',dict(expected_version=1))[0],422)
        self.assertEqual(self.request('/path-plans/999999999','PATCH',dict(expected_version=1,enabled=False))[0],404)
        for event in (dict(event_type='PICKUP'),dict(event_type='ARRIVE',station_id=source['id'])):
            self.assertEqual(self.request(base+'/events','POST',event)[0],200)
        self.assertEqual(self.request(path)[1]['status'],'NEEDS_PLANNING')
        self.assertEqual(len(self.request(base+'/path-options')[1]['plans']),2)
        chosen=dict(expected_version=0,expected_anchor_station_id=int(source['id']),reason='一次选择',plan_id=int(plan['id']),expected_plan_version=1)
        for invalid in ({**chosen,'reason':' '},{**chosen,'reason':1},{**chosen,'reason':'x'*501},{**chosen,'route_ids':[]},
                        {**chosen,'plan_id':True},{k:v for k,v in chosen.items() if k!='expected_plan_version'}):
            self.assertEqual(self.request(path,'PUT',invalid)[0],422)
        status,selected=self.request(path,'PUT',chosen)
        self.assertEqual(status,200,selected)
        self.assertEqual(self.request('/path-plans/'+plan['id'],'PATCH',dict(expected_version=1,name='更名'))[0],200)
        self.assertEqual(self.request('/path-plans/'+plan['id'],'PATCH',dict(expected_version=1,enabled=False))[0],409)
        self.assertEqual(self.request(path)[1],selected)
        for suffix in ('path','path-options','path-history'):
            self.assertEqual(self.request('/shipments/999999999/'+suffix)[0],404)
        self.assertEqual(self.request('/shipments/999999999/path','PUT',chosen)[0],404)
        clock=datetime.fromisoformat(self.request('/simulation/clock')[1]['current_time'])
        task=dict(shipment_ids=[int(parcel['id'])],expected_arrival_at=(clock+timedelta(hours=1)).isoformat())
        self.assertEqual(self.request('/transport-tasks/create','POST',task)[0],422)
        for versions in ({},{parcel['id']:'1'},{parcel['id']:True},{parcel['id']:0}):
            self.assertEqual(self.request('/transport-tasks/create','POST',{**task,'expected_path_versions':versions})[0],422)
        self.assertEqual(self.request('/transport-tasks/create','POST',{**task,'expected_path_versions':{parcel['id']:2}})[0],409)
