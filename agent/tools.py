from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from langchain_core.tools import tool
from langgraph.runtime import get_runtime

from be_client import BEClientError, get_shipment, get_stations, list_shipments
from be_client import get_order as read_order
from be_client import list_orders, list_transport_tasks
from be_client import get_transport_task as read_transport_task
from state import TurnContext


ShipmentStage = Literal[
    "PENDING_PICKUP", "PICKED_UP", "AT_A", "IN_TRANSIT_AB", "AT_B",
    "IN_TRANSIT_BC", "AT_C", "OUT_FOR_DELIVERY", "SIGNED",
]
ShipmentNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OrderNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OrderSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_no: OrderNumber | None = Field(default=None, description="完整订单号，精确筛选")
    shipment_no: ShipmentNumber | None = Field(default=None, description="关联运单的完整编号")
    stage: ShipmentStage | None = Field(default=None, description="关联运单阶段，不是订单状态")
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=10, ge=1, le=20, strict=True)


class OrderDetailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: int | None = Field(default=None, gt=0, strict=True, description="订单数据库 ID，与订单号二选一")
    order_no: OrderNumber | None = Field(default=None, description="完整订单号，与数据库 ID 二选一")

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.order_id is None) == (self.order_no is None):
            raise ValueError("order_id 和 order_no 必须且只能提供一个")
        return self


class ShipmentSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_no: ShipmentNumber | None = Field(default=None, description="完整运单号，精确筛选")
    stage: ShipmentStage | None = None
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=10, ge=1, le=20, strict=True)


class ShipmentTrackingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: int | None = Field(default=None, gt=0, strict=True, description="运单数据库 ID，与运单号二选一")
    shipment_no: ShipmentNumber | None = Field(default=None, description="完整运单号，与数据库 ID 二选一")
    include_tasks: bool = Field(default=False, strict=True, description="查询延误时设为 true，按轨迹中的任务 ID 查询关联任务")

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.shipment_id is None) == (self.shipment_no is None):
            raise ValueError("shipment_id 和 shipment_no 必须且只能提供一个")
        return self


def _result() -> dict:
    return {
        "status": "ok", "data": None, "error": None, "warnings": [],
        "meta": {"request_ids": [], "pagination": None, "truncated": False},
    }


def _record_request(result: dict, response: dict) -> None:
    if response["request_id"]:
        result["meta"]["request_ids"].append(response["request_id"])


def _error(result: dict, exc: BEClientError) -> dict:
    result["status"] = "error"
    result["error"] = {"code": exc.code, "message": str(exc)}
    if exc.request_id:
        result["meta"]["request_ids"].append(exc.request_id)
    return result


def _shipment_summary(item: dict) -> dict:
    # 仅选取查询所需字段，不将完整地址传给模型。
    return {key: item[key] for key in (
        "id", "shipment_no", "stage", "last_scanned_station_id",
    )}


def _pagination(data: dict) -> dict:
    return {key: data[key] for key in ("page", "page_size", "total")}


def _order_summary(item: dict) -> dict:
    return {key: item[key] for key in (
        "id", "order_no", "product_name", "quantity", "status",
    )}


