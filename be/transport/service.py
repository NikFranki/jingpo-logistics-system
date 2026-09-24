import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from errors import (
    IdempotencyKeyReusedError,
    InvalidExpectedArrivalError,
    InvalidTaskShipmentError,
    NetworkDataNotInitializedError,
    SimulationClockNotInitializedError,
    TransportTaskNotFoundError,
    InvalidTransportTaskStateError,
)
from models import (
    OperationLog,
    Shipment,
    SimulationSettings,
    Station,
    TaskShipment,
    TransportRoute,
    TransportTask,
    TrackingEvent,
)
from transport.schemas import TransportTaskCreateRequest

DEPARTURE_RULES = {
    "AB": {
        "required_shipment_stage": "AT_A",
        "next_shipment_stage": "IN_TRANSIT_AB",
        "event_type": "DEPART_AB",
    },
    "BC": {
        "required_shipment_stage": "AT_B",
        "next_shipment_stage": "IN_TRANSIT_BC",
        "event_type": "DEPART_BC",
    },
}

ARRIVAL_RULES = {
    "AB": {
        "required_shipment_stage": "IN_TRANSIT_AB",
        "next_shipment_stage": "AT_B",
        "event_type": "ARRIVE_B",
    },
    "BC": {
        "required_shipment_stage": "IN_TRANSIT_BC",
        "next_shipment_stage": "AT_C",
        "event_type": "ARRIVE_C",
    },
}


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
        raise NetworkDataNotInitializedError

    _, origin_station = result
    required_stage = f"AT_{origin_station.code}"

    occupied = (
        select(TaskShipment.id)
        .where(
            TaskShipment.shipment_id == Shipment.id,
            TaskShipment.released_at.is_(None),
        )
        .exists()
    )

    filters = (
        Shipment.stage == required_stage,
        Shipment.last_scanned_station_id == origin_station.id,
        ~occupied,
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
        "last_scanned_station_id": str(
            shipment.last_scanned_station_id
        ),
        "created_at": shipment.created_at,
        "updated_at": shipment.updated_at,
    }

