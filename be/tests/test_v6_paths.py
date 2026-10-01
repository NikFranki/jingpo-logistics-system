"""Full-path reuse, next-leg admission, immutable facts and future replanning."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from db import SessionLocal,engine
from models import (Shipment,PathPlan,ShipmentPathVersion,TaskShipment,OperationLog,
    TransportTask,ShipmentPathLeg)
from errors import InvalidTaskShipmentError,NetworkError,IdempotencyKeyReusedError
from planning.schemas import PathPlanCreateRequest,PathPlanUpdateRequest,ShipmentPathUpdateRequest
from planning.service import (write_plan,write_shipment_path,shipment_path_body,path_history,
    matching_plans,plan_body)
from transport.schemas import TransportTaskCreateRequest,TransportTaskCancelRequest
from transport.service import (create_transport_task,depart_transport_task,arrive_transport_task,
    cancel_transport_task,list_candidate_shipments)
from shipments.schemas import ShipmentDestinationUpdateRequest
from shipments.service import update_shipment_destination
from network.schemas import RouteUpdateRequest,StationUpdateRequest
from network.service import write_network
import test_v3_network as helpers

@unittest.skipUnless((make_url(os.environ['DATABASE_URL']).database or '').endswith('_test') if os.getenv('DATABASE_URL') else False,'requires *_test database')
class V6PathTests(unittest.TestCase):
    call=helpers.V3NetworkTests.call
    setUp=helpers.V3NetworkTests.setUp
    station=helpers.V3NetworkTests.station
    route=helpers.V3NetworkTests.route
    parcel=helpers.V3NetworkTests.parcel
    event=helpers.V3NetworkTests.event
    task=helpers.V3NetworkTests.task

    def ready(self):
        shipment,_=self.parcel(); self.event(shipment,'PICKUP'); self.event(shipment,'ARRIVE',self.source)
        return shipment

    def path(self,shipment):
        with SessionLocal() as session: return shipment_path_body(session,session.get(Shipment,shipment))

    def plan(self,suffix='FULL'):
        with SessionLocal() as session:
            plan=session.scalar(select(PathPlan).where(PathPlan.code==self.prefix+'_'+suffix))
            return plan_body(session,plan)

    def auto_task(self,shipments,versions=None):
        return self.call(create_transport_task,TransportTaskCreateRequest(
            shipment_ids=shipments,expected_arrival_at=self.clock+timedelta(hours=1),
            expected_path_versions=versions or {str(i):self.path(i)['version'] for i in shipments}),uuid4())

    def replan(self,shipment,routes,anchor=None,version=None,key=None,reason='调整未来路径'):
        current=self.path(shipment)
        return self.call(write_shipment_path,shipment,ShipmentPathUpdateRequest(
            expected_version=current['version'] if version is None else version,
            expected_anchor_station_id=int(anchor or current['anchor_station_id']),
            route_ids=[int(r['id']) for r in routes],reason=reason),key or uuid4())

    def test_reuse_full_path_batch_next_leg_without_selecting_routes(self):
        ids=[self.ready(),self.ready()]
        for shipment in ids:
            path=self.path(shipment)
            self.assertEqual((path['version'],path['status'],path['next_route_code']),(1,'READY',self.first['code']))
            self.assertEqual([leg['route_code'] for leg in path['legs']],[self.first['code'],self.second['code']])
        first=self.auto_task(ids)
        self.assertEqual(first['route_code'],self.first['code'])
        for fn in (depart_transport_task,arrive_transport_task): self.call(fn,int(first['id']),uuid4())
        for shipment in ids: self.assertEqual(self.path(shipment)['next_route_code'],self.second['code'])
        second=self.auto_task(ids)
        self.assertEqual(second['route_code'],self.second['code'])
        for fn in (depart_transport_task,arrive_transport_task): self.call(fn,int(second['id']),uuid4())
        for shipment in ids:
            self.assertEqual(self.path(shipment)['status'],'COMPLETED')
            self.assertTrue(all(leg['state']=='ARRIVED' for leg in self.path(shipment)['legs']))
            self.event(shipment,'START_DELIVERY'); self.event(shipment,'SIGN')

    def test_zero_multiple_options_and_explicit_complete_binding(self):
        plan=self.plan()
        self.call(write_plan,PathPlanUpdateRequest(expected_version=plan['version'],enabled=False),uuid4(),int(plan['id']))
        shipment=self.ready()
        self.assertEqual(self.path(shipment)['status'],'NEEDS_PLANNING')
        candidates,_=self.call(list_candidate_shipments,self.first['code'],1,100)
        self.assertNotIn(shipment,[s.id for s in candidates])
        with self.assertRaises(InvalidTaskShipmentError): self.task(self.first,shipment)
        self.replan(shipment,[self.first,self.second])
        self.assertEqual(self.path(shipment)['status'],'READY')
        self.call(write_plan,PathPlanUpdateRequest(expected_version=2,enabled=True),uuid4(),int(plan['id']))
        extra=self.call(write_plan,PathPlanCreateRequest(code=self.prefix+'_OTHER',name='另一个方案',
            route_ids=[int(self.first['id']),int(self.second['id'])]),uuid4())
        ambiguous=self.ready()
        self.assertEqual(self.path(ambiguous)['version'],0)
        with SessionLocal() as session:
            self.assertEqual(len(matching_plans(session,session.get(Shipment,ambiguous))),2)
        selected=self.call(write_shipment_path,ambiguous,ShipmentPathUpdateRequest(
            expected_version=0,expected_anchor_station_id=int(self.source['id']),plan_id=int(extra['id']),
            expected_plan_version=1,reason='一次选择完整方案'),uuid4())
        self.assertEqual(selected['next_route_code'],self.first['code'])
        self.auto_task([ambiguous])

    def test_plan_edit_disable_preserves_bound_snapshots_and_validates_versions(self):
        shipment=self.ready(); before=self.path(shipment); plan=self.plan()
        via=self.station('VIA'); one=self.route(self.source,via,'S_V'); two=self.route(via,self.target,'V_T')
        request=PathPlanUpdateRequest(expected_version=1,route_ids=[int(one['id']),int(two['id'])])
        key=uuid4(); updated=self.call(write_plan,request,key,int(plan['id']))
        self.assertEqual(updated['version'],2)
        self.assertEqual(self.call(write_plan,request,key,int(plan['id'])),updated)
        with self.assertRaises(NetworkError): self.call(write_plan,request,uuid4(),int(plan['id']))
        self.assertEqual(self.path(shipment),before)
        history,_=self.call(path_history,shipment,1,20)
        self.assertEqual(history[0]['source_plan_version'],1)
        newer=self.ready(); self.assertEqual(self.path(newer)['next_route_code'],one['code'])
        self.call(write_plan,PathPlanUpdateRequest(expected_version=2,enabled=False),uuid4(),int(plan['id']))
        self.assertEqual(self.path(newer)['status'],'READY')
        self.auto_task([newer])  # bound paths own their schedule, plan disabling is not path cancellation
        unplanned=self.ready(); self.assertEqual(self.path(unplanned)['status'],'NEEDS_PLANNING')

    def test_pending_cancel_in_transit_future_edit_preserves_prefix_and_history(self):
        shipment=self.ready(); task=self.auto_task([shipment])
        via=self.station('VIA'); mx=self.route(self.middle,via,'M_V'); xt=self.route(via,self.target,'V_T')
        with self.assertRaises(NetworkError): self.replan(shipment,[self.first,self.second])
        self.call(cancel_transport_task,int(task['id']),TransportTaskCancelRequest(reason='重新安排'),uuid4())
        task=self.auto_task([shipment]); self.call(depart_transport_task,int(task['id']),uuid4())
        before=self.path(shipment); prefix=before['legs'][0]
        key=uuid4(); edited=self.replan(shipment,[mx,xt],key=key,reason='  后续绕行  ')
        self.assertEqual(edited['version'],2)
        self.assertEqual(edited['legs'][0],prefix)
        self.assertEqual(self.replan(shipment,[mx,xt],version=1,key=key,reason='后续绕行'),edited)
        with self.assertRaises(IdempotencyKeyReusedError): self.replan(shipment,[mx,xt],version=1,key=key,reason='不同原因')
        for anchor,routes in [(self.source['id'],[self.first,self.second]),(self.middle['id'],[self.first,self.second])]:
            with self.assertRaises(NetworkError): self.replan(shipment,routes,anchor=anchor)
        with self.assertRaises(NetworkError): self.replan(shipment,[mx,xt],version=1)
        with SessionLocal() as session:
            association=session.scalar(select(TaskShipment).where(TaskShipment.task_id==int(task['id'])))
            self.assertEqual(association.path_leg_id,int(prefix['id']))
            self.assertEqual(session.get(TransportTask,int(task['id'])).route_id,int(self.first['id']))
            self.assertEqual(session.get(Shipment,shipment).stage,'IN_TRANSIT')
        history,total=self.call(path_history,shipment,1,20)
        self.assertEqual(total,2)
        self.assertEqual([r['route_code'] for r in history[-1]['legs']],[self.first['code'],self.second['code']])
        self.call(arrive_transport_task,int(task['id']),uuid4())
        self.assertEqual(self.path(shipment)['next_route_code'],mx['code'])
        replanned=self.replan(shipment,[self.second])
        self.assertEqual(replanned['legs'][0]['id'],prefix['id'])
        self.assertEqual(replanned['legs'][0]['state'],'ARRIVED')
        onward=self.auto_task([shipment])
        for fn in (depart_transport_task,arrive_transport_task): self.call(fn,int(onward['id']),uuid4())
        self.assertEqual(self.path(shipment)['status'],'COMPLETED')
        with self.assertRaises(NetworkError): self.replan(shipment,[],version=2) # old version
        self.event(shipment,'START_DELIVERY')
        with self.assertRaises(NetworkError): self.replan(shipment,[])

    def test_discontinuous_cycles_wrong_destination_disabled_future_and_station_guard(self):
        shipment=self.ready()
        reverse=self.route(self.middle,self.source,'M_S')
        for routes in ([self.second],[self.first],[self.first,reverse,self.first,self.second]):
            with self.assertRaises(NetworkError): self.replan(shipment,routes)
        for routes in ([self.second,self.first],[self.first,reverse]):
            with self.assertRaises(NetworkError):
                self.call(write_plan,PathPlanCreateRequest(code=self.prefix+'_BAD'+uuid4().hex[:4].upper(),name='bad',route_ids=[int(r['id']) for r in routes]),uuid4())
        self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=False),uuid4(),int(self.second['id']))
        self.assertEqual(self.path(shipment)['status'],'BLOCKED')
        candidates,_=self.call(list_candidate_shipments,self.first['code'],1,100)
        self.assertNotIn(shipment,[s.id for s in candidates])
        with self.assertRaises(InvalidTaskShipmentError): self.task(self.first,shipment)
        self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=True),uuid4(),int(self.second['id']))
        via=self.station('FUTURE'); one=self.route(self.source,via,'S_F'); two=self.route(via,self.target,'F_T')
        self.replan(shipment,[one,two])
        for route in (one,two): self.call(write_network,'ROUTE',RouteUpdateRequest(enabled=False),uuid4(),int(route['id']))
        with self.assertRaises(NetworkError):
            self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(via['id']))
        self.replan(shipment,[self.first,self.second])
        self.call(write_network,'STATION',StationUpdateRequest(enabled=False),uuid4(),int(via['id']))

    def test_destination_change_invalidates_only_future_and_rebinds(self):
        shipment=self.ready(); task=self.auto_task([shipment])
        for fn in (depart_transport_task,arrive_transport_task): self.call(fn,int(task['id']),uuid4())
        old=self.path(shipment)
        self.call(update_shipment_destination,shipment,ShipmentDestinationUpdateRequest(
            expected_destination_station_id=int(self.target['id']),destination_station_id=int(self.middle['id']),reason='本地派送'),uuid4())
        current=self.path(shipment)
        self.assertEqual(current['status'],'COMPLETED')
        self.assertEqual(current['legs'],old['legs'][:1])
        self.assertEqual(current['legs'][0]['state'],'ARRIVED')
        self.assertGreater(current['version'],old['version'])
        self.event(shipment,'START_DELIVERY'); self.event(shipment,'SIGN')
        history,total=self.call(path_history,shipment,1,20)
        self.assertEqual(total,2)
        self.assertEqual(len(history[-1]['legs']),2)

    def test_concurrent_replan_task_and_two_replans_have_single_winner(self):
        via=self.station('VIA'); one=self.route(self.source,via,'S_V'); two=self.route(via,self.target,'V_T')
        for race in ('task','path'):
            shipment=self.ready()
            def compete(kind):
                try:
                    if kind=='task': return self.auto_task([shipment],{str(shipment):1})
                    return self.replan(shipment,[one,two] if kind=='path' else [self.first,self.second],version=1)
                except (NetworkError,InvalidTaskShipmentError): return None
            kinds=['task','path'] if race=='task' else ['path','other']
            with ThreadPoolExecutor(max_workers=2) as executor: results=list(executor.map(compete,kinds))
            self.assertEqual(sum(r is not None for r in results),1)

    def test_path_version_failure_rolls_back_rows_and_batch_mixed_next_routes_rejected(self):
        shipment=self.ready(); before=self.path(shipment)
        via=self.station('VIA'); one=self.route(self.source,via,'S_V'); two=self.route(via,self.target,'V_T')
        function='v6_fault_'+uuid4().hex[:8]; key=uuid4()
        with engine.begin() as conn:
            conn.exec_driver_sql(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.shipment_id={shipment} THEN RAISE EXCEPTION 'V6 version failure'; END IF; RETURN NEW; END $$''')
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE INSERT ON shipment_path_versions FOR EACH ROW EXECUTE FUNCTION {function}()')
        try:
            with self.assertRaises(DBAPIError): self.replan(shipment,[one,two],key=key)
            self.assertEqual(self.path(shipment),before)
            self.assertEqual(self.call(path_history,shipment,1,20)[1],1)
            with SessionLocal() as session:
                self.assertIsNone(session.scalar(select(OperationLog).where(OperationLog.idempotency_key==key)))
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON shipment_path_versions')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        self.replan(shipment,[one,two],key=key)
        other=self.ready()
        with self.assertRaises(InvalidTaskShipmentError): self.auto_task([shipment,other])
        with self.assertRaises(InvalidTaskShipmentError): self.task(self.first,shipment)
        for i in (shipment,other): self.assertEqual(self.path(i)['status'],'READY')

    def test_auto_bind_failure_rolls_back_first_arrival_and_can_retry(self):
        from shipments.schemas import ShipmentEventRequest
        from shipments.service import process_shipment_event, get_shipment
        shipment,_=self.parcel(); self.event(shipment,'PICKUP')
        function='v6_auto_fault_'+uuid4().hex[:8]; key=uuid4()
        with engine.begin() as conn:
            conn.exec_driver_sql(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.shipment_id={shipment} THEN RAISE EXCEPTION 'V6 auto-bind failure'; END IF; RETURN NEW; END $$''')
            conn.exec_driver_sql(f'CREATE TRIGGER {function} BEFORE INSERT ON shipment_path_versions FOR EACH ROW EXECUTE FUNCTION {function}()')
        request=ShipmentEventRequest(event_type='ARRIVE',station_id=self.source['id'])
        try:
            with self.assertRaises(DBAPIError): self.call(process_shipment_event,shipment,request,key)
            with SessionLocal() as session:
                parcel,events=get_shipment(session,shipment)
                self.assertEqual((parcel.stage,parcel.last_scanned_station_id,parcel.path_version),('PICKED_UP',None,0))
                self.assertEqual(len(events),2)
                self.assertEqual(list(session.scalars(select(ShipmentPathLeg).where(ShipmentPathLeg.shipment_id==shipment))),[])
                self.assertIsNone(session.scalar(select(OperationLog).where(OperationLog.idempotency_key==key)))
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP TRIGGER {function} ON shipment_path_versions')
                conn.exec_driver_sql(f'DROP FUNCTION {function}()')
        result=self.call(process_shipment_event,shipment,request,key)
        self.assertEqual(result['transport_path']['status'],'READY')
        self.assertEqual(result['path_version'],1)