def create_order_search_tool(base_url: str):
    @tool(args_schema=OrderSearchInput)
    async def search_orders(
        order_no: str | None = None,
        shipment_no: str | None = None,
        stage: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """查询订单列表，按完整订单号、关联运单号或运单阶段筛选；支持分页，编号精确匹配。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            response = await list_orders(
                base_url, context, order_no, shipment_no, stage, page, page_size,
            )
        except BEClientError as exc:
            return _error(result, exc)
        _record_request(result, response)
        data = response["data"]
        result["data"] = {"items": [_order_summary(item) for item in data["items"]]}
        result["meta"]["pagination"] = _pagination(data)
        if not data["items"]:
            result["status"] = "empty"
            result["warnings"].append("当前页无结果，请结合 total 和页码判断；编号仅支持精确匹配。")
        return result

    return search_orders


def create_order_detail_tool(base_url: str):
    @tool(args_schema=OrderDetailInput)
    async def get_order(
        order_id: int | None = None,
        order_no: str | None = None,
    ) -> dict:
        """按订单 ID 或完整订单号查询详情及关联运单。查询物流轨迹时继续用 shipment.id 调用运单工具。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            if order_no is not None:
                found = await list_orders(base_url, context, order_no=order_no)
                _record_request(result, found)
                page = found["data"]
                matches = [item for item in page["items"] if item["order_no"] == order_no]
                if len(matches) != 1:
                    result["status"] = "empty" if page["total"] == 0 else "ambiguous"
                    result["data"] = {"candidates": [_order_summary(item) for item in page["items"]]}
                    result["meta"]["pagination"] = _pagination(page)
                    result["warnings"].append("未确认唯一订单，请核对完整编号或通过列表翻页选择明确 ID。")
                    return result
                order_id = int(matches[0]["id"])

            response = await read_order(base_url, order_id, context)
        except BEClientError as exc:
            return _error(result, exc)

        _record_request(result, response)
        order = response["data"]
        result["data"] = _order_summary(order)
        shipment = order["shipment"]
        result["data"]["shipment"] = (
            {key: shipment[key] for key in ("id", "shipment_no", "stage")}
            if shipment is not None else None
        )
        return result

    return get_order


def create_shipment_search_tool(base_url: str):
    @tool(args_schema=ShipmentSearchInput)
    async def search_shipments(
        shipment_no: str | None = None,
        stage: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """按完整运单号或阶段筛选运单列表，支持分页；运单号不支持模糊匹配。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            response = await list_shipments(
                base_url, context, shipment_no, stage, page, page_size,
            )
        except BEClientError as exc:
            return _error(result, exc)
        _record_request(result, response)
        data = response["data"]
        result["data"] = {"items": [_shipment_summary(item) for item in data["items"]]}
        result["meta"]["pagination"] = _pagination(data)
        if not data["items"]:
            result["status"] = "empty"
            result["warnings"].append("当前页无结果，请结合 total 和页码判断；编号仅支持精确匹配。")
        return result

    return search_shipments


def create_shipment_tracking_tool(base_url: str):
    @tool(args_schema=ShipmentTrackingInput)
    async def get_shipment_tracking(
        shipment_id: int | None = None,
        shipment_no: str | None = None,
        include_tasks: bool = False,
    ) -> dict:
        """查询运单阶段、站点及轨迹；查询延误时设 include_tasks=true，组合查询轨迹中的运输任务。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            if shipment_no is not None:
                found = await list_shipments(base_url, context, shipment_no=shipment_no)
                _record_request(result, found)
                page = found["data"]
                matches = [item for item in page["items"] if item["shipment_no"] == shipment_no]
                if len(matches) != 1:
                    result["status"] = "empty" if page["total"] == 0 else "ambiguous"
                    result["data"] = {"candidates": [_shipment_summary(item) for item in page["items"]]}
                    result["meta"]["pagination"] = _pagination(page)
                    result["warnings"].append("未确认唯一运单，请核对完整编号或通过列表翻页选择明确 ID。")
                    return result
                shipment_id = int(matches[0]["id"])

            response = await get_shipment(base_url, shipment_id, context)
        except BEClientError as exc:
            return _error(result, exc)

        _record_request(result, response)
        shipment = response["data"]
        result["data"] = _shipment_summary(shipment)
        result["data"]["tracking_events"] = [
            {key: event[key] for key in ("id", "event_type", "occurred_at", "station_id", "task_id")}
            for event in shipment["tracking_events"]
        ]

        stations = {}
        try:
            response = await get_stations(base_url, context)
            _record_request(result, response)
            stations = {str(item["id"]): item for item in response["data"]}
        except BEClientError as exc:
            result["status"] = "partial"
            result["warnings"].append("站点查询失败，保留站点 ID，名称暂无法确认。")
            if exc.request_id:
                result["meta"]["request_ids"].append(exc.request_id)

        def station_summary(station_id):
            if station_id is None:
                return None
            station = stations.get(str(station_id))
            if station is None:
                warning = f"站点 {station_id} 的名称未知。"
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
                result["status"] = "partial"
                return {"id": str(station_id), "code": None, "name": None}
            return {key: station[key] for key in ("id", "code", "name")}

        result["data"]["last_scanned_station"] = station_summary(shipment["last_scanned_station_id"])
        for event in result["data"]["tracking_events"]:
            event["station"] = station_summary(event["station_id"])

        if include_tasks:
            # 同一任务可能同时出现在发车和到达轨迹中，每个 ID 只请求一次。
            task_ids = list(dict.fromkeys(
                int(event["task_id"])
                for event in shipment["tracking_events"]
                if event["task_id"] is not None
            ))
            result["data"]["tasks"] = []
            result["data"]["task_errors"] = []
            result["data"]["task_scope"] = "tracking_events"
            if not task_ids:
                result["warnings"].append(
                    "轨迹中暂无任务 ID，无法据此判断延误，也不能认定没有关联任务；尚未发车的任务可能未进入轨迹。"
                )
            for task_id in task_ids:
                try:
                    response = await read_transport_task(base_url, task_id, context)
                except BEClientError as exc:
                    result["status"] = "partial"
                    result["data"]["task_errors"].append({
                        "task_id": str(task_id), "code": exc.code, "message": str(exc),
                    })
                    result["warnings"].append(f"任务 {task_id} 查询失败，其延误情况无法确认。")
                    if exc.request_id:
                        result["meta"]["request_ids"].append(exc.request_id)
                    continue
                _record_request(result, response)
                task = response["data"]
                summary = _task_summary(task)
                summary["simulation_time"] = task["simulation_time"]
                result["data"]["tasks"].append(summary)
        return result

    return get_shipment_tracking


TaskNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TaskStatus = Literal["PENDING_DEPARTURE", "IN_TRANSIT", "ARRIVED"]
RouteCode = Literal["AB", "BC"]


class TransportTaskSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_no: TaskNumber | None = Field(default=None, description="完整运输任务编号，精确匹配")
    route_code: RouteCode | None = None
    status: TaskStatus | None = Field(default=None, description="运输任务状态，不是运单阶段")
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=10, ge=1, le=20, strict=True)


class TransportTaskDetailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(default=None, gt=0, strict=True, description="运输任务数据库 ID，与任务编号二选一")
    task_no: TaskNumber | None = Field(default=None, description="完整运输任务编号，与数据库 ID 二选一")

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.task_id is None) == (self.task_no is None):
            raise ValueError("task_id 和 task_no 必须且只能提供一个")
        return self


