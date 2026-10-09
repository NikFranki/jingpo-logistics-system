from typing import Annotated, Literal
from uuid import UUID, uuid5, NAMESPACE_URL
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from db import get_db
from models import Order
from regions.addresses import address_body
from logistics_types import ShipmentStage
from shipments.schemas import ShipmentDetailResponse, ShipmentCreateRequest
from shipments.service import create_shipment
from lines.service_schedules import shipment_service_options
from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest
from scheduling.service import preview_schedule, confirm_schedule
import business_time

from errors import (
    IdempotencyKeyReusedError,
    OrderNotEditableError,
    OrderNotFoundError,
    NetworkError,
)
from orders.schemas import (
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
    OrderUpdateRequest,
    OrderDetailResponse,
)
from orders.service import (
    create_order as create_order_service,
    get_order,
    list_orders,
    update_order as update_order_service,
    get_order_detail,
)


router = APIRouter(
    prefix="/api/v1/orders",
    tags=["orders"],
)


def auto_schedule_shipment(session: Session, shipment_id: int, idempotency_key: UUID) -> None:
    from_date = business_time.server_now().date()
    options = shipment_service_options(
        session, shipment_id, from_date, from_date + timedelta(days=29)
    )
    if not options["items"]:
        return

    preview = preview_schedule(
        session,
        shipment_id,
        SchedulePreviewRequest(scheduled_trip_id=int(options["items"][0]["trip_id"])),
    )
    if not preview["can_confirm"]:
        return

    session.rollback()
    confirm_schedule(
        session,
        shipment_id,
        ScheduleConfirmRequest(
            preview_token=preview["preview_token"],
            reason="订单创建后自动安排最近可用班次",
            acknowledged_warning_codes=[item["code"] for item in preview["warnings"]],
        ),
        uuid5(NAMESPACE_URL, f"jingpo:auto-schedule:{idempotency_key}"),
    )


def to_order_response(order: Order) -> OrderResponse:
    return OrderResponse(
        **address_body(order),
        id=str(order.id),
        order_no=order.order_no,
        product_name=order.product_name,
        quantity=order.quantity,
        sender_name=order.sender_name,
        sender_address=order.sender_address,
        recipient_name=order.recipient_name,
        recipient_address=order.recipient_address,
        earliest_handover_at=order.earliest_handover_at,
        latest_delivery_at=order.latest_delivery_at,
        region_code=order.region_code,
        status=order.status,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("", response_model=OrderListResponse)
def read_orders(
    session: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    order_no: str | None = None,
    shipment_no: str | None = None,
    stage: ShipmentStage | None = None,
) -> OrderListResponse:
    orders, total = list_orders(
        session=session,
        page=page,
        page_size=page_size,
        order_no=order_no,
        shipment_no=shipment_no,
        stage=stage,
    )

    return OrderListResponse(
        items=[to_order_response(order) for order in orders],
        total=total,
        page=page,
        page_size=page_size,
    )

@router.post(
    "/create",
    response_model=OrderResponse,
    status_code=201,
)
def create_order(
    request: OrderCreateRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    try:
        response_body = create_order_service(
            session=session,
            request=request,
            idempotency_key=idempotency_key,
        )
        try:
            shipment_body, _ = create_shipment(
                session=session,
                order_id=int(response_body["id"]),
                idempotency_key=uuid5(NAMESPACE_URL, f"jingpo:auto-shipment:{idempotency_key}"),
            )
            response_body["status"] = "SHIPMENT_CREATED"
            try:
                auto_schedule_shipment(
                    session,
                    int(shipment_body["id"]),
                    uuid5(NAMESPACE_URL, f"jingpo:auto-schedule-key:{idempotency_key}"),
                )
            except NetworkError:
                pass
        except NetworkError as error:
            if error.code not in {
                "DESTINATION_ADDRESS_REQUIRED",
                "DESTINATION_NOT_FOUND",
                "DESTINATION_CONFLICT",
            }:
                raise
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return OrderResponse.model_validate(response_body)


@router.patch(
    "/{order_id}",
    response_model=OrderResponse,
)
def update_order(
    order_id: int,
    request: OrderUpdateRequest,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    try:
        response_body = update_order_service(
            session=session,
            order_id=order_id,
            request=request,
            idempotency_key=idempotency_key,
        )
    except OrderNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )
    except OrderNotEditableError:
        raise HTTPException(
            status_code=409,
            detail="Order is no longer editable",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    return OrderResponse.model_validate(response_body)

@router.post(
    "/{order_id}/shipment",
    response_model=ShipmentDetailResponse,
    status_code=201,
)
def create_order_shipment(
    order_id: int,
    request: ShipmentCreateRequest,
    response: Response,
    idempotency_key: Annotated[
        UUID,
        Header(alias="Idempotency-Key"),
    ],
    session: Annotated[Session, Depends(get_db)],
) -> ShipmentDetailResponse:
    try:
        response_body, response_status = create_shipment(
            session=session,
            order_id=order_id,
            idempotency_key=idempotency_key,
            destination_station_id=request.destination_station_id,
        )
    except OrderNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )
    except OrderNotEditableError:
        raise HTTPException(
            status_code=409,
            detail="Order cannot create a shipment",
        )
    except IdempotencyKeyReusedError:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was reused with different content",
        )

    response.status_code = response_status

    return ShipmentDetailResponse.model_validate(
        response_body
    )

@router.get(
    "/{order_id}",
    response_model=OrderDetailResponse,
)
def read_order(
    order_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> OrderDetailResponse:
    result = get_order_detail(
        session=session,
        order_id=order_id,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    order, shipment = result
    order_data = to_order_response(order).model_dump()

    shipment_data = None

    if shipment is not None:
        shipment_data = {
            "id": str(shipment.id),
            "shipment_no": shipment.shipment_no,
            "stage": shipment.stage,
        }

    return OrderDetailResponse(
        **order_data,
        shipment=shipment_data,
    )
