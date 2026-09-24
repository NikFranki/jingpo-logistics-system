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
    InvalidShipmentStateError,
    ShipmentNotFoundError,
    SimulationClockNotInitializedError,
    NetworkDataNotInitializedError,
)
from shipments.schemas import (
    ShipmentAddressUpdateRequest,
    ShipmentDetailResponse,
    ShipmentEventRequest,
    ShipmentListResponse,
)
from shipments.service import (
    build_shipment_response_body,
    get_shipment,
    update_shipment_address,
    process_shipment_event,
    build_shipment_list_item,
    list_shipments,
)


router = APIRouter(
    prefix="/api/v1/shipments",
    tags=["shipments"],
)

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
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
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
    except SimulationClockNotInitializedError:
        raise HTTPException(
            status_code=503,
            detail="Simulation clock is not initialized",
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
    stage: Literal[
        "PENDING_PICKUP",
        "PICKED_UP",
        "AT_A",
        "IN_TRANSIT_AB",
        "AT_B",
        "IN_TRANSIT_BC",
        "AT_C",
        "OUT_FOR_DELIVERY",
        "SIGNED",
    ] | None = None,
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
            shipment=shipment,
            events=events,
        )
    )
