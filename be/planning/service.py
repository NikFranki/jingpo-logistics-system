import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, delete, func
from models import (Shipment, Station, TransportRoute, TransportTask, TaskShipment, SimulationSettings,
    OperationLog, PathPlan, PathPlanLeg, ShipmentPathLeg, ShipmentPathVersion)
from logistics_types import ShipmentStage, TaskStatus
from errors import (NetworkError, ShipmentNotFoundError, IdempotencyKeyReusedError,
                    SimulationClockNotInitializedError)
from network.service import require_enabled_route


def conflict(message, code="INVALID_TRANSPORT_PATH"):
    raise NetworkError(code, message)


def clock_and_replay(session, key, digest):
    clock = session.scalar(select(SimulationSettings).where(SimulationSettings.id == 1).with_for_update())
    if clock is None:
        raise SimulationClockNotInitializedError
    log = session.scalar(select(OperationLog).where(OperationLog.idempotency_key == key))
    if log is not None and log.request_hash != digest:
        raise IdempotencyKeyReusedError
    return clock, log


def digest_request(action, resource_id, request):
    content = json.dumps({"action": action, "resource_id": resource_id,
                          "body": request.model_dump(exclude_unset=action == "UPDATE_PATH_PLAN")},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def routes_for_ids(session, route_ids):
    routes = {r.id:r for r in session.scalars(select(TransportRoute).where(TransportRoute.id.in_(route_ids)))}
    if any(i not in routes for i in route_ids):
        raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "路径中的线路不存在", 404)
    return [routes[i] for i in route_ids]


def validate_routes(session, routes, origin=None, destination=None, require_enabled=True):
    if not routes:
        if origin is None or origin != destination:
            conflict("完整路径必须连接当前起点和运单目的站")
        station = session.get(Station, destination)
        if not station or not station.enabled or not station.allows_delivery:
            conflict("目的站必须启用且允许派送")
        return
    start, end = routes[0].origin_station_id, routes[-1].destination_station_id
    if origin is not None and start != origin or destination is not None and end != destination:
        conflict("路径起终点与当前起点或目的站不一致")
    nodes = [start]
    for route in routes:
        if route.origin_station_id != nodes[-1] or route.destination_station_id in nodes:
            conflict("路径线路必须连续，且不能重复经过站点")
        nodes.append(route.destination_station_id)
        if require_enabled:
            require_enabled_route(session, route)
    station = session.get(Station, end)
    if not station or (require_enabled and (not station.enabled or not station.allows_delivery)):
        conflict("路径最终站必须允许派送")


def plan_routes(session, plan):
    return list(session.scalars(select(TransportRoute).join(PathPlanLeg, PathPlanLeg.route_id == TransportRoute.id)
        .where(PathPlanLeg.plan_id == plan.id).order_by(PathPlanLeg.position)))


def plan_body(session, plan):
    routes = plan_routes(session, plan)
    reason = None
    try:
        if not plan.enabled:
            conflict("路径方案已停用")
        validate_routes(session, routes, plan.origin_station_id, plan.destination_station_id)
    except NetworkError as error:
        reason = error.message
    return {"id":str(plan.id), "code":plan.code, "name":plan.name, "enabled":plan.enabled,
        "version":plan.version, "origin_station_id":str(plan.origin_station_id),
        "destination_station_id":str(plan.destination_station_id), "usable":reason is None,
        "reason":reason, "route_ids":[str(r.id) for r in routes]}


def list_plans(session, enabled=None, origin_station_id=None, destination_station_id=None):
    query = select(PathPlan).order_by(PathPlan.id.desc())
    for field, value in ((PathPlan.enabled, enabled),(PathPlan.origin_station_id,origin_station_id),
                         (PathPlan.destination_station_id,destination_station_id)):
        if value is not None:
            query = query.where(field == value)
    return [plan_body(session,p) for p in session.scalars(query)]


