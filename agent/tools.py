from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from langchain_core.tools import tool
from langgraph.runtime import get_runtime

from be_client import (
    BEClientError, get_shipment, get_shipment_schedule as read_shipment_schedule, get_stations,
    list_shipment_destination_changes, list_shipment_schedule_history,
    list_shipments,
)
from be_client import get_order as read_order
from be_client import list_orders, list_transport_tasks
from be_client import get_transport_task as read_transport_task
from state import TurnContext, TurnLimitError


ShipmentStage = Literal[
    "PENDING_PICKUP", "PICKED_UP", "AT_STATION", "IN_TRANSIT",
    "OUT_FOR_DELIVERY", "SIGNED",
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
    include_tasks: bool = Field(default=False, strict=True, description="查询延误时设为 true，组合完整计划、当前任务和轨迹历史任务")

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
    # 只暴露区域快照和履约时间窗，不把姓名或详细地址传给模型。
    keys = (
        "id", "shipment_no", "stage", "last_scanned_station_id", "destination_station_id",
        "planned_origin_station_id", "earliest_handover_at", "latest_delivery_at",
        "sender_province_name", "sender_city_name", "sender_district_name",
        "recipient_province_name", "recipient_city_name", "recipient_district_name",
    )
    return {key: item.get(key) for key in keys if key in item}


def _pagination(data: dict) -> dict:
    return {key: data[key] for key in ("page", "page_size", "total")}


def _schedule_summary(schedule: dict, leg_limit: int = 20) -> dict:
    """Keep the whole-plan facts while bounding nested arrays before serialization."""
    fields = (
        "shipment_id", "status", "reason", "version", "line_id", "line_version",
        "scheduled_trip_id", "origin_station_id", "destination_station_id", "server_time",
        "configuration_risks",
    )
    legs = []
    for leg in schedule.get("legs", [])[:leg_limit]:
        leg_fields = (
            "position", "route_id", "route_code", "origin_station_id", "destination_station_id",
            "task_id", "task_no", "task_status", "association_state", "ready_at",
            "origin_station", "destination_station",
            "planned_origin_arrival_at", "planned_departure_at", "planned_arrival_at", "forecast_departure_at",
            "forecast_arrival_at", "forecast_stale", "actual_departure_at", "actual_arrival_at",
            "scheduling_source", "schedule_revision", "planned_travel_minutes",
            "travel_reference_minutes", "transfer_reference_minutes", "approved_transfer_minutes",
        )
        item = {key: leg.get(key) for key in leg_fields if key in leg}
        waiting = leg.get("waiting_members", [])
        item["waiting_members"] = waiting[:10]
        if len(waiting) > 10:
            item["waiting_members_total"] = len(waiting)
            item["waiting_members_truncated"] = True
        legs.append(item)
    summary = {key: schedule.get(key) for key in fields if key in schedule}
    risks = schedule.get("configuration_risks", [])
    if len(risks) > 10:
        summary["configuration_risks"] = risks[:10]
        summary["configuration_risks_total"] = len(risks)
        summary["configuration_risks_truncated"] = True
    summary.update({"legs": legs, "legs_total": len(schedule.get("legs", [])), "legs_returned": len(legs)})
    if len(schedule.get("legs", [])) > leg_limit:
        summary["legs_truncated"] = True
    return summary


def _member_summary(item: dict, limit: int = 10) -> tuple[list[dict], int]:
    members = item.get("waiting_members", [])
    return members[:limit], len(members)


def _order_summary(item: dict) -> dict:
    keys = (
        "id", "order_no", "product_name", "quantity", "status",
        "earliest_handover_at", "latest_delivery_at",
        "sender_province_name", "sender_city_name", "sender_district_name",
        "recipient_province_name", "recipient_city_name", "recipient_district_name",
    )
    return {key: item.get(key) for key in keys if key in item}


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
        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，订单列表查询未完成。")
            return result
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
        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，订单详情查询未完成。")
            return result

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
        """查询运单阶段、站点及轨迹；查询延误时设 include_tasks=true，组合查询当前任务及轨迹关联的历史任务。"""
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
        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，尚未定位到唯一运单。")
            return result

        _record_request(result, response)
        shipment = response["data"]
        result["data"] = _shipment_summary(shipment)
        result["data"]["tracking_events"] = [
            {key: event[key] for key in ("id", "event_type", "occurred_at", "station_id", "task_id")}
            for event in shipment["tracking_events"]
        ]
        result["data"]["active_transport_task"] = shipment["active_transport_task"]

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

        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，站点与任务信息未查完。")

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

        result["data"]["destination_station"] = station_summary(shipment["destination_station_id"])
        result["data"]["last_scanned_station"] = station_summary(shipment["last_scanned_station_id"])
        active = result["data"]["active_transport_task"]
        if active is not None:
            active["origin_station"] = station_summary(active["origin_station_id"])
            active["destination_station"] = station_summary(active["destination_station_id"])
        for event in result["data"]["tracking_events"]:
            event["station"] = station_summary(event["station_id"])

        if result["meta"].get("stop_reason"):
            return result

        if include_tasks:
            result["meta"].setdefault("collections", {})
            schedule = shipment.get("schedule")
            if schedule is None:
                try:
                    response = await read_shipment_schedule(base_url, shipment_id, context)
                    _record_request(result, response)
                    schedule = response["data"]
                except BEClientError as exc:
                    result["status"] = "partial"
                    result["warnings"].append("完整运输计划查询失败，未来或已解除的计划段可能未纳入延误核对。")
                    if exc.request_id:
                        result["meta"]["request_ids"].append(exc.request_id)
                except TurnLimitError as exc:
                    result["status"] = "partial"
                    result["meta"]["stop_reason"] = exc.code
                    result["warnings"].append("本轮预算已耗尽，完整运输计划未能读取。")

            # 以当前计划为主，合并轨迹历史与当前执行任务；同一任务只查一次。
            task_sources: dict[int, dict] = {}

            def add_task(task_id, source, leg=None):
                if task_id is None:
                    return
                numeric_id = int(task_id)
                item = task_sources.setdefault(numeric_id, {"sources": [], "schedule_leg": None})
                if source not in item["sources"]:
                    item["sources"].append(source)
                if leg is not None:
                    item["schedule_leg"] = leg

            if schedule is not None:
                result["data"]["schedule"] = _schedule_summary(schedule)
                for leg in result["data"]["schedule"]["legs"]:
                    leg["origin_station"] = station_summary(leg.get("origin_station_id"))
                    leg["destination_station"] = station_summary(leg.get("destination_station_id"))
                if (result["data"]["schedule"].get("legs_truncated")
                    or result["data"]["schedule"].get("configuration_risks_truncated") or any(
                    leg.get("waiting_members_truncated") for leg in result["data"]["schedule"]["legs"]
                )):
                    result["meta"]["truncated"] = True
                    result["warnings"].append("运输计划分段或共享成员过多，结果已按长度限制裁剪。")
                for leg in schedule.get("legs", []):
                    add_task(leg.get("task_id"), "schedule", leg)
            if active is not None:
                add_task(active.get("id"), "active_transport_task")
            for event in shipment["tracking_events"]:
                add_task(event.get("task_id"), "tracking_event")

            task_ids = list(task_sources)
            result["data"]["tasks"] = []
            result["data"]["task_errors"] = []
            result["data"]["task_scope"] = "schedule_active_and_tracking_events"
            max_task_reads = 18
            if len(task_ids) > max_task_reads:
                result["meta"]["truncated"] = True
                result["meta"]["collections"]["tasks"] = {"total": len(task_ids), "returned": 0}
                result["warnings"].append("关联任务较多，本轮最多读取 18 个；任务列表和延误核对不完整。")
                task_ids = task_ids[:max_task_reads]
            for index, task_id in enumerate(task_ids):
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
                except TurnLimitError as exc:
                    result["status"] = "partial"
                    result["meta"]["stop_reason"] = exc.code
                    result["warnings"].append("本轮预算已耗尽，部分任务未查完。")
                    result["data"]["task_errors"].extend(
                        {"task_id": str(pending), "code": exc.code, "message": "本轮预算已耗尽，未查询"}
                        for pending in task_ids[index:]
                    )
                    break
                _record_request(result, response)
                task = response["data"]
                summary = _task_summary(task)
                summary["server_time"] = task["server_time"]
                source = task_sources[task_id]
                summary["sources"] = source["sources"]
                if source["schedule_leg"] is not None:
                    leg = source["schedule_leg"]
                    summary["association_state"] = leg.get("association_state")
                    waiting_members, waiting_total = _member_summary(leg)
                    summary["schedule_leg"] = {
                        key: leg.get(key) for key in (
                            "position", "planned_origin_arrival_at", "planned_departure_at", "planned_arrival_at",
                            "forecast_departure_at", "forecast_arrival_at", "forecast_stale",
                            "actual_departure_at", "actual_arrival_at", "ready_at",
                        )
                    }
                    summary["schedule_leg"]["waiting_members"] = waiting_members
                    if waiting_total > len(waiting_members):
                        summary["schedule_leg"]["waiting_members_total"] = waiting_total
                        summary["schedule_leg"]["waiting_members_truncated"] = True
                result["data"]["tasks"].append(summary)
            if len(task_ids) < len(task_sources):
                result["data"]["incomplete_task_ids"] = [str(item) for item in list(task_sources)[len(task_ids):]]
            if not task_ids:
                result["warnings"].append("计划、当前任务和轨迹中都没有关联任务，无法判断任务延误。")
            if "tasks" in result["meta"].get("collections", {}):
                result["meta"]["collections"]["tasks"]["returned"] = len(result["data"]["tasks"])
        return result

    return get_shipment_tracking


class ShipmentScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: int | None = Field(default=None, gt=0, strict=True, description="运单数据库 ID，与运单号二选一")
    shipment_no: ShipmentNumber | None = Field(default=None, description="完整运单号，与数据库 ID 二选一")
    include_history: bool = Field(default=False, strict=True, description="是否同时查询运输计划历史")
    history_page: int = Field(default=1, ge=1, strict=True)
    history_page_size: int = Field(default=10, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.shipment_id is None) == (self.shipment_no is None):
            raise ValueError("shipment_id 和 shipment_no 必须且只能提供一个")
        return self


class DestinationChangesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: int | None = Field(default=None, gt=0, strict=True, description="运单数据库 ID，与运单号二选一")
    shipment_no: ShipmentNumber | None = Field(default=None, description="完整运单号，与数据库 ID 二选一")
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=10, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def validate_identifier(self) -> Self:
        if (self.shipment_id is None) == (self.shipment_no is None):
            raise ValueError("shipment_id 和 shipment_no 必须且只能提供一个")
        return self


async def _resolve_shipment_id(base_url, context, shipment_id, shipment_no, result):
    if shipment_no is None:
        return shipment_id
    found = await list_shipments(base_url, context, shipment_no=shipment_no)
    _record_request(result, found)
    page = found["data"]
    matches = [item for item in page["items"] if item["shipment_no"] == shipment_no]
    if len(matches) != 1:
        result["status"] = "empty" if page["total"] == 0 else "ambiguous"
        result["data"] = {"candidates": [_shipment_summary(item) for item in page["items"]]}
        result["meta"]["pagination"] = _pagination(page)
        result["warnings"].append("未确认唯一运单，请核对完整编号或通过列表翻页选择明确 ID。")
        return None
    return int(matches[0]["id"])


def _station_lookup(response):
    return {str(item["id"]): item for item in response["data"]}


def _station_name(stations, station_id):
    if station_id is None:
        return None
    station = stations.get(str(station_id))
    return {"id": str(station_id), "code": station["code"], "name": station["name"]} if station else {
        "id": str(station_id), "code": None, "name": None,
    }


def _warn_unresolved_station(result, station_id, station):
    if station_id is not None and station.get("name") is None:
        result["status"] = "partial"
        warning = f"站点 {station_id} 的名称未知，已保留站点 ID。"
        if warning not in result["warnings"]:
            result["warnings"].append(warning)


def create_shipment_schedule_tool(base_url: str):
    @tool(args_schema=ShipmentScheduleInput)
    async def get_shipment_schedule(
        shipment_id: int | None = None,
        shipment_no: str | None = None,
        include_history: bool = False,
        history_page: int = 1,
        history_page_size: int = 10,
    ) -> dict:
        """只读查询运单完整运输计划、计划/预测/实际分段时间与关联状态；可选读取计划历史。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            shipment_id = await _resolve_shipment_id(base_url, context, shipment_id, shipment_no, result)
            if shipment_id is None:
                return result
            response = await read_shipment_schedule(base_url, shipment_id, context)
            _record_request(result, response)
            schedule = response["data"]
            result["data"] = {"schedule": _schedule_summary(schedule)}
            stations = {}
            try:
                stations_response = await get_stations(base_url, context)
                _record_request(result, stations_response)
                stations = _station_lookup(stations_response)
                schedule["origin_station"] = _station_name(stations, schedule["origin_station_id"])
                schedule["destination_station"] = _station_name(stations, schedule["destination_station_id"])
                _warn_unresolved_station(result, schedule["origin_station_id"], schedule["origin_station"])
                _warn_unresolved_station(result, schedule["destination_station_id"], schedule["destination_station"])
                for leg in schedule["legs"]:
                    leg["origin_station"] = _station_name(stations, leg["origin_station_id"])
                    leg["destination_station"] = _station_name(stations, leg["destination_station_id"])
                    _warn_unresolved_station(result, leg["origin_station_id"], leg["origin_station"])
                    _warn_unresolved_station(result, leg["destination_station_id"], leg["destination_station"])
                result["data"]["schedule"] = _schedule_summary(schedule)
                if (result["data"]["schedule"].get("legs_truncated")
                    or result["data"]["schedule"].get("configuration_risks_truncated") or any(
                        leg.get("waiting_members_truncated") for leg in result["data"]["schedule"]["legs"]
                    )):
                    result["meta"]["truncated"] = True
                    result["warnings"].append("运输计划分段或共享成员过多，结果已按长度限制裁剪。")
            except BEClientError as exc:
                result["status"] = "partial"
                result["warnings"].append("站点名称查询失败，保留站点 ID。")
                if exc.request_id:
                    result["meta"]["request_ids"].append(exc.request_id)
            except TurnLimitError as exc:
                result["status"] = "partial"
                result["meta"]["stop_reason"] = exc.code
                result["warnings"].append("本轮预算已耗尽，站点名称未能补齐。")

            if include_history and not result["meta"].get("stop_reason"):
                try:
                    history_response = await list_shipment_schedule_history(
                        base_url, shipment_id, context, history_page, history_page_size,
                    )
                    _record_request(result, history_response)
                    history_items = history_response["data"]["items"]
                    result["data"]["history"] = [
                        {**{key: item.get(key) for key in (
                            "version", "reason", "occurred_at", "line_id", "line_version", "scheduled_trip_id",
                        )}, "legs": _schedule_summary({"legs": item["legs"]})["legs"],
                         "legs_total": len(item["legs"]), "legs_returned": min(20, len(item["legs"])),
                         "legs_truncated": len(item["legs"]) > 20}
                        for item in history_items
                    ]
                    if any(item["legs_truncated"] for item in result["data"]["history"]):
                        result["meta"]["truncated"] = True
                        result["warnings"].append("部分历史计划的分段数量过多，仅返回前 20 段。")
                    for history_item in result["data"]["history"]:
                        for leg in history_item["legs"]:
                            leg["origin_station"] = _station_name(stations, leg.get("origin_station_id"))
                            leg["destination_station"] = _station_name(stations, leg.get("destination_station_id"))
                            _warn_unresolved_station(result, leg.get("origin_station_id"), leg["origin_station"])
                            _warn_unresolved_station(result, leg.get("destination_station_id"), leg["destination_station"])
                    result["meta"]["history_pagination"] = _pagination(history_response["data"])
                    if not history_response["data"]["items"]:
                        result["warnings"].append("该页没有计划历史记录；请结合 total 判断是否存在其他页。")
                except BEClientError as exc:
                    result["status"] = "partial"
                    result["warnings"].append("当前计划已读取，但计划历史查询失败。")
                    if exc.request_id:
                        result["meta"]["request_ids"].append(exc.request_id)
                except TurnLimitError as exc:
                    result["status"] = "partial"
                    result["meta"]["stop_reason"] = exc.code
                    result["warnings"].append("本轮预算已耗尽，计划历史未能读取。")
        except BEClientError as exc:
            return _error(result, exc)
        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，计划查询未完成。")
            return result
        return result

    return get_shipment_schedule


def create_destination_changes_tool(base_url: str):
    @tool(args_schema=DestinationChangesInput)
    async def get_shipment_destination_changes(
        shipment_id: int | None = None,
        shipment_no: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """只读查询运单目的站更正历史，返回原目的站、新目的站、原因和记录时间。"""
        result = _result()
        context = get_runtime(TurnContext).context
        try:
            shipment_id = await _resolve_shipment_id(base_url, context, shipment_id, shipment_no, result)
            if shipment_id is None:
                return result
            response = await list_shipment_destination_changes(base_url, shipment_id, context, page, page_size)
            _record_request(result, response)
            data = response["data"]
            changes = data["items"]
            result["data"] = {"items": changes}
            result["meta"]["pagination"] = _pagination(data)
            if not changes:
                result["status"] = "empty"
                result["warnings"].append("当前页没有目的站更正记录；请结合 total 判断，不据此推断全部历史为空。")
                return result
            try:
                stations_response = await get_stations(base_url, context)
                _record_request(result, stations_response)
                stations = _station_lookup(stations_response)
                for change in changes:
                    change["previous_destination_station"] = _station_name(stations, change["previous_destination_station_id"])
                    change["destination_station"] = _station_name(stations, change["destination_station_id"])
                    _warn_unresolved_station(result, change["previous_destination_station_id"], change["previous_destination_station"])
                    _warn_unresolved_station(result, change["destination_station_id"], change["destination_station"])
            except BEClientError as exc:
                result["status"] = "partial"
                result["warnings"].append("更正记录已读取，但站点名称查询失败；保留站点 ID。")
                if exc.request_id:
                    result["meta"]["request_ids"].append(exc.request_id)
            except TurnLimitError as exc:
                result["status"] = "partial"
                result["meta"]["stop_reason"] = exc.code
                result["warnings"].append("更正记录已读取，但本轮预算不足以补齐站点名称。")
        except BEClientError as exc:
            return _error(result, exc)
        except TurnLimitError as exc:
            result["status"] = "partial"
            result["meta"]["stop_reason"] = exc.code
            result["warnings"].append("本轮预算已耗尽，目的站更正历史查询未完成。")
            return result
        return result

    return get_shipment_destination_changes


TaskNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TaskStatus = Literal["WAITING_CARGO", "WAITING_PREDECESSOR", "PENDING_DEPARTURE", "IN_TRANSIT", "ARRIVED", "CANCELLED"]
RouteCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")]


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
    keys = (
        "id", "task_no", "route_code", "status", "expected_arrival_at",
        "departed_at", "arrived_at", "delay_status", "delay_minutes", "delay_monitoring_enabled",
        "scheduled_trip_id", "scheduled_trip_missed", "planned_departure_at",
        "forecast_departure_at", "forecast_arrival_at", "forecast_stale",
        "scheduling_source", "schedule_revision", "waiting_members",
    )
    summary = {key: item.get(key) for key in keys if key in item and key != "waiting_members"}
    if "waiting_members" in item:
        members, total = _member_summary(item)
        summary["waiting_members"] = members
        if total > len(members):
            summary["waiting_members_total"] = total
            summary["waiting_members_truncated"] = True
    return summary


def create_transport_task_search_tool(base_url: str):
    @tool(args_schema=TransportTaskSearchInput)
    async def search_transport_tasks(
        task_no: str | None = None,
        route_code: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict:
        """按完整任务号、线路编码、任务状态筛选运输任务，返回分页、服务器时间和 BE 延误结果。"""
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
            "server_time": data["server_time"],
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
                        "server_time": page["server_time"],
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
            "server_time": task["server_time"],
            "shipments": [
                {key: item[key] for key in ("id", "shipment_no", "stage")}
                for item in task["shipments"]
            ],
        })
        return result

    return get_transport_task
