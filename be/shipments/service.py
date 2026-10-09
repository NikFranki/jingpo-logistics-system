import business_time
from regions.addresses import address_body, request_body, resolve_changes, REGION_FIELDS, SNAPSHOT_FIELDS
import hashlib
import json
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from logistics_types import ShipmentStage, TrackingEventType, TaskStatus

from errors import (
    IdempotencyKeyReusedError,
    OrderNotEditableError,
    OrderNotFoundError,
    InvalidShipmentStateError,
    InvalidShipmentDestinationError,
    ShipmentNotFoundError,
    NetworkDataNotInitializedError,
    NetworkError,
)
from models import (
    OperationLog,
    Order,
    Shipment,
    TrackingEvent,
    Station,
    TaskShipment,
    TransportTask,
    TransportRoute,
)

from planning.service import shipment_path_body, auto_bind_path, invalidate_destination_path
from network.coverage import require_matched_destination, match_origin, planned_origin

from shipments.schemas import (
    ShipmentAddressUpdateRequest,
    ShipmentEventRequest,
    ShipmentDestinationUpdateRequest,
)

DESTINATION_EDITABLE_STAGES = frozenset({
    ShipmentStage.PENDING_PICKUP, ShipmentStage.PICKED_UP, ShipmentStage.AT_STATION,
})

SHIPMENT_EVENT_RULES = {
    TrackingEventType.PICKUP: {
        "required_stage": ShipmentStage.PENDING_PICKUP,
        "next_stage": ShipmentStage.PICKED_UP,
        "action": "PICKUP_SHIPMENT",
    },
    TrackingEventType.ARRIVE: {
        "required_stage": ShipmentStage.PICKED_UP,
        "next_stage": ShipmentStage.AT_STATION,
        "action": "ENTER_STATION",
    },
    TrackingEventType.START_DELIVERY: {
        "required_stage": ShipmentStage.AT_STATION,
        "next_stage": ShipmentStage.OUT_FOR_DELIVERY,
        "action": "START_DELIVERY",
    },
    TrackingEventType.SIGN: {
        "required_stage": ShipmentStage.OUT_FOR_DELIVERY,
        "next_stage": ShipmentStage.SIGNED,
        "action": "SIGN_SHIPMENT",
    },
}


def get_shipment(
    session: Session,
    shipment_id: int,
) -> tuple[Shipment, list[TrackingEvent]] | None:
    shipment = session.get(Shipment, shipment_id)

    if shipment is None:
        return None

    events = list(
        session.scalars(
            select(TrackingEvent)
            .where(TrackingEvent.shipment_id == shipment_id)
            .order_by(
                TrackingEvent.occurred_at.desc(),
                TrackingEvent.id.desc(),
            )
        )
    )

    return shipment, events


