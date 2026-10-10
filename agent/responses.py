"""Validate the BE fields used by the agent and discard private/unneeded fields."""
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Text = Annotated[str, StringConstraints(min_length=1)]
RouteCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")]
Stage = Literal["PENDING_PICKUP", "PICKED_UP", "AT_STATION", "IN_TRANSIT", "OUT_FOR_DELIVERY", "SIGNED"]
TaskStatus = Literal["WAITING_CARGO", "WAITING_PREDECESSOR", "PENDING_DEPARTURE", "IN_TRANSIT", "ARRIVED", "CANCELLED"]
AssociationState = Literal["PLANNED", "ACTIVE", "RELEASED"]


def aware_time(value: str) -> str:
    if datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is None:
        raise ValueError("timezone required")
    return value


Timestamp = Annotated[str, AfterValidator(aware_time)]


class Response(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class AddressRegions(Response):
    sender_province_id: Identifier | None = None
    sender_city_id: Identifier | None = None
    sender_district_id: Identifier | None = None
    recipient_province_id: Identifier | None = None
    recipient_city_id: Identifier | None = None
    recipient_district_id: Identifier | None = None
    sender_province_name: str | None = None
    sender_city_name: str | None = None
    sender_district_name: str | None = None
    recipient_province_name: str | None = None
    recipient_city_name: str | None = None
    recipient_district_name: str | None = None


class ShipmentRef(Response):
    id: Identifier
    shipment_no: Text
    stage: Stage


class Shipment(ShipmentRef, AddressRegions):
    destination_station_id: Identifier
    last_scanned_station_id: Identifier | None
    planned_origin_station_id: Identifier | None = None
    earliest_handover_at: Timestamp | None = None
    latest_delivery_at: Timestamp | None = None


class Event(Response):
    id: Identifier
    event_type: Text
    occurred_at: Timestamp
    station_id: Identifier | None
    task_id: Identifier | None


class ActiveTransportTask(Response):
    id: Identifier
    route_code: RouteCode
    origin_station_id: Identifier
    destination_station_id: Identifier
    status: TaskStatus


class ScheduleLeg(Response):
    schedule_leg_id: Identifier | None = None
    position: int = Field(ge=0)
    route_id: Identifier
    route_code: RouteCode
    origin_station_id: Identifier
    destination_station_id: Identifier
    task_id: Identifier | None = None
    association_id: Identifier | None = None
    planned_departure_at: Timestamp | None = None
    planned_arrival_at: Timestamp | None = None
    planned_origin_arrival_at: Timestamp | None = None
    forecast_departure_at: Timestamp | None = None
    forecast_arrival_at: Timestamp | None = None
    forecast_stale: bool = False
    actual_departure_at: Timestamp | None = None
    actual_arrival_at: Timestamp | None = None
    task_no: str | None = None
    task_status: str | None = None
    association_state: AssociationState | None = None
    ready_at: Timestamp | None = None
    waiting_members: list[dict] = Field(default_factory=list)
    scheduling_source: str | None = None
    schedule_revision: int | None = None
    planned_travel_minutes: int | None = None
    travel_reference_minutes: int | None = None
    transfer_reference_minutes: int | None = None
    approved_transfer_minutes: int | None = None


class Schedule(Response):
    shipment_id: Identifier
    status: Text
    reason: str | None = None
    version: int = Field(ge=0)
    line_id: Identifier | None = None
    line_version: int | None = None
    scheduled_trip_id: Identifier | None = None
    origin_station_id: Identifier | None = None
    destination_station_id: Identifier
    legs: list[ScheduleLeg]
    server_time: Timestamp
    configuration_risks: list[dict] = Field(default_factory=list)


class ShipmentDetail(Shipment):
    active_transport_task: ActiveTransportTask | None
    tracking_events: list[Event]
    schedule: Schedule | None = None


class ScheduleHistoryItem(Response):
    version: int = Field(ge=0)
    reason: Text
    occurred_at: Timestamp
    legs: list[ScheduleLeg]
    line_id: Identifier | None = None
    line_version: int | None = None
    scheduled_trip_id: Identifier | None = None


class DestinationChange(Response):
    id: Identifier
    previous_destination_station_id: Identifier
    destination_station_id: Identifier
    reason: Text
    occurred_at: Timestamp


class Station(Response):
    id: Identifier
    code: Text
    name: Text


class Order(AddressRegions):
    id: Identifier
    order_no: Text
    product_name: Text
    quantity: int = Field(gt=0)
    status: Text
    earliest_handover_at: Timestamp | None = None
    latest_delivery_at: Timestamp | None = None


class OrderDetail(Order):
    shipment: ShipmentRef | None


class Task(Response):
    delay_monitoring_enabled: bool
    id: Identifier
    task_no: Text
    route_code: RouteCode
    status: TaskStatus
    expected_arrival_at: Timestamp
    departed_at: Timestamp | None
    arrived_at: Timestamp | None
    delay_status: Literal["NONE", "OVERDUE", "LATE_ARRIVAL", "NOT_APPLICABLE"]
    delay_minutes: int | None = Field(default=None, ge=0)
    scheduled_trip_id: Identifier | None = None
    scheduled_trip_missed: bool = False
    planned_departure_at: Timestamp | None = None
    forecast_departure_at: Timestamp | None = None
    forecast_arrival_at: Timestamp | None = None
    forecast_stale: bool = False
    scheduling_source: str = "SCHEDULE"
    schedule_revision: int = 1
    waiting_members: list[dict] = Field(default_factory=list)


class TaskDetail(Task):
    origin_station_id: Identifier
    destination_station_id: Identifier
    server_time: Timestamp
    shipments: list[ShipmentRef]


T = TypeVar("T")


class Page(Response, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class TaskPage(Page[Task]):
    server_time: Timestamp


class ScheduleHistoryPage(Page[ScheduleHistoryItem]):
    pass


class DestinationChangePage(Page[DestinationChange]):
    pass


SCHEMAS = {
    "shipments": TypeAdapter(Page[Shipment]),
    "shipment": TypeAdapter(ShipmentDetail),
    "schedule": TypeAdapter(Schedule),
    "schedule_history": TypeAdapter(ScheduleHistoryPage),
    "destination_changes": TypeAdapter(DestinationChangePage),
    "orders": TypeAdapter(Page[Order]),
    "order": TypeAdapter(OrderDetail),
    "tasks": TypeAdapter(TaskPage),
    "task": TypeAdapter(TaskDetail),
    "stations": TypeAdapter(list[Station]),
}


def validate_response(data, schema: str):
    adapter = SCHEMAS[schema]
    return adapter.dump_python(adapter.validate_python(data), mode="json")
