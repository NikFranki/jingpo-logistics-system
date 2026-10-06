import business_time
from regions.addresses import address_body, request_body, resolve_changes
import hashlib
import json
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    OperationLog, Order, Shipment,
)

from errors import (
    IdempotencyKeyReusedError,
    NetworkError,
    OrderNotEditableError,
    OrderNotFoundError,
    )
from orders.schemas import OrderCreateRequest, OrderUpdateRequest


def list_orders(
    session: Session,
    page: int,
    page_size: int,
    order_no: str | None,
    shipment_no: str | None,
    stage: str | None,
) -> tuple[list[Order], int]:
    filters = []

    if order_no is not None:
        filters.append(Order.order_no == order_no)

    if shipment_no is not None:
        filters.append(Shipment.shipment_no == shipment_no)

    if stage is not None:
        filters.append(Shipment.stage == stage)

    total = session.scalar(
        select(func.count(Order.id))
        .select_from(Order)
        .outerjoin(
            Shipment,
            Shipment.order_id == Order.id,
        )
        .where(*filters)
    ) or 0

    orders = list(
        session.scalars(
            select(Order)
            .outerjoin(
                Shipment,
                Shipment.order_id == Order.id,
            )
            .where(*filters)
            .order_by(Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    return orders, total

def get_order_detail(
    session: Session,
    order_id: int,
) -> tuple[Order, Shipment | None] | None:
    return session.execute(
        select(Order, Shipment)
        .outerjoin(
            Shipment,
            Shipment.order_id == Order.id,
        )
        .where(Order.id == order_id)
    ).one_or_none()


def get_order(
    session: Session,
    order_id: int,
) -> Order | None:
    return session.get(Order, order_id)

def build_create_order_request_hash(
    request: OrderCreateRequest,
) -> str:
    content = json.dumps(
        {
            "action": "CREATE_ORDER",
            "body": request_body(request, create=True),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    )

    return hashlib.sha256(content.encode()).hexdigest()
    

def create_order(
    session: Session,
    request: OrderCreateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_create_order_request_hash(request)

    # 正常结束后才真正提交
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

        order = Order(**resolve_changes(session, request_body(request, create=True)))
        session.add(order)
        # 把 INSERT 发给数据库，但事务还没提交
        session.flush()
        session.refresh(order)

        response_body = build_order_response_body(order)

        session.add(
            OperationLog(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action="CREATE_ORDER",
                resource_type="ORDER",
                resource_id=order.id,
                before_data=None,
                after_data=response_body,
                response_body=response_body,
                response_status=201,
                occurred_at=clock,
            )
        )

    return response_body

def build_order_response_body(order: Order) -> dict:
    return {
        **address_body(order),
        "id": str(order.id),
        "order_no": order.order_no,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "sender_name": order.sender_name,
        "sender_address": order.sender_address,
        "recipient_name": order.recipient_name,
        "recipient_address": order.recipient_address,
        "earliest_handover_at": order.earliest_handover_at.isoformat() if order.earliest_handover_at else None,
        "latest_delivery_at": order.latest_delivery_at.isoformat() if order.latest_delivery_at else None,
        "region_code": order.region_code,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
    }

def build_update_order_request_hash(
    order_id: int,
    request: OrderUpdateRequest,
) -> str:
    content = json.dumps(
        {
            "action": "UPDATE_ORDER",
            "order_id": order_id,
            "body": request_body(request),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    )

    return hashlib.sha256(content.encode()).hexdigest()

def update_order(
    session: Session,
    order_id: int,
    request: OrderUpdateRequest,
    idempotency_key: UUID,
) -> dict:
    request_hash = build_update_order_request_hash(
        order_id=order_id,
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

        order = session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
        )

        if order is None:
            raise OrderNotFoundError

        if order.status != "PENDING_SHIPMENT":
            raise OrderNotEditableError

        changes = resolve_changes(session, request_body(request))
        earliest_handover_at = changes.get("earliest_handover_at", order.earliest_handover_at)
        latest_delivery_at = changes.get("latest_delivery_at", order.latest_delivery_at)
        if earliest_handover_at and latest_delivery_at and earliest_handover_at > latest_delivery_at:
            raise NetworkError("INVALID_DELIVERY_WINDOW", "最早可交运时间不能晚于最晚送达时间", 422)
        before_data = {
            field: getattr(order, field).isoformat() if isinstance(getattr(order, field), datetime) else getattr(order, field)
            for field in changes
        }

        for field, value in changes.items():
            setattr(order, field, value)

        order.updated_at = datetime.now(timezone.utc)
        session.flush()
        session.refresh(order)

        after_data = {
            field: getattr(order, field).isoformat() if isinstance(getattr(order, field), datetime) else getattr(order, field)
            for field in changes
        }
        response_body = build_order_response_body(order)

        session.add(
            OperationLog(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                action="UPDATE_ORDER",
                resource_type="ORDER",
                resource_id=order.id,
                before_data=before_data,
                after_data=after_data,
                response_body=response_body,
                response_status=200,
                occurred_at=clock,
            )
        )

    return response_body