def build_shipment_response_body(
    session: Session,
    shipment: Shipment,
    events: list[TrackingEvent],
) -> dict:
    active_task = session.execute(
        select(TransportTask, TransportRoute)
        .join(TaskShipment, TaskShipment.task_id == TransportTask.id)
        .join(TransportRoute, TransportTask.route_id == TransportRoute.id)
        .where(
            TaskShipment.shipment_id == shipment.id,
            TaskShipment.association_state == "ACTIVE",
        )
    ).one_or_none()
    task, route = active_task if active_task is not None else (None, None)
    has_arrangements = session.scalar(select(TaskShipment.id).where(
        TaskShipment.shipment_id == shipment.id,
        TaskShipment.association_state.in_(['ACTIVE', 'PLANNED'])).limit(1)) is not None
    station = session.get(Station, shipment.last_scanned_station_id) if shipment.last_scanned_station_id else None
    can_deliver = bool(station and station.enabled and station.allows_delivery
        and station.id == shipment.destination_station_id and active_task is None)
    path = shipment_path_body(session, shipment)
    can_create_task = shipment.scheduling_mode == 'LEGACY' and path['status'] == 'READY' and path['next_route_id'] is not None
    can_update_path = shipment.stage in (ShipmentStage.AT_STATION, ShipmentStage.IN_TRANSIT) and (
        task is None or task.status == TaskStatus.IN_TRANSIT)
    if shipment.scheduling_mode == 'REVIEWED' and shipment.schedule_version:
        can_update_path = False
    from scheduling.service import schedule_body
    planned_start = planned_origin(session, shipment)
    return {
        **address_body(shipment),
        "scheduling_mode": shipment.scheduling_mode,
        "schedule": schedule_body(session, shipment) if shipment.scheduling_mode == "REVIEWED" else None,
        "id": str(shipment.id),
        "shipment_no": shipment.shipment_no,
        "order_id": str(shipment.order_id),
        "sender_address": shipment.sender_address,
        "recipient_address": shipment.recipient_address,
        "region_code": shipment.region_code,
        "stage": shipment.stage,
        "planned_origin_station_id": str(planned_start) if planned_start else None,
        "earliest_handover_at": shipment.earliest_handover_at.isoformat() if shipment.earliest_handover_at else None,
        "latest_delivery_at": shipment.latest_delivery_at.isoformat() if shipment.latest_delivery_at else None,
        "destination_station_id": str(shipment.destination_station_id),
        "last_scanned_station_id": (
            str(shipment.last_scanned_station_id)
            if shipment.last_scanned_station_id is not None
            else None
        ),
        "active_transport_task": (
            {
                "id": str(task.id),
                "route_code": route.code,
                "origin_station_id": str(route.origin_station_id),
                "destination_station_id": str(route.destination_station_id),
                "status": task.status,
            }
            if task is not None else None
        ),
        "created_at": shipment.created_at.isoformat(),
        "updated_at": shipment.updated_at.isoformat(),
        "tracking_events": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "occurred_at": event.occurred_at.isoformat(),
                "station_id": (
                    str(event.station_id)
                    if event.station_id is not None
                    else None
                ),
                "task_id": (
                    str(event.task_id)
                    if event.task_id is not None
                    else None
                ),
            }
            for event in events
        ],
        "path_version": shipment.path_version,
        "transport_path": path,
        "allowed_actions": build_allowed_actions(shipment.stage, can_deliver, can_create_task)
        + [build_destination_action(
            shipment.stage,
            has_arrangements,
            bool(task and task.status == TaskStatus.IN_TRANSIT),
        ),
           {"action": "UPDATE_PATH", "enabled": can_update_path,
            "reason_code": None if can_update_path else "INVALID_PATH_STATE",
            "reason": None if can_update_path else "首次入站后可安排路径，待发车任务需先取消"}],
    }


def build_destination_action(stage: str, occupied: bool, active_in_transit: bool = False) -> dict:
    if stage == ShipmentStage.IN_TRANSIT:
        if active_in_transit:
            return {"action": "UPDATE_DESTINATION", "enabled": True,
                    "reason_code": None, "reason": None}
        return {"action": "UPDATE_DESTINATION", "enabled": False,
                "reason_code": "ACTIVE_TASK_REQUIRED", "reason": "无法确认当前在途任务，暂不能更正目的站"}
    if stage not in DESTINATION_EDITABLE_STAGES:
        code, reason = "INVALID_STAGE", "当前阶段不允许更正目的站"
    elif occupied:
        code, reason = "TASK_OCCUPIED", "运单已被运输任务占用，请先取消待发车任务"
    else:
        code, reason = None, None
    return {"action": "UPDATE_DESTINATION", "enabled": code is None,
            "reason_code": code, "reason": reason}

def build_create_shipment_request_hash(
    order_id: int,
    destination_station_id: int | None,
) -> str:
    content = f"CREATE_SHIPMENT:{order_id}:{destination_station_id if destination_station_id is not None else 'AUTO'}"
    return hashlib.sha256(content.encode()).hexdigest()

