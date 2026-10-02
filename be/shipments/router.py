from uuid import UUID
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
)
from sqlalchemy.orm import Session

from db import get_db
from logistics_types import ShipmentStage
from transport.schemas import ShipmentTaskHistoryResponse

from errors import (
    IdempotencyKeyReusedError,
    InvalidShipmentStateError,
    InvalidShipmentDestinationError,
    ShipmentNotFoundError,
    NetworkDataNotInitializedError,
)
from shipments.schemas import (
    ShipmentAddressUpdateRequest,
    ShipmentDetailResponse,
    ShipmentEventRequest,
    ShipmentListResponse,
    ShipmentDestinationUpdateRequest,
    DestinationChangeListResponse,
)
from shipments.service import (
    build_shipment_response_body,
    get_shipment,
    update_shipment_address,
    process_shipment_event,
    build_shipment_list_item,
    list_shipments,
    list_shipment_transport_tasks,
    update_shipment_destination,
    list_destination_changes,
)


router = APIRouter(
    prefix="/api/v1/shipments",
    tags=["shipments"],
)


@router.patch("/{shipment_id}/destination", response_model=ShipmentDetailResponse)
def update_destination(
    shipment_id: int,
    request: ShipmentDestinationUpdateRequest,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    session: Annotated[Session, Depends(get_db)],
) -> ShipmentDetailResponse:
    try:
        body = update_shipment_destination(session, shipment_id, request, idempotency_key)
    except ShipmentNotFoundError:
        raise HTTPException(404, "Shipment not found")
    except InvalidShipmentDestinationError as error:
        raise HTTPException(409, str(error))
    except IdempotencyKeyReusedError:
        raise HTTPException(409, "Idempotency-Key was reused with different content")
    return ShipmentDetailResponse.model_validate(body)


@router.get("/{shipment_id}/destination-changes", response_model=DestinationChangeListResponse)
def read_destination_changes(
    shipment_id: int,
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DestinationChangeListResponse:
    try:
        items, total = list_destination_changes(session, shipment_id, page, page_size)
    except ShipmentNotFoundError:
        raise HTTPException(404, "Shipment not found")
    return DestinationChangeListResponse(items=items, total=total, page=page, page_size=page_size)

@router.patch(
    "/{shipment_id}/address",
    response_model=ShipmentDetailResponse,
)
def update_address(
    shipment_id: int,
    request: ShipmentAddressUpdateRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> ShipmentDetailResponse:
    try:
        response_body = update_shipment_address(
            session=session,
            shipment_id=shipment_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    except ShipmentNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Shipment not found",
        )
    except InvalidShipmentStateError:
        raise HTTPException(
            status_code=409,
            detail="Shipment address is no longer editable",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return ShipmentDetailResponse.model_validate(
        response_body
    )

@router.post(
    "/{shipment_id}/events",
    response_model=ShipmentDetailResponse,
)
def create_shipment_event(
    shipment_id: int,
    request: ShipmentEventRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> ShipmentDetailResponse:
    try:
        response_body = process_shipment_event(
            session=session,
            shipment_id=shipment_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    except NetworkDataNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Network data is not initialized",
        )
    except ShipmentNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Shipment not found",
        )
    except InvalidShipmentStateError:
        raise HTTPException(
            status_code=409,
            detail="Shipment event is not allowed in the current stage",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return ShipmentDetailResponse.model_validate(
        response_body
    )

@router.get(
    "",
    response_model=ShipmentListResponse,
)
def read_shipments(
    session: Annotated[Session, Depends(get_db)],
    shipment_no: str | None = None,
    stage: ShipmentStage | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ShipmentListResponse:
    shipments, total = list_shipments(
        session=session,
        page=page,
        page_size=page_size,
        shipment_no=shipment_no,
        stage=stage,
    )

    return ShipmentListResponse(
        items=[
            build_shipment_list_item(shipment)
            for shipment in shipments
        ],
        total=total,
        page=page,
        page_size=page_size,
    )

@router.get(
    "/{shipment_id}",
    response_model=ShipmentDetailResponse,
)
def read_shipment(
    shipment_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> ShipmentDetailResponse:
    result = get_shipment(
        session=session,
        shipment_id=shipment_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Shipment not found",
        )

    shipment, events = result

    return ShipmentDetailResponse.model_validate(
        build_shipment_response_body(
            session=session,
            shipment=shipment,
            events=events,
        )
    )


@router.get("/{shipment_id}/transport-tasks", response_model=ShipmentTaskHistoryResponse)
def read_shipment_transport_tasks(
    shipment_id: int,
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ShipmentTaskHistoryResponse:
    try:
        items, total = list_shipment_transport_tasks(session, shipment_id, page, page_size)
    except ShipmentNotFoundError:
        raise HTTPException(404, "Shipment not found")
    return ShipmentTaskHistoryResponse(items=items, total=total, page=page, page_size=page_size)