def _task_summary(item: dict) -> dict:
    return {key: item[key] for key in (
        "id", "task_no", "route_code", "status", "expected_arrival_at",
        "departed_at", "arrived_at", "delay_status", "delay_minutes",
    )}


def create_transport_task_search_tool(base_url: str):
    @tool(args_schema=TransportTaskSearchInput)
    async def search_transport_tasks(
        task_no: str | None = None,
        route_code: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """按完整任务号、AB/BC 线路、任务状态筛选运输任务，返回分页、演示时间和 BE 延误结果。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            response = await list_transport_tasks(
                base_url, context, task_no, route_code, status, page, page_size,
            )
        except BEClientError as exc:
            return _error(result, exc)
        _record_request(result, response)
        data = response["data"]
        result["data"] = {
            "items": [_task_summary(item) for item in data["items"]],
            "simulation_time": data["simulation_time"],
        }
        result["meta"]["pagination"] = _pagination(data)
        if not data["items"]:
            result["status"] = "empty"
            result["warnings"].append("当前页无结果，请结合 total 和页码判断；任务号仅支持精确匹配。")
        return result

    return search_transport_tasks


def create_transport_task_detail_tool(base_url: str):
    @tool(args_schema=TransportTaskDetailInput)
    async def get_transport_task(
        task_id: int | None = None,
        task_no: str | None = None,
    ) -> dict:
        """按运输任务 ID 或完整任务编号查询线路、状态、时间、关联运单及 BE 延误信息。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            if task_no is not None:
                found = await list_transport_tasks(base_url, context, task_no=task_no)
                _record_request(result, found)
                page = found["data"]
                matches = [item for item in page["items"] if item["task_no"] == task_no]
                if len(matches) != 1:
                    result["status"] = "empty" if page["total"] == 0 else "ambiguous"
                    result["data"] = {
                        "candidates": [_task_summary(item) for item in page["items"]],
                        "simulation_time": page["simulation_time"],
                    }
                    result["meta"]["pagination"] = _pagination(page)
                    result["warnings"].append("未确认唯一任务，请核对完整编号或通过列表翻页选择明确 ID。")
                    return result
                task_id = int(matches[0]["id"])

            response = await read_transport_task(base_url, task_id, context)
        except BEClientError as exc:
            return _error(result, exc)

        _record_request(result, response)
        task = response["data"]
        result["data"] = _task_summary(task)
        result["data"].update({
            "origin_station_id": task["origin_station_id"],
            "destination_station_id": task["destination_station_id"],
            "simulation_time": task["simulation_time"],
            "shipments": [
                {key: item[key] for key in ("id", "shipment_no", "stage")}
                for item in task["shipments"]
            ],
        })
        return result

    return get_transport_task
