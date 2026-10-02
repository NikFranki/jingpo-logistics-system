from uuid import UUID
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Body,
)
from sqlalchemy.orm import Session

from db import get_db
from network.schemas import NetworkCode
from logistics_types import TaskStatus
from errors import (
    IdempotencyKeyReusedError,
    InvalidExpectedArrivalError,
    InvalidTaskShipmentError,
    NetworkDataNotInitializedError,
    TransportTaskNotFoundError,
    InvalidTransportTaskStateError,
)
from transport.schemas import (
    CandidateShipmentListResponse,
    TransportTaskCreateRequest,
    TransportTaskCancelRequest,
    TransportTaskDepartRequest,
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
    cancel_transport_task,
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
    route_code: NetworkCode | None = None,
    status: TaskStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TransportTaskListResponse:
    items, total, server_time = list_transport_tasks(
        session=session,
        page=page,
        page_size=page_size,
        task_no=task_no,
        route_code=route_code,
        status=status,
    )

    return TransportTaskListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        server_time=server_time,
    )

@router.get(
    "/candidates",
    response_model=CandidateShipmentListResponse,
)
def read_candidate_shipments(
    route_code: NetworkCode,
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
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )
    except InvalidExpectedArrivalError:
        raise HTTPException(
            status_code=409,
            detail="Expected arrival must be after server time",
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
    response_model=TransportTaskDetailResponse,
)
def depart_task(
    task_id: int,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
    request: TransportTaskDepartRequest | None = Body(default=None),
) -> TransportTaskDetailResponse:
    try:
        response_body = depart_transport_task(
            session=session,
            task_id=task_id,
            idempotency_key=idempotency_key,
            expected_schedule_revision=request.expected_schedule_revision if request else None,
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

    return TransportTaskDetailResponse.model_validate(response_body)

@router.post(
    "/{task_id}/arrive",
    response_model=TransportTaskDetailResponse,
)
def arrive_task(
    task_id: int,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskDetailResponse:
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

    return TransportTaskDetailResponse.model_validate(response_body)

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

    return TransportTaskDetailResponse.model_validate(
        response_body
    )

@router.post("/{task_id}/cancel", response_model=TransportTaskDetailResponse)
def cancel_task(
    task_id: int,
    request: TransportTaskCancelRequest,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[Session, Depends(get_db)],
) -> TransportTaskDetailResponse:
    try:
        body = cancel_transport_task(session, task_id, request, idempotency_key)
    except TransportTaskNotFoundError:
        raise HTTPException(404, "Transport task not found")
    except InvalidTransportTaskStateError:
        raise HTTPException(409, "Task cannot be cancelled or cancellation reason conflicts")
    except InvalidTaskShipmentError:
        raise HTTPException(409, "Task shipment associations or locations conflict")
    except IdempotencyKeyReusedError:
        raise HTTPException(409, "Idempotency-Key was reused with different content")
    except NetworkDataNotInitializedError:
        raise HTTPException(503, "Network data is not initialized")
    return TransportTaskDetailResponse.model_validate(body)