def create_shipment(
    session: Session,
    order_id: int,
    idempotency_key: UUID,
    destination_station_id: int | None = None,
    scheduling_mode: str = "LEGACY",
    scheduling_mode_explicit: bool = False,
) -> tuple[dict, int]:
    request_hash = build_create_shipment_request_hash(order_id, destination_station_id)
    if scheduling_mode_explicit:
        request_hash = hashlib.sha256((request_hash + ":" + scheduling_mode).encode()).hexdigest()

    with session.begin():
        clock = business_time.begin_business_write(session)

        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return (
                existing_log.response_body,
                existing_log.response_status,
            )

        order = session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
        )

        if order is None:
            raise OrderNotFoundError

        existing_shipment = session.scalar(
            select(Shipment).where(
                Shipment.order_id == order_id
            )
        )

        if existing_shipment is not None:
            if destination_station_id is not None and existing_shipment.destination_station_id != destination_station_id:
                raise NetworkError("INVALID_NETWORK_CONFIGURATION", "订单已有运单，目的站不能变更")
            result = get_shipment(
                session=session,
                shipment_id=existing_shipment.id,
            )
            shipment, events = result
            response_body = build_shipment_response_body(
                session=session,
                shipment=shipment,
                events=events,
            )

            session.add(
                OperationLog(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="CREATE_SHIPMENT",
                    resource_type="SHIPMENT",
                    resource_id=shipment.id,
                    before_data=None,
                    after_data=None,
                    response_body=response_body,
                    response_status=200,
                    occurred_at=clock,
                )
            )

            return response_body, 200

        if order.status != "PENDING_SHIPMENT":
            raise OrderNotEditableError

        if destination_station_id is None or order.recipient_province_id is not None:
            matched_id = require_matched_destination(session, order)
            if destination_station_id is not None and destination_station_id != matched_id:
                raise NetworkError('DESTINATION_MISMATCH', '所选目的站与订单收件区域匹配的站点不一致，请刷新后重试')
            destination_station_id = matched_id

        destination = session.get(Station, destination_station_id)
        if destination is None:
            raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "目的站不存在", 404)
        if not destination.enabled or not destination.allows_delivery:
            raise NetworkError("INVALID_NETWORK_CONFIGURATION", "目的站必须启用且允许派送")

        origin_result = match_origin(session, order)
        shipment = Shipment(
            planned_origin_station_id=int(origin_result["origin_station"]["id"]) if origin_result["status"] == "MATCHED" else None,
            **{field: getattr(order, field) for field in (*REGION_FIELDS, *SNAPSHOT_FIELDS)},
            earliest_handover_at=order.earliest_handover_at,
            latest_delivery_at=order.latest_delivery_at,
            order_id=order.id,
            scheduling_mode=scheduling_mode,
            destination_station_id=destination_station_id,
            sender_address=order.sender_address,
            recipient_address=order.recipient_address,
        )
        # 锁定订单状态 
        session.add(shipment)
        session.flush()
        session.refresh(shipment)

        order.status = "SHIPMENT_CREATED"
        order.updated_at = datetime.now(timezone.utc)

        operation_log = OperationLog(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action="CREATE_SHIPMENT",
            resource_type="SHIPMENT",
            resource_id=shipment.id,
            before_data=None,
            after_data=None,
            response_body={},
            response_status=201,
            occurred_at=clock,
        )
        # 写操作日志
        session.add(operation_log)
        session.flush()

        event = TrackingEvent(
            shipment_id=shipment.id,
            event_type="SHIPMENT_CREATED",
            occurred_at=clock,
            station_id=None,
            task_id=None,
            operation_id=operation_log.id,
        )
        # 写创建轨迹
        session.add(event)
        session.flush()

        response_body = build_shipment_response_body(
            session=session,
            shipment=shipment,
            events=[event],
        )
        operation_log.after_data = response_body
        operation_log.response_body = response_body

    return response_body, 201

