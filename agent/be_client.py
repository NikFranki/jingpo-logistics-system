import asyncio

import httpx
from time import monotonic
from pydantic import ValidationError
from responses import validate_response
from tracing import trace

from state import TurnContext


class BEClientError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


async def _get(
    base_url: str,
    path: str,
    context: TurnContext,
    params: dict | None = None,
    *,
    schema: str,
) -> dict:
    started = monotonic()
    context.reserve_http_call()
    timeout = context.remaining_timeout(10)

    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    path, params=params
                )
    except (TimeoutError, httpx.TimeoutException) as exc:
        trace(context, "http", started, operation=schema, status="TIMEOUT", http_calls=context.http_calls)
        raise BEClientError("TIMEOUT", "后端请求超时") from exc
    except httpx.RequestError as exc:
        trace(context, "http", started, operation=schema, status="NETWORK_ERROR", http_calls=context.http_calls)
        raise BEClientError("NETWORK_ERROR", "无法连接后端") from exc

    request_id = response.headers.get("X-Request-ID")

    trace(context, "http", started, operation=schema, status=response.status_code, request_id=request_id, http_calls=context.http_calls)

    if response.status_code != 200:
        message = (
            "查询对象不存在"
            if response.status_code == 404
            else f"后端请求失败，状态码 {response.status_code}"
        )
        raise BEClientError(
            f"HTTP_{response.status_code}",
            message,
            request_id,
        )

    try:
        data = validate_response(response.json(), schema)
    except (ValueError, ValidationError, TypeError):
        trace(context, "http_validation", started, operation=schema, status="INVALID_RESPONSE", request_id=request_id)
        raise BEClientError("INVALID_RESPONSE", "后端响应格式异常，暂时无法确认查询结果", request_id) from None

    return {
        "data": data,
        "request_id": request_id,
    }

async def get_shipment(base_url: str, shipment_id: int, context: TurnContext) -> dict:
    if shipment_id <= 0:
        raise ValueError("运单 ID 必须大于 0")
    return await _get(base_url, f"/api/v1/shipments/{shipment_id}", context, schema="shipment")


async def get_shipment_schedule(base_url: str, shipment_id: int, context: TurnContext) -> dict:
    if shipment_id <= 0:
        raise ValueError("运单 ID 必须大于 0")
    return await _get(
        base_url, f"/api/v1/shipments/{shipment_id}/schedule", context, schema="schedule",
    )


async def list_shipment_schedule_history(
    base_url: str, shipment_id: int, context: TurnContext,
    page: int = 1, page_size: int = 20,
) -> dict:
    if shipment_id <= 0:
        raise ValueError("运单 ID 必须大于 0")
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("计划历史分页参数无效")
    return await _get(
        base_url, f"/api/v1/shipments/{shipment_id}/schedule-history", context,
        {"page": page, "page_size": page_size}, schema="schedule_history",
    )


async def list_shipment_destination_changes(
    base_url: str, shipment_id: int, context: TurnContext,
    page: int = 1, page_size: int = 20,
) -> dict:
    if shipment_id <= 0:
        raise ValueError("运单 ID 必须大于 0")
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("目的站更正记录分页参数无效")
    return await _get(
        base_url, f"/api/v1/shipments/{shipment_id}/destination-changes", context,
        {"page": page, "page_size": page_size}, schema="destination_changes",
    )


async def list_shipments(
    base_url: str,
    context: TurnContext,
    shipment_no: str | None = None,
    stage: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    params = {"page": page, "page_size": page_size}
    if shipment_no is not None:
        params["shipment_no"] = shipment_no
    if stage is not None:
        params["stage"] = stage
    return await _get(base_url, "/api/v1/shipments", context, params, schema="shipments")


async def get_stations(base_url: str, context: TurnContext) -> dict:
    return await _get(base_url, "/api/v1/stations", context, schema="stations")


async def list_orders(
    base_url: str,
    context: TurnContext,
    order_no: str | None = None,
    shipment_no: str | None = None,
    stage: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    params = {"page": page, "page_size": page_size}
    for key, value in (
        ("order_no", order_no), ("shipment_no", shipment_no), ("stage", stage),
    ):
        if value is not None:
            params[key] = value
    return await _get(base_url, "/api/v1/orders", context, params, schema="orders")


async def get_order(base_url: str, order_id: int, context: TurnContext) -> dict:
    if order_id <= 0:
        raise ValueError("订单 ID 必须大于 0")
    return await _get(base_url, f"/api/v1/orders/{order_id}", context, schema="order")


async def list_transport_tasks(
    base_url: str,
    context: TurnContext,
    task_no: str | None = None,
    route_code: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> dict:
    params = {"page": page, "page_size": page_size}
    for key, value in (
        ("task_no", task_no), ("route_code", route_code), ("status", status),
    ):
        if value is not None:
            params[key] = value
    return await _get(base_url, "/api/v1/transport-tasks", context, params, schema="tasks")


async def get_transport_task(base_url: str, task_id: int, context: TurnContext) -> dict:
    if task_id <= 0:
        raise ValueError("任务 ID 必须大于 0")
    return await _get(base_url, f"/api/v1/transport-tasks/{task_id}", context, schema="task")
