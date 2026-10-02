"""Validate only the BE fields used by the agent; discard unrelated private fields."""
from typing import Annotated, Generic, Literal, TypeVar
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, AfterValidator

Identifier = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Text = Annotated[str, StringConstraints(min_length=1)]
RouteCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")]
Stage = Literal['PENDING_PICKUP', 'PICKED_UP', 'AT_STATION', 'IN_TRANSIT', 'OUT_FOR_DELIVERY', 'SIGNED']


def aware_time(value: str) -> str:
    if datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is None:
        raise ValueError('timezone required')
    return value


Timestamp = Annotated[str, AfterValidator(aware_time)]


class Response(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')


class ShipmentRef(Response):
    id: Identifier
    shipment_no: Text
    stage: Stage


class Shipment(ShipmentRef):
    destination_station_id: Identifier
    last_scanned_station_id: Identifier | None


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
    status: Literal['PENDING_DEPARTURE', 'IN_TRANSIT', 'ARRIVED']


class ShipmentDetail(Shipment):
    active_transport_task: ActiveTransportTask | None
    tracking_events: list[Event]


class Station(Response):
    id: Identifier
    code: Text
    name: Text


class Order(Response):
    id: Identifier
    order_no: Text
    product_name: Text
    quantity: int = Field(gt=0)
    status: Text


class OrderDetail(Order):
    shipment: ShipmentRef | None


class Task(Response):
    delay_monitoring_enabled: bool
    id: Identifier
    task_no: Text
    route_code: RouteCode
    status: Literal['PENDING_DEPARTURE', 'IN_TRANSIT', 'ARRIVED']
    expected_arrival_at: Timestamp
    departed_at: Timestamp | None
    arrived_at: Timestamp | None
    delay_status: Literal['NONE', 'OVERDUE', 'LATE_ARRIVAL', 'NOT_APPLICABLE']
    delay_minutes: int | None = Field(ge=0)


class TaskDetail(Task):
    origin_station_id: Identifier
    destination_station_id: Identifier
    server_time: Timestamp
    shipments: list[ShipmentRef]


T = TypeVar('T')


class Page(Response, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class TaskPage(Page[Task]):
    server_time: Timestamp


SCHEMAS = {
    'shipments': TypeAdapter(Page[Shipment]),
    'shipment': TypeAdapter(ShipmentDetail),
    'orders': TypeAdapter(Page[Order]),
    'order': TypeAdapter(OrderDetail),
    'tasks': TypeAdapter(TaskPage),
    'task': TypeAdapter(TaskDetail),
    'stations': TypeAdapter(list[Station]),
}


def validate_response(data, schema: str):
    adapter = SCHEMAS[schema]
    return adapter.dump_python(adapter.validate_python(data), mode='json')
