import business_time
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session, aliased
from logistics_types import ShipmentStage, TrackingEventType, TaskStatus

from errors import (
    NetworkError,
    IdempotencyKeyReusedError,
    InvalidExpectedArrivalError,
    InvalidTaskShipmentError,
    NetworkDataNotInitializedError,
    TransportTaskNotFoundError,
    InvalidTransportTaskStateError,
)
from models import (
    OperationLog,
    Shipment,
    Station,
    TaskShipment,
    TransportRoute,
    TransportTask,
    TrackingEvent,
    ShipmentPathLeg,
)
from transport.schemas import TransportTaskCreateRequest, TransportTaskCancelRequest
from planning.service import auto_bind_path, next_path_leg
from network.service import require_enabled_route

def list_candidate_shipments(
    session: Session,
    route_code: str,
    page: int,
    page_size: int,
) -> tuple[list[Shipment], int]:
    origin = aliased(Station)

    result = session.execute(
        select(TransportRoute, origin)
        .join(
            origin,
            TransportRoute.origin_station_id == origin.id,
        )
        .where(TransportRoute.code == route_code)
    ).one_or_none()

    if result is None:
        raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "线路不存在", 404)

    route, origin_station = result
    require_enabled_route(session, route)
    required_stage = ShipmentStage.AT_STATION

    occupied = (
        select(TaskShipment.id)
        .where(
            TaskShipment.shipment_id == Shipment.id,
            TaskShipment.association_state == "ACTIVE",
        )
        .exists()
    )

    arrived = select(TaskShipment.id).join(TransportTask,TaskShipment.task_id == TransportTask.id).where(
        TaskShipment.path_leg_id == ShipmentPathLeg.id, TransportTask.status == TaskStatus.ARRIVED).exists()
    first_leg = select(ShipmentPathLeg.id).where(
        ShipmentPathLeg.shipment_id == Shipment.id, ShipmentPathLeg.superseded_at.is_(None), ~arrived
        ).order_by(ShipmentPathLeg.position).limit(1).correlate(Shipment).scalar_subquery()
    follows_path = select(ShipmentPathLeg.id).where(
        ShipmentPathLeg.id == first_leg, ShipmentPathLeg.route_id == route.id).exists()
    future_leg, future_route = aliased(ShipmentPathLeg), aliased(TransportRoute)
    future_origin, future_destination = aliased(Station), aliased(Station)
    completed_future = select(TaskShipment.id).join(TransportTask,TaskShipment.task_id == TransportTask.id).where(
        TaskShipment.path_leg_id == future_leg.id, TransportTask.status == TaskStatus.ARRIVED).exists()
    blocked_future = select(future_leg.id).join(future_route,future_leg.route_id == future_route.id).join(
        future_origin,future_route.origin_station_id == future_origin.id).join(
        future_destination,future_route.destination_station_id == future_destination.id).where(
        future_leg.shipment_id == Shipment.id, future_leg.superseded_at.is_(None), ~completed_future,
        or_(future_route.enabled.is_(False),future_origin.enabled.is_(False),future_destination.enabled.is_(False))).exists()
    filters = (
        Shipment.scheduling_mode == 'LEGACY',
        Shipment.stage == required_stage,
        Shipment.last_scanned_station_id == origin_station.id,
        Shipment.destination_station_id != origin_station.id,
        ~occupied,
        follows_path,
        ~blocked_future,
    )

    total = session.scalar(
        select(func.count())
        .select_from(Shipment)
        .where(*filters)
    )

    shipments = list(
        session.scalars(
            select(Shipment)
            .where(*filters)
            .order_by(Shipment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    return shipments, total or 0


def build_candidate_response(shipment: Shipment) -> dict:
    return {
        "id": str(shipment.id),
        "shipment_no": shipment.shipment_no,
        "order_id": str(shipment.order_id),
        "sender_address": shipment.sender_address,
        "recipient_address": shipment.recipient_address,
        "region_code": shipment.region_code,
        "stage": shipment.stage,
        "destination_station_id": str(shipment.destination_station_id),
        "last_scanned_station_id": str(
            shipment.last_scanned_station_id
        ),
        "created_at": shipment.created_at,
        "updated_at": shipment.updated_at,
    }

def build_create_task_request_hash(
    request: TransportTaskCreateRequest,
) -> str:
    payload = {
            "route_code": request.route_code,
            "expected_arrival_at": (
                request.expected_arrival_at
                .astimezone(timezone.utc)
                .isoformat()
            ),
            "shipment_ids": sorted(request.shipment_ids),
        }
    if request.expected_path_versions is not None:
        payload["expected_path_versions"] = request.expected_path_versions
    content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(content.encode()).hexdigest()


def build_task_response(
    task: TransportTask,
    route: TransportRoute,
    origin: Station,
    destination: Station,
    shipments: list[Shipment],
) -> dict:
    body = {
        "id": str(task.id),
        "task_no": task.task_no,
        "scheduling_source": task.scheduling_source,
        "scheduled_trip_id": str(task.scheduled_trip_id) if task.scheduled_trip_id else None,
        "planned_departure_at": task.planned_departure_at.isoformat() if task.planned_departure_at else None,
        "schedule_revision": task.schedule_revision,
        "delay_monitoring_enabled": task.delay_monitoring_enabled,
        "route_code": route.code,
        "origin_station_id": str(origin.id),
        "destination_station_id": str(destination.id),
        "status": task.status,
        "cancelled_at": task.cancelled_at.isoformat() if task.cancelled_at else None,
        "cancel_reason": task.cancel_reason,
        "expected_arrival_at": task.expected_arrival_at.isoformat(),
        "departed_at": (
            task.departed_at.isoformat()
            if task.departed_at is not None
            else None
        ),
        "arrived_at": (
            task.arrived_at.isoformat()
            if task.arrived_at is not None
            else None
        ),
        "created_at": task.created_at.isoformat(),
        "shipments": [
            {
                "id": str(shipment.id),
                "shipment_no": shipment.shipment_no,
                "stage": shipment.stage,
            }
            for shipment in shipments
        ],
    }
    if task.scheduling_source == 'PLAN':
        from sqlalchemy.orm import object_session
        from scheduling.service import task_extra
        body.update(task_extra(object_session(task), task))
    return body

def create_transport_task(
    session: Session,
    request: TransportTaskCreateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_create_task_request_hash(request)

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

        if request.expected_arrival_at <= clock:
            raise InvalidExpectedArrivalError

        shipments = list(session.scalars(select(Shipment).where(
            Shipment.id.in_(request.shipment_ids)).order_by(Shipment.id).with_for_update()))
        if len(shipments) != len(request.shipment_ids):
            raise InvalidTaskShipmentError
        for shipment in shipments:
            if shipment.scheduling_mode != 'LEGACY':
                raise NetworkError('SCHEDULE_CONFIRM_REQUIRED', '请通过运输计划预览并人工确认创建任务')
            auto_bind_path(session, shipment, clock)
        if request.expected_path_versions is not None and any(
            request.expected_path_versions[str(s.id)] != s.path_version for s in shipments):
            raise InvalidTaskShipmentError
        route_code = request.route_code
        if route_code is None:
            legs = [next_path_leg(session, s) for s in shipments]
            if any(l is None for l in legs) or len({l.route_id for l in legs}) != 1:
                raise InvalidTaskShipmentError
            route_code = session.get(TransportRoute, legs[0].route_id).code
        origin = aliased(Station)
        destination = aliased(Station)

        route_result = session.execute(
            select(TransportRoute, origin, destination)
            .join(
                origin,
                TransportRoute.origin_station_id == origin.id,
            )
            .join(
                destination,
                TransportRoute.destination_station_id
                == destination.id,
            )
            .where(TransportRoute.code == route_code)
        ).one_or_none()

        if route_result is None:
            raise NetworkError("NETWORK_RESOURCE_NOT_FOUND", "线路不存在", 404)

        route, origin_station, destination_station = route_result
        require_enabled_route(session, route)

        required_stage = ShipmentStage.AT_STATION

        if any(
            shipment.destination_station_id == origin_station.id
            or shipment.stage != required_stage
            or shipment.last_scanned_station_id
            != origin_station.id
            for shipment in shipments
        ):
            raise InvalidTaskShipmentError

        occupied_count = session.scalar(
            select(func.count())
            .select_from(TaskShipment)
            .where(
                TaskShipment.shipment_id.in_(
                    request.shipment_ids
                ),
                TaskShipment.association_state == "ACTIVE",
            )
        )

        if occupied_count:
            raise InvalidTaskShipmentError

        path_legs = {s.id: next_path_leg(session, s) for s in shipments}
        if any(leg is None or leg.route_id != route.id for leg in path_legs.values()):
            raise InvalidTaskShipmentError
        task = TransportTask(
            route_id=route.id,
            delay_monitoring_enabled=route.delay_monitoring_enabled,
            expected_arrival_at=request.expected_arrival_at,
        )
        session.add(task)
        session.flush()

        session.add_all(
            [
                TaskShipment(
                    task_id=task.id,
                    shipment_id=shipment.id,
                    path_leg_id=path_legs[shipment.id].id,
                )
                for shipment in shipments
            ]
        )
        session.flush()

        response_body = build_task_response(
            task=task,
            route=route,
            origin=origin_station,
            destination=destination_station,
            shipments=shipments,
        )

        session.add(
            OperationLog(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action="CREATE_TRANSPORT_TASK",
                resource_type="TRANSPORT_TASK",
                resource_id=task.id,
                before_data=None,
                after_data={
                    "route_code": route.code,
                    "status": task.status,
                    "shipment_ids": request.shipment_ids,
                },
                response_body=response_body,
                response_status=201,
                occurred_at=clock,
            )
        )

    return response_body

def get_transport_task(
    session: Session,
    task_id: int,
) -> dict:
    origin = aliased(Station)
    destination = aliased(Station)

    server_time = business_time.server_now()

    result = session.execute(
        select(
            TransportTask,
            TransportRoute,
            origin,
            destination,
        )
        .join(
            TransportRoute,
            TransportTask.route_id == TransportRoute.id,
        )
        .join(
            origin,
            TransportRoute.origin_station_id == origin.id,
        )
        .join(
            destination,
            TransportRoute.destination_station_id
            == destination.id,
        )
        .where(TransportTask.id == task_id)
    ).one_or_none()

    if result is None:
        raise TransportTaskNotFoundError

    task, route, origin_station, destination_station = result

    shipments = list(
        session.scalars(
            select(Shipment)
            .join(
                TaskShipment,
                TaskShipment.shipment_id == Shipment.id,
            )
            .where(TaskShipment.task_id == task.id)
            .order_by(Shipment.id)
        )
    )

    body = build_task_detail_response(
        task=task,
        route=route,
        origin=origin_station,
        destination=destination_station,
        shipments=shipments,
        server_time=server_time,
    )
    from scheduling.service import task_extra, departure_problem
    body.update(task_extra(session, task, server_time))
    problem = departure_problem(session, task, server_time)
    if problem:
        for action in body['allowed_actions']:
            if action['action'] == 'DEPART':
                action.update(enabled=False, reason_code=problem[0], reason=problem[1])
    return body

def build_depart_task_request_hash(task_id: int) -> str:
    content = f"DEPART_TRANSPORT_TASK:{task_id}"
    return hashlib.sha256(content.encode()).hexdigest()

def depart_transport_task(
    session: Session,
    task_id: int,
    idempotency_key: UUID,
    expected_schedule_revision: int | None = None,
) -> dict:
    request_hash = build_depart_task_request_hash(task_id)
    if expected_schedule_revision is not None:
        request_hash = hashlib.sha256((request_hash + ':' + str(expected_schedule_revision)).encode()).hexdigest()

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

        task = session.scalar(
            select(TransportTask)
            .where(TransportTask.id == task_id)
            .with_for_update()
        )

        if task is None:
            raise TransportTaskNotFoundError

        if task.status == TaskStatus.IN_TRANSIT:
            response_body = get_transport_task(
                session=session,
                task_id=task.id,
            )

            session.add(
                OperationLog(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="DEPART_TRANSPORT_TASK",
                    resource_type="TRANSPORT_TASK",
                    resource_id=task.id,
                    before_data=None,
                    after_data=None,
                    response_body=response_body,
                    response_status=200,
                    occurred_at=clock,
                )
            )

            return response_body

        if task.scheduling_source == 'PLAN':
            from scheduling.service import departure_problem
            if expected_schedule_revision != task.schedule_revision:
                raise NetworkError('TASK_MEMBERSHIP_CONFLICT', '任务成员已变化，请重新确认发车名单')
            problem = departure_problem(session, task, clock)
            if problem:
                raise NetworkError(*problem)
        if task.status != TaskStatus.PENDING_DEPARTURE:
            raise InvalidTransportTaskStateError

        origin = aliased(Station)
        destination = aliased(Station)

        route_result = session.execute(
            select(TransportRoute, origin, destination)
            .join(
                origin,
                TransportRoute.origin_station_id == origin.id,
            )
            .join(
                destination,
                TransportRoute.destination_station_id
                == destination.id,
            )
            .where(TransportRoute.id == task.route_id)
        ).one_or_none()

        if route_result is None:
            raise NetworkDataNotInitializedError

        route, origin_station, destination_station = route_result

        associations = list(
            session.scalars(
                select(TaskShipment)
                .where(
                    TaskShipment.task_id == task.id,
                    TaskShipment.association_state == "ACTIVE",
                )
                .order_by(TaskShipment.shipment_id)
                .with_for_update()
            )
        )

        if not associations:
            raise InvalidTaskShipmentError

        shipment_ids = [
            association.shipment_id
            for association in associations
        ]

        shipments = list(
            session.scalars(
                select(Shipment)
                .where(Shipment.id.in_(shipment_ids))
                .order_by(Shipment.id)
                .with_for_update()
            )
        )

        if len(shipments) != len(shipment_ids):
            raise InvalidTaskShipmentError

        if any(
            shipment.stage != ShipmentStage.AT_STATION
            or shipment.last_scanned_station_id != origin_station.id
            for shipment in shipments
        ):
            raise InvalidTaskShipmentError

        before_data = {
            "status": task.status,
            "shipment_stages": {
                str(shipment.id): shipment.stage
                for shipment in shipments
            },
        }

        operation_log = OperationLog(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action="DEPART_TRANSPORT_TASK",
            resource_type="TRANSPORT_TASK",
            resource_id=task.id,
            before_data=before_data,
            after_data=None,
            response_body={},
            response_status=200,
            occurred_at=clock,
        )
        session.add(operation_log)
        session.flush()

        task.status = TaskStatus.IN_TRANSIT
        task.departed_at = clock
        updated_at = datetime.now(timezone.utc)

        for shipment in shipments:
            shipment.stage = ShipmentStage.IN_TRANSIT
            shipment.updated_at = updated_at

            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    event_type=TrackingEventType.DEPART,
                    occurred_at=clock,
                    station_id=origin_station.id,
                    task_id=task.id,
                    operation_id=operation_log.id,
                )
            )

        session.flush()

        response_body = build_task_detail_response(
            task=task,
            route=route,
            origin=origin_station,
            destination=destination_station,
            shipments=shipments,
            server_time=clock,
        )

        operation_log.after_data = {
            "status": task.status,
            "shipment_stages": {
                str(shipment.id): shipment.stage
                for shipment in shipments
            },
        }
        operation_log.response_body = response_body

    return response_body

def build_arrive_task_request_hash(task_id: int) -> str:
    content = f"ARRIVE_TRANSPORT_TASK:{task_id}"
    return hashlib.sha256(content.encode()).hexdigest()

def arrive_transport_task(
    session: Session,
    task_id: int,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_arrive_task_request_hash(task_id)

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

        task = session.scalar(
            select(TransportTask)
            .where(TransportTask.id == task_id)
            .with_for_update()
        )

        if task is None:
            raise TransportTaskNotFoundError

        if task.status == TaskStatus.ARRIVED:
            response_body = get_transport_task(
                session=session,
                task_id=task.id,
            )

            session.add(
                OperationLog(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    action="ARRIVE_TRANSPORT_TASK",
                    resource_type="TRANSPORT_TASK",
                    resource_id=task.id,
                    before_data=None,
                    after_data=None,
                    response_body=response_body,
                    response_status=200,
                    occurred_at=clock,
                )
            )

            return response_body

        if task.status != TaskStatus.IN_TRANSIT:
            raise InvalidTransportTaskStateError

        origin = aliased(Station)
        destination = aliased(Station)

        route_result = session.execute(
            select(TransportRoute, origin, destination)
            .join(
                origin,
                TransportRoute.origin_station_id == origin.id,
            )
            .join(
                destination,
                TransportRoute.destination_station_id
                == destination.id,
            )
            .where(TransportRoute.id == task.route_id)
        ).one_or_none()

        if route_result is None:
            raise NetworkDataNotInitializedError

        route, origin_station, destination_station = route_result

        associations = list(
            session.scalars(
                select(TaskShipment)
                .where(
                    TaskShipment.task_id == task.id,
                    TaskShipment.association_state == "ACTIVE",
                )
                .order_by(TaskShipment.shipment_id)
                .with_for_update()
            )
        )

        if not associations:
            raise InvalidTaskShipmentError

        shipment_ids = [
            association.shipment_id
            for association in associations
        ]

        shipments = list(
            session.scalars(
                select(Shipment)
                .where(Shipment.id.in_(shipment_ids))
                .order_by(Shipment.id)
                .with_for_update()
            )
        )

        if len(shipments) != len(shipment_ids):
            raise InvalidTaskShipmentError

        if any(
            shipment.stage != ShipmentStage.IN_TRANSIT
            or shipment.last_scanned_station_id != origin_station.id
            for shipment in shipments
        ):
            raise InvalidTaskShipmentError

        before_data = {
            "status": task.status,
            "shipment_stages": {
                str(shipment.id): shipment.stage
                for shipment in shipments
            },
        }

        operation_log = OperationLog(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action="ARRIVE_TRANSPORT_TASK",
            resource_type="TRANSPORT_TASK",
            resource_id=task.id,
            before_data=before_data,
            after_data=None,
            response_body={},
            response_status=200,
            occurred_at=clock,
        )
        session.add(operation_log)
        session.flush()

        task.status = TaskStatus.ARRIVED
        task.arrived_at = clock
        updated_at = datetime.now(timezone.utc)

        for association in associations:
            association.released_at = clock
            association.association_state = "RELEASED"

        for shipment in shipments:
            shipment.stage = ShipmentStage.AT_STATION
            shipment.last_scanned_station_id = (
                destination_station.id
            )
            shipment.updated_at = updated_at

            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    event_type=TrackingEventType.ARRIVE,
                    occurred_at=clock,
                    station_id=destination_station.id,
                    task_id=task.id,
                    operation_id=operation_log.id,
                )
            )

        session.flush()

        for shipment in shipments:
            if shipment.scheduling_mode == 'REVIEWED':
                from scheduling.service import activate_next
                activate_next(session, shipment, clock, operation_log)
            else:
                auto_bind_path(session, shipment, clock)
        response_body = build_task_detail_response(
            task=task,
            route=route,
            origin=origin_station,
            destination=destination_station,
            shipments=shipments,
            server_time=clock,
        )

        operation_log.after_data = {
            "status": task.status,
            "shipment_stages": {
                str(shipment.id): shipment.stage
                for shipment in shipments
            },
        }
        operation_log.response_body = response_body

    return response_body

def calculate_task_delay(
    task: TransportTask,
    server_time: datetime,
) -> tuple[str, int | None]:
    if task.status == TaskStatus.CANCELLED or not task.delay_monitoring_enabled:
        return "NOT_APPLICABLE", None

    if (
        task.status == TaskStatus.IN_TRANSIT
        and server_time > task.expected_arrival_at
    ):
        minutes = int(
            (
                server_time
                - task.expected_arrival_at
            ).total_seconds()
            // 60
        )
        return "OVERDUE", minutes

    if (
        task.status == TaskStatus.ARRIVED
        and task.arrived_at is not None
        and task.arrived_at > task.expected_arrival_at
    ):
        minutes = int(
            (
                task.arrived_at
                - task.expected_arrival_at
            ).total_seconds()
            // 60
        )
        return "LATE_ARRIVAL", minutes

    return "NONE", 0

def build_task_detail_response(
    task: TransportTask,
    route: TransportRoute,
    origin: Station,
    destination: Station,
    shipments: list[Shipment],
    server_time: datetime,
) -> dict:
    response_body = build_task_response(
        task=task,
        route=route,
        origin=origin,
        destination=destination,
        shipments=shipments,
    )

    delay_status, delay_minutes = calculate_task_delay(
        task=task,
        server_time=server_time,
    )

    response_body.update(
        {
            "server_time": server_time.isoformat(),
            "delay_status": delay_status,
            "delay_minutes": delay_minutes,
            "allowed_actions": build_task_allowed_actions(task.status),
        }
    )

    return response_body

def build_task_allowed_actions(status: str) -> list[dict]:
    rules = [
        ("CANCEL", status in (TaskStatus.PENDING_DEPARTURE,TaskStatus.WAITING_CARGO,TaskStatus.WAITING_PREDECESSOR),
         "Cancellation requires PENDING_DEPARTURE status"),
        (
            "DEPART",
            status == TaskStatus.PENDING_DEPARTURE,
            "Departure requires PENDING_DEPARTURE status",
        ),
        (
            "ARRIVE",
            status == TaskStatus.IN_TRANSIT,
            "Arrival requires IN_TRANSIT status",
        ),
    ]

    return [
        {
            "action": action,
            "enabled": enabled,
            "reason_code": None if enabled else "INVALID_STATUS",
            "reason": None if enabled else reason,
        }
        for action, enabled, reason in rules
    ]

def list_transport_tasks(
    session: Session,
    page: int,
    page_size: int,
    task_no: str | None,
    route_code: str | None,
    status: str | None,
) -> tuple[list[dict], int, datetime]:
    server_time = business_time.server_now()

    filters = []

    if task_no is not None:
        filters.append(TransportTask.task_no == task_no)

    if route_code is not None:
        filters.append(TransportRoute.code == route_code)

    if status is not None:
        filters.append(TransportTask.status == status)

    total = session.scalar(
        select(func.count(TransportTask.id))
        .select_from(TransportTask)
        .join(
            TransportRoute,
            TransportTask.route_id == TransportRoute.id,
        )
        .where(*filters)
    ) or 0

    rows = session.execute(
        select(TransportTask, TransportRoute)
        .join(
            TransportRoute,
            TransportTask.route_id == TransportRoute.id,
        )
        .where(*filters)
        .order_by(TransportTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = []

    for task, route in rows:
        delay_status, delay_minutes = calculate_task_delay(
            task=task,
            server_time=server_time,
        )

        items.append(
            {
                "id": str(task.id),
                "task_no": task.task_no,
                "delay_monitoring_enabled": task.delay_monitoring_enabled,
                "route_code": route.code,
                "status": task.status,
                "cancelled_at": task.cancelled_at,
                "cancel_reason": task.cancel_reason,
                "expected_arrival_at": task.expected_arrival_at,
                "departed_at": task.departed_at,
                "arrived_at": task.arrived_at,
                "created_at": task.created_at,
                "delay_status": delay_status,
                "delay_minutes": delay_minutes,
            }
        )

    from scheduling.service import task_extra
    cache = {}
    for item, (task, _) in zip(items, rows):
        item.update(task_extra(session, task, server_time, cache))
    return items, total, server_time


def cancel_transport_task(
    session: Session,
    task_id: int,
    request: TransportTaskCancelRequest,
    idempotency_key: UUID,
) -> dict:
    content = json.dumps({"action": "CANCEL_TRANSPORT_TASK", "task_id": task_id,
                          "reason": request.reason}, sort_keys=True, separators=(",", ":"))
    if request.expected_schedule_revision is not None or request.cancel_token is not None:
        content = json.dumps({'legacy_content':content, 'expected_schedule_revision':request.expected_schedule_revision,
                             'cancel_token':request.cancel_token}, sort_keys=True)
    request_hash = hashlib.sha256(content.encode()).hexdigest()
    with session.begin():
        clock = business_time.begin_business_write(session)
        existing = session.scalar(select(OperationLog).where(
            OperationLog.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyKeyReusedError
            return business_time.normalize_cached_response(existing.response_body)
        task = session.scalar(select(TransportTask).where(
            TransportTask.id == task_id).with_for_update())
        if task is None:
            raise TransportTaskNotFoundError
        if task.scheduling_source == 'PLAN':
            from scheduling.service import cancel_planned
            return cancel_planned(session, task, request, clock, idempotency_key, request_hash)
        before = {"status": task.status}
        released_ids = []
        if task.status == TaskStatus.CANCELLED:
            if task.cancel_reason != request.reason:
                raise InvalidTransportTaskStateError
        elif task.status == TaskStatus.PENDING_DEPARTURE:
            # Lock all original associations: a partially released pending task is invalid.
            associations = list(session.scalars(select(TaskShipment).where(
                TaskShipment.task_id == task_id).order_by(
                TaskShipment.shipment_id).with_for_update()))
            if not associations or any(a.released_at is not None for a in associations):
                raise InvalidTaskShipmentError
            shipments = list(session.scalars(select(Shipment).where(
                Shipment.id.in_([a.shipment_id for a in associations])).order_by(
                Shipment.id).with_for_update()))
            route = session.get(TransportRoute, task.route_id)
            if route is None:
                raise NetworkDataNotInitializedError
            if len(shipments) != len(associations) or any(
                p.stage != ShipmentStage.AT_STATION or
                p.last_scanned_station_id != route.origin_station_id for p in shipments):
                raise InvalidTaskShipmentError
            before["association_ids"] = [str(a.id) for a in associations]
            task.status = TaskStatus.CANCELLED
            task.cancelled_at = clock
            task.cancel_reason = request.reason
            for association in associations:
                association.released_at = clock
                association.association_state = "RELEASED"
                released_ids.append(str(association.id))
            session.flush()
        else:
            raise InvalidTransportTaskStateError
        response = get_transport_task(session, task_id)
        session.add(OperationLog(
            idempotency_key=idempotency_key, request_hash=request_hash,
            action="CANCEL_TRANSPORT_TASK", resource_type="TRANSPORT_TASK", resource_id=task_id,
            before_data=before, after_data={"status": task.status,
                "cancelled_at": task.cancelled_at.isoformat(), "cancel_reason": task.cancel_reason,
                "released_association_ids": released_ids}, response_body=response,
            response_status=200, occurred_at=clock))
    return response