def build_update_address_request_hash(
    shipment_id: int,
    request: ShipmentAddressUpdateRequest,
) -> str:
    content = json.dumps(
        {
            "action": "UPDATE_SHIPMENT_ADDRESS",
            "shipment_id": shipment_id,
            "body": request_body(request),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(content.encode()).hexdigest()

def update_shipment_address(
    session: Session,
    shipment_id: int,
    request: ShipmentAddressUpdateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_update_address_request_hash(
        shipment_id=shipment_id,
        request=request,
    )

    with session.begin():
        clock = business_time.begin_business_write(session)

        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return business_time.normalize_cached_response(existing_log.response_body)

        shipment = session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .with_for_update()
        )

        if shipment is None:
            raise ShipmentNotFoundError

        if shipment.stage != "PENDING_PICKUP":
            raise InvalidShipmentStateError

        changes = resolve_changes(session, request_body(request))
        origin_changed = any(field in changes and changes[field] != getattr(shipment, field)
                             for field in ('sender_province_id', 'sender_city_id', 'sender_district_id'))
        if origin_changed and session.scalar(select(TaskShipment.id).where(
                TaskShipment.shipment_id == shipment.id,
                TaskShipment.association_state.in_(['ACTIVE', 'PLANNED'])).limit(1)):
            raise NetworkError('SHIPMENT_HAS_ARRANGEMENTS', '请先取消未执行的运输安排，再修改寄件区域')
        before_data = {
            field: getattr(shipment, field)
            for field in changes
        }
        if origin_changed:
            before_data['planned_origin_station_id'] = shipment.planned_origin_station_id

        for field, value in changes.items():
            setattr(shipment, field, value)

        if origin_changed:
            origin_result = match_origin(session, shipment)
            shipment.planned_origin_station_id = int(origin_result['origin_station']['id']) if origin_result['status'] == 'MATCHED' else None
            if shipment.schedule_version:
                shipment.schedule_status = 'NEEDS_RECONFIRMATION'
                shipment.schedule_reason = '寄件区域已变化，请重新审核起点和运输计划'
        shipment.updated_at = datetime.now(timezone.utc)
        session.flush()
        session.refresh(shipment)

        result = get_shipment(
            session=session,
            shipment_id=shipment.id,
        )
        shipment, events = result

        after_data = {
            field: getattr(shipment, field)
            for field in changes
        }
        if origin_changed:
            after_data['planned_origin_station_id'] = shipment.planned_origin_station_id
        response_body = build_shipment_response_body(
            session=session,
            shipment=shipment,
            events=events,
        )

        session.add(
            OperationLog(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action="UPDATE_SHIPMENT_ADDRESS",
                resource_type="SHIPMENT",
                resource_id=shipment.id,
                before_data=before_data,
                after_data=after_data,
                response_body=response_body,
                response_status=200,
                occurred_at=clock,
            )
        )

    return response_body

def build_shipment_event_request_hash(
    shipment_id: int,
    request: ShipmentEventRequest,
) -> str:
    content = (
        f"SHIPMENT_EVENT:"
        f"{shipment_id}:"
        f"{request.event_type}:"
        f"{request.station_id or ''}"
    )

    return hashlib.sha256(content.encode()).hexdigest()

def process_shipment_event(
    session: Session,
    shipment_id: int,
    request: ShipmentEventRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_shipment_event_request_hash(
        shipment_id=shipment_id,
        request=request,
    )
    rule = SHIPMENT_EVENT_RULES[request.event_type]

    with session.begin():
        clock = business_time.begin_business_write(session)

        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return business_time.normalize_cached_response(existing_log.response_body)

        shipment = session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .with_for_update()
        )

        if shipment is None:
            raise ShipmentNotFoundError

        arrival_station_id = None
        if request.event_type == TrackingEventType.ARRIVE:
            if request.station_id is not None:
                arrival_station_id = int(request.station_id)
            else:
                first_arrival = session.scalar(select(TrackingEvent).where(
                    TrackingEvent.shipment_id == shipment_id,
                    TrackingEvent.event_type == TrackingEventType.ARRIVE,
                    TrackingEvent.task_id.is_(None)).order_by(TrackingEvent.id).limit(1))
                arrival_station_id = first_arrival.station_id if first_arrival else planned_origin(session, shipment)
            if arrival_station_id is None:
                raise NetworkError('ORIGIN_REQUIRED', '未匹配到计划始发站，请配置揽收接收范围或明确实际入站站点')

        event_filters = [
            TrackingEvent.shipment_id == shipment_id,
            TrackingEvent.event_type == request.event_type,
        ]
        if request.event_type == TrackingEventType.ARRIVE:
            event_filters.extend((
                TrackingEvent.station_id == arrival_station_id,
                TrackingEvent.task_id.is_(None),
            ))
        existing_event = session.scalar(
            select(TrackingEvent).where(*event_filters)
        )

        if existing_event is not None:
            result = get_shipment(session, shipment.id)
            shipment, events = result
            response_body = build_shipment_response_body(
                session=session,
                shipment=shipment,
                events=events,
            )

            session.add(
                OperationLog(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action=rule["action"],
                    resource_type="SHIPMENT",
                    resource_id=shipment.id,
                    before_data=None,
                    after_data=None,
                    response_body=response_body,
                    response_status=200,
                    occurred_at=clock,
                )
            )

            return response_body

        if shipment.stage != rule["required_stage"]:
            raise InvalidShipmentStateError

        order = None

        if request.event_type == TrackingEventType.SIGN:
            order = session.scalar(
                select(Order)
                .where(Order.id == shipment.order_id)
                .with_for_update()
            )

            if order is None or order.status != "SHIPMENT_CREATED":
                raise InvalidShipmentStateError

        station = None

        if request.event_type == TrackingEventType.ARRIVE:
            station = session.scalar(
                select(Station).where(Station.id == arrival_station_id)
            )
            if station is None or not station.enabled or not station.allows_first_arrival:
                raise InvalidShipmentStateError
        elif request.event_type == TrackingEventType.START_DELIVERY:
            delivery_station = session.get(Station, shipment.last_scanned_station_id)
            occupied = session.scalar(select(TaskShipment.id).where(
                TaskShipment.shipment_id == shipment.id, TaskShipment.association_state == "ACTIVE"))
            if (not delivery_station or not delivery_station.enabled or not delivery_station.allows_delivery
                or shipment.last_scanned_station_id != shipment.destination_station_id or occupied):
                raise InvalidShipmentStateError

        previous_stage = shipment.stage
        previous_station_id = shipment.last_scanned_station_id
        previous_order_status = order.status if order is not None else None
        updated_at = datetime.now(timezone.utc)

        shipment.stage = rule["next_stage"]
        shipment.last_scanned_station_id = (
            station.id if station is not None else previous_station_id
        )
        shipment.updated_at = updated_at

        if order is not None:
            order.status = "COMPLETED"
            order.updated_at = updated_at

        before_data = {
            "stage": previous_stage,
            "last_scanned_station_id": previous_station_id,
        }

        if previous_order_status is not None:
            before_data["order_status"] = previous_order_status

        operation_log = OperationLog(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action=rule["action"],
            resource_type="SHIPMENT",
            resource_id=shipment.id,
            before_data=before_data,
            after_data=None,
            response_body={},
            response_status=200,
            occurred_at=clock,
        )

        session.add(operation_log)
        session.flush()

        event = TrackingEvent(
            shipment_id=shipment.id,
            event_type=request.event_type,
            occurred_at=clock,
            station_id=station.id if station is not None else None,
            task_id=None,
            operation_id=operation_log.id,
        )
        session.add(event)
        session.flush()
        if request.event_type == TrackingEventType.ARRIVE:
            if shipment.scheduling_mode == 'REVIEWED' and shipment.schedule_version:
                from scheduling.service import activate_next
                activate_next(session, shipment, clock, operation_log)
            else:
                auto_bind_path(session, shipment, clock)
        session.flush()
        session.refresh(shipment)

        result = get_shipment(session, shipment.id)
        shipment, events = result
        response_body = build_shipment_response_body(
            session=session,
            shipment=shipment,
            events=events,
        )

        operation_log.after_data = {
            "stage": shipment.stage,
            "last_scanned_station_id": shipment.last_scanned_station_id,
        }

        if order is not None:
            operation_log.after_data["order_status"] = order.status

        operation_log.response_body = response_body

    return response_body

def build_allowed_actions(stage: str, can_deliver: bool, can_create_task: bool = False) -> list[dict]:
    rules = [
        ("CREATE_TRANSPORT_TASK", can_create_task,
         "Transport requires an unoccupied shipment at an enabled origin with an available outgoing route"),
        (
            "UPDATE_ADDRESS",
            stage == "PENDING_PICKUP",
            "Address can only be updated before pickup",
        ),
        (
            "PICKUP",
            stage == "PENDING_PICKUP",
            "Pickup requires PENDING_PICKUP stage",
        ),
        (
            "ARRIVE",
            stage == ShipmentStage.PICKED_UP,
            "Entering the first station requires PICKED_UP stage",
        ),
        (
            "START_DELIVERY",
            stage == ShipmentStage.AT_STATION and can_deliver,
            "Delivery requires arrival at the shipment destination station",
        ),
        (
            "SIGN",
            stage == "OUT_FOR_DELIVERY",
            "Signing requires OUT_FOR_DELIVERY stage",
        ),
    ]

    return [
        {
            "action": action,
            "enabled": enabled,
            "reason_code": None if enabled else "INVALID_STAGE",
            "reason": None if enabled else reason,
        }
        for action, enabled, reason in rules
    ]

def list_shipments(
    session: Session,
    page: int,
    page_size: int,
    shipment_no: str | None,
    stage: str | None,
) -> tuple[list[Shipment], int]:
    filters = []

    if shipment_no is not None:
        filters.append(Shipment.shipment_no == shipment_no)

    if stage is not None:
        filters.append(Shipment.stage == stage)

    total = session.scalar(
        select(func.count())
        .select_from(Shipment)
        .where(*filters)
    ) or 0

    shipments = list(
        session.scalars(
            select(Shipment)
            .where(*filters)
            .order_by(Shipment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    return shipments, total


def build_shipment_list_item(shipment: Shipment) -> dict:
    return {
        **address_body(shipment),
        "id": str(shipment.id),
        "shipment_no": shipment.shipment_no,
        "order_id": str(shipment.order_id),
        "sender_address": shipment.sender_address,
        "recipient_address": shipment.recipient_address,
        "earliest_handover_at": shipment.earliest_handover_at,
        "latest_delivery_at": shipment.latest_delivery_at,
        "region_code": shipment.region_code,
        "stage": shipment.stage,
        "destination_station_id": str(shipment.destination_station_id),
        "last_scanned_station_id": (
            str(shipment.last_scanned_station_id)
            if shipment.last_scanned_station_id is not None
            else None
        ),
        "created_at": shipment.created_at,
        "updated_at": shipment.updated_at,
    }


def list_shipment_transport_tasks(session: Session, shipment_id: int, page: int, page_size: int):
    if session.get(Shipment, shipment_id) is None:
        raise ShipmentNotFoundError
    total = session.scalar(select(func.count(TaskShipment.id)).where(
        TaskShipment.shipment_id == shipment_id)) or 0
    rows = session.execute(select(TaskShipment, TransportTask, TransportRoute)
        .join(TransportTask, TaskShipment.task_id == TransportTask.id)
        .join(TransportRoute, TransportTask.route_id == TransportRoute.id)
        .where(TaskShipment.shipment_id == shipment_id).order_by(TaskShipment.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    items = [{"id": str(task.id), "task_no": task.task_no, "route_code": route.code,
        "status": task.status, "origin_station_id": str(route.origin_station_id),
        "destination_station_id": str(route.destination_station_id),
        "cancelled_at": task.cancelled_at, "cancel_reason": task.cancel_reason,
        "released_at": association.released_at,
        "association_state": association.association_state,
        "schedule_version": association.schedule_version,
        "release_reason": association.release_reason} for association, task, route in rows]
    return items, total


def build_update_destination_request_hash(
    shipment_id: int, request: ShipmentDestinationUpdateRequest,
) -> str:
    content = json.dumps({
        "action": "UPDATE_SHIPMENT_DESTINATION", "shipment_id": shipment_id,
        "body": request.model_dump(),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def update_shipment_destination(
    session: Session,
    shipment_id: int,
    request: ShipmentDestinationUpdateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_update_destination_request_hash(shipment_id, request)
    with session.begin():
        # All network, shipment and transport writes take this lock first.
        clock = business_time.begin_business_write(session)
        existing = session.scalar(select(OperationLog).where(
            OperationLog.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError
            return business_time.normalize_cached_response(existing.response_body)
        shipment = session.scalar(select(Shipment).where(
            Shipment.id == shipment_id).with_for_update())
        if shipment is None:
            raise ShipmentNotFoundError
        occupied = session.scalar(select(TaskShipment.id).where(
            TaskShipment.shipment_id == shipment_id,
            TaskShipment.association_state == "ACTIVE").limit(1))
        future_reserved = session.scalar(select(TaskShipment.id).where(TaskShipment.shipment_id==shipment_id,
            TaskShipment.association_state=='PLANNED').limit(1))
        active_pair = session.execute(select(TaskShipment, TransportTask).join(
            TransportTask, TaskShipment.task_id == TransportTask.id).where(
                TaskShipment.shipment_id == shipment_id,
                TaskShipment.association_state == 'ACTIVE').with_for_update()).one_or_none()
        active_in_transit = bool(active_pair and active_pair[1].status == TaskStatus.IN_TRANSIT)
        action = build_destination_action(
            shipment.stage,
            occupied is not None or future_reserved is not None,
            active_in_transit,
        )
        if not action["enabled"]:
            raise InvalidShipmentDestinationError(action["reason"])
        if shipment.destination_station_id != request.expected_destination_station_id:
            raise InvalidShipmentDestinationError("目的站已变化，请刷新详情后重新确认")
        if shipment.destination_station_id == request.destination_station_id:
            raise InvalidShipmentDestinationError("新目的站与当前目的站相同，无需更正")
        destination = session.get(Station, request.destination_station_id)
        if destination is None:
            raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "目的站不存在", 404)
        if not destination.enabled or not destination.allows_delivery:
            raise NetworkError("INVALID_NETWORK_CONFIGURATION", "目的站必须启用且允许派送")
        previous_destination = shipment.destination_station_id
        if shipment.stage == ShipmentStage.IN_TRANSIT:
            from scheduling.service import cancel_empty, members, recompute_task_state, release_entry

            pending_entries = list(session.scalars(select(TaskShipment).where(
                TaskShipment.shipment_id == shipment_id,
                TaskShipment.association_state == 'PLANNED').with_for_update()))
            affected_tasks = {}
            release_reason = "目的站更正：" + request.reason[:494]
            for entry in pending_entries:
                task = session.scalar(select(TransportTask).where(
                    TransportTask.id == entry.task_id).with_for_update())
                release_entry(session, entry, clock, release_reason)
                task.schedule_revision += 1
                affected_tasks[task.id] = task
            for task in affected_tasks.values():
                if not members(session, task.id):
                    cancel_empty(session, task, clock, release_reason)
                else:
                    recompute_task_state(session, task)
        shipment.destination_station_id = request.destination_station_id
        invalidate_destination_path(session, shipment, clock, "目的站更正：" + request.reason[:494])
        if shipment.scheduling_mode == 'REVIEWED':
            shipment.schedule_status = 'NEEDS_RECONFIRMATION'
            shipment.schedule_reason = '目的站已更正，请重新确认运输计划'
        shipment.updated_at = datetime.now(timezone.utc)
        session.flush()
        shipment, events = get_shipment(session, shipment_id)
        body = build_shipment_response_body(session, shipment, events)
        session.add(OperationLog(
            idempotency_key=idempotency_key, request_hash=request_hash,
            action="UPDATE_SHIPMENT_DESTINATION", resource_type="SHIPMENT", resource_id=shipment_id,
            before_data={"destination_station_id": str(previous_destination)},
            after_data={"destination_station_id": str(request.destination_station_id), "reason": request.reason},
            response_body=body, response_status=200, occurred_at=clock,
        ))
    return body


def list_destination_changes(
    session: Session, shipment_id: int, page: int, page_size: int,
) -> tuple[list[dict], int]:
    if session.get(Shipment, shipment_id) is None:
        raise ShipmentNotFoundError
    filters = (
        OperationLog.resource_type == "SHIPMENT",
        OperationLog.resource_id == shipment_id,
        OperationLog.action == "UPDATE_SHIPMENT_DESTINATION",
    )
    total = session.scalar(select(func.count(OperationLog.id)).where(*filters)) or 0
    logs = session.scalars(select(OperationLog).where(*filters).order_by(OperationLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size))
    items = [{
        "id": str(log.id),
        "previous_destination_station_id": log.before_data["destination_station_id"],
        "destination_station_id": log.after_data["destination_station_id"],
        "reason": log.after_data["reason"], "occurred_at": log.occurred_at,
    } for log in logs]
    return items, total
