import hashlib
import json
from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from errors import IdempotencyKeyReusedError, NetworkError, SimulationClockNotInitializedError
from logistics_types import TaskStatus
from models import OperationLog, Shipment, SimulationSettings, Station, TransportRoute, TransportTask


def station_body(station: Station) -> dict:
    return {"id": str(station.id), **{key: getattr(station, key) for key in
            ("code", "name", "enabled", "allows_first_arrival", "allows_delivery")}}


def route_body(session: Session, route: TransportRoute) -> dict:
    return {"id": str(route.id), "code": route.code, "enabled": route.enabled,
            "delay_monitoring_enabled": route.delay_monitoring_enabled,
            "origin": station_body(session.get(Station, route.origin_station_id)),
            "destination": station_body(session.get(Station, route.destination_station_id))}


def list_stations(session: Session, enabled: bool | None = None) -> list[Station]:
    statement = select(Station).order_by(Station.code)
    if enabled is not None:
        statement = statement.where(Station.enabled == enabled)
    return list(session.scalars(statement))


def list_routes(session: Session, enabled: bool | None = None):
    origin, destination = aliased(Station), aliased(Station)
    statement = (select(TransportRoute, origin, destination)
        .join(origin, TransportRoute.origin_station_id == origin.id)
        .join(destination, TransportRoute.destination_station_id == destination.id)
        .order_by(TransportRoute.code))
    if enabled is not None:
        statement = statement.where(TransportRoute.enabled == enabled)
    return list(session.execute(statement).tuples())


def require_station(session: Session, station_id: int) -> Station:
    station = session.get(Station, station_id)
    if station is None:
        raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "站点不存在", 404)
    return station


def require_enabled_route(session: Session, route: TransportRoute):
    if not route.enabled or not require_station(session, route.origin_station_id).enabled or not require_station(session, route.destination_station_id).enabled:
        raise NetworkError("NETWORK_RESOURCE_DISABLED", "线路或起终点站已停用")


def _station_changes(session: Session, station: Station, changes: dict):
    disabling = changes.get("enabled") is False and station.enabled
    removing_delivery = changes.get("allows_delivery") is False and station.allows_delivery
    if disabling or removing_delivery:
        has_destination = session.scalar(select(Shipment.id).where(
            Shipment.destination_station_id == station.id, Shipment.stage != "SIGNED").limit(1))
        if has_destination:
            raise NetworkError("NETWORK_RESOURCE_IN_USE", "仍有未签收运单以此站为目的站")
    if disabling:
        inventory = session.scalar(select(Shipment.id).where(
            Shipment.stage == "AT_STATION", Shipment.last_scanned_station_id == station.id).limit(1))
        endpoints = or_(TransportRoute.origin_station_id == station.id, TransportRoute.destination_station_id == station.id)
        tasks = session.scalar(select(TransportTask.id).join(TransportRoute).where(
            endpoints, TransportTask.status.in_([TaskStatus.PENDING_DEPARTURE, TaskStatus.IN_TRANSIT])).limit(1))
        routes = session.scalar(select(TransportRoute.id).where(endpoints, TransportRoute.enabled.is_(True)).limit(1))
        if inventory or tasks or routes:
            raise NetworkError("NETWORK_RESOURCE_IN_USE", "站点仍有在站运单、未完成任务或启用线路，请先处理")


def write_network(session: Session, kind: str, request, key: UUID, resource_id: int | None = None) -> dict:
    changes = request.model_dump(exclude_unset=resource_id is not None)
    action = ("UPDATE_" if resource_id is not None else "CREATE_") + kind
    encoded = json.dumps({"action": action, "resource_id": resource_id, "body": changes},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    model = Station if kind == "STATION" else TransportRoute
    with session.begin():
        # This is also acquired by every business write, serializing config changes with admission.
        clock = session.scalar(select(SimulationSettings).where(SimulationSettings.id == 1).with_for_update())
        if clock is None:
            raise SimulationClockNotInitializedError
        replay = session.scalar(select(OperationLog).where(OperationLog.idempotency_key == key))
        if replay:
            if replay.request_hash != digest:
                raise IdempotencyKeyReusedError
            return replay.response_body
        before = None
        if resource_id is None:
            if session.scalar(select(model.id).where(model.code == changes["code"])):
                raise NetworkError("NETWORK_CODE_CONFLICT", "编码已存在")
            obj = model(**changes)
        else:
            obj = session.scalar(select(model).where(model.id == resource_id).with_for_update())
            if obj is None:
                raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "配置记录不存在", 404)
            before = station_body(obj) if kind == "STATION" else route_body(session, obj)
        if kind == "STATION" and resource_id is not None:
            _station_changes(session, obj, changes)
        if kind == "ROUTE":
            origin = changes.get("origin_station_id", getattr(obj, "origin_station_id", None))
            destination = changes.get("destination_station_id", getattr(obj, "destination_station_id", None))
            if origin == destination:
                raise NetworkError("INVALID_NETWORK_CONFIGURATION", "线路起点和终点不能相同")
            source, target = require_station(session, origin), require_station(session, destination)
            if changes.get("enabled", obj.enabled if resource_id else True) and (not source.enabled or not target.enabled):
                raise NetworkError("NETWORK_RESOURCE_DISABLED", "启用线路需要起终点站均启用")
            if resource_id is None and session.scalar(select(TransportRoute.id).where(
                    TransportRoute.origin_station_id == origin, TransportRoute.destination_station_id == destination)):
                raise NetworkError("NETWORK_CODE_CONFLICT", "该起终点线路已存在")
        for field, value in changes.items():
            setattr(obj, field, value)
        session.add(obj)
        session.flush()
        session.refresh(obj)
        body = station_body(obj) if kind == "STATION" else route_body(session, obj)
        session.add(OperationLog(idempotency_key=key, request_hash=digest, action=action,
            resource_type=kind, resource_id=obj.id, before_data=before, after_data=body,
            response_body=body, response_status=201 if resource_id is None else 200,
            occurred_at=clock.current_time))
    return body