def build_create_task_request_hash(
    request: TransportTaskCreateRequest,
) -> str:
    content = json.dumps(
        {
            "route_code": request.route_code,
            "expected_arrival_at": (
                request.expected_arrival_at
                .astimezone(timezone.utc)
                .isoformat()
            ),
            "shipment_ids": sorted(request.shipment_ids),
        },
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
    return {
        "id": str(task.id),
        "task_no": task.task_no,
        "route_code": route.code,
        "origin_station_id": str(origin.id),
        "destination_station_id": str(destination.id),
        "status": task.status,
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

def create_transport_task(
    session: Session,
    request: TransportTaskCreateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_create_task_request_hash(request)

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

        if request.expected_arrival_at <= clock.current_time:
            raise InvalidExpectedArrivalError

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
            .where(TransportRoute.code == request.route_code)
        ).one_or_none()

        if route_result is None:
            raise NetworkDataNotInitializedError

        route, origin_station, destination_station = route_result

        shipments = list(
            session.scalars(
                select(Shipment)
                .where(Shipment.id.in_(request.shipment_ids))
                .order_by(Shipment.id)
                .with_for_update()
            )
        )

        if len(shipments) != len(request.shipment_ids):
            raise InvalidTaskShipmentError

        required_stage = f"AT_{origin_station.code}"

        if any(
            shipment.stage != required_stage
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
                TaskShipment.released_at.is_(None),
            )
        )

        if occupied_count:
            raise InvalidTaskShipmentError

        task = TransportTask(
            route_id=route.id,
            expected_arrival_at=request.expected_arrival_at,
        )
        session.add(task)
        session.flush()

        session.add_all(
            [
                TaskShipment(
                    task_id=task.id,
                    shipment_id=shipment.id,
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
                occurred_at=clock.current_time,
            )
        )

    return response_body

def get_transport_task(
    session: Session,
    task_id: int,
) -> dict:
    origin = aliased(Station)
    destination = aliased(Station)

    simulation_time = session.scalar(
        select(SimulationSettings.current_time)
        .where(SimulationSettings.id == 1)
    )

    if simulation_time is None:
        raise SimulationClockNotInitializedError

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

    return build_task_detail_response(
        task=task,
        route=route,
        origin=origin_station,
        destination=destination_station,
        shipments=shipments,
        simulation_time=simulation_time,
    )

def build_depart_task_request_hash(task_id: int) -> str:
    content = f"DEPART_TRANSPORT_TASK:{task_id}"
    return hashlib.sha256(content.encode()).hexdigest()

def depart_transport_task(
    session: Session,
    task_id: int,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_depart_task_request_hash(task_id)

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

        task = session.scalar(
            select(TransportTask)
            .where(TransportTask.id == task_id)
            .with_for_update()
        )

        if task is None:
            raise TransportTaskNotFoundError

        if task.status == "IN_TRANSIT":
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
                    occurred_at=clock.current_time,
                )
            )

            return response_body

        if task.status != "PENDING_DEPARTURE":
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
        rule = DEPARTURE_RULES[route.code]

        associations = list(
            session.scalars(
                select(TaskShipment)
                .where(
                    TaskShipment.task_id == task.id,
                    TaskShipment.released_at.is_(None),
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
            shipment.stage
            != rule["required_shipment_stage"]
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
            occurred_at=clock.current_time,
        )
        session.add(operation_log)
        session.flush()

        task.status = "IN_TRANSIT"
        task.departed_at = clock.current_time
        updated_at = datetime.now(timezone.utc)

        for shipment in shipments:
            shipment.stage = rule["next_shipment_stage"]
            shipment.updated_at = updated_at

            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    event_type=rule["event_type"],
                    occurred_at=clock.current_time,
                    station_id=origin_station.id,
                    task_id=task.id,
                    operation_id=operation_log.id,
                )
            )

        session.flush()

        response_body = build_task_response(
            task=task,
            route=route,
            origin=origin_station,
            destination=destination_station,
            shipments=shipments,
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

        task = session.scalar(
            select(TransportTask)
            .where(TransportTask.id == task_id)
            .with_for_update()
        )

        if task is None:
            raise TransportTaskNotFoundError

        if task.status == "ARRIVED":
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
                    occurred_at=clock.current_time,
                )
            )

            return response_body

        if task.status != "IN_TRANSIT":
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
        rule = ARRIVAL_RULES[route.code]

        associations = list(
            session.scalars(
                select(TaskShipment)
                .where(
                    TaskShipment.task_id == task.id,
                    TaskShipment.released_at.is_(None),
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
            shipment.stage
            != rule["required_shipment_stage"]
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
            occurred_at=clock.current_time,
        )
        session.add(operation_log)
        session.flush()

        task.status = "ARRIVED"
        task.arrived_at = clock.current_time
        updated_at = datetime.now(timezone.utc)

        for association in associations:
            association.released_at = clock.current_time

        for shipment in shipments:
            shipment.stage = rule["next_shipment_stage"]
            shipment.last_scanned_station_id = (
                destination_station.id
            )
            shipment.updated_at = updated_at

            session.add(
                TrackingEvent(
                    shipment_id=shipment.id,
                    event_type=rule["event_type"],
                    occurred_at=clock.current_time,
                    station_id=destination_station.id,
                    task_id=task.id,
                    operation_id=operation_log.id,
                )
            )

        session.flush()

        response_body = build_task_response(
            task=task,
            route=route,
            origin=origin_station,
            destination=destination_station,
            shipments=shipments,
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
    route_code: str,
    simulation_time: datetime,
) -> tuple[str, int | None]:
    if route_code != "AB":
        return "NOT_APPLICABLE", None

    if (
        task.status == "IN_TRANSIT"
        and simulation_time > task.expected_arrival_at
    ):
        minutes = int(
            (
                simulation_time
                - task.expected_arrival_at
            ).total_seconds()
            // 60
        )
        return "OVERDUE", minutes

    if (
        task.status == "ARRIVED"
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
    simulation_time: datetime,
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
        route_code=route.code,
        simulation_time=simulation_time,
    )

    response_body.update(
        {
            "simulation_time": simulation_time.isoformat(),
            "delay_status": delay_status,
            "delay_minutes": delay_minutes,
            "allowed_actions": build_task_allowed_actions(task.status),
        }
    )

    return response_body

def build_task_allowed_actions(status: str) -> list[dict]:
    rules = [
        (
            "DEPART",
            status == "PENDING_DEPARTURE",
            "Departure requires PENDING_DEPARTURE status",
        ),
        (
            "ARRIVE",
            status == "IN_TRANSIT",
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
    simulation_time = session.scalar(
        select(SimulationSettings.current_time)
        .where(SimulationSettings.id == 1)
    )

    if simulation_time is None:
        raise SimulationClockNotInitializedError

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
            route_code=route.code,
            simulation_time=simulation_time,
        )

        items.append(
            {
                "id": str(task.id),
                "task_no": task.task_no,
                "route_code": route.code,
                "status": task.status,
                "expected_arrival_at": task.expected_arrival_at,
                "departed_at": task.departed_at,
                "arrived_at": task.arrived_at,
                "created_at": task.created_at,
                "delay_status": delay_status,
                "delay_minutes": delay_minutes,
            }
        )

    return items, total, simulation_time