def write_plan(session, request, key, plan_id=None):
    action = "CREATE_PATH_PLAN" if plan_id is None else "UPDATE_PATH_PLAN"
    digest = digest_request(action, plan_id, request)
    with session.begin():
        clock, replay = clock_and_replay(session, key, digest)
        if replay is not None:
            return replay.response_body
        before = None
        if plan_id is None:
            if session.scalar(select(PathPlan.id).where(PathPlan.code == request.code)):
                conflict("路径方案编码已存在", "NETWORK_CODE_CONFLICT")
            routes = routes_for_ids(session, request.route_ids)
            validate_routes(session, routes, require_enabled=request.enabled)
            plan = PathPlan(code=request.code,name=request.name,enabled=request.enabled,
                origin_station_id=routes[0].origin_station_id,destination_station_id=routes[-1].destination_station_id)
            session.add(plan); session.flush()
        else:
            plan = session.scalar(select(PathPlan).where(PathPlan.id == plan_id).with_for_update())
            if plan is None:
                raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "路径方案不存在", 404)
            if plan.version != request.expected_version:
                conflict("方案版本已变化，请刷新后重新确认", "PATH_VERSION_CONFLICT")
            before = plan_body(session,plan)
            changes = request.model_dump(exclude_unset=True)
            routes = routes_for_ids(session,request.route_ids) if request.route_ids is not None else plan_routes(session,plan)
            validate_routes(session,routes,plan.origin_station_id,plan.destination_station_id,
                            require_enabled=changes.get('enabled',plan.enabled))
            if request.name is not None: plan.name = request.name
            if request.enabled is not None: plan.enabled = request.enabled
            plan.version += 1
            session.execute(delete(PathPlanLeg).where(PathPlanLeg.plan_id == plan.id))
        session.add_all([PathPlanLeg(plan_id=plan.id,position=i,route_id=r.id) for i,r in enumerate(routes)])
        session.flush()
        body = plan_body(session,plan)
        session.add(OperationLog(idempotency_key=key,request_hash=digest,action=action,
            resource_type="PATH_PLAN",resource_id=plan.id,before_data=before,after_data=body,
            response_body=body,response_status=201 if plan_id is None else 200,occurred_at=clock.current_time))
    return body


def current_legs(session, shipment_id):
    return list(session.scalars(select(ShipmentPathLeg).where(
        ShipmentPathLeg.shipment_id == shipment_id,ShipmentPathLeg.superseded_at.is_(None))
        .order_by(ShipmentPathLeg.position)))


def leg_task(session, leg):
    return session.execute(select(TaskShipment,TransportTask).join(TransportTask,TaskShipment.task_id==TransportTask.id)
        .where(TaskShipment.path_leg_id==leg.id,
               (TaskShipment.released_at.is_(None)) | (TransportTask.status==TaskStatus.ARRIVED))
        .order_by(TaskShipment.id.desc()).limit(1)).one_or_none()


def leg_state(session, leg):
    pair = leg_task(session,leg)
    if pair is None: return "PENDING", None
    task = pair[1]
    return {TaskStatus.PENDING_DEPARTURE:"RESERVED",TaskStatus.IN_TRANSIT:"IN_TRANSIT",
            TaskStatus.ARRIVED:"ARRIVED"}.get(task.status,"PENDING"), task


def active_task(session, shipment):
    return session.execute(select(TaskShipment,TransportTask).join(TransportTask,TaskShipment.task_id==TransportTask.id)
        .where(TaskShipment.shipment_id==shipment.id,TaskShipment.released_at.is_(None))).one_or_none()


def anchor_station(session, shipment):
    active = active_task(session,shipment)
    if active is not None:
        task = active[1]
        if task.status == TaskStatus.IN_TRANSIT:
            return session.get(TransportRoute,task.route_id).destination_station_id
    return shipment.last_scanned_station_id


