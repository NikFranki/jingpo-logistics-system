import business_time
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from errors import IdempotencyKeyReusedError, NetworkError
from logistics_types import TaskStatus
from models import (OperationLog, Shipment, ShipmentScheduleLeg, Station, TaskShipment,
                    TransportRoute, TransportTask, TransportLine, TransportLineLeg)
from network.service import require_enabled_route


def conflict(message, code="INVALID_TRANSPORT_SCHEDULE"):
    raise NetworkError(code, message)


def time_and_replay(session, key, digest):
    clock = business_time.begin_business_write(session)
    log = session.scalar(select(OperationLog).where(OperationLog.idempotency_key == key))
    if log is not None and log.request_hash != digest:
        raise IdempotencyKeyReusedError
    return clock, log


def digest_request(action, resource_id, request):
    body = request.model_dump(exclude_unset=True)
    content = json.dumps({"action": action, "resource_id": resource_id, "body": body},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def routes_for_ids(session, route_ids):
    routes = {route.id: route for route in session.scalars(
        select(TransportRoute).where(TransportRoute.id.in_(route_ids)))}
    if any(route_id not in routes for route_id in route_ids):
        raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "线路中的运输区间不存在", 404)
    return [routes[route_id] for route_id in route_ids]


def validate_routes(session, routes, origin=None, destination=None, require_enabled=True):
    if not routes:
        if origin is None or origin != destination:
            conflict("运输线路必须连接计划起点和运单目的站")
        station = session.get(Station, destination)
        if not station or not station.enabled or not station.allows_delivery:
            conflict("目的站必须启用且允许派送")
        return
    start, end = routes[0].origin_station_id, routes[-1].destination_station_id
    if (origin is not None and start != origin) or (destination is not None and end != destination):
        conflict("运输线路起终点与计划起点或目的站不一致")
    nodes = [start]
    for route in routes:
        if route.origin_station_id != nodes[-1] or route.destination_station_id in nodes:
            conflict("运输线路必须连续，且不能重复经过站点")
        nodes.append(route.destination_station_id)
        if require_enabled:
            require_enabled_route(session, route)
    station = session.get(Station, end)
    if not station or (require_enabled and (not station.enabled or not station.allows_delivery)):
        conflict("运输线路最终站必须允许派送")


def line_routes(session, line):
    return list(session.scalars(select(TransportRoute).join(
        TransportLineLeg, TransportLineLeg.route_id == TransportRoute.id).where(
        TransportLineLeg.line_id == line.id).order_by(TransportLineLeg.position)))


def line_summary(session, line):
    routes = line_routes(session, line)
    reason = None
    try:
        if not line.enabled:
            conflict("运输线路已停用")
        validate_routes(session, routes, line.origin_station_id, line.destination_station_id)
    except NetworkError as error:
        reason = error.message
    entries = session.scalars(select(TransportLineLeg).where(
        TransportLineLeg.line_id == line.id).order_by(TransportLineLeg.position))
    overrides = [{"station_id": routes[leg.position].origin_station_id,
                  "minutes": leg.origin_transfer_override_minutes}
                 for leg in entries if leg.origin_transfer_override_minutes is not None]
    return {"id": str(line.id), "code": line.code, "name": line.name, "enabled": line.enabled,
            "version": line.version, "origin_station_id": str(line.origin_station_id),
            "destination_station_id": str(line.destination_station_id), "usable": reason is None,
            "reason": reason,
            "transfer_overrides": overrides}


def current_schedule_legs(session, shipment_id):
    return list(session.scalars(select(ShipmentScheduleLeg).where(
        ShipmentScheduleLeg.shipment_id == shipment_id,
        ShipmentScheduleLeg.superseded_at.is_(None)).order_by(ShipmentScheduleLeg.position)))


def leg_task(session, leg):
    return session.execute(select(TaskShipment, TransportTask).join(
        TransportTask, TaskShipment.task_id == TransportTask.id).where(
        TaskShipment.schedule_leg_id == leg.id,
        (TaskShipment.released_at.is_(None)) | (TransportTask.status == TaskStatus.ARRIVED)
    ).order_by(TaskShipment.id.desc()).limit(1)).one_or_none()


def leg_state(session, leg):
    pair = leg_task(session, leg)
    if pair is None:
        return "PENDING", None
    association, task = pair
    if association.association_state == "PLANNED":
        return "PLANNED", task
    return {TaskStatus.WAITING_PREDECESSOR: "RESERVED", TaskStatus.WAITING_CARGO: "RESERVED",
            TaskStatus.PENDING_DEPARTURE: "RESERVED", TaskStatus.IN_TRANSIT: "IN_TRANSIT",
            TaskStatus.ARRIVED: "ARRIVED"}.get(task.status, "PENDING"), task


def active_task(session, shipment):
    return session.execute(select(TaskShipment, TransportTask).join(
        TransportTask, TaskShipment.task_id == TransportTask.id).where(
        TaskShipment.shipment_id == shipment.id,
        TaskShipment.association_state == "ACTIVE")).one_or_none()


def anchor_station(session, shipment):
    active = active_task(session, shipment)
    if active is not None and active[1].status == TaskStatus.IN_TRANSIT:
        return session.get(TransportRoute, active[1].route_id).destination_station_id
    return shipment.last_scanned_station_id


def replace_schedule_legs(session, shipment, routes, occurred_at):
    old = current_schedule_legs(session, shipment.id)
    prefix = []
    for leg in old:
        state, _ = leg_state(session, leg)
        if state in ("ARRIVED", "IN_TRANSIT") and len(prefix) == leg.position:
            prefix.append(leg)
        else:
            break
    active = active_task(session, shipment)
    if active is not None and active[1].status == TaskStatus.PENDING_DEPARTURE:
        conflict("待发车任务占用运输计划，请先取消任务", "SCHEDULE_TASK_OCCUPIED")
    frozen_ids = {leg.id for leg in prefix}
    for leg in old:
        if leg.id not in frozen_ids:
            if leg_state(session, leg)[0] != "PENDING":
                conflict("不能修改已执行或被任务占用的计划段")
            leg.superseded_at = occurred_at
    session.flush()
    additions = [ShipmentScheduleLeg(shipment_id=shipment.id, position=len(prefix) + offset,
                                     route_id=route.id) for offset, route in enumerate(routes)]
    session.add_all(additions)
    shipment.updated_at = datetime.now(timezone.utc)
    session.flush()


def invalidate_future_schedule_legs(session, shipment, occurred_at):
    for leg in current_schedule_legs(session, shipment.id):
        state, _ = leg_state(session, leg)
        if state not in ("ARRIVED", "IN_TRANSIT"):
            leg.superseded_at = occurred_at


