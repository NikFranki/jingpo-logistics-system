import hashlib
import json
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from logistics_types import ShipmentStage, TrackingEventType

from errors import (
    IdempotencyKeyReusedError,
    OrderNotEditableError,
    OrderNotFoundError,
    SimulationClockNotInitializedError,
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
    SimulationSettings,
    TrackingEvent,
    Station,
    TaskShipment,
    TransportTask,
    TransportRoute,
)

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
            TaskShipment.released_at.is_(None),
        )
    ).one_or_none()
    task, route = active_task if active_task is not None else (None, None)
    station = session.get(Station, shipment.last_scanned_station_id) if shipment.last_scanned_station_id else None
    can_deliver = bool(station and station.enabled and station.allows_delivery
        and station.id == shipment.destination_station_id and active_task is None)
    outgoing_destination = aliased(Station)
    can_create_task = bool(
        shipment.stage == ShipmentStage.AT_STATION and active_task is None
        and station and station.enabled and station.id != shipment.destination_station_id
        and session.scalar(select(TransportRoute.id).join(outgoing_destination,
            TransportRoute.destination_station_id == outgoing_destination.id).where(
            TransportRoute.origin_station_id == station.id, TransportRoute.enabled.is_(True),
            outgoing_destination.enabled.is_(True)).limit(1))
    )
    return {
        "id": str(shipment.id),
        "shipment_no": shipment.shipment_no,
        "order_id": str(shipment.order_id),
        "sender_address": shipment.sender_address,
        "recipient_address": shipment.recipient_address,
        "region_code": shipment.region_code,
        "stage": shipment.stage,
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
        "allowed_actions": build_allowed_actions(shipment.stage, can_deliver, can_create_task)
        + [build_destination_action(shipment.stage, active_task is not None)],
    }


def build_destination_action(stage: str, occupied: bool) -> dict:
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
    destination_station_id: int,
) -> str:
    content = f"CREATE_SHIPMENT:{order_id}:{destination_station_id}"
    return hashlib.sha256(content.encode()).hexdigest()

def create_shipment(
    session: Session,
    order_id: int,
    idempotency_key: UUID,
    destination_station_id: int,
) -> tuple[dict, int]:
    request_hash = build_create_shipment_request_hash(order_id, destination_station_id)

    with session.begin():
        clock = session.scalar(
            select(SimulationSettings)
            .where(SimulationSettings.id == 1)
            .with_for_update()
        )

        if clock is None:
            raise SimulationClockNotInitializedError

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
            if existing_shipment.destination_station_id != destination_station_id:
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
                    occurred_at=clock.current_time,
                )
            )

            return response_body, 200

        if order.status != "PENDING_SHIPMENT":
            raise OrderNotEditableError

        destination = session.get(Station, destination_station_id)
        if destination is None:
            raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "目的站不存在", 404)
        if not destination.enabled or not destination.allows_delivery:
            raise NetworkError("INVALID_NETWORK_CONFIGURATION", "目的站必须启用且允许派送")

        shipment = Shipment(
            order_id=order.id,
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
            occurred_at=clock.current_time,
        )
        # 写操作日志
        session.add(operation_log)
        session.flush()

        event = TrackingEvent(
            shipment_id=shipment.id,
            event_type="SHIPMENT_CREATED",
            occurred_at=clock.current_time,
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
            "body": request.model_dump(exclude_unset=True),
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
        clock = session.scalar(
            select(SimulationSettings)
            .where(SimulationSettings.id == 1)
            .with_for_update()
        )

        if clock is None:
            raise SimulationClockNotInitializedError

        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return existing_log.response_body

        shipment = session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .with_for_update()
        )

        if shipment is None:
            raise ShipmentNotFoundError

        if shipment.stage != "PENDING_PICKUP":
            raise InvalidShipmentStateError

        changes = request.model_dump(exclude_unset=True)
        before_data = {
            field: getattr(shipment, field)
            for field in changes
        }

        for field, value in changes.items():
            setattr(shipment, field, value)

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
                occurred_at=clock.current_time,
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
        clock = session.scalar(
            select(SimulationSettings)
            .where(SimulationSettings.id == 1)
            .with_for_update()
        )

        if clock is None:
            raise SimulationClockNotInitializedError

        existing_log = session.scalar(
            select(OperationLog).where(
                OperationLog.idempotency_key == idempotency_key
            )
        )

        if existing_log is not None:
            if existing_log.request_hash != request_hash:
                raise IdempotencyKeyReusedError

            return existing_log.response_body

        shipment = session.scalar(
            select(Shipment)
            .where(Shipment.id == shipment_id)
            .with_for_update()
        )

        if shipment is None:
            raise ShipmentNotFoundError

        event_filters = [
            TrackingEvent.shipment_id == shipment_id,
            TrackingEvent.event_type == request.event_type,
        ]
        if request.event_type == TrackingEventType.ARRIVE:
            event_filters.extend((
                TrackingEvent.station_id == int(request.station_id),
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
                    occurred_at=clock.current_time,
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
                select(Station).where(Station.id == int(request.station_id))
            )
            if station is None or not station.enabled or not station.allows_first_arrival:
                raise InvalidShipmentStateError
        elif request.event_type == TrackingEventType.START_DELIVERY:
            delivery_station = session.get(Station, shipment.last_scanned_station_id)
            occupied = session.scalar(select(TaskShipment.id).where(
                TaskShipment.shipment_id == shipment.id, TaskShipment.released_at.is_(None)))
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
            occurred_at=clock.current_time,
        )

        session.add(operation_log)
        session.flush()

        event = TrackingEvent(
            shipment_id=shipment.id,
            event_type=request.event_type,
            occurred_at=clock.current_time,
            station_id=station.id if station is not None else None,
            task_id=None,
            operation_id=operation_log.id,
        )
        session.add(event)
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
        "id": str(shipment.id),
        "shipment_no": shipment.shipment_no,
        "order_id": str(shipment.order_id),
        "sender_address": shipment.sender_address,
        "recipient_address": shipment.recipient_address,
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
        "released_at": association.released_at} for association, task, route in rows]
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
        clock = session.scalar(select(SimulationSettings).where(
            SimulationSettings.id == 1).with_for_update())
        if clock is None:
            raise SimulationClockNotInitializedError
        existing = session.scalar(select(OperationLog).where(
            OperationLog.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError
            return existing.response_body
        shipment = session.scalar(select(Shipment).where(
            Shipment.id == shipment_id).with_for_update())
        if shipment is None:
            raise ShipmentNotFoundError
        occupied = session.scalar(select(TaskShipment.id).where(
            TaskShipment.shipment_id == shipment_id,
            TaskShipment.released_at.is_(None)).limit(1))
        action = build_destination_action(shipment.stage, occupied is not None)
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
        shipment.destination_station_id = request.destination_station_id
        shipment.updated_at = datetime.now(timezone.utc)
        session.flush()
        shipment, events = get_shipment(session, shipment_id)
        body = build_shipment_response_body(session, shipment, events)
        session.add(OperationLog(
            idempotency_key=idempotency_key, request_hash=request_hash,
            action="UPDATE_SHIPMENT_DESTINATION", resource_type="SHIPMENT", resource_id=shipment_id,
            before_data={"destination_station_id": str(previous_destination)},
            after_data={"destination_station_id": str(request.destination_station_id), "reason": request.reason},
            response_body=body, response_status=200, occurred_at=clock.current_time,
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
