from uuid import UUID
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
)
from sqlalchemy.orm import Session

from db import get_db
from errors import (
    IdempotencyKeyReusedError,
    InvalidExpectedArrivalError,
    InvalidTaskShipmentError,
    NetworkDataNotInitializedError,
    SimulationClockNotInitializedError,
    TransportTaskNotFoundError,
    InvalidTransportTaskStateError,
)
from transport.schemas import (
    CandidateShipmentListResponse,
    TransportTaskCreateRequest,
    TransportTaskResponse,
    TransportTaskDetailResponse,
    TransportTaskListResponse,
)
from transport.service import (
    build_candidate_response,
    create_transport_task,
    get_transport_task,
    list_candidate_shipments,
    depart_transport_task,
    arrive_transport_task,
    list_transport_tasks,
)



router = APIRouter(
    prefix="/api/v1/transport-tasks",
    tags=["transport-tasks"],
)

@router.get(
    "",
    response_model=TransportTaskListResponse,
)
def read_transport_tasks(
    session: Annotated[Session, Depends(get_db)],
    task_no: str | None = None,
    route_code: Literal["AB", "BC"] | None = None,
    status: Literal[
        "PENDING_DEPARTURE",
        "IN_TRANSIT",
        "ARRIVED",
    ] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TransportTaskListResponse:
    try:
        items, total, simulation_time = list_transport_tasks(
            session=session,
            page=page,
            page_size=page_size,
            task_no=task_no,
            route_code=route_code,
            status=status,
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )

    return TransportTaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        simulation_time=simulation_time,
    )

@router.get(
    "/candidates",
    response_model=CandidateShipmentListResponse,
)
def read_candidate_shipments(
    route_code: Literal["AB", "BC"],
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CandidateShipmentListResponse:
    try:
        shipments, total = list_candidate_shipments(
            session=session,
            route_code=route_code,
            page=page,
            page_size=page_size,
        )
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )

    return CandidateShipmentListResponse(
        items=[
            build_candidate_response(shipment)
            for shipment in shipments
        ],
        total=total,
        page=page,
        page_size=page_size,
    )

@router.post(
    "/create",
    response_model=TransportTaskResponse,
    status_code=201,
)
def create_task(
    request: TransportTaskCreateRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskResponse:
    try:
        response_body = create_transport_task(
            session=session,
            request=request,
            idempotency_key=idempotency_key,
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )
    except InvalidExpectedArrivalError:
        raise HTTPException(
            status_code=409,
            detail="Expected arrival must be after simulation time",
        )
    except InvalidTaskShipmentError:
        raise HTTPException(
            status_code=409,
            detail="Shipment is invalid or already occupied",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return TransportTaskResponse.model_validate(response_body)

@router.post(
    "/{task_id}/depart",
    response_model=TransportTaskResponse,
)
def depart_task(
    task_id: int,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskResponse:
    try:
        response_body = depart_transport_task(
            session=session,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
    except TransportTaskNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Transport task not found",
        )
    except InvalidTransportTaskStateError:
        raise HTTPException(
            status_code=409,
            detail="Transport task cannot depart",
        )
    except InvalidTaskShipmentError:
        raise HTTPException(
            status_code=409,
            detail="Task shipments are invalid",
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return TransportTaskResponse.model_validate(response_body)

@router.post(
    "/{task_id}/arrive",
    response_model=TransportTaskResponse,
)
def arrive_task(
    task_id: int,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskResponse:
    try:
        response_body = arrive_transport_task(
            session=session,
            task_id=task_id,
            idempotency_key=idempotency_key,
        )
    except TransportTaskNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Transport task not found",
        )
    except InvalidTransportTaskStateError:
        raise HTTPException(
            status_code=409,
            detail="Transport task cannot arrive",
        )
    except InvalidTaskShipmentError:
        raise HTTPException(
            status_code=409,
            detail="Task shipments are invalid",
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return TransportTaskResponse.model_validate(response_body)

@router.get(
    "/{task_id}",
    response_model=TransportTaskDetailResponse,
)
def read_transport_task(
    task_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskDetailResponse:
    try:
        response_body = get_transport_task(
            session=session,
            task_id=task_id,
        )
    except TransportTaskNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Transport task not found",
        )
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
        )

    return TransportTaskDetailResponse.model_validate(
        response_body
    )