def shipment_path_body(session, shipment):
    legs = current_legs(session,shipment.id)
    items = []
    next_leg = None
    for leg in legs:
        route = session.get(TransportRoute,leg.route_id)
        state, task = leg_state(session,leg)
        items.append({"id":str(leg.id),"position":leg.position,"route_id":str(route.id),"route_code":route.code,
            "origin_station_id":str(route.origin_station_id),"destination_station_id":str(route.destination_station_id),
            "state":state,"task_id":str(task.id) if task else None})
        if state == "PENDING" and next_leg is None: next_leg = leg
    anchor = anchor_station(session,shipment)
    active = active_task(session,shipment)
    status, code, reason = "READY", None, None
    next_route = session.get(TransportRoute,next_leg.route_id) if next_leg else None
    if shipment.stage in (ShipmentStage.PENDING_PICKUP,ShipmentStage.PICKED_UP):
        status, code, reason = "WAITING_FIRST_ARRIVAL", "FIRST_STATION_UNKNOWN", "首次入站后确定路径"
    elif shipment.last_scanned_station_id == shipment.destination_station_id and active is None:
        status = "COMPLETED"
        next_route = None
    elif active is not None:
        status = "IN_TRANSIT" if active[1].status == TaskStatus.IN_TRANSIT else "RESERVED"
        next_route = None
        if shipment.path_version == 0:
            code, reason = "PATH_NOT_BOUND", "当前任务可继续，后续新任务前需确定完整路径"
        elif status == "IN_TRANSIT":
            try:
                remaining = [session.get(TransportRoute,int(i['route_id'])) for i in items if i['state']=='PENDING']
                validate_routes(session,remaining,anchor,shipment.destination_station_id)
            except NetworkError as error:
                code, reason = "PATH_FUTURE_BLOCKED", "后续路径不可用：" + error.message
    else:
        try:
            remaining = [session.get(TransportRoute,int(i['route_id'])) for i in items if i['state']=='PENDING']
            validate_routes(session,remaining,shipment.last_scanned_station_id,shipment.destination_station_id)
        except NetworkError as error:
            status = "BLOCKED" if remaining else "NEEDS_PLANNING"
            code, reason = (error.code, error.message) if remaining else (
                "PATH_NOT_BOUND", "尚未绑定完整路径，请配置方案或一次性选择完整路径")
            next_route = None
    return {"version":shipment.path_version,"status":status,"anchor_station_id":str(anchor) if anchor else None,
        "destination_station_id":str(shipment.destination_station_id),
        "next_route_id":str(next_route.id) if next_route else None,"next_route_code":next_route.code if next_route else None,
        "reason_code":code,"reason":reason,"legs":items}


def matching_plans(session, shipment):
    anchor = anchor_station(session,shipment)
    if anchor is None: return []
    return [p for p in list_plans(session,True,anchor,shipment.destination_station_id) if p['usable']]


def save_path(session, shipment, routes, clock_time, reason, plan=None):
    old = current_legs(session,shipment.id)
    prefix = []
    for leg in old:
        state, _ = leg_state(session,leg)
        if state in ('ARRIVED','IN_TRANSIT') and len(prefix) == leg.position:
            prefix.append(leg)
        else:
            break
    active = active_task(session,shipment)
    if active is not None and active[1].status == TaskStatus.PENDING_DEPARTURE:
        conflict("待发车任务占用路径，请先取消任务", "PATH_TASK_OCCUPIED")
    # Legacy in-transit tasks have no path leg: attach their unchanged route as the frozen prefix.
    if active is not None and active[1].status == TaskStatus.IN_TRANSIT and active[0].path_leg_id is None:
        if old: conflict("旧任务与当前路径关联不一致")
        leg = ShipmentPathLeg(shipment_id=shipment.id,position=0,route_id=active[1].route_id)
        session.add(leg); session.flush()
        active[0].path_leg_id = leg.id
        prefix.append(leg)
    frozen_ids = {leg.id for leg in prefix}
    for leg in old:
        if leg.id not in frozen_ids:
            if leg_state(session,leg)[0] != 'PENDING': conflict("不能修改已执行或被任务占用的路径段")
            leg.superseded_at = clock_time
    session.flush()
    new = [ShipmentPathLeg(shipment_id=shipment.id,position=len(prefix)+i,route_id=r.id) for i,r in enumerate(routes)]
    session.add_all(new); session.flush()
    shipment.path_version += 1
    shipment.updated_at = datetime.now(timezone.utc)
    snapshot = []
    for leg in prefix+new:
        route = session.get(TransportRoute,leg.route_id)
        snapshot.append({"id":str(leg.id),"position":leg.position,"route_id":str(route.id),"route_code":route.code,
            "origin_station_id":str(route.origin_station_id),"destination_station_id":str(route.destination_station_id)})
    session.add(ShipmentPathVersion(shipment_id=shipment.id,version=shipment.path_version,
        destination_station_id=shipment.destination_station_id,source_plan_id=plan.id if plan else None,
        source_plan_version=plan.version if plan else None,reason=reason,legs=snapshot,occurred_at=clock_time))
    session.flush()


def auto_bind_path(session, shipment, clock_time):
    if shipment.stage != ShipmentStage.AT_STATION or active_task(session,shipment) is not None:
        return
    if shipment.last_scanned_station_id == shipment.destination_station_id:
        return
    legs = current_legs(session,shipment.id)
    if any(leg_state(session,l)[0] == 'PENDING' for l in legs): return
    matches = matching_plans(session,shipment)
    if len(matches) == 1:
        plan = session.get(PathPlan,int(matches[0]['id']))
        save_path(session,shipment,plan_routes(session,plan),clock_time,"自动匹配唯一可用路径方案",plan)


def write_shipment_path(session, shipment_id, request, key):
    digest = digest_request("UPDATE_SHIPMENT_PATH",shipment_id,request)
    with session.begin():
        clock, replay = clock_and_replay(session,key,digest)
        if replay is not None: return replay.response_body
        shipment = session.scalar(select(Shipment).where(Shipment.id==shipment_id).with_for_update())
        if shipment is None: raise ShipmentNotFoundError
        if shipment.stage not in (ShipmentStage.AT_STATION,ShipmentStage.IN_TRANSIT):
            conflict("仅在站或运输中的运单可安排未来路径")
        if request.expected_version != shipment.path_version:
            conflict("运单路径版本已变化，请刷新后重新确认", "PATH_VERSION_CONFLICT")
        anchor = anchor_station(session,shipment)
        if anchor != request.expected_anchor_station_id:
            conflict("当前路径接续站已变化，请刷新后重新确认", "PATH_ANCHOR_CONFLICT")
        plan = None
        if request.plan_id is not None:
            plan = session.get(PathPlan,request.plan_id)
            if plan is None: raise NetworkError("NETWORK_RESOURCE_NOT_FOUND","路径方案不存在",404)
            if not plan.enabled: conflict("路径方案已停用")
            if plan.version != request.expected_plan_version:
                conflict("方案版本已变化，请刷新后重新确认", "PATH_VERSION_CONFLICT")
            routes = plan_routes(session,plan)
        else:
            routes = routes_for_ids(session,request.route_ids)
        validate_routes(session,routes,anchor,shipment.destination_station_id)
        before = shipment_path_body(session,shipment)
        save_path(session,shipment,routes,clock.current_time,request.reason,plan)
        body = shipment_path_body(session,shipment)
        session.add(OperationLog(idempotency_key=key,request_hash=digest,action="UPDATE_SHIPMENT_PATH",
            resource_type="SHIPMENT",resource_id=shipment.id,before_data=before,after_data=body,
            response_body=body,response_status=200,occurred_at=clock.current_time))
    return body


def invalidate_destination_path(session,shipment,clock_time,reason):
    if shipment.path_version:
        save_path(session,shipment,[],clock_time,reason)
    auto_bind_path(session,shipment,clock_time)


def next_path_leg(session,shipment):
    if shipment_path_body(session,shipment)['status'] != 'READY': return None
    return next((leg for leg in current_legs(session,shipment.id) if leg_state(session,leg)[0]=='PENDING'),None)


def path_history(session, shipment_id, page, page_size):
    if session.get(Shipment,shipment_id) is None: raise ShipmentNotFoundError
    rows = session.scalars(select(ShipmentPathVersion).where(ShipmentPathVersion.shipment_id==shipment_id)
        .order_by(ShipmentPathVersion.version.desc()).offset((page-1)*page_size).limit(page_size))
    total = session.scalar(select(func.count(ShipmentPathVersion.id)).where(ShipmentPathVersion.shipment_id==shipment_id)) or 0
    items = [{"version":r.version,"destination_station_id":str(r.destination_station_id),
        "source_plan_id":str(r.source_plan_id) if r.source_plan_id else None,"source_plan_version":r.source_plan_version,
        "reason":r.reason,"occurred_at":r.occurred_at,"legs":r.legs} for r in rows]
    return items,total
